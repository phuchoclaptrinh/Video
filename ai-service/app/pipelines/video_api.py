"""Standalone Person 3 entrypoint; the shared app.main remains owned by the team."""

import base64
import logging
import os
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, SecretStr, model_validator

from app.core.credential_vault import CredentialVault
from app.core.voice_presets import tts_voice_instructions
from app.pipelines.marketing_planner import create_marketing_campaign
from app.pipelines.planner_transport import generation_params
from app.pipelines.reference_grounding import ground_book_story
from app.pipelines.seedvis_images import generate_reference_image
from app.pipelines.story_planner import create_book_story_plan, expand_story_narration
from app.pipelines.style_learning import learn_style
from app.pipelines.video_generation import VideoGenerationPipeline
from app.pipelines.visual_input import InlineImage
from app.providers.catalog import provider_catalog
from app.providers.fake import FakeVideoProvider
from app.providers.poyo import PoyoVeoProvider
from app.providers.registry import ProviderRegistry
from app.providers.seedance import SeedanceProvider
from app.providers.seedvis import SeedvisProvider
from app.providers.seedvis_keys import MAX_SLOTS, SeedvisKeyPool
from app.providers.seedvis_models import VIDEO_MODELS
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError, ProviderResult, VideoProviderCapabilities

logger = logging.getLogger(__name__)


class CredentialInput(BaseModel):
    api_key: SecretStr = Field(min_length=8, max_length=1000)


class PoolKeyInput(CredentialInput):
    label: str = Field(default="", max_length=40)
    slots: int = Field(default=4, ge=1, le=MAX_SLOTS)


class PoolKeyUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    slots: int | None = Field(default=None, ge=1, le=MAX_SLOTS)


# Pool key ids only; validated so a request can never address another vault entry.
CredentialId = Annotated[str | None, Query(pattern=r"^[a-z0-9]{4,16}$")]
PoolKeyId = Annotated[str, PathParam(pattern=r"^[a-z0-9]{4,16}$")]


class PlannerInput(BaseModel):
    prompt: str = Field(min_length=2, max_length=2000, strip_whitespace=True)
    project_context: dict | None = None
    provider: str | None = Field(default=None, pattern=r"^[\w-]{1,32}$")
    video_model: str | None = Field(default=None, min_length=1, max_length=128)
    image_data_url: InlineImage | None = None
    image_data_urls: list[InlineImage] = Field(default_factory=list, max_length=6)
    planning_goal: Literal["general", "sales"] = "general"
    target_duration_seconds: int | None = Field(default=None, ge=1, le=600, strict=True)

    @model_validator(mode="after")
    def bounded_images(self):
        if self.image_data_url and self.image_data_urls:
            raise ValueError("Use either image_data_url or image_data_urls")
        return self


ReferenceImage = Annotated[
    str, Field(min_length=100, max_length=12_000_000, pattern=r"^data:image/jpeg;base64,[A-Za-z0-9+/]+={0,2}$"),
]


class SceneImageInput(BaseModel):
    provider: Literal["openai", "seedvis"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str = Field(min_length=20, max_length=4000)
    aspect_ratio: str = Field(pattern=r"^(16:9|9:16)$")
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Product photos and earlier keyframes; when present the image is an edit that must match them.
    reference_images: list[ReferenceImage] = Field(default_factory=list, max_length=4)


class CampaignPlannerInput(PlannerInput):
    script_count: int = Field(default=3, ge=2, le=10, strict=True)


class StyleFrame(BaseModel):
    time_seconds: float = Field(ge=0, le=600)
    image_data_url: ReferenceImage


class StyleLearningInput(BaseModel):
    frames: list[StyleFrame] = Field(min_length=1, max_length=16)
    transcript: str = Field(default="", max_length=8000)
    duration_seconds: float = Field(gt=0, le=600)
    cut_times: list[float] = Field(default_factory=list, max_length=300)
    audio_present: bool = True
    source_label: str = Field(default="", max_length=300)
    speech_metrics: dict = Field(default_factory=dict)


class ReferenceGroundingInput(BaseModel):
    image_data_url: str = Field(min_length=100, max_length=16000000)
    story_plan: dict
    user_request: str = Field(min_length=2, max_length=2000)


class VoiceInput(BaseModel):
    text: str = Field(min_length=2, max_length=4096)
    response_format: str = Field(default="mp3", pattern=r"^(mp3|wav)$")
    voice_bible: str = Field(default="", max_length=600)


class NarrationExpansionInput(BaseModel):
    clips: list[dict] = Field(min_length=1, max_length=5)
    target_duration_seconds: int = Field(ge=8, le=40)
    current_duration_seconds: float = Field(gt=0, le=120)
    voice_bible: str = Field(default="", max_length=600)


class ChatMessage(BaseModel):
    role: str = Field(pattern=r"^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=16000)


class ChatCompletionInput(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=64)


def create_app(
    registry: ProviderRegistry | None = None, internal_key: str | None = None
) -> FastAPI:
    key = internal_key or os.getenv("INTERNAL_API_KEY", "")
    vault = CredentialVault(
        os.getenv("PROVIDER_CREDENTIAL_PATH", "./storage/provider-credentials.json.enc"), key
    )
    seedvis_keys = SeedvisKeyPool(vault, os.getenv("SEEDVIS_API_KEY"))
    # Saved keys live in the vault file; adapters keep a copy in memory per process.
    credential_stamp = {"value": None}

    def vault_stamp():
        try:
            stat = vault.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if not key:
            raise RuntimeError("INTERNAL_API_KEY is required")
        async with AsyncExitStack() as stack:
            providers = registry
            if providers is None:
                configured = []
                if os.getenv("SEEDANCE_API_KEY"):
                    client = await stack.enter_async_context(
                        httpx.AsyncClient(
                            base_url=os.getenv(
                                "SEEDANCE_BASE_URL",
                                "https://ark.ap-southeast.bytepluses.com/api/v3",
                            ).rstrip("/")
                            + "/",
                            headers={"Authorization": "Bearer " + os.environ["SEEDANCE_API_KEY"]},
                            timeout=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "120")),
                            follow_redirects=False,
                        )
                    )
                    configured.append(
                        SeedanceProvider(
                            client, os.getenv("SEEDANCE_MODEL", "seedance-1-5-pro-251215")
                        )
                    )
                # Register UI-configurable adapters without issuing provider requests.
                poyo_client = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=os.getenv("POYO_BASE_URL", "https://api.poyo.ai").rstrip("/") + "/",
                    timeout=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "120")), follow_redirects=False,
                ))
                poyo = PoyoVeoProvider(poyo_client, os.getenv("POYO_MODEL", "veo3.1-fast"))
                stored_key = vault.get("poyo") or os.getenv("POYO_API_KEY")
                if stored_key:
                    poyo.set_api_key(stored_key)
                configured.append(poyo)
                seedvis_client = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=os.getenv("SEEDVIS_BASE_URL", "https://seedvis.com/api/v1").rstrip("/") + "/",
                    timeout=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "120")), follow_redirects=False,
                ))
                seedvis = SeedvisProvider(seedvis_client, os.getenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1"))
                stored_key = seedvis_keys.primary_key()
                if stored_key:
                    seedvis.set_api_key(stored_key)
                configured.append(seedvis)
                if os.getenv("VIDEO_ENABLE_FAKE", "false").lower() == "true":
                    configured.append(
                        FakeVideoProvider(
                            os.getenv("VIDEO_FAKE_DB", "./storage/person3-fake.sqlite3")
                        )
                    )
                providers = ProviderRegistry(configured)
            application.state.pipeline = VideoGenerationPipeline(providers, seedvis_keys)
            credential_stamp["value"] = vault_stamp()
            yield

    application = FastAPI(title="Person 3 Video Engine", lifespan=lifespan)

    @application.middleware("http")
    async def keep_credentials_fresh(request, call_next):
        # Another worker process may have saved or removed a key since this one loaded them.
        if hasattr(application.state, "pipeline") and vault_stamp() != credential_stamp["value"]:
            credential_stamp["value"] = vault_stamp()
            for name, value in (
                ("poyo", vault.get("poyo") or os.getenv("POYO_API_KEY")),
                ("seedvis", seedvis_keys.primary_key()),
            ):
                try:
                    adapter = credential_provider(name)
                except ProviderError:
                    continue
                if value:
                    adapter.set_api_key(value)
                else:
                    adapter.clear_api_key()
        return await call_next(request)

    def authorize(x_internal_key: str = Header(default="")):
        if not key or not secrets.compare_digest(x_internal_key, key):
            raise HTTPException(401, "Invalid internal credentials")

    @application.exception_handler(ProviderError)
    async def provider_error(_request, exc: ProviderError):
        return JSONResponse(
            status_code=503 if exc.retryable else 422,
            content={
                "code": exc.code,
                "retryable": exc.retryable,
                "ambiguous": exc.ambiguous,
                **({"retry_after_seconds": exc.retry_after_seconds} if exc.retry_after_seconds is not None else {}),
            },
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc: RequestValidationError):
        # Field locations and error types only; values may contain images or prompts.
        logger.warning("Invalid execution request; path=%s issues=%s", request.url.path, [
            {"loc": [str(part) for part in error.get("loc", ())][:6], "type": error.get("type")}
            for error in exc.errors()[:10]
        ])
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_execution_request",
                "retryable": False,
                "ambiguous": False,
            },
        )

    @application.get("/health")
    def health():
        configured = application.state.pipeline.registry.providers
        return {"status": "ok", "providers": sorted(configured)}

    def credential_provider(name: str):
        provider = application.state.pipeline.registry.get(name)
        if not isinstance(provider, (PoyoVeoProvider, SeedvisProvider)):
            raise ProviderError("unknown_provider")
        return provider

    @application.get("/internal/v1/video/{provider}/credential", dependencies=[Depends(authorize)])
    def credential_status(provider: str):
        return {"configured": credential_provider(provider).credential_configured}

    def refresh_seedvis_default():
        # The first pool key also serves model listing and reference images.
        primary = seedvis_keys.primary_key()
        try:
            adapter = credential_provider("seedvis")
        except ProviderError:
            return bool(primary)
        if primary:
            adapter.set_api_key(primary)
        else:
            adapter.clear_api_key()
        return adapter.credential_configured

    @application.put("/internal/v1/video/{provider}/credential", dependencies=[Depends(authorize)])
    def set_credential(provider: str, body: CredentialInput):
        adapter = credential_provider(provider)
        value = body.api_key.get_secret_value().strip()
        if len(value) < 8:
            raise HTTPException(422, "Invalid provider credential")
        if provider == "seedvis":
            seedvis_keys.set_primary(value)
            return {"configured": refresh_seedvis_default()}
        vault.set(provider, value)
        adapter.set_api_key(value)
        return {"configured": True}

    @application.delete("/internal/v1/video/{provider}/credential", dependencies=[Depends(authorize)])
    def delete_credential(provider: str):
        adapter = credential_provider(provider)
        if provider == "seedvis":
            seedvis_keys.remove_primary()
            return {"configured": refresh_seedvis_default()}
        vault.delete(provider)
        adapter.clear_api_key()
        return {"configured": False}

    @application.get("/internal/v1/video/seedvis/credentials", dependencies=[Depends(authorize)])
    def seedvis_key_pool():
        return {"keys": seedvis_keys.public()}

    @application.post("/internal/v1/video/seedvis/credentials", status_code=201, dependencies=[Depends(authorize)])
    def add_seedvis_key(body: PoolKeyInput):
        value = body.api_key.get_secret_value().strip()
        if len(value) < 8:
            raise HTTPException(422, "Invalid provider credential")
        entry = seedvis_keys.add(value, body.label.strip(), body.slots)
        refresh_seedvis_default()
        return entry

    @application.patch("/internal/v1/video/seedvis/credentials/{key_id}", dependencies=[Depends(authorize)])
    def update_seedvis_key(key_id: PoolKeyId, body: PoolKeyUpdate):
        entry = seedvis_keys.update(key_id, label=None if body.label is None else body.label.strip(), slots=body.slots)
        if entry is None:
            raise HTTPException(404, "seedvis_key_not_found")
        return entry

    @application.delete("/internal/v1/video/seedvis/credentials/{key_id}", dependencies=[Depends(authorize)])
    def remove_seedvis_key(key_id: PoolKeyId):
        if not seedvis_keys.remove(key_id):
            raise HTTPException(404, "seedvis_key_not_found")
        return {"configured": refresh_seedvis_default()}

    @application.get("/internal/v1/seedvis/models", dependencies=[Depends(authorize)])
    async def seedvis_models():
        catalog = await provider_catalog(application.state.pipeline.registry, vault, refresh=True)
        seedvis = next((p for p in catalog["video_providers"] if p["id"] == "seedvis"), None)
        images = next((p for p in catalog["image_providers"] if p["id"] == "seedvis"), None)
        return {"video_models": [{"id": key, "label": value.label, "durations": list(value.durations),
                                  "native_audio_verified": value.native_audio,
                                  "available": next((m.get("available") for m in (seedvis or {}).get("models", []) if m["id"] == key), None)} for key, value in VIDEO_MODELS.items()],
                "image_models": (images or {}).get("models", []),
                "catalog_error": (seedvis or {}).get("catalog_error"),
                "video_model": os.getenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1"),
                "image_model": os.getenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2")}

    @application.get("/internal/v1/providers", dependencies=[Depends(authorize)])
    async def available_providers(refresh: bool = False):
        return await provider_catalog(application.state.pipeline.registry, vault, refresh=refresh)

    @application.get("/internal/v1/planner/credential", dependencies=[Depends(authorize)])
    def planner_credential_status():
        return {"configured": bool(vault.get("openai_planner"))}

    @application.put("/internal/v1/planner/credential", dependencies=[Depends(authorize)])
    def set_planner_credential(body: CredentialInput):
        value = body.api_key.get_secret_value().strip()
        if len(value) < 8:
            raise HTTPException(422, "Invalid planner credential")
        vault.set("openai_planner", value)
        return {"configured": True}

    @application.delete("/internal/v1/planner/credential", dependencies=[Depends(authorize)])
    def delete_planner_credential():
        vault.delete("openai_planner")
        return {"configured": False}

    @application.post("/internal/v1/planner/campaign", dependencies=[Depends(authorize)])
    async def plan_campaign(body: CampaignPlannerInput):
        if not vault.get("openai_planner"):
            raise HTTPException(409, "planner_credentials_missing")
        try:
            return await create_marketing_campaign(
                body.prompt, vault, os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"), body.script_count,
                body.image_data_urls or ([body.image_data_url] if body.image_data_url else []), body.project_context,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(502, str(exc)) from None

    @application.post("/internal/v1/planner/learn-style", dependencies=[Depends(authorize)])
    async def learn_reference_style(body: StyleLearningInput):
        if not vault.get("openai_planner"):
            raise HTTPException(409, "planner_credentials_missing")
        try:
            blueprint = await learn_style(
                frames=[frame.model_dump() for frame in body.frames], transcript=body.transcript,
                duration_seconds=body.duration_seconds, cut_times=body.cut_times,
                audio_present=body.audio_present, source_label=body.source_label,
                vault=vault, model=os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"),
                speech_metrics=body.speech_metrics,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(502, str(exc)) from None
        return {"blueprint": blueprint}

    @application.post("/internal/v1/planner/story", dependencies=[Depends(authorize)])
    async def plan_book_story(body: PlannerInput):
        if not vault.get("openai_planner"):
            raise HTTPException(409, "planner_credentials_missing")
        try:
            plan = await create_book_story_plan(
                body.prompt,
                vault,
                os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"),
                body.project_context,
                application.state.pipeline.registry.get(body.provider, body.video_model).continuity_capabilities()
                if body.provider else None,
                target_duration_seconds=body.target_duration_seconds,
                **({"reference_image_data_url": body.image_data_url} if body.image_data_url else {}),
                **({"reference_image_data_urls": body.image_data_urls} if body.image_data_urls else {}),
                **({"planning_goal": body.planning_goal} if body.planning_goal == "sales" else {}),
            )
        except RuntimeError as exc:
            reason = str(exc)
            if reason in {
                "planner_invalid_key",
                "planner_quota_or_rate_limit",
                "planner_model_unavailable",
                "planner_invalid_json",
                "planner_invalid_response",
                "planner_schema_mismatch",
                "planner_dialogue_mismatch",
                "planner_spoken_text_invalid",
                "planner_refused",
                "planner_output_truncated",
                "planner_empty_output",
                "planner_connection_failed",
                "planner_timeout",
                "planner_upstream_error",
                "planner_duration_mismatch",
                "planner_duration_unsupported",
            }:
                detail = reason
            else:
                detail = "planner_unavailable"
            raise HTTPException(422 if detail == "planner_duration_unsupported" else 502, detail) from None
        return {"plan": plan}

    @application.post("/internal/v1/planner/expand-narration", dependencies=[Depends(authorize)])
    async def expand_narration(body: NarrationExpansionInput):
        if not vault.get("openai_planner"):
            raise HTTPException(409, "planner_credentials_missing")
        try:
            clips = await expand_story_narration(
                body.clips,
                body.target_duration_seconds,
                body.current_duration_seconds,
                body.voice_bible,
                vault,
                os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"),
            )
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"clips": clips}

    @application.post("/internal/v1/planner/scene-image", dependencies=[Depends(authorize)])
    async def generate_scene_image(body: SceneImageInput):
        image_provider = body.provider or os.getenv("IMAGE_PROVIDER", "openai")
        if image_provider == "seedvis":
            try:
                image_data = await generate_reference_image(
                    credential_provider("seedvis"), model=body.model or os.getenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2"),
                    prompt=body.prompt, ratio=body.aspect_ratio,
                    request_id=body.request_id or secrets.token_hex(16),
                    reference_images=body.reference_images,
                )
                return Response(image_data, media_type="image/jpeg")
            except ProviderError as exc:
                raise HTTPException(502, exc.code) from None
            except httpx.HTTPError:
                raise HTTPException(502, "seedvis_image_download_failed") from None
        image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
        if body.model is not None and body.model != image_model:
            raise HTTPException(422, "unsupported_image_model")
        api_key = vault.get("openai_planner")
        if not api_key:
            raise HTTPException(409, "planner_credentials_missing")
        size = "1536x864" if body.aspect_ratio == "16:9" else "864x1536"
        options = {
            "model": image_model,
            "prompt": body.prompt,
            "size": size,
            "quality": os.getenv("OPENAI_IMAGE_QUALITY", "high"),
            "output_format": "jpeg",
            "n": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
                if body.reference_images:
                    # Edit mode keeps garments and identity from the supplied photos.
                    files = [
                        ("image[]", (f"reference-{index}.jpg", base64.b64decode(image.split(",", 1)[1]), "image/jpeg"))
                        for index, image in enumerate(body.reference_images, 1)
                    ]
                    if os.getenv("OPENAI_IMAGE_INPUT_FIDELITY"):
                        options["input_fidelity"] = os.environ["OPENAI_IMAGE_INPUT_FIDELITY"]
                    result = await client.post(
                        "https://api.openai.com/v1/images/edits",
                        headers={"Authorization": "Bearer " + api_key},
                        data={key: str(value) for key, value in options.items()}, files=files,
                    )
                else:
                    result = await client.post(
                        "https://api.openai.com/v1/images/generations",
                        headers={"Authorization": "Bearer " + api_key},
                        json=options,
                    )
                result.raise_for_status()
                image_data = __import__("base64").b64decode(result.json()["data"][0]["b64_json"])
                return Response(image_data, media_type="image/jpeg")
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            raise HTTPException(502, "scene_image_generation_failed") from None

    @application.post("/internal/v1/planner/ground-reference", dependencies=[Depends(authorize)])
    async def ground_reference(body: ReferenceGroundingInput):
        api_key = vault.get("openai_planner")
        if not api_key:
            raise HTTPException(409, "planner_credentials_missing")
        if not body.image_data_url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
            raise HTTPException(422, "reference_image_format_unsupported")
        try:
            result = await ground_book_story(
                body.image_data_url, body.story_plan, body.user_request, api_key,
                os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"),
            )
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from None
        return {"grounding": result}

    @application.post("/internal/v1/planner/transcribe-clip", dependencies=[Depends(authorize)])
    async def transcribe_clip(request: Request):
        api_key = vault.get("openai_planner")
        if not api_key:
            raise HTTPException(409, "planner_credentials_missing")
        if request.headers.get("content-type", "").split(";")[0] != "video/mp4":
            raise HTTPException(415, "video_mp4_required")
        video = await request.body()
        if not video or len(video) > 25_000_000:
            raise HTTPException(413, "clip_size_invalid")
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": "Bearer " + api_key},
                    files={"file": ("scene.mp4", video, "video/mp4")},
                    data={
                        "model": os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
                        "language": "vi",
                        "response_format": "json",
                    },
                )
                response.raise_for_status()
                transcript = response.json().get("text")
                if not isinstance(transcript, str):
                    raise ValueError("missing transcription")
                return {"text": transcript.strip()}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "clip_transcription_failed") from None

    @application.post("/internal/v1/planner/voice", dependencies=[Depends(authorize)])
    async def generate_story_voice(body: VoiceInput):
        api_key = vault.get("openai_planner")
        if not api_key:
            raise HTTPException(409, "planner_credentials_missing")
        try:
            async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
                result = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": "Bearer " + api_key},
                    json={
                        "model": os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                        "voice": os.getenv("OPENAI_TTS_VOICE", "coral"),
                        "input": body.text,
                        "instructions": tts_voice_instructions(body.voice_bible),
                        "response_format": body.response_format,
                        "speed": 1.0,
                    },
                )
                result.raise_for_status()
                return Response(result.content, media_type="audio/wav" if body.response_format == "wav" else "audio/mpeg")
        except httpx.HTTPError:
            raise HTTPException(502, "story_voice_generation_failed") from None

    @application.post("/internal/v1/chat/completions", dependencies=[Depends(authorize)])
    async def chat_completion(body: ChatCompletionInput):
        api_key = vault.get("openai_planner")
        if not api_key:
            raise HTTPException(409, "planner_credentials_missing")
        try:
            async with httpx.AsyncClient(
                base_url="https://api.openai.com/v1/",
                headers={"Authorization": "Bearer " + api_key},
                timeout=httpx.Timeout(120),
                follow_redirects=False,
            ) as client:
                chat_model = os.getenv("OPENAI_CHAT_MODEL", os.getenv("OPENAI_PLANNER_MODEL", "gpt-4o-mini"))
                response = await client.post(
                    "chat/completions",
                    json={
                        "model": chat_model,
                        "messages": [message.model_dump() for message in body.messages],
                        **generation_params(chat_model, temperature=0.6, max_tokens=1800),
                    },
                )
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty_chat_response")
                return {"message": content.strip()}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                detail = "planner_invalid_key"
            elif exc.response.status_code == 429:
                detail = "planner_quota_or_rate_limit"
            else:
                detail = "chat_upstream_error"
            raise HTTPException(502, detail) from None
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            raise HTTPException(502, "chat_upstream_error") from None

    @application.get(
        "/internal/v1/video/{provider}/capabilities",
        response_model=VideoProviderCapabilities,
        dependencies=[Depends(authorize)],
    )
    async def capabilities(provider: str, model: str | None = None):
        return application.state.pipeline.registry.get(provider, model).continuity_capabilities()

    @application.post("/internal/v1/video/{provider}/validate", dependencies=[Depends(authorize)])
    async def validate(provider: str, request: ExecutionRequest):
        application.state.pipeline.validate(provider, request)
        canonical = request.model_dump(mode="json")
        if request.model is None:
            canonical.pop("model", None)
        # Preserve legacy idempotency fingerprints when native audio is not opted in.
        if not request.spec.output.generate_audio and (
            provider != "poyo" or "generate_audio" not in request.spec.output.model_fields_set
        ):
            canonical["spec"]["output"].pop("generate_audio", None)
        return {"valid": True, "execution_request": canonical}

    @application.post(
        "/internal/v1/video/{provider}/tasks",
        status_code=202,
        response_model=ProviderResult,
        dependencies=[Depends(authorize)],
    )
    async def submit(provider: str, request: ExecutionRequest, credential: CredentialId = None):
        return await application.state.pipeline.submit(provider, request, credential)

    @application.get(
        "/internal/v1/video/{provider}/tasks/{task_id}",
        response_model=ProviderResult,
        dependencies=[Depends(authorize)],
    )
    async def status(provider: str, task_id: str, poll_url: str | None = None, credential: CredentialId = None):
        return await application.state.pipeline.status(provider, task_id, poll_url=poll_url, credential=credential)

    @application.post(
        "/internal/v1/video/{provider}/tasks/{task_id}/cancel",
        response_model=ProviderResult,
        dependencies=[Depends(authorize)],
    )
    async def cancel(provider: str, task_id: str, credential: CredentialId = None):
        return await application.state.pipeline.cancel(provider, task_id, credential)

    return application


app = create_app()
