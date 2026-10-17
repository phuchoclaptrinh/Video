import os
from contextlib import contextmanager

import httpx
from sqlalchemy import create_engine

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.video_execution_client import VideoExecutionClient
from app.services.video_execution_store import ExecutionStore
from app.workers.celery_app import celery_app


@contextmanager
def execution_runtime():
    key = os.environ["INTERNAL_API_KEY"]
    # Explicit dedicated URL keeps platform tables and migrations outside this module.
    engine = create_engine(os.environ["VIDEO_EXECUTION_DATABASE_URL"], pool_pre_ping=True)
    try:
        with httpx.Client(
            base_url=os.getenv("AI_SERVICE_URL", "http://localhost:8001"),
            headers={"X-Internal-Key": key},
            timeout=150,
            follow_redirects=False,
        ) as client:
            yield GenerationOrchestrator(
                ExecutionStore(engine),
                VideoExecutionClient(client),
                ExecutionSettings(
                    max_retries=int(os.getenv("PROVIDER_MAX_RETRIES", "3")),
                    backoff_seconds=float(os.getenv("PROVIDER_RETRY_BACKOFF_SECONDS", "5")),
                    poll_seconds=float(os.getenv("VIDEO_POLL_INTERVAL_SECONDS", "10")),
                    timeout_seconds=float(os.getenv("VIDEO_JOB_TIMEOUT_SECONDS", "900")),
                ),
            )
    finally:
        engine.dispose()


@celery_app.task(name="video.advance")
def advance_video(generation_id: str):
    with execution_runtime() as runtime:
        runtime.tick(generation_id)


@celery_app.task(name="video.recover")
def recover_video():
    with execution_runtime() as runtime:
        for generation_id in runtime.store.due():
            advance_video.delay(generation_id)


def enqueue_video(provider: str, payload: dict) -> dict:
    with execution_runtime() as runtime:
        job = runtime.enqueue(provider, payload)
    # The periodic recovery task handles dispatch, including broker outages on API requests.
    return job


def cancel_video(generation_id: str) -> dict:
    with execution_runtime() as runtime:
        return runtime.store.request_cancel(generation_id)


if __name__ == "__main__":
    with execution_runtime() as runtime:
        runtime.store.initialize()
