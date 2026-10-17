# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import time

from sqlalchemy import insert

from app.services.video_execution_store import executions
from tests.test_platform import client  # noqa: F401 - shared fixture


def failed_output_job(platform, *, created_at=None, next_run_at=0.0):
    now = time.time()
    row = {
        "generation_id": "g" * 64, "fingerprint": "f" * 64, "provider": "seedvis", "payload": {},
        "status": "NEEDS_USER_ACTION", "provider_task_id": "task-1", "error_code": "output_persistence_failed",
        "result": {"status": "completed", "outputs": [{"source_url": "https://cdn.seedvis.com/v.mp4"}]},
        "attempts": 0, "cancel_requested": False, "created_at": created_at or now, "run_started_at": now,
        "next_run_at": next_run_at, "lease_until": 0.0, "lease_token": None,
    }
    with platform.engine.begin() as db:
        db.execute(insert(executions).values(**row))
    return platform.store.get(row["generation_id"])


def test_failed_output_download_is_retried_and_spaced_out(client):
    platform = client.platform
    job = failed_output_job(platform)
    finalized = []

    def finalize(current):
        finalized.append(current["status"])
        raise RuntimeError("cdn not ready")

    platform.finalize = finalize
    platform._retry_output_download(job)
    assert finalized == ["POST_PROCESSING"]
    # The claim is atomic and the next attempt waits; the job is not retried in a tight loop.
    platform._retry_output_download(platform.store.get(job["generation_id"]))
    assert len(finalized) == 1
    assert platform.store.get(job["generation_id"])["next_run_at"] > time.time() + 60


def test_old_outputs_are_not_retried(client):
    platform = client.platform
    job = failed_output_job(platform, created_at=time.time() - 2 * 24 * 3600)
    platform.finalize = lambda current: (_ for _ in ()).throw(AssertionError("must not retry"))
    platform._retry_output_download(job)
    assert platform.store.get(job["generation_id"])["status"] == "NEEDS_USER_ACTION"
