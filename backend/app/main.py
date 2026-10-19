import hashlib
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import delete

from app.services.object_storage import ObjectStorage
from app.services.platform import Platform, assets, sessions
from app.services.platform_style_learning import MAX_REFERENCE_BYTES
from app.services.video_execution_client import ExecutionError


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=8, max_length=128)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=5, max_length=2000)
    asset_id: str | None = None
    asset_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=6)
    planning_goal: Literal["general", "sales"] | None = None
    duration: int = Field(default=5, ge=1, le=120, strict=True)
    target_duration_seconds: int | None = Field(default=None, ge=1, le=600, strict=True)
    ratio: Literal["16:9", "9:16", "1:1", "3:4", "4:3", "21:9"] = "16:9"
    style: Literal["cinematic", "natural", "animation"] = "cinematic"
    conversation_id: str | None = None
    project_id: str | None = None
    require_planner: bool = False
    use_planner: bool = True
    provider: Literal["poyo", "seedvis", "seedance", "fake"] | None = None
    video_model: str | None = Field(default=None, min_length=1, max_length=128)
    image_provider: Literal["openai", "seedvis"] | None = None
    image_model: str | None = Field(default=None, min_length=1, max_length=128)
    style_reference_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def normalize_assets(self):
        if len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Không chọn cùng một ảnh nhiều lần")
        ids = ([self.asset_id] if self.asset_id else []) + [aid for aid in self.asset_ids if aid != self.asset_id]
        if len(ids) > 6:
            raise ValueError("Tối đa 6 ảnh đầu vào")
        self.asset_ids = ids
        self.asset_id = ids[0] if ids else None
        return self


class CampaignInput(PlanInput):
    request_id: str | None = Field(default=None, pattern=r"^[\w-]{16,64}$")
    script_count: int = Field(default=3, ge=2, le=10, strict=True)
    confirm_planning_costs: bool = False


class CampaignRetryInput(BaseModel):
    confirm_planning_costs: bool = False


class GenerateInput(BaseModel):
    confirm_paid: bool = False
    expected_draft_id: str | None = Field(default=None, min_length=1, max_length=128)
    clip_index: int | None = Field(default=None, ge=1, le=5)
    native_audio: bool | None = Field(default=None, strict=True)
    voice_mode: Literal["native_veo", "consistent_voice"] | None = None


class ProviderCredentialInput(BaseModel):
    api_key: SecretStr = Field(min_length=8, max_length=1000)


class SeedvisKeyInput(ProviderCredentialInput):
    label: str = Field(default="", max_length=40)
    slots: int = Field(default=4, ge=1, le=20)


class SeedvisKeyUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    slots: int | None = Field(default=None, ge=1, le=20)


SeedvisKeyId = Annotated[str, PathParam(pattern=r"^[a-z0-9]{4,16}$")]


class AdminUserUpdate(BaseModel):
    is_admin: bool | None = None
    disabled: bool | None = None


class PasswordInput(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class CreditAdjustment(BaseModel):
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(default="", max_length=200)


class ClipPrice(BaseModel):
    credits: int = Field(ge=0, le=100_000)
    seconds: int = Field(ge=1, le=60)


class CreditPricing(BaseModel):
    signup_bonus: int = Field(ge=0, le=1_000_000)
    video_clip: dict[Annotated[str, Field(max_length=80)], ClipPrice] = Field(max_length=100)
    plan: int = Field(ge=0, le=100_000)
    campaign: int = Field(ge=0, le=100_000)
    image: int = Field(ge=0, le=100_000)
    hidden_models: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=100)


class ChatMessageInput(BaseModel):
    conversation_id: str | None = None
    content: str = Field(min_length=1, max_length=12000, strip_whitespace=True)


class ChatVideoEventInput(BaseModel):
    conversation_id: str | None = None
    prompt: str = Field(min_length=5, max_length=2000, strip_whitespace=True)
    plan: dict


class ChatVideoJobInput(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)


class ChatVideoPlanInput(BaseModel):
    plan: dict


class FailedSceneRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scene_index: int = Field(ge=1, le=5)
    video_prompt: str = Field(min_length=30, max_length=3000)
    voice_script: str = Field(min_length=2, max_length=500)


class MemoryInput(BaseModel):
    content: str = Field(min_length=2, max_length=1000, strip_whitespace=True)


def create_app(platform: Platform | None = None, run_worker: bool = True):
    @asynccontextmanager
    async def lifespan(app):
        current = platform
        if current is None:
            key = os.environ["INTERNAL_API_KEY"]
            storage = Path(os.getenv("STORAGE_LOCAL_PATH", "./storage"))
            current = Platform(
                os.getenv(
                    "VIDEO_EXECUTION_DATABASE_URL", "sqlite:///" + str(storage / "mvp.sqlite3")
                ),
                storage,
                os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8001"),
                key,
                provider=os.getenv("VIDEO_PROVIDER", "fake").lower(),
                paid_enabled=os.getenv("ENABLE_PAID_GENERATION", "false").lower() == "true",
                public_base_url=os.getenv("PUBLIC_BASE_URL"),
                object_storage=ObjectStorage.from_env(),
            )
        app.state.platform = current
        if run_worker and os.getenv("LOCAL_VIDEO_WORKER", "true") == "true":
            current.start()
        yield
        current.close()

    app = FastAPI(title="VO3 Studio API", lifespan=lifespan)

    def video_file(path: Path, filename: str | None):
        if path.is_file():
            return FileResponse(path, media_type="video/mp4", filename=filename)
        # Moved off the volume: the browser plays or downloads it straight from object storage.
        return RedirectResponse(app.state.platform.media_url(path, "video/mp4", filename), status_code=307)
    attempts = defaultdict(deque)

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = set(
                os.getenv("APP_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            )
            if origin and origin not in allowed:
                return JSONResponse(status_code=403, content={"detail": "Origin không hợp lệ"})
        if request.url.path.startswith("/api/v1/auth/") and request.method == "POST":
            address = request.client.host if request.client else "unknown"
            queue = attempts[address]
            now = time.monotonic()
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= 15:
                return JSONResponse(status_code=429, content={"detail": "Thử lại sau một phút"})
            queue.append(now)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def current(request: Request):
        return app.state.platform.user(request.cookies.get("vo3_session", ""))

    def admin(user=Depends(current)):
        return app.state.platform.require_admin(user)

    @app.exception_handler(ExecutionError)
    async def execution_error(request, exc):
        return JSONResponse(
            status_code=503, content={"detail": "Dịch vụ video chưa sẵn sàng. Vui lòng thử lại."}
        )

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "mode": app.state.platform.mode}

    @app.get("/api/v1/config")
    def public_config():
        platform = app.state.platform
        is_poyo = platform.provider == "poyo"
        planner = platform.planner_credential_status()
        capabilities = platform._continuity_strategy().capabilities
        return {
            "mode": platform.mode,
            "provider": platform.provider,
            "paid_generation": platform.paid_enabled,
            "planner": "openai" if planner["configured"] else "template",
            "planner_model": os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"),
            "model": capabilities.model,
            "image_provider": os.getenv("IMAGE_PROVIDER", "openai"),
            "image_model": os.getenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2") if os.getenv("IMAGE_PROVIDER") == "seedvis" else os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare"),
            "native_audio": capabilities.native_audio,
            "duration_control": bool(capabilities.generation_durations),
            "duration_options": capabilities.generation_durations if platform.provider != "fake" or planner["configured"] else [5, 8, 10],
            "audio_control": capabilities.audio_control,
            "voice_modes": ["native_veo", "consistent_voice"],
            "consistent_voice_available": False,
            "consistent_voice_limitations": ["speech_alignment_unavailable", "lip_sync_unavailable"],
            "ratio_options": (["16:9", "9:16"] if is_poyo or (platform.provider == "seedvis" and capabilities.model in {"Veo-3.1", "Omni-Flash"})
                              else ["16:9", "9:16", "1:1", "3:4", "4:3", "21:9"] if platform.provider == "seedvis" else ["16:9", "9:16", "1:1"]),
            "local_image_upload": True,
            "continuity_enabled": platform.provider in {"seedvis", "poyo"} or platform.public_base_url.startswith("https://"),
        }

    @app.post("/api/v1/auth/{action}")
    def auth(action: Literal["login", "register"], body: Credentials, response: Response):
        token, user = app.state.platform.login(
            body.email.lower().strip(), body.password, action == "register"
        )
        response.set_cookie(
            "vo3_session",
            token,
            httponly=True,
            samesite="lax",
            max_age=86400,
            secure=os.getenv("COOKIE_SECURE", "false") == "true",
        )
        # Same shape as /me, so the client can route admins to the admin console right away.
        account = app.state.platform.user(token)
        return {"id": user["id"], "email": user["email"], "is_admin": app.state.platform.is_admin(account)}

    @app.delete("/api/v1/auth/session")
    def logout(request: Request, response: Response):
        token = hashlib.sha256(request.cookies.get("vo3_session", "").encode()).hexdigest()
        with app.state.platform.engine.begin() as db:
            db.execute(delete(sessions).where(sessions.c.token == token))
        response.delete_cookie("vo3_session")
        return {"ok": True}

    @app.get("/api/v1/me")
    def me(user=Depends(current)):
        return {"id": user["id"], "email": user["email"], "is_admin": app.state.platform.is_admin(user)}

    @app.get("/api/v1/credits")
    def credits(user=Depends(current)):
        return app.state.platform.credit_summary(user["id"])

    @app.get("/api/v1/chat/conversations")
    def chat_conversations(user=Depends(current)):
        return app.state.platform.conversation_list(user["id"])

    @app.get("/api/v1/chat/conversations/{conversation_id}/messages")
    def chat_messages(conversation_id: str, user=Depends(current)):
        return app.state.platform.conversation_messages(user["id"], conversation_id)

    @app.post("/api/v1/chat/messages")
    def send_chat_message(body: ChatMessageInput, user=Depends(current)):
        return app.state.platform.chat(user["id"], body.conversation_id, body.content)

    @app.post("/api/v1/chat/video-events", status_code=201)
    def save_chat_video_event(body: ChatVideoEventInput, user=Depends(current)):
        return app.state.platform.save_video_event(
            user["id"], body.conversation_id, body.prompt, body.plan
        )

    @app.put("/api/v1/chat/video-events/{message_id}")
    def update_chat_video_event(message_id: str, body: ChatVideoJobInput, user=Depends(current)):
        return app.state.platform.update_video_event(user["id"], message_id, body.job_id)

    @app.put("/api/v1/chat/video-events/{message_id}/plan")
    def update_chat_video_plan(message_id: str, body: ChatVideoPlanInput, user=Depends(current)):
        return app.state.platform.update_video_event_plan(user["id"], message_id, body.plan)

    @app.put("/api/v1/chat/video-events/{message_id}/failed-scene")
    def revise_failed_scene(message_id: str, body: FailedSceneRevisionInput, user=Depends(current)):
        return app.state.platform.revise_failed_story_scene(
            user["id"], message_id, body.scene_index, body.video_prompt, body.voice_script
        )

    @app.get("/api/v1/video-projects/{project_id}")
    def video_project(project_id: str, user=Depends(current)):
        return app.state.platform.video_project(user["id"], project_id)

    @app.post("/api/v1/video-projects/{project_id}/reference-image")
    def prepare_video_reference(project_id: str, user=Depends(current)):
        return app.state.platform.prepare_video_reference(user["id"], project_id)

    @app.post("/api/v1/video-projects/{project_id}/reference-image/regenerate")
    def regenerate_failed_first_reference(project_id: str, user=Depends(current)):
        return app.state.platform.regenerate_failed_first_reference(user["id"], project_id)

    @app.get("/api/v1/style-references")
    def list_style_references(user=Depends(current)):
        return app.state.platform.style_references(user["id"])

    @app.post("/api/v1/style-references", status_code=201)
    async def learn_style_reference(
        file: UploadFile | None = File(None), url: str | None = Form(None), title: str | None = Form(None),
        user=Depends(current),
    ):
        data = None
        if file is not None:
            data = await file.read(MAX_REFERENCE_BYTES + 1)
            await file.close()
            if len(data) > MAX_REFERENCE_BYTES:
                raise HTTPException(413, "Video tham khảo tối đa 50 MB")
        # Analysis blocks on ffmpeg and the AI service, so it runs off the event loop.
        return await run_in_threadpool(
            app.state.platform.learn_style, user["id"], data=data,
            filename=(file.filename if file else "") or "", url=url, title=title,
        )

    @app.delete("/api/v1/style-references/{reference_id}")
    def delete_style_reference(reference_id: str, user=Depends(current)):
        app.state.platform.delete_style_reference(user["id"], reference_id)
        return {"ok": True}

    @app.get("/api/v1/video-projects/{project_id}/reference-image")
    def video_reference(project_id: str, user=Depends(current)):
        path = app.state.platform.video_reference_path(user["id"], project_id)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/v1/video-projects/{project_id}/scene-reference/{scene_index}")
    def video_scene_reference(project_id: str, scene_index: int, user=Depends(current)):
        path = app.state.platform.video_scene_reference_path(user["id"], project_id, scene_index)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/v1/marketing-campaigns", status_code=202)
    def create_campaign(body: CampaignInput, user=Depends(current)):
        return app.state.platform.create_campaign(user["id"], body)

    @app.get("/api/v1/marketing-campaigns")
    def campaign_list(conversation_id: str | None = None, user=Depends(current)):
        return app.state.platform.campaign_list(user["id"], conversation_id)

    @app.get("/api/v1/marketing-campaigns/{campaign_id}")
    def campaign(campaign_id: str, user=Depends(current)):
        return app.state.platform.campaign(user["id"], campaign_id)

    @app.post("/api/v1/marketing-campaigns/{campaign_id}/cancel")
    def cancel_campaign(campaign_id: str, user=Depends(current)):
        return app.state.platform.cancel_campaign(user["id"], campaign_id)

    @app.post("/api/v1/marketing-campaigns/{campaign_id}/retry", status_code=202)
    def retry_campaign(campaign_id: str, body: CampaignRetryInput, user=Depends(current)):
        return app.state.platform.retry_campaign(user["id"], campaign_id, body.confirm_planning_costs)

    @app.post("/api/v1/video-projects/{project_id}/approve")
    def approve_video_plan(project_id: str, user=Depends(current)):
        return app.state.platform.approve_video_plan(user["id"], project_id)

    @app.post("/api/v1/video-projects/{project_id}/renders", status_code=202)
    def generate_approved_video(
        project_id: str, body: GenerateInput, user=Depends(current)
    ):
        return app.state.platform.generate_approved_video(
            user["id"], project_id, body.confirm_paid,
            voice_mode=body.voice_mode, native_audio=body.native_audio,
            expected_draft_id=body.expected_draft_id,
        )

    @app.post("/api/v1/video-projects/{project_id}/plans", status_code=201)
    def replan_video_project(project_id: str, body: PlanInput, user=Depends(current)):
        revised = body.model_copy(update={"project_id": project_id})
        return app.state.platform.plan(user["id"], revised)

    @app.delete("/api/v1/chat/conversations/{conversation_id}")
    def delete_chat_conversation(conversation_id: str, user=Depends(current)):
        app.state.platform.delete_conversation(user["id"], conversation_id)
        return {"ok": True}

    @app.get("/api/v1/memories")
    def memories(user=Depends(current)):
        return app.state.platform.user_memories(user["id"])

    @app.post("/api/v1/memories", status_code=201)
    def add_memory(body: MemoryInput, user=Depends(current)):
        return app.state.platform.add_memory(user["id"], body.content)

    @app.delete("/api/v1/memories/{memory_id}")
    def delete_memory(memory_id: str, user=Depends(current)):
        app.state.platform.delete_memory(user["id"], memory_id)
        return {"ok": True}

    @app.get("/api/v1/provider-credential")
    def provider_credential_status(provider: Literal["poyo", "seedvis"] | None = None, user=Depends(current)):
        return app.state.platform.provider_credential_status(provider)

    @app.get("/api/v1/providers")
    def available_providers(refresh: bool = False, user=Depends(current)):
        platform = app.state.platform
        return platform.visible_catalog(platform.provider_catalog(refresh=refresh), user["id"])

    @app.put("/api/v1/provider-credential")
    def set_provider_credential(body: ProviderCredentialInput, provider: Literal["poyo", "seedvis"] | None = None, user=Depends(admin)):
        return app.state.platform.set_provider_credential(body.api_key.get_secret_value(), provider)

    @app.delete("/api/v1/provider-credential")
    def delete_provider_credential(provider: Literal["poyo", "seedvis"] | None = None, user=Depends(admin)):
        return app.state.platform.delete_provider_credential(provider)

    @app.get("/api/v1/seedvis/keys")
    def seedvis_keys(user=Depends(admin)):
        return app.state.platform.seedvis_keys()

    @app.get("/api/v1/seedvis/capacity")
    def seedvis_capacity(user=Depends(current)):
        return app.state.platform.seedvis_capacity()

    @app.post("/api/v1/seedvis/keys", status_code=201)
    def add_seedvis_key(body: SeedvisKeyInput, user=Depends(admin)):
        return app.state.platform.add_seedvis_key(
            body.api_key.get_secret_value().strip(), body.label.strip(), body.slots,
        )

    @app.patch("/api/v1/seedvis/keys/{key_id}")
    def update_seedvis_key(key_id: SeedvisKeyId, body: SeedvisKeyUpdate, user=Depends(admin)):
        return app.state.platform.update_seedvis_key(
            key_id, None if body.label is None else body.label.strip(), body.slots,
        )

    @app.delete("/api/v1/seedvis/keys/{key_id}")
    def delete_seedvis_key(key_id: SeedvisKeyId, user=Depends(admin)):
        return app.state.platform.delete_seedvis_key(key_id)

    @app.get("/api/v1/seedvis/models")
    def seedvis_models(user=Depends(current)):
        try:
            response = app.state.platform.http.get("/internal/v1/seedvis/models")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Không thể đọc danh sách model Seedvis") from None

    @app.get("/api/v1/planner-credential")
    def planner_credential_status(user=Depends(current)):
        return app.state.platform.planner_credential_status()

    @app.put("/api/v1/planner-credential")
    def set_planner_credential(body: ProviderCredentialInput, user=Depends(admin)):
        return app.state.platform.set_planner_credential(body.api_key.get_secret_value())

    @app.delete("/api/v1/planner-credential")
    def delete_planner_credential(user=Depends(admin)):
        return app.state.platform.delete_planner_credential()

    @app.get("/api/v1/admin/backup")
    def admin_backup(user=Depends(admin)):
        snapshot = app.state.platform.database_snapshot()
        return FileResponse(
            snapshot, media_type="application/vnd.sqlite3",
            filename=time.strftime("vo3-backup-%Y%m%d-%H%M%S.sqlite3"),
            background=BackgroundTask(snapshot.unlink, missing_ok=True),
        )

    @app.get("/api/v1/admin/overview")
    def admin_overview(user=Depends(admin)):
        return app.state.platform.admin_overview()

    @app.get("/api/v1/admin/videos")
    def admin_videos(status: Literal["active", "FAILED", "COMPLETED", "CANCELLED", "all"] = "active",
                     user=Depends(admin)):
        return app.state.platform.admin_videos(status)

    @app.get("/api/v1/admin/queue")
    def admin_queue(user=Depends(admin)):
        return app.state.platform.admin_queue()

    @app.post("/api/v1/admin/videos/{batch_id}/cancel")
    def admin_cancel_video(batch_id: Annotated[str, PathParam(pattern=r"^[a-f0-9]{32}$")], user=Depends(admin)):
        return app.state.platform.admin_cancel_video(batch_id)

    @app.get("/api/v1/admin/users")
    def admin_users(user=Depends(admin)):
        return app.state.platform.admin_users()

    @app.patch("/api/v1/admin/users/{user_id}")
    def admin_update_user(user_id: Annotated[str, PathParam(pattern=r"^[a-f0-9]{32}$")], body: AdminUserUpdate,
                          user=Depends(admin)):
        return app.state.platform.admin_update_user(user, user_id, is_admin=body.is_admin, disabled=body.disabled)

    @app.post("/api/v1/admin/users", status_code=201)
    def admin_create_user(body: Credentials, user=Depends(admin)):
        return app.state.platform.admin_create_user(body.email, body.password)

    @app.put("/api/v1/admin/users/{user_id}/password")
    def admin_set_password(user_id: Annotated[str, PathParam(pattern=r"^[a-f0-9]{32}$")], body: PasswordInput,
                           user=Depends(admin)):
        return app.state.platform.admin_set_password(user_id, body.password)

    @app.get("/api/v1/admin/users/{user_id}/credits")
    def admin_user_credits(user_id: Annotated[str, PathParam(pattern=r"^[a-f0-9]{32}$")], user=Depends(admin)):
        return app.state.platform.credit_summary(user_id, limit=200)

    @app.post("/api/v1/admin/users/{user_id}/credits")
    def admin_adjust_credits(user_id: Annotated[str, PathParam(pattern=r"^[a-f0-9]{32}$")], body: CreditAdjustment,
                             user=Depends(admin)):
        return app.state.platform.admin_adjust_credits(user, user_id, body.delta, body.note)

    @app.get("/api/v1/admin/credit-pricing")
    def admin_credit_pricing(user=Depends(admin)):
        return app.state.platform.credit_pricing()

    @app.put("/api/v1/admin/credit-pricing")
    def admin_set_credit_pricing(body: CreditPricing, user=Depends(admin)):
        return app.state.platform.set_credit_pricing(body.model_dump())

    @app.post("/api/v1/assets", status_code=201)
    async def upload(file: UploadFile = File(...), user=Depends(current)):
        data = await file.read(8 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "Ảnh tối đa 8 MB")
        return app.state.platform.upload(data, user["id"])

    @app.get("/api/v1/assets/{aid}/file")
    def asset_file(aid: str, user=Depends(current)):
        row = app.state.platform.owned(assets, aid, user["id"])
        return FileResponse(app.state.platform.storage / row["filename"], media_type="image/jpeg")

    @app.api_route(
        "/api/v1/continuity-reference/{kind}/{reference_id}/{expires}/{signature}",
        methods=["GET", "HEAD"],
    )
    def continuity_reference(kind: str, reference_id: str, expires: int, signature: str):
        path = app.state.platform.continuity_reference_path(
            kind, reference_id, expires, signature
        )
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/v1/plans", status_code=201)
    def plan(body: PlanInput, user=Depends(current)):
        return app.state.platform.plan(user["id"], body)

    @app.post("/api/v1/plans/{did}/generate", status_code=202)
    def generate(did: str, body: GenerateInput | None = None, user=Depends(current)):
        return app.state.platform.generate(
            user["id"],
            did,
            body.confirm_paid if body is not None else False,
            body.clip_index if body is not None else None,
            native_audio=body.native_audio if body else None,
            voice_mode=body.voice_mode if body else None,
        )

    @app.get("/api/v1/generations")
    def generations(user=Depends(current)):
        return app.state.platform.jobs(user["id"])

    @app.get("/api/v1/generations/{gid}")
    def generation(gid: str, user=Depends(current)):
        return app.state.platform.job(user["id"], gid)

    @app.post("/api/v1/generations/{gid}/cancel")
    def cancel(gid: str, user=Depends(current)):
        app.state.platform.job(user["id"], gid)
        app.state.platform.store.request_cancel(gid)
        return app.state.platform.job(user["id"], gid)

    @app.get("/api/v1/generations/{gid}/video")
    def video(gid: str, download: bool = False, user=Depends(current)):
        job = app.state.platform.job(user["id"], gid)
        if job["status"] != "COMPLETED":
            raise HTTPException(409, "Video chưa hoàn thành")
        fixture = app.state.platform.video_path(user["id"], gid)
        return video_file(fixture, "vo3-video.mp4" if download else None)

    @app.get("/api/v1/story-batches/{bid}/video")
    def story_video(bid: str, download: bool = False, user=Depends(current)):
        path = app.state.platform.story_batch_video_path(user["id"], bid)
        return video_file(path, "video-hoan-chinh.mp4" if download else None)

    @app.post("/api/v1/story-batches/{bid}/merge")
    def retry_story_merge(bid: str, user=Depends(current)):
        return app.state.platform.retry_story_batch_merge(user["id"], bid)

    return app


app = create_app()
