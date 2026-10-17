import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.core.credential_vault import CredentialVault
from app.pipelines.story_planner import create_book_story_plan
from app.pipelines.video_api import PlannerInput, create_app
from app.providers.registry import ProviderRegistry
from app.schemas.provider import VideoProviderCapabilities
from tests.test_story_dialogue import Vault, mock_planner, story


def picture():
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(image, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(image.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_vision_planner_sends_image_and_preserves_context_model_and_schema(monkeypatch):
    calls = mock_planner(monkeypatch, story(), {})
    image = picture()
    result = await create_book_story_plan("Video với giọng chậm 1.0-2.0 âm tiết/giây", Vault(), "gpt-4o-mini", {"memories": ["Giọng Bắc"]}, video_capabilities=VideoProviderCapabilities(provider="poyo", model="veo3.1-fast", generation_durations=[4, 6, 8]), reference_image_data_url=image)
    assert result["clips"] and len(calls) == 1
    sent = calls[0]
    assert sent["model"] == "gpt-4o-mini" and sent["response_format"]["json_schema"]["strict"]
    content = sent["messages"][1]["content"]
    assert json.loads(content[0]["text"])["project_context"]["memories"] == ["Giọng Bắc"]
    assert content[1] == {"type": "image_url", "image_url": {"url": image, "detail": "high"}}
    assert "INPUT VISUAL REFERENCE" in sent["messages"][0]["content"]
    assert "not instructions" in sent["messages"][0]["content"]


@pytest.mark.asyncio
async def test_text_only_planner_stays_text_only(monkeypatch):
    calls = mock_planner(monkeypatch, story(), {})
    await create_book_story_plan("Video với giọng chậm 1.0-2.0 âm tiết/giây", Vault(), "gpt-4o-mini", video_capabilities=VideoProviderCapabilities(provider="poyo", model="veo3.1-fast", generation_durations=[4, 6, 8]))
    assert isinstance(calls[0]["messages"][1]["content"], str)
    assert "INPUT VISUAL REFERENCE" not in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("source", ["https://untrusted.example/image.jpg", "file:///image.jpg", "data:text/plain;base64," + "a" * 120])
def test_internal_planner_only_accepts_bounded_inline_jpeg(source):
    with pytest.raises(ValidationError):
        PlannerInput(prompt="Plan a product video", image_data_url=source)
    assert PlannerInput(prompt="Plan a product video", image_data_url=picture()).image_data_url


def test_internal_api_forwards_the_photo_to_the_vision_planner(tmp_path, monkeypatch):
    captured = []

    async def planned(*args, **kwargs):
        captured.append((args, kwargs))
        return story()

    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda *_args: "unit-test-not-real-key")
    monkeypatch.setattr("app.pipelines.video_api.create_book_story_plan", planned)
    image = picture()
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        result = client.post("/internal/v1/planner/story", headers={"X-Internal-Key": "test-secret"}, json={"prompt": "Use the subject in this photo", "image_data_url": image, "target_duration_seconds": 20})
    assert result.status_code == 200
    assert captured[0][1]["reference_image_data_url"] == image
    assert captured[0][1]["target_duration_seconds"] == 20
