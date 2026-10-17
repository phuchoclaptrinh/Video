from copy import deepcopy

import pytest
from sqlalchemy import create_engine, update

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.video_execution_store import ExecutionStore, executions
from tests.test_platform import client, register  # noqa: F401 - shared fixture
from tests.test_video_execution import payload


class PoolClient:
    """Two Seedvis keys with one slot each; records which key every provider call used."""

    def __init__(self, slots=None):
        self.slots = slots if slots is not None else [{"id": "keya", "slots": 1}, {"id": "keyb", "slots": 1}]
        self.calls = []
        self.status = "generating"

    def credential_slots(self, provider):
        assert provider == "seedvis"
        return self.slots

    def call(self, provider, action, **kwargs):
        if action == "validate":
            return {"valid": True, "execution_request": kwargs["payload"]}
        self.calls.append((action, kwargs.get("credential")))
        task = "task-" + str(len(self.calls))
        if action == "submit":
            return {"provider": provider, "provider_task_id": task, "status": "queued"}
        return {"provider": provider, "provider_task_id": kwargs["task_id"], "status": self.status,
                "outputs": [{"source_url": "https://cdn.seedvis.com/video.mp4"}] if self.status == "completed" else []}


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "execution.sqlite"))
    store = ExecutionStore(engine)
    store.initialize()
    yield GenerationOrchestrator(store, PoolClient(), ExecutionSettings(poll_seconds=0, backoff_seconds=0))
    engine.dispose()


def enqueue(runtime, request_id):
    body = deepcopy(payload())
    body["spec"]["request"]["request_id"] = request_id
    return runtime.enqueue("seedvis", body)["generation_id"]


def make_due(runtime, gid):
    with runtime.store.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == gid).values(next_run_at=0))


def test_tasks_spread_over_keys_and_wait_when_every_key_is_busy(runtime):
    first, second, third = (enqueue(runtime, f"scene-{index}") for index in range(3))
    assert runtime.tick(first)["result"]["credential"] == "keya"
    assert runtime.tick(second)["result"]["credential"] == "keyb"
    waiting = runtime.tick(third)
    assert waiting["status"] == "QUEUED" and waiting["error_code"] is None
    assert waiting["next_run_at"] > waiting["created_at"] and not waiting["result"]
    assert runtime.client.calls == [("submit", "keya"), ("submit", "keyb")]
    # Polling keeps using the key that created the task.
    assert runtime.tick(first)["result"]["credential"] == "keya"
    assert runtime.client.calls[-1] == ("status", "keya")
    runtime.client.status = "completed"
    make_due(runtime, first)
    assert runtime.tick(first)["status"] == "POST_PROCESSING"
    make_due(runtime, third)
    assert runtime.tick(third)["result"]["credential"] == "keya"
    assert runtime.client.calls[-1] == ("submit", "keya")


def test_resubmission_reuses_the_key_that_owns_its_idempotency_key(runtime):
    gid = enqueue(runtime, "scene-retry")
    with runtime.store.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == gid)
                   .values(result={"credential": "keyb"}, status="QUEUED"))
    assert runtime.tick(gid)["result"]["credential"] == "keyb"
    assert runtime.client.calls == [("submit", "keyb")]


def test_queue_wait_does_not_count_against_the_run_timeout(runtime):
    runtime.client.slots = [{"id": "keya", "slots": 1}]
    busy, waiting = enqueue(runtime, "busy"), enqueue(runtime, "waiting")
    runtime.tick(busy)
    with runtime.store.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == waiting)
                   .values(run_started_at=0, created_at=0))
    runtime.forget_credential_slots()
    # run_started_at=0 would already be past the timeout; waiting in the queue must not fail the task.
    result = runtime.tick(waiting)
    assert result["status"] == "QUEUED" and result["run_started_at"] > 0


def test_tasks_submitted_before_key_pools_count_against_the_first_key(runtime):
    legacy = enqueue(runtime, "legacy")
    with runtime.store.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == legacy)
                   .values(status="GENERATING", provider_task_id="old-task", result={"status": "generating"}))
    assert runtime.tick(enqueue(runtime, "new"))["result"]["credential"] == "keyb"


def test_without_a_key_pool_tasks_use_the_default_key_without_queueing(runtime):
    runtime.client.slots = None
    gids = [enqueue(runtime, f"scene-{index}") for index in range(3)]
    assert all(runtime.tick(gid)["status"] == "SUBMITTED" for gid in gids)
    assert runtime.client.calls == [("submit", None)] * 3


def test_settings_api_lists_usage_and_refuses_to_delete_a_busy_key(client):  # noqa: F811
    from sqlalchemy import insert

    register(client)
    platform = client.platform
    keys = [{"id": "keya", "label": "A", "slots": 4, "hint": "…aaaa"}]
    sent = []

    class Response:
        def __init__(self, data, status=200):
            self.data, self.status_code = data, status

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    class PoolHttp:
        def request(self, method, path, json=None, timeout=None):
            sent.append((method, path, json))
            if method == "POST":
                keys.append({"id": "keyb", "label": json["label"], "slots": json["slots"], "hint": "…bbbb"})
            if method == "DELETE":
                keys[:] = [key for key in keys if not path.endswith(key["id"])]
            return Response({"keys": keys} if method == "GET" else {"configured": True})

        def close(self):
            return None

    platform.http = PoolHttp()
    with platform.engine.begin() as db:
        db.execute(insert(executions).values(
            generation_id="running", fingerprint="f", provider="seedvis", payload={}, status="GENERATING",
            provider_task_id="task", result={"credential": "keya"}, attempts=0, cancel_requested=False,
            created_at=1, run_started_at=1, next_run_at=1, lease_until=0,
        ))
    listed = client.get("/api/v1/seedvis/keys").json()
    assert listed == {"keys": [keys[0] | {"active": 1}], "total_slots": 4, "active": 1, "waiting": 0}
    assert client.get("/api/v1/seedvis/capacity").json() == {"keys": 1, "total_slots": 4, "active": 1, "waiting": 0}
    added = client.post("/api/v1/seedvis/keys", json={"api_key": "second-seedvis-key", "label": "B", "slots": 3})
    assert added.status_code == 201 and added.json()["total_slots"] == 7
    assert sent[2] == ("POST", "/internal/v1/video/seedvis/credentials",
                       {"api_key": "second-seedvis-key", "label": "B", "slots": 3})
    busy = client.delete("/api/v1/seedvis/keys/keya")
    assert busy.status_code == 409 and "đang chạy 1 video" in busy.json()["detail"]
    assert client.delete("/api/v1/seedvis/keys/keyb").json()["total_slots"] == 4
    assert client.delete("/api/v1/seedvis/keys/..%2Fpoyo").status_code in {404, 422}
