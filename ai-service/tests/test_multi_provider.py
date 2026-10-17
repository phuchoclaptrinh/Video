import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.pipelines import video_api
from app.pipelines.video_generation import VideoGenerationPipeline
from app.providers.fake import FakeVideoProvider
from app.providers.poyo import PoyoVeoProvider
from app.providers.registry import ProviderRegistry
from app.providers.seedvis import SeedvisProvider
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError
from tests.test_seedvis import lifecycle, request_body


@pytest.fixture
def registry():
    def unexpected(_request):
        if _request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": name} for name in ("Veo-3.1", "seedance_2.5", "seedance_2.0_fast", "Omni-Flash", "gpt-image-2", "GEM_PIX_2", "NARWHAL", "HARBOR_SEAL")]})
        raise AssertionError("No upstream calls are allowed in this test")

    poyo = PoyoVeoProvider(
        httpx.AsyncClient(
            base_url="https://poyo.invalid/", transport=httpx.MockTransport(unexpected)
        ),
        "veo3.1-fast",
    )
    seedvis = SeedvisProvider(
        httpx.AsyncClient(
            base_url="https://seedvis.invalid/", transport=httpx.MockTransport(unexpected)
        ),
        "Veo-3.1",
    )
    return ProviderRegistry([poyo, seedvis])


def test_model_selection_does_not_mutate_default_or_credential(registry):
    default = registry.get("seedvis")
    default.set_api_key("test-shared-secret")
    selected = registry.get("seedvis", "seedance_2.5")
    assert selected.compiler.model == "seedance_2.5"
    assert default.compiler.model == "Veo-3.1"
    assert selected.client is default.client
    default.clear_api_key()
    assert not selected.credential_configured
    with pytest.raises(ProviderError, match="unsupported_model"):
        registry.get("seedvis", "veo3.1-fast")


def test_catalog_and_model_capabilities_are_authenticated_and_secret_free(
    tmp_path, monkeypatch, registry
):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    with TestClient(video_api.create_app(registry, "test-internal-key")) as client:
        assert client.get("/internal/v1/providers").status_code == 401
        client.headers["X-Internal-Key"] = "test-internal-key"
        for provider in ("poyo", "seedvis"):
            assert (
                client.put(
                    f"/internal/v1/video/{provider}/credential",
                    json={"api_key": "test-secret-not-real"},
                ).status_code
                == 200
            )
        response = client.get("/internal/v1/providers")
        assert response.status_code == 200
        assert "test-secret" not in response.text
        catalog = response.json()
        assert {p["id"] for p in catalog["video_providers"]} == {"poyo", "seedvis"}
        assert all(p["configured"] for p in catalog["video_providers"])
        assert (
            len(next(p for p in catalog["video_providers"] if p["id"] == "seedvis")["models"]) == 4
        )
        poyo = next(p for p in catalog["video_providers"] if p["id"] == "poyo")
        assert (
            next(m for m in poyo["models"] if m["id"] == "veo3.1-lite")["story_supported"] is False
        )
        assert (
            next(m for m in poyo["models"] if m["id"] == "veo3.1-fast")["story_supported"] is True
        )
        assert {p["id"] for p in catalog["image_providers"]} == {"seedvis", "openai"}
        cap = client.get("/internal/v1/video/seedvis/capabilities?model=seedance_2.5").json()
        assert cap["model"] == "seedance_2.5" and cap["generation_durations"] == [
            5,
            10,
            15,
            20,
            25,
            30,
        ]
        assert (
            client.get("/internal/v1/video/seedvis/capabilities?model=not-a-model").status_code
            == 422
        )
        invalid = request_body() | {"model": "not-a-model"}
        assert client.post("/internal/v1/video/seedvis/validate", json=invalid).status_code == 422
        assert client.post("/internal/v1/video/seedvis/tasks", json=invalid).status_code == 422


def test_startup_exposes_both_provider_keys_without_enable_flags(tmp_path, monkeypatch):
    for name in (
        "VIDEO_ENABLE_POYO",
        "VIDEO_ENABLE_SEEDVIS",
        "POYO_API_KEY",
        "SEEDVIS_API_KEY",
        "SEEDANCE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    with TestClient(video_api.create_app(internal_key="test-internal-key")) as client:
        client.headers["X-Internal-Key"] = "test-internal-key"
        for provider in ("poyo", "seedvis"):
            path = f"/internal/v1/video/{provider}/credential"
            assert client.get(path).json() == {"configured": False}
            assert client.put(path, json={"api_key": "test-secret-not-real"}).status_code == 200
    with TestClient(video_api.create_app(internal_key="test-internal-key")) as client:
        client.headers["X-Internal-Key"] = "test-internal-key"
        assert client.get("/internal/v1/video/seedvis/credential").json() == {"configured": True}


def test_demo_catalog_preserves_story_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    registry = ProviderRegistry([FakeVideoProvider(str(tmp_path / "demo.sqlite"))])
    with TestClient(video_api.create_app(registry, "test-internal-key")) as client:
        client.headers["X-Internal-Key"] = "test-internal-key"
        model = client.get("/internal/v1/providers").json()["video_providers"][0]["models"][0]
        assert model["id"] == "fake" and model["story_supported"] is True


@pytest.mark.asyncio
async def test_concurrent_models_use_their_own_compiler():
    sent = []

    async def handler(request):
        import json

        sent.append(json.loads(request.content))
        await asyncio.sleep(0)
        return httpx.Response(200, json=lifecycle())

    async with httpx.AsyncClient(
        base_url="https://seedvis.com/api/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        adapter = SeedvisProvider(client, "Veo-3.1")
        adapter.set_api_key("test-key-only")
        pipeline = VideoGenerationPipeline(ProviderRegistry([adapter]))
        requests = [
            ExecutionRequest.model_validate(request_body(duration) | {"model": model})
            for model, duration in (("Veo-3.1", 8), ("seedance_2.5", 10), ("Omni-Flash", 8))
        ]
        await asyncio.gather(*(pipeline.submit("seedvis", request) for request in requests))
        assert {body["model"] for body in sent} == {"Veo-3.1", "seedance_2.5", "Omni-Flash"}
        assert "duration" not in next(body for body in sent if body["model"] == "Omni-Flash")
        assert adapter.compiler.model == "Veo-3.1"


def test_planner_and_reference_image_use_requested_models(tmp_path, monkeypatch, registry):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "keys.enc"))
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    observed = {}

    async def planner(prompt, vault, model, context, capabilities, **kwargs):
        observed["planner_video_model"] = capabilities.model
        return {"clips": []}

    async def image(adapter, **kwargs):
        observed["image_model"] = kwargs["model"]
        return b"mock-image"

    monkeypatch.setattr(video_api, "create_book_story_plan", planner)
    monkeypatch.setattr(video_api, "generate_reference_image", image)
    with TestClient(video_api.create_app(registry, "test-internal-key")) as client:
        client.headers["X-Internal-Key"] = "test-internal-key"
        client.put("/internal/v1/planner/credential", json={"api_key": "test-openai-not-real"})
        assert (
            client.post(
                "/internal/v1/planner/story",
                json={
                    "prompt": "A presenter",
                    "provider": "poyo",
                    "video_model": "veo3.1-fast-official",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/internal/v1/planner/scene-image",
                json={
                    "prompt": "A calm presenter in a bright room",
                    "aspect_ratio": "16:9",
                    "provider": "seedvis",
                    "model": "GEM_PIX_2",
                },
            ).status_code
            == 200
        )
        assert observed == {
            "planner_video_model": "veo3.1-fast-official",
            "image_model": "GEM_PIX_2",
        }
