import re

import httpx
from pydantic import ValidationError

from app.providers.base import VideoProviderAdapter
from app.providers.seedance_compiler import SeedanceCompiler
from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import (
    ProviderError,
    ProviderResult,
    VideoOutput,
    VideoProviderCapabilities,
)


class SeedanceProvider(VideoProviderAdapter):
    name = "seedance"

    def __init__(self, client: httpx.AsyncClient, model: str):
        self.client = client
        self.compiler = SeedanceCompiler(model)

    def supports(self, spec: GenerationSpec) -> bool:
        return self.compiler.capabilities.supports(spec)

    def compile(self, request: ExecutionRequest) -> dict:
        return self.compiler.compile(request)

    def continuity_capabilities(self) -> VideoProviderCapabilities:
        simulated = self.name == "fake"
        return VideoProviderCapabilities(
            provider=self.name, model=self.compiler.model,
            native_audio=not simulated and self.compiler.model.startswith("seedance-1-5-pro"),
            audio_control=not simulated and self.compiler.model.startswith("seedance-1-5-pro"),
            first_frame=not simulated,
            generation_durations=list(range(
                self.compiler.capabilities.min_duration, self.compiler.capabilities.max_duration + 1,
            )),
            reason="simulated_provider" if simulated else "no_verified_continuation_contract_in_adapter",
        )

    @staticmethod
    def task_path(task_id: str) -> str:
        if not re.fullmatch(r"[\w.-]{1,128}", task_id):
            raise ProviderError("invalid_task_id")
        return "contents/generations/tasks/" + task_id

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        submitting = method == "POST"
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
            # A server failure on POST can occur after a paid task was accepted.
            raise ProviderError(
                f"provider_http_{code}",
                retryable=code == 429 or (code >= 500 and not submitting),
                ambiguous=submitting and (code >= 500 or code == 408),
            )
        if method == "DELETE":
            return {}
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise ProviderError("invalid_provider_response", ambiguous=submitting) from None

    async def submit(self, payload: dict) -> ProviderResult:
        data = await self._request("POST", "contents/generations/tasks", json=payload)
        try:
            return ProviderResult(provider=self.name, provider_task_id=data["id"], status="queued")
        except (KeyError, ValidationError):
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
        # Running jobs cannot be cancelled. Keep polling for the billed result.
        if current.status == "generating":
            raise ProviderError("cancel_not_supported_running")
        await self._request("DELETE", self.task_path(task_id))
        return ProviderResult(provider=self.name, provider_task_id=task_id, status="cancelled")

    def normalize_result(self, response: dict) -> ProviderResult:
        statuses = {
            "queued": "queued",
            "running": "generating",
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "expired": "failed",
        }
        try:
            status = statuses[response["status"]]
            outputs = []
            if status == "completed":
                outputs = [VideoOutput(source_url=response["content"]["video_url"])]
            usage = response.get("usage") or {}
            usage = {
                key: value
                for key, value in usage.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            usage["estimated_cost"] = None
            return ProviderResult(
                provider=self.name,
                provider_task_id=response["id"],
                status=status,
                outputs=outputs,
                usage=usage,
                error_code="provider_generation_failed" if status == "failed" else None,
            )
        except (KeyError, TypeError, AttributeError, ValidationError):
            raise ProviderError("invalid_provider_response") from None
