import base64
import binascii
import hashlib
import logging
import re
import time

import httpx
from pydantic import ValidationError

from app.providers.base import VideoProviderAdapter
from app.providers.poyo_veo_compiler import PoyoVeoCompiler
from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import (
    ProviderError,
    ProviderResult,
    VideoOutput,
    VideoProviderCapabilities,
)

logger = logging.getLogger(__name__)
INLINE_IMAGE_PREFIX = "data:image/jpeg;base64,"
# PoYo keeps uploaded images for 72 hours; reuse only with a wide safety margin.
UPLOAD_REUSE_SECONDS = 48 * 3600


class PoyoVeoProvider(VideoProviderAdapter):
    name = "poyo"

    def __init__(self, client: httpx.AsyncClient, model: str):
        self.client = client
        self.compiler = PoyoVeoCompiler(model)
        self._uploads: dict[str, tuple[str, float]] = {}

    @property
    def credential_configured(self) -> bool:
        return "authorization" in self.client.headers

    def set_api_key(self, api_key: str) -> None:
        self.client.headers["Authorization"] = "Bearer " + api_key

    def clear_api_key(self) -> None:
        self.client.headers.pop("Authorization", None)

    def supports(self, spec: GenerationSpec) -> bool:
        return self.compiler.capabilities.supports(
            spec, images_enabled=True, model=self.compiler.model
        )

    def compile(self, request: ExecutionRequest) -> dict:
        return self.compiler.compile(request)

    def continuity_capabilities(self) -> VideoProviderCapabilities:
        # The documented veo3.1-fast contract accepts images, not source video/audio.
        return VideoProviderCapabilities(
            provider=self.name, model=self.compiler.model, native_audio=True,
            audio_control=self.compiler.capabilities.audio_control,
            first_frame=self.compiler.capabilities.first_frame,
            reference_images=self.compiler.capabilities.reference_images,
            generation_durations=list(self.compiler.capabilities.generation_durations),
            reference_image_durations=[8] if self.compiler.capabilities.reference_images else [],
            reason="video_continuation_not_documented_for_model",
            evidence_url="https://docs.poyo.ai/api-manual/video-series/veo-3-1"
            + ("-official" if self.compiler.capabilities.audio_control else ""),
        )

    @staticmethod
    def task_path(task_id: str) -> str:
        if not re.fullmatch(r"[\w.-]{1,128}", task_id):
            raise ProviderError("invalid_task_id")
        return "api/generate/status/" + task_id

    async def _request(self, method: str, path: str, *, submitting: bool | None = None, **kwargs) -> dict:
        submitting = method == "POST" if submitting is None else submitting
        if not self.credential_configured:
            raise ProviderError("provider_credentials_missing")
        try:
            response = await self.client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderError("connection_unavailable", retryable=True) from None
        except httpx.TransportError:
            raise ProviderError(
                "transport_error", retryable=not submitting, ambiguous=submitting
            ) from None
        if response.status_code >= 400:
            code = response.status_code
            raise ProviderError(
                f"provider_http_{code}",
                retryable=code == 429 or (code >= 500 and not submitting),
                ambiguous=submitting and (code >= 500 or code == 408),
            )
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("code") != 200:
                raise ValueError
            return data
        except ValueError:
            raise ProviderError("invalid_provider_response", ambiguous=submitting) from None

    async def _upload_inline_image(self, data_url: str) -> str:
        """Host an inline JPEG on PoYo storage so no public backend tunnel is required."""
        digest = hashlib.sha256(data_url.encode()).hexdigest()
        cached = self._uploads.get(digest)
        if cached and cached[1] > time.time():
            return cached[0]
        try:
            raw = base64.b64decode(data_url.removeprefix(INLINE_IMAGE_PREFIX), validate=True)
        except (ValueError, binascii.Error):
            raise ProviderError("invalid_inline_image") from None
        # Uploading creates no paid task, so failures are never ambiguous.
        data = await self._request(
            "POST", "api/common/upload/stream", submitting=False,
            files={"file": (f"vo3-{digest[:24]}.jpg", raw, "image/jpeg")},
            data={"file_name": f"vo3-{digest[:24]}.jpg"},
        )
        try:
            url = data["data"]["file_url"]
        except (KeyError, TypeError):
            raise ProviderError("poyo_upload_missing_url") from None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderError("poyo_upload_missing_url")
        self._uploads[digest] = (url, time.time() + UPLOAD_REUSE_SECONDS)
        return url

    async def _host_inline_images(self, payload: dict) -> dict:
        inputs = payload.get("input", {})
        urls = inputs.get("image_urls") or []
        if not any(url.startswith(INLINE_IMAGE_PREFIX) for url in urls):
            return payload
        hosted = [
            await self._upload_inline_image(url) if url.startswith(INLINE_IMAGE_PREFIX) else url
            for url in urls
        ]
        logger.info("[VIDEO] provider=poyo uploaded_inline_images=%s",
                    sum(url.startswith(INLINE_IMAGE_PREFIX) for url in urls))
        return {**payload, "input": {**inputs, "image_urls": hosted}}

    async def submit(self, payload: dict) -> ProviderResult:
        payload = await self._host_inline_images(payload)
        inputs = payload.get("input", {})
        logger.info("[VIDEO] provider=poyo model=%s duration=%s sound_sent_to_provider=%s image_count=%s",
                    payload.get("model", self.compiler.model), inputs.get("duration"),
                    inputs.get("sound", "NOT_SENT_PROVIDER_DEFAULT"),
                    len(inputs.get("image_urls", [])))
        data = await self._request("POST", "api/generate/submit", json=payload)
        try:
            return ProviderResult(
                provider=self.name,
                provider_task_id=data["data"]["task_id"],
                status="queued",
            )
        except (KeyError, TypeError, ValidationError):
            raise ProviderError("missing_task_id", ambiguous=True) from None

    async def get_status(self, task_id: str) -> ProviderResult:
        result = self.normalize_result(await self._request("GET", self.task_path(task_id)))
        if result.provider_task_id != task_id:
            raise ProviderError("provider_task_mismatch")
        return result

    async def cancel(self, task_id: str) -> ProviderResult:
        current = await self.get_status(task_id)
        if current.status in {"completed", "failed", "cancelled"}:
            return current
        # PoYo does not document a cancellation endpoint. Continue polling so a billed result is kept.
        raise ProviderError("cancel_not_supported_running")

    def normalize_result(self, response: dict) -> ProviderResult:
        statuses = {
            "not_started": "queued",
            "running": "generating",
            "finished": "completed",
            "failed": "failed",
        }
        try:
            data = response["data"]
            status = statuses[data["status"]]
            outputs = []
            if status == "completed":
                video = next(item for item in data["files"] if item.get("file_type") == "video")
                outputs = [VideoOutput(source_url=video["file_url"])]
            credits = data.get("credits_amount")
            usage = {
                "credits": credits
                if isinstance(credits, (int, float)) and not isinstance(credits, bool)
                else None,
                "estimated_cost": None,
            }
            error_detail = None
            if status == "failed":
                candidates = [
                    data.get(key)
                    for key in ("error_detail", "error_message", "failure_reason", "reason", "message")
                ]
                nested_error = data.get("error")
                if isinstance(nested_error, dict):
                    candidates.extend((nested_error.get("message"), nested_error.get("detail")))
                elif isinstance(nested_error, str):
                    candidates.append(nested_error)
                detail = next(
                    (value.strip() for value in candidates if isinstance(value, str) and value.strip()),
                    None,
                )
                if detail:
                    error_detail = re.sub(r"[\x00-\x1f\x7f]+", " ", detail)[:240]
            return ProviderResult(
                provider=self.name,
                provider_task_id=data["task_id"],
                status=status,
                outputs=outputs,
                usage=usage,
                error_code="poyo_generation_failed" if status == "failed" else None,
                error_detail=error_detail,
            )
        except (KeyError, TypeError, StopIteration, ValidationError):
            raise ProviderError("invalid_provider_response") from None
