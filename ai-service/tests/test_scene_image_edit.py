import base64
import json

import httpx
from fastapi.testclient import TestClient

from app.pipelines import video_api
from app.providers.registry import ProviderRegistry
from app.providers.seedvis import SeedvisProvider

PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8product-photo" * 20).decode()


def client_with_openai(tmp_path, monkeypatch, sent, providers=()):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "vault.enc"))
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    image = base64.b64encode(b"\xff\xd8generated").decode()

    async def post(self, url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(200, json={"data": [{"b64_json": image}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = TestClient(video_api.create_app(ProviderRegistry(list(providers)), "test-internal-key"))
    client.headers["X-Internal-Key"] = "test-internal-key"
    return client


def test_reference_images_switch_scene_image_to_edit_mode(tmp_path, monkeypatch):
    sent = []
    with client_with_openai(tmp_path, monkeypatch, sent) as client:
        client.put("/internal/v1/planner/credential", json={"api_key": "test-openai-not-real"})
        body = {"prompt": "A Vietnamese child wearing the outfit in a sunny hallway",
                "aspect_ratio": "16:9", "reference_images": [PHOTO, PHOTO]}
        response = client.post("/internal/v1/planner/scene-image", json=body)
        assert response.status_code == 200 and response.content == b"\xff\xd8generated"
        plain = client.post("/internal/v1/planner/scene-image", json={**body, "reference_images": []})
        assert plain.status_code == 200
    (edit_url, edit), (plain_url, generation) = sent
    assert edit_url.endswith("/v1/images/edits") and plain_url.endswith("/v1/images/generations")
    assert [name for name, _file in edit["files"]] == ["image[]", "image[]"]
    assert edit["files"][0][1][1].startswith(b"\xff\xd8product-photo")
    assert edit["data"]["size"] == "1536x864" and "files" not in generation
    assert json.dumps(generation["json"])


def test_seedvis_scene_image_forwards_reference_images(tmp_path, monkeypatch):
    sent, captured = [], []

    async def fake_image(_provider, **kwargs):
        captured.append(kwargs)
        return b"\xff\xd8seedvis"

    monkeypatch.setattr(video_api, "generate_reference_image", fake_image)
    seedvis = SeedvisProvider(httpx.AsyncClient(base_url="https://seedvis.test/api/v1/"), "Veo-3.1")
    with client_with_openai(tmp_path, monkeypatch, sent, [seedvis]) as client:
        response = client.post("/internal/v1/planner/scene-image", json={
            "provider": "seedvis", "model": "gpt-image-2", "prompt": "A Vietnamese child wearing the outfit",
            "aspect_ratio": "16:9", "reference_images": [PHOTO],
        })
    assert response.status_code == 200 and response.content == b"\xff\xd8seedvis"
    assert captured[0]["reference_images"] == [PHOTO] and not sent


def test_seedvis_image_request_uses_documented_image_to_image_mode():
    from app.pipelines.seedvis_images import image_request

    path, body = image_request("gpt-image-2", "prompt", "16:9", [PHOTO])
    assert path == "images/edits" and body["image"] == [PHOTO] and "mode" not in body
    path, body = image_request("gpt-image-2", "prompt", "16:9")
    assert path == "images/generations" and "image" not in body
    path, body = image_request("GEM_PIX_2", "prompt", "16:9", [PHOTO])
    assert path == "google/v1beta/interactions" and body["mode"] == "image-to-image"
    assert body["reference_images"] == [PHOTO]
