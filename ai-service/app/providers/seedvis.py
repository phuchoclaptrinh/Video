import asyncio
import re
import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.providers.base import VideoProviderAdapter
from app.providers.seedvis_compiler import SeedvisCompiler
from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import (
    ProviderError,
    ProviderResult,
    VideoOutput,
    VideoProviderCapabilities,
)


class SeedvisProvider(VideoProviderAdapter):
    name = "seedvis"

    def __init__(self, client: httpx.AsyncClient, model: str, api_key: str | None = None):
        self.client = client
        self.compiler = SeedvisCompiler(model)
        # A task-specific key from the key pool; otherwise the shared client's default key is used.
        self.api_key = api_key
        self._models_cache = None
        self._models_lock = asyncio.Lock()

    @property
    def credential_configured(self) -> bool:
        return bool(self.api_key) or "authorization" in self.client.headers

    def set_api_key(self, api_key: str) -> None:
        self.client.headers["Authorization"] = "Bearer " + api_key

    def clear_api_key(self) -> None:
        self.client.headers.pop("Authorization", None)

    async def available_models(self, *, refresh: bool = False) -> list[dict]:
        credential = self.client.headers.get("Authorization", "")
        async with self._models_lock:
            if not refresh and self._models_cache:
                key, expires, models = self._models_cache
                if key == credential and time.monotonic() < expires:
                    return models
            response = await self.request("GET", "models", timeout=10)
            models = response.get("data", response.get("models"))
            if isinstance(models, dict):
                models = models.get("models")
            if not isinstance(models, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in models
            ):
                raise ProviderError("invalid_seedvis_model_catalog")
            self._models_cache = (credential, time.monotonic() + 60, models)
            return models

    def supports(self, spec: GenerationSpec) -> bool:
        return self.compiler.supports(spec)

    def compile(self, request: ExecutionRequest) -> dict:
        return self.compiler.compile(request)

    def continuity_capabilities(self) -> VideoProviderCapabilities:
        cap = self.compiler.capabilities
        return VideoProviderCapabilities(
            provider=self.name,
            model=self.compiler.model,
            native_audio=cap.native_audio,
            first_frame=cap.first_frame,
            reference_images=True,
            generation_durations=list(cap.durations),
            reason="native_audio_unverified_model"
            if not cap.native_audio
            else "video_continuation_not_documented_for_model",
            evidence_url="https://seedvis.com/api/v1/developer-api/docs.md?early_access=1",
        )

    @staticmethod
    def task_path(task_id: str) -> str:
        if not re.fullmatch(r"[\w.-]{1,128}", task_id):
            raise ProviderError("invalid_task_id")
        return "developer/generations/" + task_id

    def safe_poll_path(self, value: str, task_id: str) -> str:
        try:
            target, base = urlsplit(value), urlsplit(str(self.client.base_url))
        except (ValueError, TypeError):
            raise ProviderError("invalid_poll_url") from None
        expected = base.path.rstrip("/") + "/" + self.task_path(task_id)
        if (
            target.scheme != "https"
            or target.netloc != base.netloc
            or target.path != expected
            or target.fragment
            or target.username
            or target.password
        ):
            raise ProviderError("invalid_poll_url")
        return self.task_path(task_id) + ("?" + target.query if target.query else "")

    @staticmethod
    def retry_delay(value: str | None) -> float | None:
        if not value:
            return None
        try:
            delay = (
                float(value)
                if value.isdigit()
                else (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            )
            return max(0, delay)
        except (TypeError, ValueError, OverflowError):
            return None

    async def request(self, method: str, path: str, **kwargs) -> dict:
        if not self.credential_configured:
            raise ProviderError("provider_credentials_missing")
        submitting = method == "POST" and not path.endswith("/cancel")
        replayable = False
        if submitting:
            try:
                uuid.UUID((kwargs.get("headers") or {}).get("Idempotency-Key", ""))
                replayable = True
            except (ValueError, TypeError, AttributeError):
                pass
        if self.api_key:
            kwargs["headers"] = {**(kwargs.get("headers") or {}), "Authorization": "Bearer " + self.api_key}
        try:
            response = await self.client.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderError("connection_unavailable", retryable=True) from None
        except httpx.TransportError:
            raise ProviderError(
                "transport_error",
                retryable=not submitting or replayable,
                ambiguous=submitting and not replayable,
            ) from None
        try:
            data = response.json()
        except ValueError:
            raise ProviderError(
                "invalid_provider_response",
                retryable=replayable and response.status_code >= 500,
                ambiguous=submitting and not replayable,
            ) from None
        if not isinstance(data, dict):
            raise ProviderError("invalid_provider_response", ambiguous=submitting)
        if response.is_error:
            nested = data.get("error")
            code = data.get("code") or (nested.get("code") if isinstance(nested, dict) else None)
            if not isinstance(code, str) or not re.fullmatch(r"[a-z_]{1,80}", code):
                code = f"provider_http_{response.status_code}"
            known_rejection = code in {
                "model_maintenance",
                "server_busy",
                "cancel_unavailable",
                "rate_limit_exceeded",
            }
            # v2.0 answers a full plan (running + queued streams) with the same 422 as a schema error;
            # only the former clears up by waiting, so it is told apart by its message.
            plan_full = code == "validation_error" and bool(
                re.search(r"concurren|queue|limit|đồng thời|hàng đợi|giới hạn", str(data), re.IGNORECASE)
            )
            known_rejection = known_rejection or plan_full
            retry_status = (
                response.status_code == 429
                or response.status_code >= 500
                or (response.status_code == 408 and replayable)
                or plan_full
            )
            raise ProviderError(
                "seedvis_" + code,
                retryable=retry_status and (not submitting or replayable or known_rejection),
                ambiguous=submitting
                and (response.status_code >= 500 or response.status_code == 408)
                and not (replayable or known_rejection),
                retry_after_seconds=self.retry_delay(response.headers.get("Retry-After")),
            )
        if data.get("success") is False:
            raise ProviderError("invalid_provider_response", ambiguous=submitting)
        return data

    @staticmethod
    def job_data(response: dict) -> dict:
        data = response.get("data", response)
        if not isinstance(data, dict):
            raise ProviderError("invalid_provider_response")
        return data

    @classmethod
    def validate_reference_echo(cls, response: dict, mode: str, count: int) -> None:
        job = cls.job_data(response)
        references = job.get("references")
        if job.get("mode") != mode or not isinstance(references, list) or len(references) != count:
            raise ProviderError("provider_reference_mismatch", ambiguous=True)

    async def submit(self, payload: dict) -> ProviderResult:
        data = await self.request(
            "POST",
            "developer/generations",
            json=payload["body"],
            headers={"Idempotency-Key": payload["idempotency_key"]},
        )
        try:
            self.validate_reference_echo(data, payload["body"]["mode"], payload["reference_count"])
            result = self.normalize_result(data)
            return result
        except ProviderError as exc:
            raise ProviderError(exc.code, ambiguous=True) from None

    async def get_status(self, task_id: str, poll_url: str | None = None) -> ProviderResult:
        path = (
            self.safe_poll_path(poll_url, task_id)
            if poll_url
            else self.task_path(task_id) + "?wait=0"
        )
        result = self.normalize_result(await self.request("GET", path))
        if result.provider_task_id != task_id:
            raise ProviderError("provider_task_mismatch")
        return result

    async def cancel(self, task_id: str) -> ProviderResult:
        try:
            response = await self.request("POST", self.task_path(task_id) + "/cancel")
        except ProviderError as exc:
            if exc.code == "seedvis_not_cancellable":
                return await self.get_status(task_id)
            raise
        result = self.normalize_result(response)
        if result.provider_task_id != task_id:
            raise ProviderError("provider_task_mismatch")
        return result

    def normalize_result(self, response: dict) -> ProviderResult:
        try:
            data = self.job_data(response)
            self.task_path(data["id"])
            statuses = {
                "queued": "queued",
                "processing": "generating",
                "completed": "completed",
                "failed": "failed",
            }
            status = statuses[data["status"]]
            if data["is_final"] is not (status in {"completed", "failed"}):
                raise ValueError("inconsistent terminal state")
            error = data.get("error") or {}
            cancelled = status == "failed" and error.get("code") == "cancelled"
            outputs = [
                VideoOutput(source_url=o["url"])
                for o in data.get("outputs", [])
                if o.get("type") == "video"
            ]
            if status == "completed" and len(outputs) != 1:
                raise ValueError("expected one video")
            delay = (data.get("next") or {}).get("after_seconds", 10)
            poll_url = None
            if status in {"queued", "generating"}:
                poll_url = (data.get("next") or {}).get("url")
                if not isinstance(poll_url, str):
                    raise ValueError("missing polling URL")
                self.safe_poll_path(poll_url, data["id"])
            detail = error.get("message")
            error_code = error.get("code", "generation_failed")
            if not isinstance(error_code, str) or not re.fullmatch(r"[a-z_]{1,80}", error_code):
                error_code = "generation_failed"
            return ProviderResult(
                provider=self.name,
                provider_task_id=data["id"],
                status="cancelled" if cancelled else status,
                outputs=outputs if status == "completed" else [],
                poll_after_seconds=max(1, min(120, delay))
                if isinstance(delay, (int, float)) and not isinstance(delay, bool)
                else 10,
                error_code="seedvis_" + error_code
                if status == "failed" and not cancelled
                else None,
                error_detail=re.sub(r"[\x00-\x1f\x7f]+", " ", detail)[:240]
                if isinstance(detail, str)
                else None,
                poll_url=poll_url,
            )
        except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
            raise ProviderError("invalid_provider_response") from None
