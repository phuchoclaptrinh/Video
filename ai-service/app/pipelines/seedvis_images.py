"""Reference image generation using Seedvis' native image protocols."""

import asyncio
import hashlib
import io
import json
import time
import uuid
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from app.providers.seedvis import SeedvisProvider
from app.providers.seedvis_models import IMAGE_MODELS
from app.schemas.provider import ProviderError


def image_mode(references: list[str] | None) -> str:
    return "image-to-image" if references else "text-to-image"


def image_request(
    model: str, prompt: str, ratio: str, references: list[str] | None = None,
) -> tuple[str, dict]:
    """Reference images (data URLs) switch to Seedvis' documented image-to-image mode."""
    if model not in IMAGE_MODELS:
        raise ProviderError("unsupported_seedvis_image_model")
    if references and len(references) > 10:
        raise ProviderError("seedvis_too_many_reference_images")
    if model == "gpt-image-2":
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": ratio,
            "quality": "auto",
            "response_format": "url",
        }
        if references:
            return "images/edits", body | {"mode": "image-to-image", "images": list(references)}
        return "images/generations", body
    body = {
        "model": model,
        "input": prompt,
        "aspect_ratio": ratio,
        "mode": image_mode(references),
        "count": 1,
        "upscale_image": "none",
    }
    return "google/v1beta/interactions", body | ({"reference_images": list(references)} if references else {})


def output_url(response: dict) -> str | None:
    data = response.get("data", response)
    if isinstance(data, list):
        return data[0].get("url") if len(data) == 1 and isinstance(data[0], dict) else None
    if isinstance(data, dict) and data.get("status") == "completed":
        images = [item for item in data.get("outputs", []) if item.get("type") == "image"]
        if len(images) == 1:
            return images[0].get("url")
    return None


async def download_reference_image(url: str) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "cdn.seedvis.com"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        raise ProviderError("untrusted_seedvis_image_url")
    # Never forward the Seedvis API credential to the media CDN.
    async with (
        httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ProviderError("seedvis_invalid_image")
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > 20 * 1024 * 1024:
                raise ProviderError("seedvis_image_too_large")
            chunks.append(chunk)
    try:
        with Image.open(io.BytesIO(b"".join(chunks))) as image:
            if image.width * image.height > 25_000_000:
                raise ProviderError("seedvis_image_too_large")
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=95)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ProviderError("seedvis_invalid_image") from None


async def generate_reference_image(
    provider: SeedvisProvider,
    *,
    model: str,
    prompt: str,
    ratio: str,
    request_id: str,
    timeout: float = 180,  # noqa: ASYNC109 - bounded with asyncio.timeout
    reference_images: list[str] | None = None,
) -> bytes:
    try:
        async with asyncio.timeout(timeout):
            return await _generate_reference_image(
                provider,
                model=model,
                prompt=prompt,
                ratio=ratio,
                request_id=request_id,
                deadline=time.monotonic() + timeout,
                references=reference_images or None,
            )
    except TimeoutError:
        raise ProviderError("seedvis_image_pending_retry_same_draft") from None


async def _generate_reference_image(
    provider: SeedvisProvider,
    *,
    model: str,
    prompt: str,
    ratio: str,
    request_id: str,
    deadline: float,
    references: list[str] | None = None,
) -> bytes:
    path, body = image_request(model, prompt, ratio, references)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"vo3:seedvis:image:{request_id}:{digest}"))
    response = await _image_request(
        provider, "POST", path, deadline=deadline, json=body, headers={"Idempotency-Key": key}
    )
    task_id = None
    while True:
        # OpenAI's synchronous Images format has no lifecycle envelope or reference echo.
        if not isinstance(response.get("data"), list):
            provider.validate_reference_echo(response, image_mode(references), len(references or ()))
            data = provider.job_data(response)
            if task_id and data.get("id") != task_id:
                raise ProviderError("provider_task_mismatch")
            if data.get("status") == "completed" and data.get("is_final") is not True:
                raise ProviderError("invalid_provider_response")
        elif task_id:
            raise ProviderError("provider_task_mismatch")
        url = output_url(response)
        if url:
            return await download_reference_image(url)
        data = provider.job_data(response)
        if data.get("status") == "failed":
            raise ProviderError("seedvis_image_generation_failed")
        if data.get("status") not in {"queued", "processing"} or data.get("is_final") is not False:
            raise ProviderError("invalid_provider_response")
        if not isinstance(data.get("id"), str) or (task_id and data["id"] != task_id):
            raise ProviderError("provider_task_mismatch")
        task_id = data["id"]
        next_step = data.get("next") or {}
        path = provider.safe_poll_path(next_step.get("url", ""), task_id)
        delay = next_step.get("after_seconds", 10)
        delay = (
            max(1, min(120, delay))
            if isinstance(delay, (int, float)) and not isinstance(delay, bool)
            else 10
        )
        if time.monotonic() + delay >= deadline:
            # A retry from the same draft reuses its idempotency key; it must not create a second image.
            raise ProviderError("seedvis_image_pending_retry_same_draft")
        await asyncio.sleep(delay)
        response = await _image_request(provider, "GET", path, deadline=deadline)


async def _image_request(
    provider: SeedvisProvider, method: str, path: str, *, deadline: float, **kwargs
) -> dict:
    for attempt in range(3):
        try:
            return await provider.request(method, path, **kwargs)
        except ProviderError as exc:
            if not exc.retryable or exc.ambiguous or attempt == 2:
                raise
            delay = max(2**attempt, exc.retry_after_seconds or 0)
            if time.monotonic() + delay >= deadline:
                raise ProviderError("seedvis_image_pending_retry_same_draft") from None
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
