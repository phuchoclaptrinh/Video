import base64
import io
import json

import httpx
from PIL import Image
from sqlalchemy import select

from app.services.platform_shared import assets, drafts, story_plan_versions
from tests.test_platform import register

pytest_plugins = ["tests.test_platform"]


def configure_planner(client, monkeypatch):
    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    calls = []
    output = {"core_value": "A visual product review", "clips": [{
        "index": 1, "beat": "Opening", "duration_seconds": 8,
        "video_prompt": "The presenter holds the visible headphones in the same room.",
        "voice_script": "Hôm nay mình cùng tìm hiểu chiếc tai nghe này và chia sẻ trải nghiệm sử dụng nhé.",
    }]}

    def planned(path, *, json):
        assert path == "/internal/v1/planner/story"
        calls.append(json)
        return httpx.Response(200, request=httpx.Request("POST", "http://ai.test" + path), json={"plan": output})

    monkeypatch.setattr(platform.http, "post", planned)
    return platform, calls


def upload(client):
    picture = io.BytesIO()
    Image.new("RGB", (1600, 900), "blue").save(picture, format="PNG")
    response = client.post("/api/v1/assets", files={"file": ("input.png", picture.getvalue(), "image/png")})
    assert response.status_code == 201
    return response.json()


def test_uploaded_image_reaches_planner_without_persisting_base64(client, monkeypatch):
    platform, calls = configure_planner(client, monkeypatch)
    image = upload(client)
    response = client.post("/api/v1/plans", json={"prompt": "Review the headphones in this image", "duration": 8, "asset_id": image["id"], "require_planner": True})
    assert response.status_code == 201, response.text
    data_url = calls[0]["image_data_url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as sent:
        assert sent.size == (1024, 576) and sent.format == "JPEG"
    assert calls[0]["project_context"]["input_reference"]["asset_id"] == image["id"]
    with platform.engine.connect() as db:
        for table in (drafts, story_plan_versions):
            stored = [dict(row) for row in db.execute(select(table)).mappings()]
            assert "data:image" not in json.dumps(stored)
    assert response.json()["spec"]["references"][0]["asset_id"] == image["id"]


def test_text_only_planner_keeps_legacy_request_shape(client, monkeypatch):
    _, calls = configure_planner(client, monkeypatch)
    assert client.post("/api/v1/plans", json={"prompt": "Review a pair of headphones", "duration": 8}).status_code == 201
    assert "image_data_url" not in calls[0]
    assert "input_reference" not in calls[0]["project_context"]


def test_foreign_image_is_rejected_before_planner_call(client, monkeypatch):
    _, calls = configure_planner(client, monkeypatch)
    image = upload(client)
    register(client, "other@example.com")
    response = client.post("/api/v1/plans", json={"prompt": "Review this image", "duration": 8, "asset_id": image["id"]})
    assert response.status_code == 404 and not calls


def test_missing_image_returns_actionable_error_without_draft(client, monkeypatch):
    platform, calls = configure_planner(client, monkeypatch)
    image = upload(client)
    with platform.engine.connect() as db:
        filename = db.execute(select(assets.c.filename).where(assets.c.id == image["id"])).scalar_one()
    (platform.storage / filename).unlink()
    response = client.post("/api/v1/plans", json={"prompt": "Review this image", "duration": 8, "asset_id": image["id"]})
    assert response.status_code == 422 and not calls
    assert "ảnh đầu vào" in response.text


def test_render_checks_the_draft_user_actually_reviewed(client, monkeypatch):
    register(client)
    first = client.post("/api/v1/plans", json={"prompt": "Create a video of a calm room"}).json()
    path = f"/api/v1/video-projects/{first['project_id']}"
    assert client.post(path + "/approve").status_code == 200
    generated = []
    monkeypatch.setattr(client.platform, "generate", lambda uid, draft, paid, **kwargs: generated.append(draft) or {"id": "test-job"})
    denied = client.post(path + "/renders", json={"confirm_paid": True, "expected_draft_id": "obsolete-draft"})
    assert denied.status_code == 409 and not generated
    response = client.post(path + "/renders", json={"confirm_paid": True, "expected_draft_id": first["id"]})
    assert response.status_code == 202 and generated == [first["id"]]
