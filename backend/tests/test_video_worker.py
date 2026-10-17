from contextlib import contextmanager

from sqlalchemy import create_engine

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.video_execution_store import ExecutionStore
from app.workers.celery_app import celery_app
from app.workers.tasks import video_tasks


def test_celery_dispatch_recovery_and_duplicate_delivery(tmp_path, monkeypatch):
    class Client:
        submits = 0

        def call(self, provider, action, **kwargs):
            if action == "validate":
                return {"execution_request": kwargs["payload"]}
            if action == "submit":
                self.submits += 1
                return {"provider_task_id": "task1", "status": "queued"}
            return {"status": "completed", "outputs": [{"source_url": "https://example.com"}]}

    engine = create_engine("sqlite:///" + str(tmp_path / "worker.sqlite"))
    store = ExecutionStore(engine)
    store.initialize()
    client = Client()
    runtime = GenerationOrchestrator(store, client, ExecutionSettings(poll_seconds=0))

    @contextmanager
    def factory():
        yield runtime

    monkeypatch.setattr(video_tasks, "execution_runtime", factory)
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")
    try:
        job = video_tasks.enqueue_video(
            "fake",
            {
                "spec": {
                    "request": {"request_id": "r", "user_id": "u", "tenant_id": "t"},
                }
            },
        )
        video_tasks.recover_video.apply().get()
        assert store.get(job["generation_id"])["status"] == "SUBMITTED"
        video_tasks.recover_video.apply().get()
        assert store.get(job["generation_id"])["status"] == "POST_PROCESSING"
        video_tasks.advance_video.apply(args=[job["generation_id"]]).get()
        assert client.submits == 1
    finally:
        engine.dispose()
