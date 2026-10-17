import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.pipelines import seedvis_images
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError
from tests.test_seedvis import adapter, lifecycle, request_body


def test_live_catalog_cache_refresh_and_credential_change(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    calls = []

    def handler(request):
        assert request.method == "GET" and request.url.path == "/api/v1/models"
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "seedance_2.5",
                        "modes": [
                            {
                                "mode": "text-to-video",
                                "endpoint": "/developer/generations",
                                "images": {"min": 0, "max": 0},
                            }
                        ],
                    },
                    {
                        "id": "gpt-image-2",
                        "modes": [
                            {
                                "mode": "text-to-image",
                                "endpoint": "/images/generations",
                                "images": {"min": 0, "max": 0},
                            }
                        ],
                    },
                ]
            },
        )

    provider = adapter(handler)
    with TestClient(create_app(ProviderRegistry([provider]), "test-key")) as client:
        client.headers["X-Internal-Key"] = "test-key"
        for _ in range(2):
            result = client.get("/internal/v1/providers")
            assert result.status_code == 200 and "test-seedvis-not-real" not in result.text
            data = result.json()
            models = {m["id"]: m for m in data["video_providers"][0]["models"]}
            assert models["Veo-3.1"]["available"] is False
            assert models["seedance_2.5"]["available"] is True
            images = next(p["models"] for p in data["image_providers"] if p["id"] == "seedvis")
            assert next(m for m in images if m["id"] == "GEM_PIX_2")["available"] is False
        assert len(calls) == 1
        client.get("/internal/v1/providers?refresh=true")
        assert len(calls) == 2
        provider.set_api_key("different-test-key")
        client.get("/internal/v1/providers")
        assert len(calls) == 3


def test_catalog_error_does_not_claim_models_are_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    provider = adapter(lambda _: httpx.Response(401, json={"error": {"code": "invalid_api_key"}}))
    with TestClient(create_app(ProviderRegistry([provider]), "test-key")) as client:
        client.headers["X-Internal-Key"] = "test-key"
        result = client.get("/internal/v1/providers").json()["video_providers"][0]
        assert result["catalog_error"] == "seedvis_invalid_api_key"
        assert all(m["available"] is None for m in result["models"])


@pytest.mark.asyncio
async def test_poll_next_url_exactly_and_reject_foreign_url_before_request():
    calls = []
    poll = "https://seedvis.com/api/v1/developer/generations/task-1"

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json=lifecycle("processing", next={"url": poll + "?wait=0", "after_seconds": 15})
        )

    provider = adapter(handler)
    async with provider.client:
        result = await provider.get_status("task-1", poll_url=poll)
        assert not calls[0].url.query
        assert result.poll_url == poll + "?wait=0"
        await provider.get_status("task-1", poll_url=result.poll_url)
        assert str(calls[1].url) == result.poll_url
        for invalid in (
            "https://evil.test/task-1",
            poll.replace("task-1", "task-2"),
            "https://[invalid",
        ):
            with pytest.raises(ProviderError, match="invalid_poll_url"):
                await provider.get_status("task-1", poll_url=invalid)
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_seedvis_replay_same_key_and_body_after_disconnect():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("disconnected", request=request)
        return httpx.Response(202, json=lifecycle())

    provider = adapter(handler)
    async with provider.client:
        compiled = provider.compile(ExecutionRequest.model_validate(request_body()))
        with pytest.raises(ProviderError) as error:
            await provider.submit(compiled)
        assert error.value.retryable and not error.value.ambiguous
        assert (await provider.submit(compiled)).status == "queued"
        assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]
        assert calls[0].content == calls[1].content
        failure = provider.normalize_result(
            lifecycle("failed", error={"code": "not_started", "message": "No charge"})
        )
        assert failure.error_code == "seedvis_not_started"


@pytest.mark.parametrize(
    "changes",
    [{"mode": "image-to-image"}, {"references": [{"url": "unexpected"}]}, {"is_final": False}],
)
@pytest.mark.asyncio
async def test_image_lifecycle_rejects_wrong_echo_before_download(changes, monkeypatch):
    async def unexpected_download(_url):
        pytest.fail("An inconsistent response must not download an image")

    monkeypatch.setattr(seedvis_images, "download_reference_image", unexpected_download)
    response = lifecycle(
        "completed",
        mode="text-to-image",
        outputs=[{"type": "image", "url": "https://cdn.seedvis.com/result.png"}],
    )
    response["data"].update(changes)
    provider = adapter(lambda _: httpx.Response(200, json=response))
    async with provider.client:
        with pytest.raises(ProviderError):
            await seedvis_images.generate_reference_image(
                provider,
                model="GEM_PIX_2",
                prompt="Photo of a room",
                ratio="16:9",
                request_id="draft",
            )


@pytest.mark.asyncio
async def test_image_retry_after_and_task_identity(monkeypatch):
    calls, sleeps = [], []

    async def sleep(delay):
        sleeps.append(delay)

    async def download(_url):
        return b"jpeg"

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"code": "server_busy"}, headers={"Retry-After": "7"})
        if request.method == "POST":
            return httpx.Response(202, json=lifecycle(mode="text-to-image"))
        return httpx.Response(
            200,
            json=lifecycle(
                "completed",
                mode="text-to-image",
                id="wrong-task",
                outputs=[{"type": "image", "url": "https://cdn.seedvis.com/result.png"}],
            ),
        )

    monkeypatch.setattr(seedvis_images.asyncio, "sleep", sleep)
    monkeypatch.setattr(seedvis_images, "download_reference_image", download)
    provider = adapter(handler)
    async with provider.client:
        with pytest.raises(ProviderError, match="provider_task_mismatch"):
            await seedvis_images.generate_reference_image(
                provider, model="gpt-image-2", prompt="Photo", ratio="16:9", request_id="same-draft"
            )
    assert sleeps == [7, 30]
    assert [r.method for r in calls] == ["POST", "POST", "GET"]
    assert calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]
    assert json.loads(calls[0].content) == json.loads(calls[1].content)
