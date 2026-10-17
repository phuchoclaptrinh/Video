from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import create_engine, update

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.video_execution_client import ExecutionError, VideoExecutionClient
from app.services.video_execution_store import ExecutionStore, executions


def payload():
    return {
        "spec": {
            "request": {"request_id": "r1", "user_id": "u1", "tenant_id": "t1"},
            "intent": {"operation": "TEXT_GENERATION", "goal": "sunrise"},
        },
        "preflight_decision": "ALLOW",
        "policy_decision_id": "p1",
    }


class StubClient:
    def __init__(self):
        self.submits = 0
        self.error = None
        self.poll_error = None

    def call(self, provider, action, **kwargs):
        if action == "validate":
            return {"valid": True, "execution_request": kwargs["payload"]}
        if action == "submit":
            self.submits += 1
            if self.error:
                raise self.error
            return {"provider_task_id": "task1", "status": "queued"}
        if self.poll_error:
            raise self.poll_error
        return {
            "provider": provider,
            "provider_task_id": "task1",
            "status": "cancelled" if action == "cancel" else "completed",
            "outputs": [{"source_url": "https://example.com/video.mp4"}],
            "usage": {"completion_tokens": 123},
        }


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "execution.sqlite"))
    store = ExecutionStore(engine)
    store.initialize()
    orchestrator = GenerationOrchestrator(
        store, StubClient(), ExecutionSettings(poll_seconds=0, backoff_seconds=0)
    )
    yield orchestrator
    engine.dispose()


def test_complete_and_redelivery_do_not_resubmit(runtime):
    job = runtime.enqueue("fake", payload())
    gid = job["generation_id"]
    assert runtime.tick(gid)["status"] == "SUBMITTED"
    assert runtime.tick(gid)["status"] == "POST_PROCESSING"
    assert runtime.tick(gid)["result"]["usage"]["completion_tokens"] == 123
    assert runtime.client.submits == 1


def test_provider_retry_after_prevents_immediate_resubmit(runtime):
    runtime.client.error = ExecutionError("seedvis_rate_limit_exceeded", retryable=True, retry_after_seconds=60)
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    assert runtime.tick(gid)["status"] == "QUEUED"
    runtime.tick(gid)
    assert runtime.client.submits == 1


def test_provider_poll_hint_delays_polling_without_resubmitting(runtime):
    original = runtime.client.call
    def call(provider, action, **kwargs):
        result = original(provider, action, **kwargs)
        if action == "submit":
            result["poll_after_seconds"] = 45
        return result
    runtime.client.call = call
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    assert runtime.tick(gid)["status"] == "SUBMITTED"
    assert runtime.tick(gid)["status"] == "SUBMITTED"
    assert runtime.client.submits == 1


def test_seedvis_poll_hint_can_be_faster_than_server_default(runtime):
    runtime.settings = ExecutionSettings(poll_seconds=10)
    original = runtime.client.call
    def call(provider, action, **kwargs):
        result = original(provider, action, **kwargs)
        if action == "submit":
            result["poll_after_seconds"] = 2
        return result
    runtime.client.call = call
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    result = runtime.tick(gid)
    assert 2 <= result["next_run_at"] - result["run_started_at"] < 4


def test_idempotency_conflict_and_tenant_scope(runtime):
    first = runtime.enqueue("fake", payload())
    assert runtime.enqueue("fake", payload())["generation_id"] == first["generation_id"]
    changed = deepcopy(payload())
    changed["spec"]["intent"]["goal"] = "changed"
    with pytest.raises(ValueError, match="idempotency_conflict"):
        runtime.enqueue("fake", changed)
    changed["spec"]["request"]["tenant_id"] = "t2"
    assert runtime.enqueue("fake", changed)["generation_id"] != first["generation_id"]


def test_atomic_claim_across_connections(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: runtime.store.claim(gid, 600), range(4)))
    assert sum(claim is not None for claim in claims) == 1


def test_uncertain_submit_never_retries(runtime):
    runtime.client.error = ExecutionError("timeout", ambiguous=True)
    gid = runtime.enqueue("fake", payload())["generation_id"]
    assert runtime.tick(gid)["status"] == "NEEDS_USER_ACTION"
    runtime.tick(gid)
    assert runtime.client.submits == 1


def test_crash_after_intent_requires_reconciliation(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    job = runtime.store.claim(gid, 600)
    runtime.store.save(job, status="SUBMITTING", lease_until=0)
    assert runtime.tick(gid)["status"] == "NEEDS_USER_ACTION"
    assert runtime.client.submits == 0


def test_restart_polls_existing_task(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    runtime.tick(gid)
    restarted = GenerationOrchestrator(
        ExecutionStore(runtime.store.engine), runtime.client, runtime.settings
    )
    assert restarted.tick(gid)["status"] == "POST_PROCESSING"
    assert runtime.client.submits == 1


def test_seedvis_restart_replays_committed_submit_intent(runtime):
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    job = runtime.store.claim(gid, 600)
    runtime.store.save(job, status="SUBMITTING", lease_until=0)
    before = runtime.store.get(gid)["payload"]
    assert runtime.tick(gid)["status"] == "SUBMITTED"
    assert runtime.client.submits == 1
    assert runtime.store.get(gid)["payload"] == before


def test_seedvis_cancel_after_unknown_submit_does_not_create_a_job(runtime):
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    job = runtime.store.claim(gid, 600)
    runtime.store.save(job, status="SUBMITTING", lease_until=0)
    runtime.store.request_cancel(gid)
    assert runtime.tick(gid)["status"] == "NEEDS_USER_ACTION"
    assert runtime.client.submits == 0


def test_seedvis_poll_url_survives_restart_and_follows_new_hint(runtime):
    original, polls = runtime.client.call, []
    first = "https://seedvis.com/api/v1/developer/generations/task1"
    second = first + "?wait=0"
    def call(provider, action, **kwargs):
        if action == "status":
            polls.append(kwargs["poll_url"])
            return {"provider_task_id": "task1", "status": "generating", "poll_url": second}
        result = original(provider, action, **kwargs)
        if action == "submit":
            result["poll_url"] = first
        return result
    runtime.client.call = call
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    assert runtime.tick(gid)["result"]["poll_url"] == first
    restarted = GenerationOrchestrator(ExecutionStore(runtime.store.engine), runtime.client, runtime.settings)
    assert restarted.tick(gid)["result"]["poll_url"] == second
    restarted.tick(gid)
    assert polls == [first, second] and runtime.client.submits == 1


def test_terminal_submit_is_recorded_without_extra_poll(runtime):
    original = runtime.client.call
    def call(provider, action, **kwargs):
        if action == "submit":
            return {"provider_task_id": "task1", "status": "failed", "error_code": "seedvis_not_started"}
        assert action == "validate"
        return original(provider, action, **kwargs)
    runtime.client.call = call
    gid = runtime.enqueue("seedvis", payload())["generation_id"]
    result = runtime.tick(gid)
    assert result["status"] == "FAILED" and result["error_code"] == "seedvis_not_started"


def test_http_status_forwards_poll_url_as_encoded_parameter():
    poll = "https://seedvis.com/api/v1/developer/generations/task1?wait=0"
    def handler(request):
        assert request.url.params["poll_url"] == poll
        return httpx.Response(200, json={"provider": "seedvis", "provider_task_id": "task1", "status": "generating"})
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)) as http:
        assert VideoExecutionClient(http).call("seedvis", "status", task_id="task1", poll_url=poll)["status"] == "generating"


@pytest.mark.parametrize("after_submit", [False, True])
def test_cancellation(runtime, after_submit):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    if after_submit:
        runtime.tick(gid)
    runtime.store.request_cancel(gid)
    assert runtime.tick(gid)["status"] == "CANCELLED"
    assert runtime.client.submits == int(after_submit)


def test_retry_budget(runtime):
    runtime.client.error = ExecutionError("rate_limit", retryable=True)
    gid = runtime.enqueue("fake", payload())["generation_id"]
    assert runtime.tick(gid)["status"] == "QUEUED"
    assert runtime.tick(gid)["status"] == "QUEUED"
    assert runtime.tick(gid)["status"] == "FAILED"
    runtime.tick(gid)
    assert runtime.client.submits == 3


def test_poll_retry_never_submits_again(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    runtime.tick(gid)
    runtime.client.poll_error = ExecutionError("unavailable", retryable=True)
    runtime.tick(gid)
    runtime.client.poll_error = None
    assert runtime.tick(gid)["status"] == "POST_PROCESSING"
    assert runtime.client.submits == 1


def test_timeout_keeps_provider_task_for_recovery(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    runtime.tick(gid)
    with runtime.store.engine.begin() as db:
        db.execute(update(executions).values(run_started_at=0))
    result = runtime.tick(gid)
    assert result["status"] == "NEEDS_USER_ACTION"
    assert result["provider_task_id"] == "task1"


def test_due_outbox_excludes_terminal_and_leased(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    assert runtime.store.due() == [gid]
    runtime.store.claim(gid, 600)
    assert runtime.store.due() == []


def test_lost_lease_cannot_overwrite_new_owner(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    old = runtime.store.claim(gid, 0)
    runtime.store.claim(gid, 600)
    with pytest.raises(RuntimeError, match="execution_lease_lost"):
        runtime.store.release(old, status="FAILED")


def test_client_submit_timeout_is_ambiguous():
    def handler(_):
        raise httpx.ReadTimeout("secret-url")

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ExecutionError) as caught:
            VideoExecutionClient(http).call("fake", "submit", payload=payload())
        assert caught.value.ambiguous
        assert "secret" not in str(caught.value)


def test_reconciliation_resumes_poll_without_submit(runtime):
    runtime.client.error = ExecutionError("timeout", ambiguous=True)
    gid = runtime.enqueue("fake", payload())["generation_id"]
    runtime.tick(gid)
    assert runtime.reconcile(gid, "task1")["status"] == "SUBMITTED"
    assert runtime.tick(gid)["status"] == "POST_PROCESSING"
    assert runtime.client.submits == 1


def test_cancel_between_claim_and_submit_is_atomic(runtime):
    gid = runtime.enqueue("fake", payload())["generation_id"]
    job = runtime.store.claim(gid, 600)
    runtime.store.request_cancel(gid)
    assert runtime.store.begin_submit(job) is False


def test_nonretryable_error_fails_once(runtime):
    runtime.client.error = ExecutionError("provider_http_400")
    gid = runtime.enqueue("fake", payload())["generation_id"]
    assert runtime.tick(gid)["status"] == "FAILED"
    runtime.tick(gid)
    assert runtime.client.submits == 1
