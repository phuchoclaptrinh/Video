import copy
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, update

from app.services.platform import drafts, story_batches, story_plan_versions
from tests.test_platform import register

pytest_plugins = ["tests.test_platform"]


CATALOG = {
    "video_providers": [
        {
            "id": "poyo",
            "configured": True,
            "models": [
                {"id": "veo3.1-fast", "image_input": True},
                {"id": "veo3.1-quality", "image_input": True},
                {"id": "veo3.1-lite", "image_input": False},
            ],
        },
        {
            "id": "seedvis",
            "configured": True,
            "models": [
                {"id": model, "image_input": True}
                for model in ("Veo-3.1", "seedance_2.5", "Omni-Flash")
            ],
        },
    ],
    "image_providers": [
        {"id": "openai", "models": [{"id": "test-image"}]},
        {"id": "seedvis", "models": [{"id": "GEM_PIX_2"}]},
    ],
}


def configure(client, monkeypatch):
    platform = client.platform
    platform.paid_enabled = True
    platform.public_base_url = "https://vo3.example.test"
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "test-image")
    calls = []
    picture = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(picture, "JPEG")

    def handler(request):
        calls.append(request)
        if request.url.path == "/internal/v1/providers":
            return httpx.Response(200, json=copy.deepcopy(CATALOG))
        if request.url.path.endswith("/credential"):
            return httpx.Response(200, json={"configured": "/planner/" not in request.url.path})
        if request.url.path.endswith("/scene-image"):
            return httpx.Response(200, content=picture.getvalue())
        return httpx.Response(404)

    platform.http.close()
    platform.http = httpx.Client(
        base_url="http://example.test", transport=httpx.MockTransport(handler)
    )
    register(client)
    return platform, calls


def plan(client, provider, model, **extra):
    return client.post(
        "/api/v1/plans",
        json={
            "prompt": "A stable presenter in a studio",
            "duration": 8,
            "provider": provider,
            "video_model": model,
            "image_provider": "seedvis",
            "image_model": "GEM_PIX_2",
            "use_planner": False,
            **extra,
        },
    )


def test_catalog_requires_session_and_allows_poyo_images_without_tunnel(client, monkeypatch):
    assert client.get("/api/v1/providers").status_code == 401
    platform, _ = configure(client, monkeypatch)
    platform.public_base_url = ""
    result = client.get("/api/v1/providers").json()["video_providers"]
    assert result[0]["models"][0]["local_image_upload"] is True
    assert result[1]["models"][0]["local_image_upload"] is True
    platform.paid_enabled = False
    assert not any(p["enabled"] for p in client.get("/api/v1/providers").json()["video_providers"])


def test_drafts_pin_provider_model_and_image_despite_default_changes(client, monkeypatch):
    platform, _ = configure(client, monkeypatch)
    first = plan(client, "poyo", "veo3.1-quality").json()
    second = plan(client, "seedvis", "seedance_2.5", duration=10).json()
    assert first["render_target"]["provider"] == "poyo"
    assert second["render_target"]["image_model"] == "GEM_PIX_2"
    assert platform.provider == "fake"
    platform.provider = "seedvis"
    monkeypatch.setenv("SEEDVIS_VIDEO_MODEL", "Omni-Flash")
    for draft, expected, model in (
        (first, "poyo", "veo3.1-quality"),
        (second, "seedvis", "seedance_2.5"),
    ):
        path = f"/api/v1/plans/{draft['id']}/generate"
        assert client.post(path, json={"confirm_paid": False}).status_code == 409
        generated = client.post(path, json={"confirm_paid": True})
        assert generated.status_code == 202, generated.text
        job = platform.store.get(generated.json()["id"])
        assert job["provider"] == expected and job["payload"]["model"] == model
        assert "render_target" not in job["payload"]
        assert platform.provider == "seedvis"
    revised = client.post(
        f"/api/v1/video-projects/{first['project_id']}/plans",
        json={
            "prompt": "Change only the background in this video",
            "duration": 8,
            "use_planner": False,
        },
    ).json()
    assert revised["render_target"] == first["render_target"]


@pytest.mark.parametrize("provider,model", [("seedvis", "veo3.1-fast"), ("poyo", "Omni-Flash")])
def test_reject_provider_model_mismatch_before_saving(client, monkeypatch, provider, model):
    platform, _ = configure(client, monkeypatch)
    assert plan(client, provider, model).status_code == 422
    with platform.engine.connect() as db:
        assert db.execute(select(drafts)).first() is None


def test_server_paid_gate_cannot_be_bypassed_by_selection(client, monkeypatch):
    platform, _ = configure(client, monkeypatch)
    draft = plan(client, "seedvis", "Veo-3.1").json()
    platform.paid_enabled = False
    assert plan(client, "seedvis", "Veo-3.1").status_code == 409
    assert (
        client.post(
            f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "extra", [{"asset_id": "image-not-uploaded"}, {"require_planner": True, "use_planner": True}]
)
def test_text_only_model_rejects_image_or_story_before_planner_calls(client, monkeypatch, extra):
    platform, calls = configure(client, monkeypatch)
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    assert plan(client, "poyo", "veo3.1-lite", **extra).status_code == 422
    assert not any(call.url.path.endswith(("/story", "/scene-image")) for call in calls)
    with platform.engine.connect() as db:
        assert db.execute(select(drafts)).first() is None


def test_reference_image_uses_persisted_image_provider(client, monkeypatch):
    platform, calls = configure(client, monkeypatch)
    draft = plan(client, "seedvis", "Veo-3.1").json()
    with platform.engine.begin() as db:
        payload = db.execute(
            select(drafts.c.payload).where(drafts.c.id == draft["id"])
        ).scalar_one()
        payload["story_plan"] = {
            "reference_image_prompt": "A calm presenter in a bright studio",
            "clips": [],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    result = client.post(f"/api/v1/video-projects/{draft['project_id']}/reference-image")
    assert result.status_code == 200, result.text
    body = json.loads(
        next(call.content for call in calls if call.url.path.endswith("/scene-image"))
    )
    assert body["provider"] == "seedvis" and body["model"] == "GEM_PIX_2"


def test_batch_worker_routes_from_draft_snapshot(client, monkeypatch):
    platform, _ = configure(client, monkeypatch)
    draft = plan(client, "seedvis", "Veo-3.1").json()
    uid = client.get("/api/v1/me").json()["id"]
    story = {
        "clips": [
            {
                "index": 1,
                "video_prompt": "A calm presenter speaks in one stable room.",
                "voice_script": "Mình cùng khám phá một câu chuyện thú vị trong hôm nay nhé.",
                "duration_seconds": 8,
            }
        ]
    }
    with platform.engine.begin() as db:
        payload = db.execute(
            select(drafts.c.payload).where(drafts.c.id == draft["id"])
        ).scalar_one()
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    monkeypatch.setattr(platform, "_story_media_ready", lambda *_args: True)
    monkeypatch.setattr(
        platform,
        "_validate_public_backend_target",
        lambda: pytest.fail("Seedvis must not use a PoYo tunnel"),
    )
    generated = platform.generate(uid, draft["id"], True)
    assert generated["provider"] == "seedvis"
    platform.provider = "poyo"
    platform.process_story_batches()
    with platform.engine.connect() as db:
        row = dict(
            db.execute(select(story_batches).where(story_batches.c.id == generated["id"]))
            .mappings()
            .one()
        )
        version = db.execute(
            select(story_plan_versions.c.payload).where(
                story_plan_versions.c.draft_id == draft["id"]
            )
        ).scalar_one()
    assert version["render_target"] == draft["render_target"]
    assert len(row["generation_ids"]) == 1, row
    job = platform.store.get(row["generation_ids"][0])
    assert job["provider"] == "seedvis" and job["payload"]["model"] == "Veo-3.1"
    assert platform.present_story_batch(row)["provider"] == "seedvis"
    assert platform.provider == "poyo"
