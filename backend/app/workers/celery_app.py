import os

from celery import Celery

celery_app = Celery(
    "video_execution",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    include=["app.workers.tasks.video_tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue="default",
    task_routes={"video.*": {"queue": "video"}},
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    beat_schedule={
        "recover-video-executions": {
            "task": "video.recover",
            "schedule": 15.0,
        }
    },
)
