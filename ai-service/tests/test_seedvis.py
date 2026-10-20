import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.pipelines import seedvis_images
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.providers.seedvis import SeedvisProvider
from app.providers.seedvis_compiler import SeedvisCompiler
from app.providers.seedvis_models import IMAGE_MODELS, VIDEO_MODELS
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError


def request_body(duration=8, roles=(), ratio="16:9"):
    return {
        "spec": {
            "request": {"request_id": "request-1", "user_id": "user-1", "tenant_id": "tenant-1"},
            "intent": {"operation": "REFERENCE_GENERATION" if roles else "TEXT_GENERATION", "goal": "A calm presenter speaks."},
            "output": {"duration_seconds": duration, "aspect_ratio": ratio},
            "references": [{"asset_id": f"image-{i}", "role": role} for i, role in enumerate(roles)],
        },
        "assets": [{"asset_id": f"image-{i}", "source_url": f"data:image/jpeg;base64,aW1hZ2U{i}",
                    "mime_type": "image/jpeg", "moderation_status": "APPROVED"} for i in range(len(roles))],
        "preflight_decision": "ALLOW", "policy_decision_id": "review-1",
    }


def lifecycle(status="queued", **overrides):
    data = {"id": "task-1", "status": status, "is_final": status in {"completed", "failed"},
            "mode": "text-to-video", "references": [], "outputs": [],
            "next": {"url": "https://seedvis.com/api/v1/developer/generations/task-1?wait=0", "after_seconds": 30}}
    if status == "completed":
        data["outputs"] = [{"type": "video", "url": "https://cdn.seedvis.com/output.mp4?signature=keep-me"}]
    return {"success": True, "data": data | overrides}


def adapter(handler, model="Veo-3.1"):
    provider = SeedvisProvider(httpx.AsyncClient(base_url="https://seedvis.com/api/v1/",
                              transport=httpx.MockTransport(handler)), model)
    provider.set_api_key("test-seedvis-not-real")
    return provider


@pytest.mark.parametrize("model", VIDEO_MODELS)
@pytest.mark.parametrize("roles", [(), ("FIRST_FRAME",), ("CHARACTER_REFERENCE", "STYLE_REFERENCE", "FIRST_FRAME")])
def test_model_payloads_only_documented_fields(model, roles):
    info = VIDEO_MODELS[model]
    duration = info.durations[0] if info.durations else 8
    request = ExecutionRequest.model_validate(request_body(duration, roles))
    compiler = SeedvisCompiler(model)
    payload = compiler.compile(request)
    body = payload["body"]
    assert body["model"] == model and body["count"] == 1
    assert payload["reference_count"] == len(roles)
    assert payload["idempotency_key"] == compiler.compile(request)["idempotency_key"]
    assert not {"sound", "generate_audio", "previous_video", "seed"}.intersection(body)
    if model == "Omni-Flash":
        assert "duration" not in body
    elif model == "Veo-3.1":
        assert body["duration"] == f"{duration}s"
    if model != "Omni-Flash" and model != "Veo-3.1":
        assert body["duration"] == duration
    assert "upscale_video" not in body
    if roles:
        field = "reference_images" if model.startswith("seedance_") else (
            "image" if len(roles) == 1 else "referenceImages" if model == "Veo-3.1" else "images")
        expected = [asset.source_url for asset in request.assets]
        assert body[field] == (expected[0] if field == "image" else expected)
    else:
        assert body["mode"] == "text-to-video"


def test_frames_audio_and_duration_guards():
    compiler = SeedvisCompiler("Veo-3.1")
    body = request_body(8, ("FIRST_FRAME", "LAST_FRAME"))
    payload = compiler.compile(ExecutionRequest.model_validate(body))["body"]
    assert payload["mode"] == "batch-frame" and "lastFrame" in payload
    for model in ("seedance_2.5", "Omni-Flash"):
        body["spec"]["output"]["duration_seconds"] = 5
        with pytest.raises(ProviderError, match="unsupported_capability"):
            SeedvisCompiler(model).compile(ExecutionRequest.model_validate(body))
    body = request_body(7)
    with pytest.raises(ProviderError, match="unsupported_capability"):
        compiler.compile(ExecutionRequest.model_validate(body))
    body = request_body()
    body["spec"]["output"]["generate_audio"] = False
    with pytest.raises(ProviderError, match="native_audio_disable"):
        compiler.compile(ExecutionRequest.model_validate(body))
    body["spec"]["output"]["generate_audio"] = True
    compiler.compile(ExecutionRequest.model_validate(body))
    body["spec"]["output"]["duration_seconds"] = 5
    with pytest.raises(ProviderError, match="native_audio_unverified"):
        SeedvisCompiler("seedance_2.5").compile(ExecutionRequest.model_validate(body))


@pytest.mark.asyncio
async def test_submit_poll_cancel_preserve_key_and_signed_url():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(409, json={"code": "not_cancellable"})
        return httpx.Response(200, json=lifecycle("queued" if request.method == "POST" else "completed"))
    provider = adapter(handler)
    async with provider.client:
        payload = provider.compile(ExecutionRequest.model_validate(request_body()))
        for _ in range(2):
            result = await provider.submit(payload)
            assert result.status == "queued" and result.poll_after_seconds == 30
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
        assert json.loads(requests[0].content) == payload["body"]
        completed = await provider.cancel("task-1")
        assert completed.status == "completed"
        assert "signature=keep-me" in str(completed.outputs[0].source_url)
        assert requests[-1].url.params["wait"] == "0"


@pytest.mark.parametrize("status,expected", [("queued", "queued"), ("processing", "generating"), ("failed", "failed"), ("completed", "completed")])
@pytest.mark.asyncio
async def test_lifecycle_states(status, expected):
    provider = adapter(lambda _: httpx.Response(200, json=lifecycle(status)))
    async with provider.client:
        assert (await provider.get_status("task-1")).status == expected
        cancelled = lifecycle("failed", error={"code": "cancelled"})
        assert provider.normalize_result(cancelled).status == "cancelled"
        with pytest.raises(ProviderError):
            provider.normalize_result(lifecycle("completed", outputs=[]))
        with pytest.raises(ProviderError):
            provider.normalize_result(lifecycle("queued", is_final=True))
        with pytest.raises(ProviderError):
            provider.safe_poll_path("https://evil.test/api/v1/developer/generations/task-1", "task-1")


@pytest.mark.parametrize("status,code,retryable,ambiguous", [
    (401, "invalid_api_key", False, False), (402, "insufficient_credits", False, False),
    (400, "invalid_image", False, False), (429, "rate_limit_exceeded", True, False),
    (503, "model_maintenance", True, False), (503, "server_busy", True, False),
    (500, "internal_error", True, False), (408, "request_timeout", True, False),
])
@pytest.mark.asyncio
async def test_no_blind_retry_on_ambiguous_submit(status, code, retryable, ambiguous):
    provider = adapter(lambda _: httpx.Response(status, json={"code": code}, headers={"Retry-After": "37"}))
    async with provider.client:
        with pytest.raises(ProviderError) as raised:
            await provider.submit(provider.compile(ExecutionRequest.model_validate(request_body())))
        assert raised.value.retryable is retryable and raised.value.ambiguous is ambiguous
        assert raised.value.retry_after_seconds == 37


@pytest.mark.asyncio
async def test_transport_uncertainty_and_reference_mismatch():
    def handler(request):
        raise httpx.ReadError("disconnect", request=request)
    provider = adapter(handler)
    async with provider.client:
        with pytest.raises(ProviderError) as raised:
            await provider.submit(provider.compile(ExecutionRequest.model_validate(request_body())))
        assert raised.value.retryable and not raised.value.ambiguous
    provider = adapter(lambda _: httpx.Response(200, json=lifecycle()))
    async with provider.client:
        with pytest.raises(ProviderError) as raised:
            await provider.submit(provider.compile(ExecutionRequest.model_validate(request_body(roles=("FIRST_FRAME",)))))
        assert raised.value.ambiguous and raised.value.code == "provider_reference_mismatch"


@pytest.mark.asyncio
async def test_api_validation_key_vault_and_image_route(tmp_path, monkeypatch):
    vault = tmp_path / "credentials.enc"
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(vault))
    monkeypatch.setenv("IMAGE_PROVIDER", "seedvis")
    monkeypatch.setenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2")
    provider = adapter(lambda _: httpx.Response(202, json=lifecycle()))
    provider.clear_api_key()
    captured = []
    async def fake_image(_provider, **kwargs):
        captured.append(kwargs)
        return b"jpeg-test"
    monkeypatch.setattr("app.pipelines.video_api.generate_reference_image", fake_image)
    async with provider.client:
        with TestClient(create_app(ProviderRegistry([provider]), "test-internal-key")) as client:
            assert client.get("/internal/v1/video/seedvis/credential").status_code == 401
            client.headers["X-Internal-Key"] = "test-internal-key"
            path = "/internal/v1/video/seedvis/credential"
            assert not client.get(path).json()["configured"]
            assert client.put(path, json={"api_key": "test-api-key-123"}).json() == {"configured": True}
            assert b"test-api-key-123" not in vault.read_bytes()
            canonical = client.post("/internal/v1/video/seedvis/validate", json=request_body()).json()["execution_request"]
            assert "generate_audio" not in canonical["spec"]["output"]
            assert client.post("/internal/v1/video/seedvis/tasks", json=canonical).status_code == 202
            image = client.post("/internal/v1/planner/scene-image", json={"prompt": "A realistic still image of a room", "aspect_ratio": "16:9", "request_id": "draft-1"})
            assert image.content == b"jpeg-test" and captured[0]["request_id"] == "draft-1"
            assert client.delete(path).json() == {"configured": False}


@pytest.mark.parametrize("model", IMAGE_MODELS)
@pytest.mark.asyncio
async def test_image_models_poll_only_once_and_retry_same_key(model, monkeypatch):
    sent = []
    sleeps = []
    async def fake_sleep(delay):
        sleeps.append(delay)
    async def fake_download(url):
        assert url == "https://cdn.seedvis.com/result.png?signature=keep"
        return b"jpeg"
    def handler(request):
        sent.append(request)
        if request.method == "POST":
            return httpx.Response(202, json=lifecycle(mode="text-to-image"))
        return httpx.Response(200, json=lifecycle("completed", mode="text-to-image", outputs=[{"type": "image", "url": "https://cdn.seedvis.com/result.png?signature=keep"}]))
    monkeypatch.setattr(seedvis_images.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(seedvis_images, "download_reference_image", fake_download)
    provider = adapter(handler)
    async with provider.client:
        for _ in range(2):
            result = await seedvis_images.generate_reference_image(provider, model=model, prompt="A still photo", ratio="9:16", request_id="same-draft")
            assert result == b"jpeg"
    assert [r.method for r in sent] == ["POST", "GET", "POST", "GET"]
    assert sent[0].headers["Idempotency-Key"] == sent[2].headers["Idempotency-Key"]
    body = json.loads(sent[0].content)
    assert body["model"] == model
    if model == "gpt-image-2":
        assert sent[0].url.path.endswith("/images/generations") and body["size"] == "9:16"
    else:
        assert sent[0].url.path.endswith("/google/v1beta/interactions") and body["aspect_ratio"] == "9:16"
    assert sleeps == [30, 30]


@pytest.mark.asyncio
async def test_cdn_download_does_not_leak_auth_and_converts_to_jpeg(monkeypatch):
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(png, format="PNG")
    def handler(request):
        assert "authorization" not in request.headers
        assert request.url.params["signature"] == "keep"
        return httpx.Response(200, content=png.getvalue(), headers={"content-type": "image/png"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    result = await seedvis_images.download_reference_image("https://cdn.seedvis.com/x.png?signature=keep")
    assert result.startswith(b"\xff\xd8")
    for url in ("http://cdn.seedvis.com/x.png", "https://evil.test/x.png", "https://user:pass@cdn.seedvis.com/x.png"):
        with pytest.raises(ProviderError, match="untrusted"):
            await seedvis_images.download_reference_image(url)


@pytest.mark.asyncio
async def test_image_pending_keeps_same_draft_identity():
    provider = adapter(lambda _: httpx.Response(202, json=lifecycle(mode="text-to-image")))
    async with provider.client:
        with pytest.raises(ProviderError, match="pending_retry_same_draft"):
            await seedvis_images.generate_reference_image(provider, model="gpt-image-2", prompt="Photo", ratio="16:9", request_id="draft-1", timeout=0.1)


def test_v2_duration_schema_rejects_shorter_veo_and_15s_seedance_2_5():
    for model, duration in (("Veo-3.1", 4), ("Veo-3.1", 6), ("seedance_2.5", 15)):
        body = request_body(duration)
        body["spec"]["output"]["generate_audio"] = model == "Veo-3.1"
        with pytest.raises(ProviderError, match="unsupported_capability"):
            SeedvisCompiler(model).compile(ExecutionRequest.model_validate(body))
    assert SeedvisCompiler("seedance_2.0_fast").supports(ExecutionRequest.model_validate(request_body(15)).spec)


def test_gpt_image_edits_send_documented_image_field():
    path, body = seedvis_images.image_request("gpt-image-2", "A still photo", "16:9", ["data:image/jpeg;base64,aW1n"])
    assert path == "images/edits" and body["image"] == ["data:image/jpeg;base64,aW1n"]
    assert "images" not in body and "mode" not in body


@pytest.mark.parametrize("payload,retryable", [
    ({"code": "validation_error", "message": "Concurrency and queue limit of your plan reached."}, True),
    ({"code": "validation_error", "message": "aspect_ratio must be one of 16:9, 9:16",
      "errors": {"aspect_ratio": ["invalid"]}}, False),
])
@pytest.mark.asyncio
async def test_full_plan_is_retried_but_schema_errors_are_not(payload, retryable):
    provider = adapter(lambda _: httpx.Response(422, json={"success": False, "status": 422, **payload},
                                                headers={"Retry-After": "20"}))
    async with provider.client:
        with pytest.raises(ProviderError) as raised:
            await provider.submit(provider.compile(ExecutionRequest.model_validate(request_body())))
    assert raised.value.code == "seedvis_validation_error"
    assert raised.value.retryable is retryable and not raised.value.ambiguous


@pytest.mark.asyncio
async def test_reference_image_moves_to_the_next_key_when_one_is_out_of_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    provider = adapter(lambda _: httpx.Response(202, json=lifecycle()))
    used = []

    async def fake_image(image_provider, **_kwargs):
        key = image_provider.api_key or image_provider.client.headers["Authorization"].removeprefix("Bearer ")
        used.append(key)
        if key != "test-key-third-123":
            raise ProviderError("seedvis_insufficient_credits")
        return b"jpeg-third"

    monkeypatch.setattr("app.pipelines.video_api.generate_reference_image", fake_image)
    async with provider.client:
        with TestClient(create_app(ProviderRegistry([provider]), "test-internal-key")) as client:
            client.headers["X-Internal-Key"] = "test-internal-key"
            for key in ("test-key-first-123", "test-key-second-12", "test-key-third-123"):
                assert client.post("/internal/v1/video/seedvis/credentials",
                                   json={"api_key": key, "slots": 1}).status_code == 201
            body = {"prompt": "A realistic still image of a room", "aspect_ratio": "16:9",
                    "request_id": "draft-1", "provider": "seedvis", "model": "gpt-image-2"}
            image = client.post("/internal/v1/planner/scene-image", json=body)
            assert image.status_code == 200 and image.content == b"jpeg-third"
            assert used == ["test-key-first-123", "test-key-second-12", "test-key-third-123"]
            used.clear()
            client.delete(f"/internal/v1/video/seedvis/credentials/{client.get('/internal/v1/video/seedvis/credentials').json()['keys'][2]['id']}")
            failed = client.post("/internal/v1/planner/scene-image", json=body)
            assert failed.status_code == 502 and failed.json()["detail"] == "seedvis_insufficient_credits"
            assert used == ["test-key-first-123", "test-key-second-12"]


@pytest.mark.parametrize("body", [{"success": False, "message": "Số dư không đủ"}, {"error": {"code": "PAYMENT"}}, {}])
@pytest.mark.asyncio
async def test_any_402_counts_as_out_of_credit(body):
    provider = adapter(lambda _: httpx.Response(402, json=body))
    async with provider.client:
        with pytest.raises(ProviderError) as raised:
            await provider.submit(provider.compile(ExecutionRequest.model_validate(request_body())))
    assert raised.value.code == "seedvis_insufficient_credits"
    assert not raised.value.retryable and not raised.value.ambiguous
