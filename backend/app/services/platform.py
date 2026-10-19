"""Application platform facade preserving the existing public import path."""

from app.services.platform_admin import PlatformAdminMixin
from app.services.platform_auto_retry import AUTO_RETRY_LIMIT, PlatformAutoRetryMixin
from app.services.platform_campaigns import PlatformCampaignsMixin
from app.services.platform_continuity import PlatformContinuityMixin
from app.services.platform_credits import PlatformCreditsMixin
from app.services.platform_execution import PlatformExecutionMixin
from app.services.platform_generation import PlatformGenerationMixin
from app.services.platform_identity import PlatformIdentityMixin
from app.services.platform_keyframes import PlatformKeyframesMixin
from app.services.platform_media_storage import PlatformMediaStorageMixin
from app.services.platform_planning import PlatformPlanningMixin
from app.services.platform_projects import PlatformProjectsMixin
from app.services.platform_providers import PlatformProvidersMixin
from app.services.platform_scene_continuity import PlatformSceneContinuityMixin
from app.services.platform_seedvis_keys import PlatformSeedvisKeysMixin
from app.services.platform_shared import *  # noqa: F403
from app.services.platform_story_preparation import PlatformStoryPreparationMixin
from app.services.platform_story_render import PlatformStoryRenderMixin
from app.services.platform_style_learning import PlatformStyleLearningMixin


class Platform(
    PlatformAdminMixin,
    PlatformAutoRetryMixin,
    PlatformCreditsMixin,
    PlatformCampaignsMixin,
    PlatformSeedvisKeysMixin,
    PlatformProvidersMixin,
    PlatformSceneContinuityMixin,
    PlatformExecutionMixin,
    PlatformMediaStorageMixin,
    PlatformStoryRenderMixin,
    PlatformStoryPreparationMixin,
    PlatformProjectsMixin,
    PlatformKeyframesMixin,
    PlatformStyleLearningMixin,
    PlatformPlanningMixin,
    PlatformContinuityMixin,
    PlatformGenerationMixin,
    PlatformIdentityMixin,
):
    def __init__(
        self,
        database: str,
        storage: Path,
        ai_url: str,
        key: str,
        provider: str = "fake",
        paid_enabled: bool = False,
        public_base_url: str | None = None,
        object_storage=None,
    ):
        if provider not in {"fake", "seedance", "poyo", "seedvis"}:
            raise RuntimeError("VIDEO_PROVIDER must be fake, seedance, poyo or seedvis")
        if provider != "fake" and not paid_enabled:
            raise RuntimeError("Paid providers require ENABLE_PAID_GENERATION=true")
        storage.mkdir(parents=True, exist_ok=True)
        self.storage = storage
        self.objects = object_storage
        self.public_base_url = (public_base_url or "").rstrip("/")
        self.signing_key = key.encode()
        self.provider = provider
        self.paid_enabled = paid_enabled
        self.planner_configured = False
        self.engine = create_engine(database, pool_pre_ping=True)
        meta.create_all(self.engine)
        batch_columns = {
            column["name"] for column in sqlalchemy_inspect(self.engine).get_columns("platform_story_batches")
        }
        if "draft_id" not in batch_columns:
            with self.engine.begin() as db:
                db.execute(text("ALTER TABLE platform_story_batches ADD COLUMN draft_id VARCHAR"))
        if "audio_mode" not in batch_columns:
            with self.engine.begin() as db:
                db.execute(text(
                    "ALTER TABLE platform_story_batches ADD COLUMN audio_mode VARCHAR "
                    "NOT NULL DEFAULT 'narration'"
                ))
        if "auto_retries" not in batch_columns:
            with self.engine.begin() as db:
                db.execute(text(
                    "ALTER TABLE platform_story_batches ADD COLUMN auto_retries INTEGER NOT NULL DEFAULT 0"
                ))
                db.execute(text("ALTER TABLE platform_story_batches ADD COLUMN retry_after FLOAT"))
                # Renders that failed before automatic retries existed are not started again on their own.
                db.execute(text("UPDATE platform_story_batches SET auto_retries = :limit"),
                           {"limit": AUTO_RETRY_LIMIT})
        scene_columns = {
            column["name"] for column in sqlalchemy_inspect(self.engine).get_columns("platform_video_scenes")
        }
        user_columns = {column["name"] for column in sqlalchemy_inspect(self.engine).get_columns("platform_users")}
        for column, ddl in (("is_admin", "INTEGER NOT NULL DEFAULT 0"), ("disabled", "INTEGER NOT NULL DEFAULT 0"),
                            ("created", "FLOAT"), ("credits", "INTEGER NOT NULL DEFAULT 0")):
            if column not in user_columns:
                with self.engine.begin() as db:
                    db.execute(text(f"ALTER TABLE platform_users ADD COLUMN {column} {ddl}"))
                    if column == "credits":
                        # Accounts from before credits start with the same welcome balance as new ones.
                        for (uid,) in db.execute(select(users.c.id)).all():
                            self.grant_signup_credits(db, uid)
        if "continuity_metadata" not in scene_columns:
            with self.engine.begin() as db:
                db.execute(text("ALTER TABLE platform_video_scenes ADD COLUMN continuity_metadata JSON"))
        self.store = ExecutionStore(self.engine)
        self.store.initialize()
        self.http = httpx.Client(base_url=ai_url, headers={"X-Internal-Key": key}, timeout=150)
        self.runtime = GenerationOrchestrator(
            self.store, VideoExecutionClient(self.http), ExecutionSettings(poll_seconds=10)
        )
        self.stop = threading.Event()
        self.batch_lock = threading.Lock()
        self.campaign_lock = threading.Lock()
        self.thread = None
        self.campaign_thread = None
        self.media_thread = None

    def start(self):
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()
        self.campaign_thread = threading.Thread(target=self.campaign_work, daemon=True)
        self.campaign_thread.start()
        if self.objects:
            self.media_thread = threading.Thread(target=self.media_work, daemon=True)
            self.media_thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=155)
        if self.campaign_thread:
            self.campaign_thread.join(timeout=155)
        if self.media_thread:
            self.media_thread.join(timeout=155)
        self.http.close()
        self.engine.dispose()

    def work(self):
        import logging

        while not self.stop.wait(1):
            try:
                for gid in self.store.due(20):
                    if self.stop.is_set():
                        return
                    job = self.runtime.tick(gid)
                    if job["status"] == "POST_PROCESSING":
                        self.finalize(job)
                with self.engine.connect() as db:
                    pending = (
                        db.execute(
                            select(executions)
                            .where(
                                executions.c.status == "POST_PROCESSING",
                                executions.c.provider.in_(("seedance", "poyo", "seedvis")),
                            )
                            .limit(20)
                        )
                        .mappings()
                        .all()
                    )
                for job in pending:
                    self.finalize(dict(job))
                self.process_story_batches()
                self.auto_retry_failed_stories()
                self.settle_render_credits()
            except Exception:
                logging.getLogger(__name__).exception("Local worker failed; journal retained")
