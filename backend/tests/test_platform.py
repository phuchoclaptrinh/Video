import copy
import hashlib
import io
import json
import re
import subprocess
from typing import ClassVar

import httpx
import imageio_ffmpeg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import insert, select, update

from app.main import create_app
from app.services.generation_orchestrator import ExecutionSettings
from app.services.platform import (
    Platform,
    drafts,
    story_batches,
    story_plan_versions,
    video_quality_reports,
    videos,
)
from app.services.platform_shared import video_scenes
from app.services.platform_story_render import _native_audio_gap_issues, _native_speech_issues
from app.services.video_execution_client import ExecutionError
from app.services.video_execution_store import executions

NATIVE_FIRST = "Hôm nay mình muốn cùng các bạn tìm hiểu cuốn sách trên tay và chia sẻ những điều mình đang quan sát,"
NATIVE_NEXT = "để mỗi người có thể dành thêm chút thời gian suy nghĩ và chọn điều phù hợp với nhu cầu của mình."


@pytest.mark.parametrize("model,durations", [
    ("Veo-3.1", [4, 6, 8]), ("seedance_2.5", [5, 10, 15, 20, 25, 30]),
    ("seedance_2.0_fast", [5, 10, 15]), ("Omni-Flash", [10]),
])
def test_seedvis_config_and_direct_planning(client, monkeypatch, model, durations):
    monkeypatch.setenv("SEEDVIS_VIDEO_MODEL", model)
    platform = client.platform
    use_legacy_test_provider(platform, "seedvis")
    platform.paid_enabled = True
    register(client)
    config = client.get("/api/v1/config").json()
    assert config["model"] == model and config["duration_options"] == durations
    assert config["continuity_enabled"] is True and config["local_image_upload"] is True
    assert config["duration_control"] is bool(durations)
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    body = {"prompt": "A beautiful landscape at sunrise", "duration": durations[0] if durations else 8, "use_planner": False}
    response = client.post("/api/v1/plans", json=body)
    assert response.status_code == 201, response.text
    assert response.json().get("story_plan") is None
    if model not in {"Veo-3.1", "Omni-Flash"}:
        response = client.post("/api/v1/plans", json=body | {"require_planner": True})
        assert response.status_code == 422 and "chưa được xác nhận" in response.text


def test_seedvis_uploaded_image_uses_inline_bytes_no_public_tunnel(client, monkeypatch):
    monkeypatch.setenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1")
    platform = client.platform
    use_legacy_test_provider(platform, "seedvis")
    platform.paid_enabled = True
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    register(client)
    data = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(data, format="PNG")
    asset = client.post("/api/v1/assets", files={"file": ("input.png", data.getvalue(), "image/png")})
    assert asset.status_code == 201
    draft = client.post("/api/v1/plans", json={"prompt": "A beautiful landscape", "duration": 8, "asset_id": asset.json()["id"]}).json()
    denied = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": False})
    assert denied.status_code == 409
    response = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True})
    assert response.status_code == 202, response.text
    job = platform.store.get(response.json()["id"])
    assert job["provider"] == "seedvis"
    assert job["payload"]["assets"][0]["source_url"].startswith("data:image/jpeg;base64,")
    assert not platform.public_base_url


def test_poyo_uploaded_image_is_sent_inline_without_public_tunnel(client):
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.paid_enabled = True
    platform.public_base_url = ""
    platform.provider_credential_status = lambda provider=None: {"configured": True}
    register(client)
    data = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(data, format="PNG")
    asset = client.post("/api/v1/assets", files={"file": ("input.png", data.getvalue(), "image/png")})
    assert asset.status_code == 201
    draft = client.post("/api/v1/plans", json={"prompt": "A beautiful landscape", "duration": 8, "asset_id": asset.json()["id"]})
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    with platform.engine.begin() as db:
        stored = db.execute(select(drafts.c.payload).where(drafts.c.id == draft_id)).scalar_one()
        # Planning-only fields from sales/chat drafts must never reach the execution contract.
        stored = {**stored, "input_asset_ids": [asset.json()["id"]], "planning_goal": "sales"}
        db.execute(update(drafts).where(drafts.c.id == draft_id).values(payload=stored))
    response = client.post(f"/api/v1/plans/{draft_id}/generate", json={"confirm_paid": True})
    assert response.status_code == 202, response.text
    job = platform.store.get(response.json()["id"])
    assert job["provider"] == "poyo"
    assert job["payload"]["assets"][0]["source_url"].startswith("data:image/jpeg;base64,")
    assert set(job["payload"]) <= {"model", "spec", "assets", "preflight_decision", "policy_decision_id"}


def test_seedvis_separate_image_credential_requires_login(client):
    path = "/api/v1/provider-credential?provider=seedvis"
    assert client.put(path, json={"api_key": "test-not-real"}).status_code == 401
    register(client)
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"configured": request.method != "DELETE"})
    client.platform.http.close()
    client.platform.http = httpx.Client(base_url="http://example.test", transport=httpx.MockTransport(handler))
    for method in ("PUT", "GET", "DELETE"):
        response = client.request(method, path, **({"json": {"api_key": "test-not-real"}} if method == "PUT" else {}))
        assert response.status_code == 200
        assert "test-not-real" not in response.text
        assert sent[-1].url.path == "/internal/v1/video/seedvis/credential"


@pytest.fixture
def client(tmp_path):
    platform = Platform(
        "sqlite:///" + str(tmp_path / "platform.sqlite"),
        tmp_path / "assets",
        "http://example.test",
        "test-key",
    )
    def internal_handler(request):
        if request.method == "GET" and request.url.path == "/internal/v1/planner/credential":
            return httpx.Response(200, json={"configured": False})
        return httpx.Response(404)

    platform.http.close()
    platform.http = httpx.Client(base_url="http://example.test", transport=httpx.MockTransport(internal_handler))

    class Client:
        def call(self, provider, action, **kwargs):
            if action == "validate":
                return {"execution_request": kwargs["payload"]}
            if action == "submit":
                return {"provider_task_id": "fake1"}
            return {"status": "completed", "outputs": [{"source_url": "https://example.com/demo"}]}

    platform.runtime.client = Client()
    with TestClient(create_app(platform, run_worker=False)) as http:
        http.platform = platform
        yield http


def register(client, email="one@example.com"):
    """Sign up, or when self sign-up is closed create the account like an admin would and sign in."""
    credentials = {"email": email, "password": "test-password-123"}
    response = client.post("/api/v1/auth/register", json=credentials)
    if response.status_code == 403:
        with client.platform.engine.begin() as db:
            client.platform.create_account(db, email, credentials["password"])
        response = client.post("/api/v1/auth/login", json=credentials)
    return response


def use_legacy_test_provider(platform, provider):
    # Older continuity fixtures construct synthetic demo drafts before choosing an adapter.
    # Remove the new snapshot only in these fixtures to exercise legacy-draft compatibility.
    platform.provider = provider
    platform.paid_enabled = True
    with platform.engine.begin() as db:
        for table in (drafts, story_plan_versions):
            for row in db.execute(select(table)).mappings().all():
                payload = copy.deepcopy(row["payload"])
                payload.pop("render_target", None)
                db.execute(update(table).where(table.c.id == row["id"]).values(payload=payload))


def persist_completed_clip(platform, generation_id, uid, source_url=None):
    path = platform.storage / "videos" / f"{generation_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unit-test native video output")
    with platform.engine.begin() as db:
        if db.execute(select(videos.c.generation_id).where(videos.c.generation_id == generation_id)).first() is None:
            db.execute(insert(videos).values(
                generation_id=generation_id, user_id=uid, filename=path.name,
                size_bytes=path.stat().st_size, created=1,
            ))
        db.execute(update(executions).where(executions.c.generation_id == generation_id).values(
            status="COMPLETED", result={"outputs": [{"source_url": source_url or f"https://storage.poyo.ai/{generation_id}.mp4"}]},
        ))
    return path


def test_sessions_and_logout(client):
    assert client.get("/api/v1/me").status_code == 401
    response = register(client)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/v1/me").json()["email"] == "one@example.com"
    client.delete("/api/v1/auth/session")
    assert client.get("/api/v1/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "one@example.com", "password": "incorrect"}
        ).status_code
        == 401
    )


def test_planner_dialogue_failure_has_specific_message_and_does_not_save_draft(client, monkeypatch):
    import httpx

    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})

    def failed_planner(*_args, **_kwargs):
        request = httpx.Request("POST", "http://ai.test/internal/v1/planner/story")
        response = httpx.Response(502, request=request, json={"detail": "planner_dialogue_mismatch"})
        response.raise_for_status()

    monkeypatch.setattr(platform.http, "post", failed_planner)
    response = client.post("/api/v1/plans", json={"prompt": "A coherent Vietnamese presenter video"})
    assert response.status_code == 502
    assert "bài nói tổng thể" in response.json()["detail"]
    assert "thiếu hoặc sai trường" not in response.json()["detail"]
    with platform.engine.connect() as db:
        assert db.execute(select(drafts.c.id)).first() is None


@pytest.mark.parametrize("reason,message", [
    ("planner_connection_failed", "Kết nối tới OpenAI bị gián đoạn"),
    ("planner_timeout", "OpenAI phản hồi quá lâu"),
    ("planner_upstream_error", "Dịch vụ OpenAI đang gặp lỗi"),
    ("planner_duration_mismatch", "thời lượng yêu cầu"),
    ("planner_spoken_text_invalid", "ký hiệu"),
    ("planner_refused", "từ chối"),
    ("planner_output_truncated", "bị cắt"),
])
def test_planner_transport_failure_has_specific_message_and_does_not_save_draft(client, monkeypatch, reason, message):
    import httpx

    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})

    def failed_planner(*_args, **_kwargs):
        request = httpx.Request("POST", "http://ai.test/internal/v1/planner/story")
        response = httpx.Response(502, request=request, json={"detail": reason})
        response.raise_for_status()

    monkeypatch.setattr(platform.http, "post", failed_planner)
    response = client.post("/api/v1/plans", json={"prompt": "A coherent Vietnamese presenter video"})
    assert response.status_code == 502 and message in response.json()["detail"]
    assert "thiếu hoặc sai trường" not in response.json()["detail"]
    with platform.engine.connect() as db:
        assert db.execute(select(drafts.c.id)).first() is None


def test_planner_total_duration_is_forwarded_saved_and_retained_during_replan(client, monkeypatch):
    import httpx

    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    calls = []
    output = {"requested_duration_seconds": 20, "duration_target_seconds": 24, "creative_format": "storytime",
              "core_value": "A coherent book review", "clips": [
                  {"index": index, "beat": "Continuation", "duration_seconds": 8,
                   "video_prompt": "The same seated presenter speaks in one continuous locked shot.",
                   "voice_script": f"Đây là phần chia sẻ thứ {index} về cuốn sách trên tay."}
                  for index in range(1, 4)
              ]}

    def planned(_path, *, json):
        calls.append(copy.deepcopy(json))
        return httpx.Response(200, request=httpx.Request("POST", "http://ai.test/internal/v1/planner/story"), json={"plan": output})

    monkeypatch.setattr(platform.http, "post", planned)
    first = client.post("/api/v1/plans", json={"prompt": "A calm book review", "duration": 8, "target_duration_seconds": 20})
    assert first.status_code == 201 and calls[0]["target_duration_seconds"] == 20
    plan = first.json()
    assert plan["spec"]["output"]["duration_seconds"] == 8
    assert len(plan["story_plan"]["clips"]) == 3 and plan["story_plan"]["duration_target_seconds"] == 24
    with platform.engine.connect() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
    assert payload["story_plan"]["requested_duration_seconds"] == 20
    revised = client.post(f"/api/v1/video-projects/{plan['project_id']}/plans",
                          json={"prompt": "Keep the story but change the regional accent", "duration": 8})
    assert revised.status_code == 201
    assert calls[1]["project_context"]["previous_target_duration_seconds"] == 20
    assert calls[1]["project_context"]["previous_creative_format"] == "storytime"
    assert calls[1]["target_duration_seconds"] is None


def test_planner_unsupported_total_duration_has_actionable_error_and_no_draft(client, monkeypatch):
    import httpx

    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})

    def unsupported(*_args, **_kwargs):
        response = httpx.Response(422, request=httpx.Request("POST", "http://ai.test/internal/v1/planner/story"),
                                  json={"detail": "planner_duration_unsupported"})
        response.raise_for_status()

    monkeypatch.setattr(platform.http, "post", unsupported)
    response = client.post("/api/v1/plans", json={"prompt": "A one minute video", "duration": 8, "target_duration_seconds": 60})
    assert response.status_code == 422 and "tối đa 5 cảnh" in response.json()["detail"]
    with platform.engine.connect() as db:
        assert db.execute(select(drafts.c.id)).first() is None


@pytest.mark.parametrize("kind", ["valid", "placeholder", "empty", "bad_partition"])
def test_planner_cleans_spoken_fields_after_partition_validation_before_persistence(client, monkeypatch, kind):
    import httpx

    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    spoken = "**Hôm nay** mình chia sẻ “cuốn sách này” 😊, giá $5 giảm 10%."
    if kind == "empty":
        spoken = "*** 😊 !!!"
    if kind == "placeholder":
        spoken = "Mình chia sẻ [thông tin bổ sung] hôm nay."
    output = {"duration_target_seconds": 8, "voice_bible": "Vietnamese; Hanoi Northern accent", "clips": [
        {"index": 1, "beat": "intro", "duration_seconds": 8,
         "video_prompt": "Keep the same book [prop] in a locked shot.", "voice_script": spoken},
    ], "full_voice_script": spoken if kind != "bad_partition" else "Nội dung hoàn toàn khác."}
    original = copy.deepcopy(output)

    def planned(_path, **_kwargs):
        return httpx.Response(200, request=httpx.Request("POST", "http://ai.test/internal/v1/planner/story"), json={"plan": output})

    monkeypatch.setattr(platform.http, "post", planned)
    response = client.post("/api/v1/plans", json={"prompt": "KOC nữ nói giọng Bắc giới thiệu cuốn sách"})
    with platform.engine.connect() as db:
        if kind in {"empty", "bad_partition"}:
            assert response.status_code == 502 and db.execute(select(drafts.c.id)).first() is None
            if kind == "empty":
                assert "lời thoại rỗng" in response.json()["detail"]
        else:
            assert response.status_code == 201
            result = response.json()
            story = result["story_plan"]
            expected = "Hôm nay mình chia sẻ cuốn sách này, giá 5 đô la Mỹ giảm 10 phần trăm."
            if kind == "placeholder":
                expected = "Mình chia sẻ thông tin bổ sung hôm nay."
                assert story["clips"][0]["unresolved_voice_placeholder"] is True
            assert story["full_voice_script"] == story["clips"][0]["voice_script"] == expected
            assert story["clips"][0]["video_prompt"] == output["clips"][0]["video_prompt"]
            assert story["duration_target_seconds"] == 8 and story["voice_bible"] == output["voice_bible"]
            assert db.execute(select(drafts.c.payload).where(drafts.c.id == result["id"])).scalar_one()["story_plan"] == story
            assert db.execute(select(story_plan_versions.c.payload).where(story_plan_versions.c.draft_id == result["id"])).scalar_one()["story_plan"] == story
            assert db.execute(select(video_scenes.c.payload).where(video_scenes.c.draft_id == result["id"])).scalar_one() == story["clips"][0]
    if kind == "placeholder":
        approval = client.post(f"/api/v1/video-projects/{response.json()['project_id']}/approve")
        assert approval.status_code == 409 and "chỗ trống" in approval.json()["detail"]
        render = client.post(f"/api/v1/plans/{response.json()['id']}/generate", json={"confirm_paid": False})
        assert render.status_code == 409 and "chỗ trống" in render.json()["detail"]
    assert output == original


def test_total_duration_requires_planner_instead_of_silently_using_template(client, monkeypatch):
    register(client)
    monkeypatch.setattr(client.platform, "planner_credential_status", lambda: {"configured": False})
    response = client.post("/api/v1/plans", json={"prompt": "A calm book review", "duration": 8, "target_duration_seconds": 20})
    assert response.status_code == 409 and "tổng thời lượng" in response.json()["detail"]


def test_chat_persists_history_and_injects_saved_memory(client):
    register(client)
    platform = client.platform
    platform.planner_credential_status = lambda: {"configured": True}
    captured = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": "Tôi sẽ ghi nhớ ngữ cảnh cuộc trò chuyện này."}

    class InternalClient:
        def post(self, path, **kwargs):
            assert path == "/internal/v1/chat/completions"
            captured.append(kwargs["json"]["messages"])
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    first = client.post(
        "/api/v1/chat/messages", json={"content": "Tôi đang xây dựng ứng dụng tạo video."}
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    memory = client.post(
        "/api/v1/memories", json={"content": "Tôi muốn câu trả lời bằng tiếng Việt."}
    )
    assert memory.status_code == 201
    second = client.post(
        "/api/v1/chat/messages",
        json={"conversation_id": conversation_id, "content": "Bạn nhớ tôi đang làm gì không?"},
    )
    assert second.status_code == 200
    assert len(client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()) == 4
    assert "Tôi muốn câu trả lời bằng tiếng Việt." in captured[1][1]["content"]
    assert captured[1][-2]["content"] == "Bạn nhớ tôi đang làm gì không?" or captured[1][-1]["content"] == "Bạn nhớ tôi đang làm gì không?"
    assert len(client.get("/api/v1/chat/conversations").json()) == 1
    client.delete(f"/api/v1/chat/conversations/{conversation_id}")
    assert client.get("/api/v1/chat/conversations").json() == []


def test_chat_video_plan_and_job_persist_in_conversation(client):
    register(client)
    plan = client.post(
        "/api/v1/plans", json={"prompt": "A quiet cinematic scene in a forest"}
    ).json()
    saved = client.post(
        "/api/v1/chat/video-events",
        json={
            "prompt": "A quiet cinematic scene in a forest",
            "plan": plan,
        },
    )
    assert saved.status_code == 201
    conversation_id = saved.json()["conversation_id"]
    event_message = saved.json()["messages"][1]
    event = json.loads(event_message["content"])
    assert event["type"] == "video_generation"
    assert event["plan"]["id"] == plan["id"]

    job = client.post(f"/api/v1/plans/{plan['id']}/generate", json={}).json()
    updated = client.put(
        f"/api/v1/chat/video-events/{event_message['id']}", json={"job_id": job["id"]}
    )
    assert updated.status_code == 200
    history = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()
    stored_event = json.loads(history[1]["content"])
    assert stored_event["job"]["id"] == job["id"]
    assert client.get("/api/v1/chat/conversations").json()[0]["id"] == conversation_id


def test_failed_unbilled_scene_can_be_revised_without_regenerating_completed_clips(client, monkeypatch):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "A book review with three scenes"}).json()
    story = {
        "core_value": "Book review",
        "visual_continuity": "Same host and bookshelf in both scenes.",
        "clips": [
            {"index": index, "beat": f"scene {index}",
             "video_prompt": f"The host presents a book in the same room, scene {index}.",
             "voice_script": "A book [title] is here." if index == 3 else f"Book scene {index}."}
            for index in (1, 2, 3)
        ],
    }
    story["clips"][2]["unresolved_voice_placeholder"] = True
    plan["story_plan"] = story
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        version = db.execute(
            select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])
        ).mappings().one()
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = story
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"])
                   .values(payload=version_payload))
    saved = client.post("/api/v1/chat/video-events", json={"prompt": "A book review", "plan": plan})
    assert saved.status_code == 201
    message_id = saved.json()["messages"][1]["id"]
    # Legacy approved plans could contain placeholders before approval validation existed.
    with platform.engine.begin() as db:
        db.execute(update(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])
                   .values(status="APPROVED"))
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    owner_id = client.get("/api/v1/me").json()["id"]
    batch_id = hashlib.sha256(f"{plan['id']}:complete-story".encode()).hexdigest()[:32]
    generation_ids = []
    for index in (1, 2):
        job_payload = copy.deepcopy(payload)
        job_payload.pop("story_plan")
        job_payload["spec"]["request"]["request_id"] = f"test-scene-{index}"
        job = platform.store.create("poyo", job_payload)
        generation_ids.append(job["generation_id"])
    child_id = hashlib.sha256(f"{plan['id']}:clip:2".encode()).hexdigest()[:32]
    legacy_payload = copy.deepcopy(job_payload)
    legacy_payload["spec"]["references"] = [
        {"asset_id": "old-character", "role": "CHARACTER_REFERENCE"},
        {"asset_id": "old-style", "role": "STYLE_REFERENCE"},
        {"asset_id": "old-frame", "role": "FIRST_FRAME"},
    ]
    frame_dir = platform.storage / "continuity"
    frame_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(frame_dir / f"{generation_ids[0]}.jpg")
    with platform.engine.begin() as db:
        db.execute(insert(drafts).values(
            id=child_id, user_id=owner_id, title="Old scene 2", payload=legacy_payload,
            generation_id=generation_ids[1], created=1,
        ))
        db.execute(update(executions).where(executions.c.generation_id == generation_ids[0])
                   .values(status="COMPLETED"))
        db.execute(update(executions).where(executions.c.generation_id == generation_ids[1])
                   .values(status="FAILED", error_code="poyo_generation_failed",
                           result={"usage": {"credits": 0.0},
                                   "error_detail": "The content does not comply with the platform regulations."}))
        db.execute(insert(story_batches).values(
            id=batch_id, user_id=owner_id, title="Book review", draft_id=plan["id"],
            ratio="16:9", generation_ids=generation_ids, status="FAILED", audio_mode="native",
            error_code="poyo_generation_failed", created=1,
        ))
    revision_path = f"/api/v1/chat/video-events/{message_id}/failed-scene"
    revision = {
        "scene_index": 2,
        "video_prompt": "The same host closes the book and smiles at the camera in the same room.",
        "voice_script": "Thanks for watching this book review and sharing a little time with me today to explore what feels useful for you.",
    }
    assert client.put(revision_path, json=revision | {"scene_index": 1}).status_code == 409
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == generation_ids[1])
                   .values(result={"usage": {"credits": 1.0}}))
    assert client.put(revision_path, json=revision).status_code == 409
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == generation_ids[1])
                   .values(result={"usage": {"credits": 0.0},
                                   "error_detail": "The content does not comply with the platform regulations."}))
    assert client.put(revision_path, json=revision | {"voice_script": "*** 😊 !!!"}).status_code == 422
    updated = client.put(
        revision_path,
        json=revision | {"voice_script": "**Thanks** for watching this book review and sharing a little time with me today to explore what feels useful for you 😊."},
    )
    assert updated.status_code == 200
    revised_event = json.loads(updated.json()["content"])
    assert revised_event["plan"]["story_plan"]["clips"][0] == story["clips"][0]
    assert revised_event["plan"]["story_plan"]["clips"][1]["voice_script"] == revision["voice_script"]
    blocked = client.post(f"/api/v1/video-projects/{plan['project_id']}/renders", json={"confirm_paid": True})
    assert blocked.status_code == 409
    assert "cảnh 3" in blocked.json()["detail"]
    pending_revision = revision | {
        "scene_index": 3,
        "voice_script": "I keep this same book with me on the ride.",
    }
    revised_pending = client.put(revision_path, json=pending_revision)
    assert revised_pending.status_code == 200
    assert json.loads(revised_pending.json()["content"])["plan"]["story_plan"]["clips"][2]["voice_script"] == pending_revision["voice_script"]
    assert "unresolved_voice_placeholder" not in json.loads(revised_pending.json()["content"])["plan"]["story_plan"]["clips"][2]
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image/regenerate").status_code == 409
    with platform.engine.connect() as db:
        revised_payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        assert revised_payload["story_plan"]["clips"][1]["video_prompt"] == revised_event["plan"]["story_plan"]["clips"][1]["video_prompt"]
        assert db.execute(select(story_batches.c.generation_ids).where(story_batches.c.id == batch_id)).scalar_one() == generation_ids
    retry = client.post(f"/api/v1/video-projects/{plan['project_id']}/renders", json={"confirm_paid": True})
    assert retry.status_code == 202
    with platform.engine.connect() as db:
        assert db.execute(select(story_batches.c.generation_ids).where(story_batches.c.id == batch_id)).scalar_one() == generation_ids[:1]
    monkeypatch.delenv("POYO_CONTINUITY_MODE", raising=False)
    persist_completed_clip(platform, generation_ids[0], owner_id)
    replacement = platform.generate(
        owner_id, plan["id"], True, clip_index=2,
        continuity_generation_id=generation_ids[0], native_audio=True,
    )
    assert replacement["id"] != generation_ids[1]
    generated = platform.store.get(replacement["id"])["payload"]
    generated_goal = generated["spec"]["intent"]["goal"]
    assert revision["video_prompt"] in generated_goal
    with platform.engine.connect() as db:
        unchanged_plan = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()["story_plan"]
    assert unchanged_plan["clips"][1]["voice_script"] in generated_goal
    assert unchanged_plan["clips"][1]["voice_script"] == revision["voice_script"]
    assert unchanged_plan["clips"][0] == story["clips"][0]
    assert generated["spec"]["references"] == [
        {"asset_id": generation_ids[0], "role": "FIRST_FRAME", "strength": "high"}
    ]


def test_reference_image_grounds_book_story_and_persists_chat_plan(client, monkeypatch):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "KOC nữ giới thiệu một cuốn sách"}).json()
    story = {
        "book_title": "",
        "core_value": "Giới thiệu một cuốn sách được cầm trên tay",
        "reference_image_prompt": "A woman holds a visible book while speaking to the camera.",
        "clips": [
            {"index": 1, "beat": "mở đầu", "video_prompt": "The host holds a book in a quiet room.",
             "voice_script": "Cuốn sách [Tên sách] sẽ được giới thiệu hôm nay."},
            {"index": 2, "beat": "tiếp nối", "video_prompt": "The host looks at the same book in the same room.",
             "voice_script": "Đây là [tóm tắt nội dung sách] của cuốn sách."},
        ],
    }
    plan["story_plan"] = story
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        version = db.execute(select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])).mappings().one()
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = story
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"]).values(payload=version_payload))
    saved = client.post("/api/v1/chat/video-events", json={"prompt": "KOC nữ giới thiệu một cuốn sách", "plan": plan})
    message_id = saved.json()["messages"][1]["id"]
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/approve").status_code == 409
    anchor = platform.storage / "continuity" / f"anchor-{plan['id']}.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(anchor, format="JPEG")
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"grounding": {"book_title": "Những điều nhỏ bé tạo nên hạnh phúc", "clips": [
                {"index": 1, "video_prompt": "The same host holds the same book in the same room.",
                 "voice_script": "**Hôm nay** mình mang theo cuốn “Những điều nhỏ bé tạo nên hạnh phúc” để chia sẻ cùng mọi người 😊."},
                {"index": 2, "video_prompt": "The same host gently opens the same book in the same room.",
                 "voice_script": "Mình thích khoảnh khắc mở sách trên chuyến đi này và dành chút thời gian đọc thật chậm."},
            ]}}

    class InternalClient:
        def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    prepared = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image")
    assert prepared.status_code == 200
    assert prepared.json()["draft_id"] == plan["id"]
    assert prepared.json()["story_plan"]["book_title"] == "Những điều nhỏ bé tạo nên hạnh phúc"
    assert calls[0][0] == "/internal/v1/planner/ground-reference"
    assert calls[0][1]["json"]["image_data_url"].startswith("data:image/jpeg;base64,")
    with platform.engine.connect() as db:
        grounded = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()["story_plan"]
        version_plan = db.execute(select(story_plan_versions.c.payload).where(story_plan_versions.c.draft_id == plan["id"])).scalar_one()["story_plan"]
        assert grounded == version_plan
        assert grounded["clips"][0]["voice_script"] == "Hôm nay mình mang theo cuốn Những điều nhỏ bé tạo nên hạnh phúc để chia sẻ cùng mọi người."
        assert grounded["full_voice_script"] == " ".join(clip["voice_script"] for clip in grounded["clips"])
    history = client.get(f"/api/v1/chat/conversations/{saved.json()['conversation_id']}/messages").json()
    assert json.loads(next(row["content"] for row in history if row["id"] == message_id))["plan"]["story_plan"] == grounded
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image").status_code == 200
    assert len(calls) == 1
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/approve").status_code == 200

    def failed_grounding(*_args):
        raise HTTPException(502, "Không thể đọc lại ảnh")

    monkeypatch.setattr(platform, "_ground_reference_story", failed_grounding)
    warning = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image")
    assert warning.status_code == 200
    assert warning.json()["grounding_error"] == "Không thể đọc lại ảnh"
    assert anchor.is_file()


def test_literal_book_title_is_grounded_before_first_clip(client):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "KOC bán sách trong ô tô"}).json()
    story = {
        "book_title": "",
        "core_value": "Giới thiệu một cuốn sách trong ô tô",
        "reference_image_prompt": "A presenter holds a book inside a parked car.",
        "clips": [{
            "index": 1,
            "beat": "giới thiệu",
            "video_prompt": "The presenter holds the same book inside the parked car.",
            "voice_script": 'Đây là cuốn "Tên sách" mà mình muốn chia sẻ hôm nay.',
        }],
    }
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        version = db.execute(select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])).mappings().one()
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = story
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"]).values(payload=version_payload))
        db.execute(insert(story_batches).values(
            id="pending-book-batch", user_id=draft["user_id"], title=draft["title"],
            draft_id=plan["id"], ratio="16:9", generation_ids=[], status="GENERATING",
            audio_mode="native", created=1,
        ))
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/approve").status_code == 409
    anchor = platform.storage / "continuity" / f"anchor-{plan['id']}.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(anchor, format="JPEG")
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"grounding": {"book_title": "Sống chậm lại để hạnh phúc", "clips": [{
                "index": 1,
                "video_prompt": "The same presenter holds the same book inside the parked car.",
                "voice_script": "Hôm nay mình chia sẻ cuốn Sống chậm lại để hạnh phúc với các bạn.",
            }]}}

    class InternalClient:
        def post(self, path, **kwargs):
            calls.append(path)
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    result = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image")
    assert result.status_code == 200
    assert calls == ["/internal/v1/planner/ground-reference"]
    assert result.json()["story_plan"]["book_title"] == "Sống chậm lại để hạnh phúc"
    with platform.engine.connect() as db:
        saved = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
    assert "Tên sách" not in saved["story_plan"]["clips"][0]["voice_script"]
    assert client.post(f"/api/v1/video-projects/{plan['project_id']}/approve").status_code == 200


def test_native_speech_quality_detects_missing_and_repeated_words():
    line = "Hôm nay mình chia sẻ cuốn sách này với mọi người."
    assert _native_speech_issues(line, line)[0] == []
    assert "native_speech_unintelligible" in _native_speech_issues(line, "")[0]
    assert "native_speech_repetition" in _native_speech_issues(
        line, "Hôm nay mình chia sẻ, hôm nay mình chia sẻ cuốn sách này với mọi người."
    )[0]


def test_native_audio_gap_quality_reports_long_intro_and_tail():
    ffmpeg_log = (
        "[silencedetect] silence_start: 0\n"
        "[silencedetect] silence_end: 2.907 | silence_duration: 2.907\n"
        "[silencedetect] silence_start: 6.84\n"
        "[silencedetect] silence_end: 8 | silence_duration: 1.16\n"
    )
    issues, metrics = _native_audio_gap_issues(ffmpeg_log, 8.0)
    assert issues == ["native_audio_leading_silence", "native_audio_trailing_silence"]
    assert metrics == {"leading_silence_seconds": 2.907, "trailing_silence_seconds": 1.16}


def test_native_speech_review_warns_when_audio_has_long_gaps(client, monkeypatch):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "A presenter talks about a book"}).json()
    owner_id = client.get("/api/v1/me").json()["id"]
    line = "Hom nay minh chia se cuon sach nay voi moi nguoi."
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [{"index": 1, "voice_script": "**Hom nay** minh chia se cuon sach nay voi moi nguoi 😊."}]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    platform.planner_configured = True

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": line}

    class InternalClient:
        def post(self, path, **kwargs):
            assert path == "/internal/v1/planner/transcribe-clip"
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    monkeypatch.setattr(platform, "_native_audio_timing", lambda _path: (
        ["native_audio_trailing_silence"], {"trailing_silence_seconds": 1.5}
    ))
    path = platform.storage / "videos" / "gap-scene.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test clip")
    draft = platform.owned(drafts, plan["id"], owner_id)
    assert platform._review_native_scene_audio(draft, 1, "gap-scene", path)
    with platform.engine.connect() as db:
        report = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"]
        )).mappings().one()
    assert report["status"] == "WARN"
    assert report["issues"] == ["native_audio_trailing_silence"]
    assert report["metrics"]["trailing_silence_seconds"] == 1.5
    assert report["metrics"]["expected"] == line


def test_reference_drift_stops_compounding_change_even_when_boundary_matches(client):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A presenter holds one book"}).json()
    directory = client.platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    anchor = directory / "anchor-test.jpg"
    previous_end = directory / "previous-test.jpg"
    current_end = directory / "current-test.jpg"
    Image.new("RGB", (64, 64), (40, 40, 40)).save(anchor)
    Image.new("RGB", (64, 64), (60, 60, 60)).save(previous_end)
    Image.new("RGB", (64, 64), (120, 120, 120)).save(current_end)

    platform = client.platform
    assert not platform._review_scene_reference(plan["id"], 2, anchor, previous_end, current_end)
    platform._record_scene_boundary_quality(plan["id"], 3, current_end, current_end, anchor)
    with platform.engine.connect() as db:
        reports = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"]
        )).mappings().all()
    reference = next(report for report in reports if report["stage"] == "SCENE_REFERENCE")
    boundary = next(report for report in reports if report["stage"] == "SCENE_BOUNDARY")
    assert reference["status"] == "FAIL"
    assert reference["issues"] == ["visual_reference_drift"]
    assert boundary["issues"] == ["large_anchor_visual_change"]
    assert boundary["metrics"]["previous_end_to_current_start_mean_pixel_delta"] == 0
    Image.new("RGB", (64, 64), (70, 70, 70)).save(current_end)
    assert platform._review_scene_reference(plan["id"], 2, anchor, previous_end, current_end)
    with platform.engine.connect() as db:
        states = db.execute(select(video_quality_reports.c.status).where(
            video_quality_reports.c.project_id == plan["project_id"],
            video_quality_reports.c.stage == "SCENE_REFERENCE",
        )).scalars().all()
    assert sorted(states) == ["PASS", "RESOLVED"]


@pytest.mark.parametrize("quality_error", ["native_speech_quality_failed", "visual_reference_drift", "native_audio_missing"])
def test_retry_completed_bad_scene_creates_new_paid_job(client, monkeypatch, quality_error):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A presenter holds one book"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    owner_id = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = {"clips": [
            {"index": 1, "video_prompt": "The presenter holds the book in one stable shot.",
             "voice_script": NATIVE_FIRST},
            {"index": 2, "video_prompt": "The same presenter keeps holding the same book.",
             "voice_script": NATIVE_NEXT},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        db.execute(insert(video_scenes).values(
            id=f"retry-scene-{quality_error}", project_id=plan["project_id"], plan_version=1,
            scene_index=2, draft_id=plan["id"], payload=payload["story_plan"]["clips"][1],
            status="PLANNED", created=1, updated=1,
        ))
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "white").save(directory / f"anchor-{plan['id']}.jpg")
    first = platform.generate(owner_id, plan["id"], confirm_paid=True, clip_index=1, native_audio=True)
    persist_completed_clip(platform, first["id"], owner_id)
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == first["id"]).values(status="COMPLETED"))
    platform._record_scene_continuity(platform.owned(drafts, plan["id"], owner_id), 1, continuity_status="PASSED")
    Image.new("RGB", (64, 64), "white").save(directory / f"{first['id']}.jpg")
    second = platform.generate(
        owner_id, plan["id"], confirm_paid=True, clip_index=2,
        continuity_generation_id=first["id"], native_audio=True,
    )
    batch_id = hashlib.sha256(f"{plan['id']}:complete-story".encode()).hexdigest()[:32]
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id.in_([first["id"], second["id"]]))
                   .values(status="COMPLETED"))
        db.execute(insert(story_batches).values(
            id=batch_id, user_id=owner_id, title=draft["title"], draft_id=plan["id"],
            ratio="16:9", generation_ids=[first["id"], second["id"]], status="FAILED",
            error_code=quality_error, audio_mode="native", created=1,
        ))
    failed = client.get(f"/api/v1/generations/{batch_id}").json()
    assert failed["retryable"] is True
    assert failed["clip_progress"] == {"completed": 1, "total": 2}
    retried = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert retried.status_code == 202
    with platform.engine.connect() as db:
        remaining = db.execute(select(story_batches.c.generation_ids).where(
            story_batches.c.id == batch_id
        )).scalar_one()
    assert remaining == [first["id"]]
    replacement = platform.generate(
        owner_id, plan["id"], confirm_paid=True, clip_index=2,
        continuity_generation_id=first["id"], native_audio=True,
    )
    assert replacement["id"] != second["id"]
    assert replacement["draft_id"] == second["draft_id"]
    with platform.engine.connect() as db:
        metadata = db.execute(select(video_scenes.c.continuity_metadata).where(
            video_scenes.c.draft_id == plan["id"], video_scenes.c.scene_index == 2,
        )).scalar_one()
    assert metadata["parent_generation_id"] == first["id"]
    assert metadata["retry_count"] == 1
    assert metadata["continuation_generation_id"] is None


def test_native_speech_review_records_failure_and_paid_scene_retry(client, monkeypatch):
    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "_validate_story_video", lambda _path: True)
    plan = client.post("/api/v1/plans", json={"prompt": "Một người giới thiệu sách trong xe"}).json()
    owner_id = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = {"clips": [
            {"index": 1, "beat": "mở đầu", "video_prompt": "The same presenter calmly holds the same book.",
             "voice_script": "Hôm nay mình chia sẻ cuốn sách này với mọi người."},
            {"index": 2, "beat": "tiếp nối", "video_prompt": "The same presenter gently opens the same book.",
             "voice_script": "Mình thích dành thời gian đọc cuốn sách này mỗi ngày."},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    platform.planner_configured = True
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "Hôm nay mình chia sẻ hôm nay mình chia sẻ cuốn sách này với mọi người."}

    class InternalClient:
        def post(self, path, **kwargs):
            assert path == "/internal/v1/planner/transcribe-clip"
            assert kwargs["headers"]["Content-Type"] == "video/mp4"
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    video_path = platform.storage / "videos" / "scene.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video bytes")
    revised_draft = platform.owned(drafts, plan["id"], owner_id)
    assert not platform._review_native_scene_audio(revised_draft, 1, "bad-scene", video_path)
    assert not platform._review_native_scene_audio(revised_draft, 1, "bad-scene", video_path)
    platform.planner_configured = False
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": False})
    assert platform._review_native_scene_audio(revised_draft, 1, "no-key", video_path)
    with platform.engine.connect() as db:
        report = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"],
            video_quality_reports.c.scene_index == 1,
        ).order_by(video_quality_reports.c.created.desc()).limit(1)).mappings().one()
    assert report["status"] == "WARN"
    assert report["issues"] == ["native_speech_check_unavailable"]
    platform.planner_configured = True
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    good = platform.store.create("poyo", payload)
    bad_payload = copy.deepcopy(payload)
    bad_payload["spec"]["request"]["request_id"] = "bad-audio"
    bad = platform.store.create("poyo", bad_payload)
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id.in_([good["generation_id"], bad["generation_id"]]))
                   .values(status="COMPLETED"))
        batch_id = hashlib.sha256(f"{plan['id']}:complete-story".encode()).hexdigest()[:32]
        db.execute(insert(videos).values(
            generation_id=bad["generation_id"], user_id=owner_id,
            filename=video_path.name, size_bytes=video_path.stat().st_size, created=1,
        ))
        db.execute(insert(story_batches).values(
            id=batch_id, user_id=owner_id, title=draft["title"], draft_id=plan["id"], ratio="16:9",
            generation_ids=[good["generation_id"], bad["generation_id"]], status="GENERATING",
            audio_mode="native", created=1,
        ))
    platform.process_story_batches()
    failed = client.get(f"/api/v1/generations/{batch_id}").json()
    assert failed["status"] == "FAILED"
    assert failed["error"].startswith("native_speech_quality_failed")
    assert failed["retryable"] is True
    assert failed["clip_progress"] == {"completed": 1, "total": 2}
    assert client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": False}).status_code == 409
    retried = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert retried.status_code == 202
    assert retried.json()["status"] == "GENERATING"
    with platform.engine.connect() as db:
        ids = db.execute(select(story_batches.c.generation_ids).where(story_batches.c.id == batch_id)).scalar_one()
    assert ids == [good["generation_id"]]


def test_speech_failed_by_an_older_checker_is_kept_on_retry_without_new_transcription(client, monkeypatch):
    from app.services.spoken_text import clean_spoken_text

    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "Một người giới thiệu bộ đồ cho bé"}).json()
    owner_id = client.get("/api/v1/me").json()["id"]
    voice = "Chất vải gồm chín mươi lăm phần trăm cotton, dành cho bé từ hai đến sáu tuổi, mẹ tham khảo nha."
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = {"clips": [
            {"index": 1, "beat": "mở đầu", "video_prompt": "The same presenter holds the outfit by its hanger.",
             "voice_script": "Bộ này mặc Tết là xinh nhất luôn các mẹ ơi."},
            {"index": 2, "beat": "chốt", "video_prompt": "The same presenter smiles at the camera.",
             "voice_script": voice},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))

    class NoTranscription:
        def post(self, path, **kwargs):
            raise AssertionError("the stored transcript must be re-scored, not transcribed again")

        def close(self):
            return None

    platform.http = NoTranscription()
    platform.planner_configured = True
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    first = platform.store.create("poyo", payload)
    second_payload = copy.deepcopy(payload)
    second_payload["spec"]["request"]["request_id"] = "scene-two"
    second = platform.store.create("poyo", second_payload)
    video_path = platform.storage / "videos" / "scene-two.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video bytes")
    batch_id = hashlib.sha256(f"{plan['id']}:complete-story".encode()).hexdigest()[:32]
    ids = [first["generation_id"], second["generation_id"]]
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id.in_(ids)).values(status="COMPLETED"))
        db.execute(insert(videos).values(generation_id=ids[1], user_id=owner_id, filename=video_path.name,
                                         size_bytes=video_path.stat().st_size, created=1))
        db.execute(insert(video_quality_reports).values(
            id="old-speech-check", project_id=plan["project_id"], scene_index=2, stage="NATIVE_SPEECH",
            status="FAIL", issues=["native_speech_incomplete"], created=1,
            metrics={"generation_id": ids[1], "review_profile": "continuity-v3",
                     "expected": clean_spoken_text(voice),
                     "transcript": "chất vải gồm 95% cotton, dành cho bé từ 2 đến 6 tuổi, mẹ tham khảo nha."},
        ))
        db.execute(insert(story_batches).values(
            id=batch_id, user_id=owner_id, title=draft["title"], draft_id=plan["id"], ratio="16:9",
            generation_ids=ids, status="FAILED", error_code="native_speech_quality_failed: Cảnh 2",
            audio_mode="native", created=1,
        ))
    retried = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert retried.status_code == 202
    with platform.engine.connect() as db:
        batch = db.execute(select(story_batches).where(story_batches.c.id == batch_id)).mappings().one()
        latest = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"], video_quality_reports.c.scene_index == 2,
            video_quality_reports.c.stage == "NATIVE_SPEECH",
        ).order_by(video_quality_reports.c.created.desc()).limit(1)).mappings().one()
    assert batch["status"] == "GENERATING" and batch["error_code"] is None
    assert batch["generation_ids"] == ids
    assert latest["status"] != "FAIL" and latest["metrics"]["similarity"] > 0.9


def test_reference_grounding_keeps_completed_scene_in_failed_batch(client):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "Giới thiệu sách trên ô tô"}).json()
    story = {
        "core_value": "Giới thiệu sách trên ô tô",
        "clips": [
            {"index": 1, "beat": "đầu", "video_prompt": "The host holds one book inside the car.",
             "voice_script": "**Mình** đang đọc cuốn sách này trên ô tô 😊."},
            {"index": 2, "beat": "cuối", "video_prompt": "The same host still holds one book inside the car.",
             "voice_script": "Cuốn Tên sách có tóm tắt nội dung.", "unresolved_voice_placeholder": True},
        ],
    }
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        version = db.execute(select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])).mappings().one()
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = story
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"]).values(payload=version_payload, status="APPROVED"))
        db.execute(insert(story_batches).values(
            id="failed-book-batch", user_id=draft["user_id"], title="Book", draft_id=plan["id"],
            ratio="16:9", generation_ids=["completed-1", "failed-2"], status="FAILED",
            audio_mode="native", error_code="poyo_generation_failed", created=1,
        ))
    platform.store.get = lambda gid: {"provider": "poyo", "status": "FAILED", "error_code": "poyo_generation_failed", "result": {"usage": {"credits": 0}}}
    anchor = platform.storage / "continuity" / f"anchor-{plan['id']}.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(anchor, format="JPEG")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"grounding": {"book_title": "Những điều nhỏ bé tạo nên hạnh phúc", "clips": [
                {"index": 1, "video_prompt": "A changed prompt for scene one, which must not be saved.",
                 "voice_script": "Một câu thoại mới cho cảnh đã hoàn thành."},
                {"index": 2, "video_prompt": "The same host continues holding the book inside the same car.",
                 "voice_script": "Cuốn “Những điều nhỏ bé tạo nên hạnh phúc” ở trên tay mình trong chuyến xe hôm nay 😊."},
            ]}}

    class InternalClient:
        def post(self, path, **kwargs):
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    result = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image")
    assert result.status_code == 200
    assert result.json()["story_plan"]["clips"][0] == story["clips"][0]
    assert result.json()["story_plan"]["clips"][1]["voice_script"] == "Cuốn Những điều nhỏ bé tạo nên hạnh phúc ở trên tay mình trong chuyến xe hôm nay."
    assert "unresolved_voice_placeholder" not in result.json()["story_plan"]["clips"][1]


def test_reference_url_changes_with_content_even_when_filesystem_time_does_not(client):
    import os

    from PIL import Image

    platform = client.platform
    anchor = platform.storage / "continuity" / "anchor-story.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), "red").save(anchor, format="JPEG")
    timestamp = anchor.stat().st_mtime_ns
    original = platform._reference_image_url("project", "story")
    assert platform._reference_image_url("project", "story") == original
    Image.new("RGB", (32, 32), "blue").save(anchor, format="JPEG")
    os.utime(anchor, ns=(timestamp, timestamp))
    assert anchor.stat().st_mtime_ns == timestamp
    assert platform._reference_image_url("project", "story") != original
    assert platform._reference_image_url("project", "missing") is None


def test_rejected_first_scene_can_replace_generated_reference_without_retrying_poyo(client):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "KOC giới thiệu sách trong ô tô"}).json()
    story = {
        "book_title": "",
        "core_value": "Giới thiệu một cuốn sách trong ô tô",
        "reference_image_prompt": "An adult Vietnamese presenter holds one book in a parked car.",
        "clips": [{
            "index": 1,
            "beat": "giới thiệu",
            "video_prompt": "The presenter holds a book inside a parked car.",
            "voice_script": "Hôm nay mình giới thiệu một cuốn sách.",
        }],
    }
    with platform.engine.begin() as db:
        draft = db.execute(select(drafts).where(drafts.c.id == plan["id"])).mappings().one()
        payload = copy.deepcopy(draft["payload"])
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        version = db.execute(select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])).mappings().one()
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = story
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"])
                   .values(payload=version_payload, status="APPROVED"))
    owner_id = client.get("/api/v1/me").json()["id"]
    job_payload = copy.deepcopy(payload)
    job_payload.pop("story_plan")
    job = platform.store.create("poyo", job_payload)
    batch_id = hashlib.sha256(f"{plan['id']}:complete-story".encode()).hexdigest()[:32]
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == job["generation_id"])
                   .values(status="FAILED", error_code="poyo_generation_failed",
                           result={"usage": {"credits": 0.0},
                                   "error_detail": "The content does not comply with the platform regulations."}))
        db.execute(insert(story_batches).values(
            id=batch_id, user_id=owner_id, title="Book", draft_id=plan["id"],
            ratio="16:9", generation_ids=[job["generation_id"]], status="FAILED",
            audio_mode="native", error_code="poyo_generation_failed", created=1,
        ))
    anchor = platform.storage / "continuity" / f"anchor-{plan['id']}.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "red").save(anchor, format="JPEG")
    old_url = client.get(f"/api/v1/video-projects/{plan['project_id']}").json()["reference_image_url"]
    old_crops = [anchor.parent / f"{kind}-{plan['id']}.jpg" for kind in ("character", "style")]
    for crop in old_crops:
        crop.write_bytes(anchor.read_bytes())
    replacement = io.BytesIO()
    Image.new("RGB", (640, 360), "blue").save(replacement, format="JPEG")
    calls = []

    class Response:
        def __init__(self, path):
            self.content = replacement.getvalue() if path.endswith("scene-image") else b""
            self.path = path

        def raise_for_status(self):
            return None

        def json(self):
            return {"grounding": {"book_title": "Một ngày thong dong", "clips": [{
                "index": 1,
                "video_prompt": "The same adult presenter holds the same fictional book in a parked car.",
                "voice_script": "Hôm nay mình chia sẻ cuốn Một ngày thong dong khi đang ngồi trong chiếc xe đã đỗ.",
            }]}}

    class InternalClient:
        def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return Response(path)

        def close(self):
            return None

    platform.http = InternalClient()
    result = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image/regenerate")
    assert result.status_code == 200
    assert result.json()["draft_id"] == plan["id"]
    assert result.json()["reference_image_url"] != old_url
    assert all(not crop.exists() for crop in old_crops)
    assert result.json()["story_plan"]["book_title"] == "Một ngày thong dong"
    with Image.open(anchor) as image:
        assert image.convert("RGB").getpixel((320, 180))[2] > 200
    assert [path for path, _ in calls] == [
        "/internal/v1/planner/scene-image", "/internal/v1/planner/ground-reference",
    ]
    assert "safely parked" in calls[0][1]["json"]["prompt"]
    assert "fictional" in calls[0][1]["json"]["prompt"]
    assert platform.store.get(job["generation_id"])["status"] == "FAILED"
    assert platform.story_batch(owner_id, batch_id)["status"] == "FAILED"
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == job["generation_id"])
                   .values(result={"usage": {"credits": 36.0}}))
    blocked = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image/regenerate")
    assert blocked.status_code == 409
    with Image.open(anchor) as image:
        assert image.convert("RGB").getpixel((320, 180))[2] > 200
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == job["generation_id"])
                   .values(result={"usage": {"credits": 0.0}}))
    replacement = io.BytesIO(b"invalid-image")
    failed_replacement = client.post(f"/api/v1/video-projects/{plan['project_id']}/reference-image/regenerate")
    assert failed_replacement.status_code == 502
    with Image.open(anchor) as image:
        assert image.convert("RGB").getpixel((320, 180))[2] > 200


def test_upload_validation_and_ownership(client):
    register(client)
    assert (
        client.post(
            "/api/v1/assets", files={"file": ("x.png", b"not-image", "image/png")}
        ).status_code
        == 422
    )
    image = io.BytesIO()
    Image.new("RGB", (64, 48)).save(image, format="PNG")
    response = client.post(
        "/api/v1/assets", files={"file": ("test.png", image.getvalue(), "image/png")}
    )
    assert response.status_code == 201
    url = response.json()["url"]
    assert client.get(url).status_code == 200
    register(client, "two@example.com")
    assert client.get(url).status_code == 404


def test_plan_generate_idempotency_and_isolation(client):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A city at sunrise"}).json()
    assert draft["planner"] == "template"
    path = "/api/v1/plans/" + draft["id"] + "/generate"
    job = client.post(path).json()
    assert client.post(path).json()["id"] == job["id"]
    assert len(client.get("/api/v1/generations").json()) == 1
    client.platform.runtime.tick(job["id"])
    assert client.get("/api/v1/generations/" + job["id"] + "/video").status_code == 409
    register(client, "two@example.com")
    assert client.get("/api/v1/generations").json() == []
    assert client.post(path).status_code == 404
    assert client.post("/api/v1/generations/" + job["id"] + "/cancel").status_code == 404


def test_story_plan_generates_and_serves_one_merged_story(client, monkeypatch):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A thoughtful book podcast"}).json()
    with client.platform.engine.begin() as db:
        row = db.execute(select(drafts).where(drafts.c.id == draft["id"])).mappings().one()
        payload = row["payload"]
        payload["story_plan"] = {
            "book_title": "Test book",
            "core_value": "A story about finding confidence.",
            "visual_continuity": "The exact same host, clothes, studio, camera, book, props and lighting in every clip.",
            "clips": [
                {
                    "index": index,
                    "beat": f"beat {index}",
                    "video_prompt": f"A detailed visual prompt for scene number {index}.",
                    "voice_script": f"Line {index}.",
                }
                for index in range(1, 6)
            ],
        }
        payload["spec"]["output"]["duration_seconds"] = 8
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))

    response = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True})
    assert response.status_code == 202
    batch = response.json()
    assert batch["story_batch"] is True
    assert batch["clip_progress"] == {"completed": 0, "total": 5}
    with client.platform.engine.begin() as db:
        generation_ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
        child_payloads = [
            db.execute(
                select(drafts.c.payload).where(drafts.c.generation_id == generation_id)
            ).scalar_one()
            for generation_id in generation_ids
        ]
    for child_payload in child_payloads:
        goal = child_payload["spec"]["intent"]["goal"]
        assert "Continuity:" in goal
        assert "exact same host, clothes, studio" in goal
        assert child_payload["spec"]["constraints"]["preserve_identity"] is False

    owner_id = client.get("/api/v1/me").json()["id"]
    with client.platform.engine.begin() as db:
        generation_ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
        recovered_ids = {generation_ids[2], generation_ids[4]}
        db.execute(
            update(executions)
            .where(executions.c.generation_id.in_(generation_ids))
            .values(status="COMPLETED", provider="poyo")
        )
        db.execute(
            update(executions)
            .where(executions.c.generation_id.in_(recovered_ids))
            .values(status="NEEDS_USER_ACTION", error_code="output_persistence_failed")
        )
        db.execute(
            update(story_batches)
            .where(story_batches.c.id == batch["id"])
            .values(status="FAILED", error_code="clip_generation_failed")
        )
        for generation_id in recovered_ids:
            existing_clip = client.platform.storage / "videos" / (generation_id + ".mp4")
            existing_clip.parent.mkdir(parents=True, exist_ok=True)
            existing_clip.write_bytes(b"already persisted clip")
            db.execute(
                insert(videos).values(
                    generation_id=generation_id,
                    user_id=owner_id,
                    filename=existing_clip.name,
                    size_bytes=existing_clip.stat().st_size,
                    created=1,
                )
            )

    def merge(batch_id, clip_ids, ratio, audio_mode):
        assert audio_mode == "narration"
        target = client.platform.storage / "videos" / (batch_id + ".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"merged story video")
        return target.name, target.stat().st_size

    monkeypatch.setattr(client.platform, "_merge_story_videos", merge)
    client.platform.process_story_batches()
    result = client.get("/api/v1/generations").json()
    assert len(result) == 1
    assert result[0]["status"] == "COMPLETED"
    assert result[0]["duration"] == 40
    assert client.get(result[0]["video_url"]).content == b"merged story video"


def test_story_batch_uses_planner_selected_clip_count(client):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A short book podcast"}).json()
    with client.platform.engine.begin() as db:
        row = db.execute(select(drafts).where(drafts.c.id == draft["id"])).mappings().one()
        payload = row["payload"]
        payload["story_plan"] = {
            "book_title": "Short story",
            "core_value": "A concise lesson.",
            "visual_continuity": "Keep the host, studio, camera, lighting and props unchanged.",
            "clips": [
                {
                    "index": index,
                    "beat": f"beat {index}",
                    "video_prompt": f"A consistent, detailed scene for beat {index}.",
                    "voice_script": f"Line {index}.",
                }
                for index in range(1, 3)
            ],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))

    response = client.post(
        f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}
    )
    assert response.status_code == 202
    batch = response.json()
    assert batch["duration"] == 16
    assert batch["clip_progress"] == {"completed": 0, "total": 2}
    with client.platform.engine.connect() as db:
        ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
    assert len(ids) == 2


def test_prepare_story_media_uses_internal_openai_routes_without_live_calls(client):
    platform = client.platform
    calls = []
    platform.planner_credential_status = lambda: {"configured": False}
    platform._compact_story_audio = lambda path: None
    platform._media_duration = lambda path: 16.0
    platform._record_audio_quality = lambda *args: 16.0
    platform._story_audio_quality_issues = lambda draft: []

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    reference = io.BytesIO()
    Image.new("RGB", (32, 18), "gray").save(reference, format="JPEG")

    class InternalClient:
        def post(self, path, **kwargs):
            calls.append((path, kwargs))
            # Reference preparation now runs first and verifies a real image.
            return Response(reference.getvalue() if path.endswith("scene-image") else b"test-media")

        def close(self):
            return None

    platform.http = InternalClient()
    draft = {
        "id": "draft-test",
        "payload": {"spec": {"output": {"aspect_ratio": "16:9"}}},
    }
    clips = [
        {
            "index": 1,
            "video_prompt": "Fallback scene prompt.",
            "voice_script": "A natural complete narration line for the first scene.",
        },
        {
            "index": 2,
            "video_prompt": "Fallback scene prompt.",
            "voice_script": "A natural complete narration line for the second scene.",
        },
    ]
    platform._prepare_story_media("batch-test", draft, clips)
    assert [path for path, _ in calls] == [
        "/internal/v1/planner/scene-image",
        "/internal/v1/planner/voice",
    ]
    assert (platform.storage / "continuity" / "story-batch-test.mp3").read_bytes() == b"test-media"
    assert (platform.storage / "continuity" / "anchor-draft-test.jpg").is_file()
    assert calls[1][1]["json"]["voice_bible"]
    platform._prepare_story_media("batch-test", draft, clips)
    assert len(calls) == 2
    profile = "Vietnamese; same adult speaker; warm reflective delivery; pace 2.6-3.0 syllables/s."
    draft["payload"]["story_plan"] = {"voice_bible": profile}
    platform._prepare_story_media("batch-test", draft, clips)
    assert len(calls) == 3 and calls[-1][0] == "/internal/v1/planner/voice"
    # A numeric rate saved in an older plan is dropped; only the delivery description reaches TTS.
    assert calls[-1][1]["json"]["voice_bible"] == profile.replace("; pace 2.6-3.0 syllables/s", "")
    platform._prepare_story_media("batch-test", draft, clips)
    assert len(calls) == 3


def test_signed_continuity_frame_is_public_but_unforgeable(client):
    platform = client.platform
    platform.public_base_url = "https://video.example.test"
    frame = platform.storage / "continuity" / "generation-1.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"frame image bytes")
    url = platform.continuity_reference_url("frame", "generation-1")
    path = url.split("https://video.example.test", 1)[1]
    assert client.get(path).content == b"frame image bytes"
    assert client.head(path).status_code == 200
    forged = path.rsplit("/", 1)[0] + "/" + ("0" * 64)
    assert client.get(forged).status_code == 404


def test_signed_character_and_style_references_are_public(client):
    platform = client.platform
    platform.public_base_url = "https://video.example.test"
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    for kind in ("character", "style"):
        image_path = directory / f"{kind}-draft-1.jpg"
        image_path.write_bytes(f"{kind} image".encode())
        url = platform.continuity_reference_url(kind, "draft-1")
        path = url.split("https://video.example.test", 1)[1]
        assert client.get(path).content == image_path.read_bytes()


def test_continuity_uses_actual_first_and_last_decoded_frames(client):
    register(client)
    platform = client.platform
    owner_id = client.get("/api/v1/me").json()["id"]
    video_dir = platform.storage / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    clip = video_dir / "frame-edges.mp4"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=4:d=0.5",
            "-f", "lavfi", "-i", "color=c=white:s=640x360:r=4:d=0.5",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
            "-map", "[outv]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "4", str(clip),
        ],
        check=True, capture_output=True, timeout=30,
    )
    with platform.engine.begin() as db:
        db.execute(insert(videos).values(
            generation_id="edge-test", user_id=owner_id, filename=clip.name,
            size_bytes=clip.stat().st_size, created=1,
        ))
    first = platform._extract_video_frame("edge-test", "start")
    last = platform._extract_continuity_frame("edge-test")
    review_end = platform._extract_video_frame("edge-test", "end")
    with Image.open(first) as image:
        assert image.convert("RGB").getpixel((320, 180))[0] < 20
    for path in (last, review_end):
        with Image.open(path) as image:
            assert image.convert("RGB").getpixel((320, 180))[0] > 235


def test_poyo_story_batch_uses_previous_clip_frame_before_submitting_next(client, monkeypatch):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A connected five-scene story"}).json()
    with client.platform.engine.begin() as db:
        row = db.execute(select(drafts).where(drafts.c.id == draft["id"])).mappings().one()
        payload = row["payload"]
        payload["story_plan"] = {
            "book_title": "Test",
            "core_value": "A calm story about change.",
            "visual_continuity": "Keep the exact same set, host, outfit, camera, lighting and props in each scene.",
            "clips": [
                {
                    "index": index,
                    "beat": f"beat {index}",
                    "video_prompt": f"Same room and same host, scene {index}.",
                    "voice_script": NATIVE_FIRST if index < 5 else NATIVE_NEXT,
                }
                for index in range(1, 6)
            ],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))

    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast")
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda draft_id: None)
    monkeypatch.setattr(platform, "_story_media_ready", lambda batch_id, draft, audio_mode: True)
    monkeypatch.setattr(platform, "_validate_story_video", lambda path: True)
    continuity_dir = platform.storage / "continuity"
    continuity_dir.mkdir(parents=True, exist_ok=True)
    anchor_bytes = io.BytesIO()
    Image.new("RGB", (640, 360), "white").save(anchor_bytes, format="JPEG")
    (continuity_dir / f"anchor-{draft['id']}.jpg").write_bytes(anchor_bytes.getvalue())
    batch = client.post(
        f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}
    ).json()
    assert batch["clip_progress"] == {"completed": 0, "total": 5}
    platform.process_story_batches()
    owner_id = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        generation_ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
        db.execute(
            update(executions)
            .where(executions.c.generation_id == generation_ids[0])
            .values(status="COMPLETED")
        )
        clip_path = platform.storage / "videos" / f"{generation_ids[0]}.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_bytes(b"completed clip")
        db.execute(
            insert(videos).values(
                generation_id=generation_ids[0],
                user_id=owner_id,
                filename=clip_path.name,
                size_bytes=clip_path.stat().st_size,
                created=1,
            )
        )

    frame = platform.storage / "continuity" / f"{generation_ids[0]}.jpg"

    def extract(_generation_id):
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), "white").save(frame, format="JPEG")
        return frame

    monkeypatch.setattr(platform, "_extract_continuity_frame", extract)
    monkeypatch.setattr(platform, "_extract_video_frame", lambda *_args: extract(None))
    errors = []
    original_generate = platform.generate

    def capture_generate(*args, **kwargs):
        try:
            return original_generate(*args, **kwargs)
        except Exception as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(platform, "generate", capture_generate)
    platform.process_story_batches()
    assert not errors, repr(errors)
    with platform.engine.connect() as db:
        next_ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
        batch_state = db.execute(
            select(story_batches.c.status, story_batches.c.error_code).where(story_batches.c.id == batch["id"])
        ).one()
    assert len(next_ids) == 2, f"batch={batch_state}, ids={next_ids}"
    with platform.engine.connect() as db:
        next_payload = db.execute(
            select(drafts.c.payload).where(drafts.c.generation_id == next_ids[1])
        ).scalar_one()
    assert batch["audio_mode"] == "native"
    assert next_payload["spec"]["references"] == [
        {"asset_id": f"character-{draft['id']}", "role": "CHARACTER_REFERENCE", "strength": "high"},
        {"asset_id": f"style-{draft['id']}", "role": "STYLE_REFERENCE", "strength": "high"},
        {"asset_id": generation_ids[0], "role": "FIRST_FRAME", "strength": "high"},
    ]
    assert len(next_payload["assets"]) == 3
    assert all(asset["source_url"].startswith("https://video.example.test/") for asset in next_payload["assets"])
    with platform.engine.connect() as db:
        unchanged = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()["story_plan"]
    assert json.dumps(unchanged["clips"][1]["voice_script"], ensure_ascii=False) in next_payload["spec"]["intent"]["goal"]
    assert unchanged["clips"][1]["voice_script"] == NATIVE_FIRST
    assert 21 <= len(unchanged["clips"][1]["voice_script"].split()) <= 22
    assert "Do not generate audio" not in next_payload["spec"]["intent"]["goal"]


def test_visual_drift_stops_before_submitting_next_paid_clip(client, monkeypatch):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A host holds a book"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda _id: None)
    monkeypatch.setattr(platform, "_story_media_ready", lambda *_args: True)
    monkeypatch.setattr(platform, "_validate_story_video", lambda _path: True)
    monkeypatch.setattr(platform, "_review_native_scene_audio", lambda *_args: True)
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [
            {"index": 1, "video_prompt": "The host holds the same book in the same room.",
             "voice_script": NATIVE_FIRST},
            {"index": 2, "video_prompt": "The host continues with the same book and room.",
             "voice_script": NATIVE_NEXT},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    anchor = directory / f"anchor-{plan['id']}.jpg"
    Image.new("RGB", (64, 64), (40, 40, 40)).save(anchor)
    batch = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True}).json()
    platform.process_story_batches()
    with platform.engine.connect() as db:
        generation_ids = db.execute(select(story_batches.c.generation_ids).where(
            story_batches.c.id == batch["id"]
        )).scalar_one()
    first_id = generation_ids[0]
    owner_id = client.get("/api/v1/me").json()["id"]
    video = platform.storage / "videos" / "first-scene.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"completed clip")
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == first_id)
                   .values(status="COMPLETED"))
        db.execute(insert(videos).values(
            generation_id=first_id, user_id=owner_id, filename=video.name,
            size_bytes=video.stat().st_size, created=1,
        ))
    monkeypatch.setattr(platform, "_extract_video_frame", lambda *_args: anchor)

    def extract_end(_id):
        end = directory / f"{first_id}.jpg"
        Image.new("RGB", (64, 64), (120, 120, 120)).save(end)
        return end

    monkeypatch.setattr(platform, "_extract_continuity_frame", extract_end)
    platform.process_story_batches()
    failed = client.get(f"/api/v1/generations/{batch['id']}").json()
    assert failed["status"] == "FAILED"
    assert failed["error"] == "visual_reference_drift"
    assert failed["clip_progress"] == {"completed": 0, "total": 2}
    assert failed["retryable"] is True
    with platform.engine.connect() as db:
        assert db.execute(select(story_batches.c.generation_ids).where(
            story_batches.c.id == batch["id"]
        )).scalar_one() == [first_id]


@pytest.mark.parametrize("provider", ["poyo", "seedance", "seedvis"])
def test_native_story_prepares_direct_draft_reference_without_tts(client, monkeypatch, provider):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A host explains a recipe"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, provider)
    monkeypatch.setenv("SEEDANCE_MODEL", "seedance-1-5-pro-251215")
    monkeypatch.setenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda draft_id: None)
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()
        payload["story_plan"] = {
            "reference_image_prompt": "A presenter in the same kitchen",
            "clips": [
                {"index": 1, "video_prompt": "Host introduces the meal.",
                 "voice_script": "Hôm nay mình muốn cùng các bạn tìm hiểu một món ăn và cách chuẩn bị những nguyên liệu cần thiết."},
                {"index": 2, "video_prompt": "Host shows the ingredients.",
                 "voice_script": "Mình sẽ giới thiệu từng nguyên liệu để các bạn có thể quan sát và cùng chuẩn bị trước khi thực hiện."},
            ],
        }
        payload["reference_grounding_sha256"] = "a" * 64
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))

    image = io.BytesIO()
    Image.new("RGB", (64, 48)).save(image, format="JPEG")
    calls = []

    class Response:
        content = image.getvalue()

        def raise_for_status(self):
            return None

    class InternalClient:
        def post(self, path, **kwargs):
            calls.append(path)
            return Response()

        def close(self):
            return None

    platform.http = InternalClient()
    batch = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}).json()
    platform.process_story_batches()
    current = client.get(f"/api/v1/generations/{batch['id']}").json()
    assert current["audio_mode"] == "native"
    assert current["status"] == "GENERATING"
    assert calls == ["/internal/v1/planner/scene-image"]
    assert (platform.storage / "continuity" / f"anchor-{draft['id']}.jpg").is_file()
    assert not (platform.storage / "continuity" / f"story-{batch['id']}.mp3").exists()
    with platform.engine.connect() as db:
        generation_ids = db.execute(
            select(story_batches.c.generation_ids).where(story_batches.c.id == batch["id"])
        ).scalar_one()
        clip = db.execute(select(drafts.c.payload).where(drafts.c.generation_id == generation_ids[0])).scalar_one()
    assert "reference_grounding_sha256" not in clip
    assert clip["spec"]["story"]["shots"] == []
    assert "stop when the line is done" in clip["spec"]["intent"]["goal"]
    assert "within 0.2 seconds" not in clip["spec"]["intent"]["goal"]
    assert "syllables/s" not in clip["spec"]["intent"]["goal"]
    assert "takes priority over" not in clip["spec"]["intent"]["goal"]
    assert "without unrequested swaying, prop changes or abrupt pose resets" in clip["spec"]["intent"]["goal"]
    if provider in {"seedance", "seedvis"}:
        assert clip["spec"]["output"]["generate_audio"] is True


def test_continuation_reference_uses_retained_native_video_window(client, monkeypatch):
    platform = client.platform
    directory = platform.storage / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    clip = directory / "trim-reference.mp4"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([
        ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:d=1",
        "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=1",
        "-f", "lavfi", "-i", "color=c=white:s=1280x720:d=1",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
        "-map", "[outv]", "-c:v", "libx264", "-preset", "ultrafast", str(clip),
    ], capture_output=True, check=True, timeout=30)
    with platform.engine.begin() as db:
        db.execute(insert(videos).values(
            generation_id="trim-reference", user_id="owner", filename=clip.name,
            size_bytes=clip.stat().st_size, created=1,
        ))
    monkeypatch.setattr(platform, "_scene_native_trim", lambda _id: {
        "trim_start_seconds": 1.1, "trim_end_seconds": 1.8,
    })
    for frame in (platform._extract_continuity_frame("trim-reference"),
                  platform._extract_video_frame("trim-reference", "start")):
        with Image.open(frame) as image:
            red, green, blue = image.convert("RGB").getpixel((640, 360))
        assert blue > 200 and red < 20 and green < 20


def test_new_story_rejects_a_model_without_native_audio_instead_of_substituting_tts(client, monkeypatch):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A presenter speaking in one continuous shot"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "seedance")
    monkeypatch.setenv("SEEDANCE_MODEL", "seedance-1-0-pro")
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [
            {"index": 1, "video_prompt": "The presenter remains still and speaks calmly.",
             "voice_script": "Hôm nay mình sẽ chia sẻ một thiết kế mới"},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert response.status_code == 409
    assert response.json()["detail"].startswith("native_audio_unsupported_model")
    with platform.engine.connect() as db:
        assert db.execute(select(story_batches.c.id).where(story_batches.c.draft_id == plan["id"])).first() is None


def test_story_batch_reports_execution_validation_error(client, monkeypatch):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A host explains a recipe"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()
        payload["story_plan"] = {
            "clips": [{
                "index": 1,
                "video_prompt": "The host introduces the meal in the kitchen.",
                "voice_script": "Xin chao.",
            }],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda _draft_id: None)
    monkeypatch.setattr(platform, "_story_media_ready", lambda *_args: True)

    def reject_clip(*_args, **_kwargs):
        raise ExecutionError("invalid_execution_request")

    batch = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}).json()
    monkeypatch.setattr(platform, "generate", reject_clip)
    platform.process_story_batches()
    failed = client.get(f"/api/v1/generations/{batch['id']}").json()
    assert failed["status"] == "FAILED"
    assert failed["error"] == "clip_preparation_failed:invalid_execution_request"


@pytest.mark.parametrize("model,mode", [
    ("veo3.1-fast", "first_frame"),
    ("veo3.1-quality", "first_frame"),
    ("veo3.1-fast", "reference"),
    ("veo3.1-fast", "adaptive"),
    ("veo3.1-fast", None),
])
def test_poyo_story_scene_uses_previous_end_as_first_frame(client, monkeypatch, model, mode):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "Same presenter in two scenes"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setenv("POYO_MODEL", model)
    if mode is None:
        monkeypatch.delenv("POYO_CONTINUITY_MODE", raising=False)
    else:
        monkeypatch.setenv("POYO_CONTINUITY_MODE", mode)
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()
        payload["story_plan"] = {
            "visual_bible": "Same presenter, outfit and room.",
            "voice_bible": "warm voice " * 60,
            "clips": [
                {
                    "index": 1, "video_prompt": "Presenter introduces the topic.",
                    "voice_script": NATIVE_FIRST, "end_state": "Presenter faces the camera smiling.",
                },
                {
                    "index": 2,
                    "video_prompt": "Presenter continues speaking. " * 50,
                    "voice_script": NATIVE_NEXT,
                    "start_state": "The presenter faces the camera. " * 20,
                    "end_state": "The presenter stays in the same room. " * 20,
                    "transition_intent": "Continue the motion smoothly. " * 10,
                },
            ],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    image_bytes = io.BytesIO()
    Image.new("RGB", (640, 360), "white").save(image_bytes, format="JPEG")
    for name in (f"anchor-{draft['id']}", "frame-previous-clip-start", "previous-clip"):
        (directory / f"{name}.jpg").write_bytes(image_bytes.getvalue())

    owner_id = client.get("/api/v1/me").json()["id"]
    first = platform.generate(owner_id, draft["id"], confirm_paid=True, clip_index=1)
    previous_id = first["id"]
    persist_completed_clip(platform, previous_id, owner_id)
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == previous_id).values(status="COMPLETED"))
    platform._record_scene_continuity(platform.owned(drafts, draft["id"], owner_id), 1, continuity_status="PASSED")
    (directory / f"{previous_id}.jpg").write_bytes(image_bytes.getvalue())
    result = platform.generate(
        owner_id, draft["id"], confirm_paid=True, clip_index=2,
        continuity_generation_id=previous_id,
    )
    with platform.engine.connect() as db:
        child = db.execute(select(drafts.c.payload).where(drafts.c.id == result["draft_id"])).scalar_one()
    if mode in {"reference", "adaptive", None}:
        assert [ref["role"] for ref in child["spec"]["references"]] == [
            "CHARACTER_REFERENCE", "STYLE_REFERENCE", "FIRST_FRAME"
        ]
        assert child["spec"]["references"][2]["asset_id"] == previous_id
        assert len(child["assets"]) == 3
        assert "final reference guides the opening pose" in child["spec"]["intent"]["goal"]
        assert "exact opening composition" not in child["spec"]["intent"]["goal"]
    else:
        assert child["spec"]["references"] == [
            {"asset_id": previous_id, "role": "FIRST_FRAME", "strength": "high"}
        ]
        assert len(child["assets"]) == 1
    assert all(asset["source_url"].startswith("https://video.example.test/") for asset in child["assets"])
    assert "Starting state: Follow the visible pose, prop positions and framing" in child["spec"]["intent"]["goal"]
    assert "Hold the existing camera framing and angle" in child["spec"]["intent"]["goal"]
    assert "Visual context:" in child["spec"]["intent"]["goal"]
    assert "Scene action: " + payload["story_plan"]["clips"][1]["video_prompt"].strip() in child["spec"]["intent"]["goal"]
    assert child["spec"]["story"]["shots"] == []
    if mode == "first_frame":
        assert "exact opening composition" in child["spec"]["intent"]["goal"]
    assert len(child["spec"]["intent"]["goal"]) <= 4000


def test_adaptive_continuity_starts_first_scene_from_shared_anchor(client, monkeypatch):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A presenter speaks to camera"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast")
    monkeypatch.delenv("POYO_CONTINUITY_MODE", raising=False)
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()
        payload["story_plan"] = {
            "visual_bible": "Same presenter in the same room.",
            "clips": [{
                "index": 1, "video_prompt": "The presenter speaks calmly to the camera.",
                "voice_script": NATIVE_NEXT,
            }],
        }
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    anchor = platform.storage / "continuity" / f"anchor-{draft['id']}.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(anchor)
    uid = client.get("/api/v1/me").json()["id"]
    result = platform.generate(uid, draft["id"], confirm_paid=True, clip_index=1)
    with platform.engine.connect() as db:
        child = db.execute(select(drafts.c.payload).where(drafts.c.id == result["draft_id"])).scalar_one()
    assert [ref["role"] for ref in child["spec"]["references"]] == ["FIRST_FRAME"]
    assert child["spec"]["references"][0]["asset_id"] == f"anchor-{draft['id']}"
    assert len(child["assets"]) == 1


def test_character_references_refresh_after_anchor_changes(client):
    platform = client.platform
    anchor = platform.storage / "continuity" / "anchor-draft-refresh.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "red").save(anchor)
    platform._ensure_character_reference_images("draft-refresh", anchor)
    character = anchor.parent / "character-draft-refresh.jpg"
    style = anchor.parent / "style-draft-refresh.jpg"
    with Image.open(character) as image:
        assert image.convert("RGB").getpixel((10, 10))[0] > 200
    Image.new("RGB", (640, 360), "blue").save(anchor)
    anchor_time = max(character.stat().st_mtime_ns, style.stat().st_mtime_ns) + 1_000_000
    import os
    os.utime(anchor, ns=(anchor_time, anchor_time))
    platform._ensure_character_reference_images("draft-refresh", anchor)
    for path in (character, style):
        with Image.open(path) as image:
            red, _, blue = image.convert("RGB").getpixel((10, 10))
            assert blue > 200 and red < 50


def test_story_voice_rejects_large_tempo_change(client, monkeypatch):
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": False})
    monkeypatch.setattr(platform, "_compact_story_audio", lambda path: None)
    monkeypatch.setattr(platform, "_media_duration", lambda path: 12.4)
    monkeypatch.setattr(platform, "_record_audio_quality", lambda *args: 12.4)

    class Response:
        content = b"narration"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(platform.http, "post", lambda *args, **kwargs: Response())
    anchor = platform.storage / "continuity" / "anchor-voice-test.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18), "gray").save(anchor, format="JPEG")
    draft = {"id": "voice-test", "payload": {}}
    clips = [{"index": n, "voice_script": "A spoken scene."} for n in (1, 2)]
    with pytest.raises(HTTPException, match="voice_timeline_mismatch"):
        platform._prepare_story_media("voice-test", draft, clips)


def test_native_story_media_only_requires_reference_image(client):
    platform = client.platform
    anchor = platform.storage / "continuity" / "anchor-native-draft.jpg"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(b"reference image")
    assert platform._story_media_ready("native-batch", {"id": "native-draft"}, "native")
    assert not (anchor.parent / "story-native-batch.mp3").exists()


@pytest.mark.parametrize(
    "colors,expected_duration",
    [
        (["black", "black"], 2.0),
        (["black", "#202020"], 2.0),
        (["black", "black", "white"], 3.0),
        (["black", "black", "white", "white"], 4.0),
    ],
)
def test_native_story_merge_keeps_clip_audio_and_clean_cuts(client, colors, expected_duration):
    platform = client.platform
    directory = platform.storage / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    generation_ids = [f"native-clip-{index}" for index in range(1, len(colors) + 1)]
    for index, generation_id in enumerate(generation_ids):
        clip = directory / f"{generation_id}.mp4"
        subprocess.run(
            [
                ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c={colors[index]}:s=64x64:d=1",
                "-f", "lavfi", "-i", f"sine=frequency={440 + index * 220}:duration=1",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(clip),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        with platform.engine.begin() as db:
            db.execute(insert(videos).values(
                generation_id=generation_id, user_id="test-user", filename=clip.name,
                size_bytes=clip.stat().st_size, created=1,
            ))
    narration = platform.storage / "continuity" / "story-native-batch.mp3"
    narration.parent.mkdir(parents=True, exist_ok=True)
    narration.write_bytes(b"stale narration must not be used")
    for index, generation_id in enumerate(generation_ids):
        if index < len(generation_ids) - 1:
            Image.new("RGB", (1280, 720), colors[index]).save(
                narration.parent / f"{generation_id}.jpg", format="JPEG"
            )
        if index > 0:
            Image.new("RGB", (1280, 720), colors[index]).save(
                narration.parent / f"frame-{generation_id}-start.jpg", format="JPEG"
            )
    filename, size = platform._merge_story_videos(
        "native-batch", generation_ids, "16:9", "native"
    )
    merged = directory / filename
    assert size == merged.stat().st_size
    assert platform._validate_story_video(merged)
    assert expected_duration - 0.15 < platform._media_duration(merged) < expected_duration + 0.15
    metadata = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(merged)], capture_output=True, text=True
    )
    assert "24 fps" in metadata.stderr
    stream_durations = []
    for stream in ("v:0", "a:0"):
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(merged), "-map", f"0:{stream}", "-f", "null", "-"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        timestamps = re.findall(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)", decoded.stderr)
        hours, minutes, seconds = timestamps[-1]
        stream_durations.append(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    assert abs(stream_durations[0] - stream_durations[1]) < 0.15
    if colors[-1] != "black":
        frame_pattern = str(directory / "last-frame-%02d.png")
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(expected_duration - 0.5), "-i", str(merged),
             "-frames:v", "1", frame_pattern],
            capture_output=True, text=True, check=True, timeout=30,
        )
        with Image.open(next(directory.glob("last-frame-*.png"))) as image:
            final_pixel = image.convert("RGB").getpixel((640, 360))[0]
        assert final_pixel > (25 if colors[-1] == "#202020" else 200)
    if colors == ["black", "#202020"]:
        frame_pattern = str(directory / "boundary-%02d.png")
        subprocess.run(
            [ffmpeg, "-y", "-i", str(merged), "-ss", "0.90", "-t", "0.20", frame_pattern],
            capture_output=True, text=True, check=True, timeout=30,
        )
        boundary_pixels = []
        for path in directory.glob("boundary-*.png"):
            with Image.open(path) as image:
                boundary_pixels.append(image.convert("RGB").getpixel((640, 360))[0])
        assert boundary_pixels and all(pixel <= 4 or pixel >= 28 for pixel in boundary_pixels), boundary_pixels


def test_story_scene_cannot_generate_without_validated_parent(client, monkeypatch):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "One continuous presenter shot"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    owner = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"voice_bible": "Vietnamese; Northern accent; warm adult voice.", "clips": [
            {"index": 1, "voice_script": NATIVE_FIRST, "video_prompt": "The same presenter sits and speaks calmly."},
            {"index": 2, "voice_script": NATIVE_NEXT, "video_prompt": "The presenter continues without changing pose."},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        db.execute(insert(video_scenes).values(
            id="dependent-scene-two", project_id=plan["project_id"], plan_version=1, scene_index=2,
            draft_id=plan["id"], payload=payload["story_plan"]["clips"][1], status="PLANNED", created=1, updated=1,
        ))
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), "white").save(directory / f"anchor-{plan['id']}.jpg")
    with pytest.raises(HTTPException, match="continuity_parent_required"):
        platform.generate(owner, plan["id"], True, clip_index=2, native_audio=True)
    first = platform.generate(owner, plan["id"], True, clip_index=1, native_audio=True)
    with pytest.raises(HTTPException, match="continuity_parent_not_completed"):
        platform.generate(owner, plan["id"], True, clip_index=2, continuity_generation_id=first["id"], native_audio=True)
    with platform.engine.begin() as db:
        db.execute(update(executions).where(executions.c.generation_id == first["id"]).values(status="COMPLETED"))
    with pytest.raises(HTTPException, match="continuity_parent_validation_pending"):
        platform.generate(owner, plan["id"], True, clip_index=2, continuity_generation_id=first["id"], native_audio=True)
    draft = platform.owned(drafts, plan["id"], owner)
    platform._record_scene_continuity(draft, 1, continuity_status="FAILED")
    with pytest.raises(HTTPException, match="continuity_parent_failed_validation"):
        platform.generate(owner, plan["id"], True, clip_index=2, continuity_generation_id=first["id"], native_audio=True)
    platform._record_scene_continuity(draft, 1, continuity_status="PASSED")
    persist_completed_clip(platform, first["id"], owner)
    Image.new("RGB", (640, 360), "white").save(directory / f"{first['id']}.jpg")
    second = platform.generate(owner, plan["id"], True, clip_index=2, continuity_generation_id=first["id"], native_audio=True)
    project = platform.video_project(owner, plan["project_id"])
    one, two = project["scenes"]
    assert two["generation_id"] == second["id"]
    assert two["continuity_metadata"]["previous_scene_id"] == one["id"]
    assert one["continuity_metadata"]["next_scene_id"] == two["id"]
    assert one["continuity_metadata"]["voice_bible"] == two["continuity_metadata"]["voice_bible"]
    assert two["continuity_metadata"]["voice_identity_verified"] is False


def test_native_audio_review_fails_repetition_across_scene_boundary(client, monkeypatch):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "Continuous book review"}).json()
    platform = client.platform
    owner = client.get("/api/v1/me").json()["id"]
    before = "Hôm nay mình muốn chia sẻ cuốn sách này"
    current = "vì nội dung giúp mình suy nghĩ chậm lại"
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [{"index": 1, "voice_script": before}, {"index": 2, "voice_script": current}]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        db.execute(update(video_scenes).where(video_scenes.c.draft_id == plan["id"]).values(generation_id="first-speech"))
    platform.planner_configured = True

    class Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": self.text}

    monkeypatch.setattr(platform.http, "post", lambda *_args, **kwargs: Response(
        before if kwargs["content"] == b"first" else "cuốn sách này " + current,
    ))
    monkeypatch.setattr(platform, "_native_audio_timing", lambda _path: ([], {}))
    monkeypatch.setattr(platform, "_native_audio_measurements", lambda _path: {"duration_seconds": 8, "active_audio_seconds": 7})
    path = platform.storage / "test-speech.mp4"
    path.write_bytes(b"first")
    draft = platform.owned(drafts, plan["id"], owner)
    assert platform._review_native_scene_audio(draft, 1, "first-speech", path)
    path.write_bytes(b"second")
    assert not platform._review_native_scene_audio(draft, 2, "second-speech", path)
    with platform.engine.connect() as db:
        reports = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"], video_quality_reports.c.scene_index == 2,
        )).mappings().all()
    speech = next(report for report in reports if report["stage"] == "NATIVE_SPEECH")
    assert "native_speech_boundary_repetition" in speech["issues"]
    voice = next(report for report in reports if report["stage"] == "NATIVE_VOICE_CONTINUITY")
    assert voice["status"] == "WARN"
    assert voice["metrics"]["voice_identity_verified"] is False


def test_native_merge_trims_silence_from_both_audio_and_video(client):
    platform = client.platform
    directory = platform.storage / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    generation_ids = ["silent-edge-one", "silent-edge-two"]
    for generation_id in generation_ids:
        clip = directory / f"{generation_id}.mp4"
        subprocess.run([
            ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=white:s=64x64:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-af", "adelay=500,apad", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip),
        ], capture_output=True, check=True, timeout=30)
        with platform.engine.begin() as db:
            db.execute(insert(videos).values(generation_id=generation_id, user_id="owner", filename=clip.name,
                                            size_bytes=clip.stat().st_size, created=1))
    filename, _ = platform._merge_story_videos("trimmed-native", generation_ids, "16:9", "native")
    output = directory / filename
    assert platform._validate_story_video(output)
    assert 2.3 <= platform._media_duration(output) <= 2.7
    signal = subprocess.run([ffmpeg, "-hide_banner", "-i", str(output), "-vn", "-af",
                             "silencedetect=noise=-45dB:d=0.05", "-f", "null", "-"],
                            capture_output=True, text=True, check=True, timeout=30)
    gaps = [float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", signal.stderr)]
    assert gaps and max(gaps) < 0.4
    stream_durations = []
    for stream in ("0:v:0", "0:a:0"):
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(output), "-map", stream, "-f", "null", "-"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        hours, minutes, seconds = re.findall(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)", decoded.stderr)[-1]
        stream_durations.append(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    assert abs(stream_durations[0] - stream_durations[1]) < 0.15


def test_planned_camera_transition_does_not_fail_whole_frame_identity_check(client):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "The presenter changes camera angle intentionally"}).json()
    platform = client.platform
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [
            {"index": 1, "transition_intent": "continue"},
            {"index": 2, "transition_intent": "intentional_camera_transition"},
        ]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    anchor, end = platform.storage / "old-shot.jpg", platform.storage / "new-shot.jpg"
    Image.new("RGB", (128, 128), "black").save(anchor)
    Image.new("RGB", (128, 128), "white").save(end)
    assert platform._review_scene_reference(plan["id"], 2, anchor, anchor, end)
    with platform.engine.connect() as db:
        report = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"], video_quality_reports.c.stage == "SCENE_REFERENCE",
        )).mappings().one()
    assert report["status"] == "WARN"
    assert report["metrics"]["face_identity_verified"] is False
    assert report["issues"] == ["visual_identity_unverified_planned_transition"]


def test_existing_scene_database_adds_metadata_without_losing_rows(tmp_path):
    database = "sqlite:///" + str(tmp_path / "old-platform.sqlite")
    storage = tmp_path / "assets"
    original = Platform(database, storage, "http://example.test", "test-key")
    from sqlalchemy import inspect, text
    with original.engine.begin() as db:
        db.execute(insert(video_scenes).values(
            id="old-scene", project_id="old-project", plan_version=1, scene_index=1,
            payload={"voice_script": "An existing line"}, status="PLANNED", created=1, updated=1,
        ))
        db.execute(text("ALTER TABLE platform_video_scenes DROP COLUMN continuity_metadata"))
    original.close()
    upgraded = Platform(database, storage, "http://example.test", "test-key")
    try:
        assert "continuity_metadata" in {column["name"] for column in inspect(upgraded.engine).get_columns("platform_video_scenes")}
        with upgraded.engine.connect() as db:
            row = db.execute(select(video_scenes).where(video_scenes.c.id == "old-scene")).mappings().one()
        assert row["payload"] == {"voice_script": "An existing line"}
        assert row["continuity_metadata"] is None
    finally:
        upgraded.close()


def make_continuity_story(client, monkeypatch, scene_count, voice_mode=None, provider="poyo"):
    from app.services.video_provider_capabilities import VideoProviderCapabilities

    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "One continuous Vietnamese presenter video"}).json()
    uid = client.get("/api/v1/me").json()["id"]
    use_legacy_test_provider(platform, provider)
    platform.public_base_url = "https://video.example.test" if provider == "poyo" else ""
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast")
    monkeypatch.setenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1")
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda _id: None)
    monkeypatch.setattr(platform, "_validate_story_video", lambda _path: True)
    monkeypatch.setattr(platform, "_review_native_scene_audio", lambda *_args: True)
    monkeypatch.setattr(platform, "_review_scene_reference", lambda *_args: True)
    durations = {}
    monkeypatch.setattr(platform, "_media_duration", lambda path: durations.get(str(path), 8.0))
    story = {"voice_bible": "Vietnamese; Northern accent; adult woman; warm; steady; clear pronunciation.", "clips": [
        {"index": index, "video_prompt": "The same presenter continues in the same pose and locked camera.",
         "voice_script": f"Mình tiếp tục chia sẻ nội dung ở phần {index} này để các bạn cùng tìm hiểu và lựa chọn phù hợp.", "duration_seconds": 8,
         "start_state": "Seated, hands still, same framing.", "end_state": "Seated, hands still, same framing."}
        for index in range(1, scene_count + 1)
    ]}
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = story
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
        for clip in story["clips"][1:]:
            db.execute(insert(video_scenes).values(
                id=f"{plan['id']}-scene-{clip['index']}", project_id=plan["project_id"], plan_version=1,
                scene_index=clip["index"], draft_id=plan["id"], payload=clip, status="PLANNED", created=1, updated=1,
            ))
    directory = platform.storage / "continuity"
    directory.mkdir(parents=True, exist_ok=True)
    anchor = directory / f"anchor-{plan['id']}.jpg"
    Image.new("RGB", (640, 360), "white").save(anchor)

    def extract(gid):
        target = directory / f"{gid}.jpg"
        target.write_bytes(anchor.read_bytes())
        return target

    def merge(batch_id, gids, ratio, audio_mode):
        assert audio_mode == "native"
        target = platform.storage / "videos" / platform._story_output_filename(batch_id, gids, audio_mode)
        target.write_bytes(b"unit-test merged native output")
        durations[str(target)] = sum(durations.get(str(platform.storage / "videos" / f"{gid}.mp4"), 8.0) for gid in gids)
        return target.name, target.stat().st_size

    monkeypatch.setattr(platform, "_extract_continuity_frame", extract)
    monkeypatch.setattr(platform, "_extract_video_frame", lambda *_args: anchor)
    monkeypatch.setattr(platform, "_merge_story_videos", merge)
    original_client = platform.runtime.client

    class AdapterClient:
        def call(self, provider, action, **kwargs):
            if action == "capabilities":
                caps = VideoProviderCapabilities.documented_fallback(provider, "Veo-3.1" if provider == "seedvis" else "veo3.1-fast")
                return caps.model_copy(update={"capability_source": "ADAPTER"}).model_dump(mode="json")
            return original_client.call(provider, action, **kwargs)

    platform.runtime.client = AdapterClient()
    request = {"confirm_paid": True}
    if voice_mode:
        request["voice_mode"] = voice_mode
    batch = client.post(f"/api/v1/plans/{plan['id']}/generate", json=request).json()
    platform.process_story_batches()
    return platform, plan, uid, batch, durations


def chain_generation_ids(platform, batch_id):
    with platform.engine.connect() as db:
        return db.execute(select(story_batches.c.generation_ids).where(story_batches.c.id == batch_id)).scalar_one()


@pytest.mark.parametrize("native_audio", [True, False])
def test_official_single_scene_preserves_explicit_audio_flag_through_api(client, monkeypatch, native_audio):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "One stable presenter shot"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast-official")
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={
        "confirm_paid": True, "native_audio": native_audio, "voice_mode": "native_veo",
    })
    assert response.status_code == 202
    job = platform.store.get(response.json()["id"])
    assert job["payload"]["spec"]["output"]["generate_audio"] is native_audio
    assert response.json()["voice_mode"] == "native_veo"
    assert "story_plan" not in job["payload"] and "voice_policy" not in job["payload"]
    with platform.engine.connect() as db:
        scene = db.execute(select(video_scenes).where(video_scenes.c.draft_id == plan["id"])).mappings().one()
    assert not (scene["continuity_metadata"] or {}).get("parent_generation_id")


def test_regular_fast_audio_off_rejected_before_enqueue(client, monkeypatch):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "One stable presenter shot"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast")
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True, "native_audio": False})
    assert response.status_code == 422
    assert "native_audio_disable_unsupported_model" in response.json()["detail"]
    with platform.engine.connect() as db:
        assert db.execute(select(drafts.c.generation_id).where(drafts.c.id == plan["id"])).scalar_one() is None


def test_official_public_duration_capabilities_and_api_validation(client, monkeypatch):
    register(client)
    use_legacy_test_provider(client.platform, "poyo")
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast-official")
    assert client.get("/api/v1/config").json()["duration_options"] == [4, 6, 8]
    for duration in (4, 6, 8):
        response = client.post("/api/v1/plans", json={"prompt": "A simple locked shot", "duration": duration})
        assert response.status_code == 201
        with client.platform.engine.connect() as db:
            payload = db.execute(select(drafts.c.payload).where(drafts.c.id == response.json()["id"])).scalar_one()
        assert payload["spec"]["output"]["duration_seconds"] == duration
    for duration in (5, 7, 10):
        assert client.post("/api/v1/plans", json={"prompt": "A simple locked shot", "duration": duration}).status_code == 422


def test_consistent_voice_fallback_is_persisted_and_exposed(client, monkeypatch):
    platform, plan, uid, batch, _ = make_continuity_story(client, monkeypatch, 2, "consistent_voice")
    assert batch["voice_mode"] == "native_veo" and batch["requested_voice_mode"] == "consistent_voice"
    assert {"speech_alignment_unavailable", "lip_sync_unavailable"}.issubset(batch["voice_mode_limitations"])
    root = platform.owned(drafts, plan["id"], uid)
    assert root["payload"]["voice_policy"]["voice_mode"] == "native_veo"
    first, = chain_generation_ids(platform, batch["id"])
    assert platform.store.get(first)["payload"]["spec"]["output"]["generate_audio"] is True


def test_consistent_voice_single_scene_does_not_silently_ignore_request(client):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A stable presenter shot"}).json()
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"voice_mode": "consistent_voice"})
    assert response.status_code == 202
    assert response.json()["requested_voice_mode"] == "consistent_voice"
    assert response.json()["voice_mode"] == "native_veo"
    assert "lip_sync_unavailable" in response.json()["voice_mode_limitations"]


@pytest.mark.parametrize("transition,expected_start", [("continue", 0), (None, 0), ("legacy transition", 0), ("hard_cut", 0.88)])
def test_native_review_preserves_continuous_opening_pose(client, monkeypatch, transition, expected_start):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A continuous presenter shot"}).json()
    platform = client.platform
    uid = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        payload["story_plan"] = {"clips": [{"index": 2, "voice_script": "The next complete clause.", "transition_intent": transition}]}
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload))
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": False})
    monkeypatch.setattr(platform, "_native_audio_timing", lambda _path: ([], {}))
    monkeypatch.setattr(platform, "_native_audio_measurements", lambda _path: {
        "duration_seconds": 8, "active_audio_seconds": 5, "leading_silence_seconds": 1, "trailing_silence_seconds": 2,
    })
    assert platform._review_native_scene_audio(platform.owned(drafts, plan["id"], uid), 2, "next-scene", platform.storage / "unused.mp4")
    with platform.engine.connect() as db:
        report = db.execute(select(video_quality_reports).where(
            video_quality_reports.c.project_id == plan["project_id"], video_quality_reports.c.stage == "NATIVE_SPEECH",
        )).mappings().one()
    assert report["metrics"]["trim_start_seconds"] == expected_start
    assert ("continuous_scene_leading_silence_preserved" in report["issues"]) == (expected_start == 0)


def test_selected_reference_frame_and_compositor_share_safe_end_time(client, monkeypatch):
    import app.services.platform_continuity as continuity_module

    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A stable presenter shot"}).json()
    platform = client.platform
    uid = client.get("/api/v1/me").json()["id"]
    gid = "safe-silent-end"
    directory = platform.storage / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{gid}.mp4"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([
        ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-af", "apad", "-t", "8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], capture_output=True, check=True, timeout=30)
    with platform.engine.begin() as db:
        db.execute(insert(videos).values(generation_id=gid, user_id=uid, filename=path.name,
                                        size_bytes=path.stat().st_size, created=1))
        db.execute(update(video_scenes).where(video_scenes.c.draft_id == plan["id"]).values(generation_id=gid))
        db.execute(insert(video_quality_reports).values(
            id="safe-end-report", project_id=plan["project_id"], scene_index=1, stage="NATIVE_SPEECH", status="PASS",
            issues=[], created=1, metrics={"generation_id": gid, "duration_seconds": 8, "active_audio_seconds": 6,
                                          "trailing_silence_seconds": 2, "trim_start_seconds": 0, "trim_end_seconds": 6.12},
        ))
    monkeypatch.setattr(continuity_module, "choose_continuity_frame", lambda candidates: (candidates[-1], {"face_pose_verified": False}))
    reference = platform._extract_continuity_frame(gid)
    assert reference.is_file()
    trim = platform._scene_native_trim(gid)
    assert trim["trim_end_seconds"] == pytest.approx(6.32)
    with platform.engine.connect() as db:
        metadata = db.execute(select(video_scenes.c.continuity_metadata).where(video_scenes.c.generation_id == gid)).scalar_one()
    assert metadata["continuity_frame_time_seconds"] < trim["trim_end_seconds"]
    assert metadata["frame_selection_method"] == "SAFE_SILENT_TAIL"
    assert platform._extract_continuity_frame(gid) == reference
    assert platform._scene_native_trim(gid)["trim_end_seconds"] == trim["trim_end_seconds"]
    filename, _ = platform._merge_story_videos("paired-end", [gid], "16:9", "native")
    output = directory / filename
    assert platform._validate_story_video(output)
    assert 6.25 <= platform._media_duration(output) <= 6.4
    stream_durations = []
    for stream in ("0:v:0", "0:a:0"):
        decoded = subprocess.run([ffmpeg, "-hide_banner", "-i", str(output), "-map", stream, "-f", "null", "-"],
                                 capture_output=True, text=True, check=True, timeout=30)
        hours, minutes, seconds = re.findall(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)", decoded.stderr)[-1]
        stream_durations.append(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
    assert abs(stream_durations[0] - stream_durations[1]) < 0.15


def test_native_single_scene_has_no_fake_continuation_and_actual_duration(client, monkeypatch):
    platform, plan, uid, batch, durations = make_continuity_story(client, monkeypatch, 1)
    gid, = chain_generation_ids(platform, batch["id"])
    path = persist_completed_clip(platform, gid, uid, "https://storage.poyo.ai/scene-one.mp4")
    durations[str(path)] = 7.62
    platform.process_story_batches()
    scene, = platform.video_project(uid, plan["project_id"])["scenes"]
    metadata = scene["continuity_metadata"]
    assert metadata["native_audio"] is True and metadata["native_continuation"] is False
    assert metadata["parent_generation_id"] is None and metadata["parent_video_url"] is None
    assert metadata["output_video_url"] == "https://storage.poyo.ai/scene-one.mp4"
    assert metadata["duration_seconds"] == 7.62
    assert metadata["validation_status"] == "PASSED"
    assert metadata["continuity_status"] == "BEST_EFFORT"
    assert metadata["voice_continuity_status"] == "BEST_EFFORT"
    assert client.get(f"/api/v1/generations/{batch['id']}").json()["duration"] == 7.62
    assert chain_generation_ids(platform, batch["id"]) == [gid]


@pytest.mark.parametrize("provider", ["poyo", "seedvis"])
def test_three_scene_chain_uses_latest_real_video_url_and_native_audio(client, monkeypatch, caplog, provider):
    caplog.set_level("INFO")
    platform, plan, uid, batch, _durations = make_continuity_story(client, monkeypatch, 3, provider=provider)
    first, = chain_generation_ids(platform, batch["id"])
    platform.process_story_batches()
    assert chain_generation_ids(platform, batch["id"]) == [first]
    v1 = "https://storage.poyo.ai/secret-path/one.mp4?token=private-token"
    persist_completed_clip(platform, first, uid, v1)
    platform.process_story_batches()
    first, second = chain_generation_ids(platform, batch["id"])
    v2 = "https://storage.poyo.ai/two.mp4"
    persist_completed_clip(platform, second, uid, v2)
    platform.process_story_batches()
    first, second, third = chain_generation_ids(platform, batch["id"])
    scenes = platform.video_project(uid, plan["project_id"])["scenes"]
    m1, m2, m3 = [scene["continuity_metadata"] for scene in scenes]
    assert m2["parent_generation_id"] == first and m2["parent_video_url"] == v1
    assert m3["parent_generation_id"] == second and m3["parent_video_url"] == v2
    assert m3["parent_video_url"] != v1
    assert m3["parent_asset_path"] == f"videos/{second}.mp4"
    assert m1["voice_bible"] == m2["voice_bible"] == m3["voice_bible"]
    prompts = [platform.store.get(gid)["payload"]["spec"]["intent"]["goal"] for gid in (first, second, third)]
    if provider == "seedvis":
        assert not platform.public_base_url
        for gid in (first, second, third):
            job = platform.store.get(gid)
            assert job["provider"] == "seedvis"
            assert all(asset["source_url"].startswith("data:image/jpeg;base64,") for asset in job["payload"]["assets"])
    voice_lines = [next(line for line in prompt.splitlines() if line.startswith("VOICE DESCRIPTION")) for prompt in prompts]
    assert voice_lines[0] == voice_lines[1] == voice_lines[2]
    assert all("VOICE MATCH RULES" not in prompt and "CONTINUE THE PREVIOUS VIDEO" not in prompt for prompt in prompts)
    assert all(m["native_audio"] and not m["native_continuation"] for m in (m1, m2, m3))
    assert all(m["first_frame_present"] and m["character_reference_present"] for m in (m2, m3))
    assert all(not m["first_frame_exact"] for m in (m2, m3))
    assert m3["continuation_generation_id"] is None
    assert m3["fallback_reason"] == "video_continuation_not_documented_for_model"
    assert all(m["voice_continuity_status"] == "BEST_EFFORT" for m in (m1, m2, m3))
    assert "private-token" not in caplog.text and "secret-path" not in caplog.text
    persist_completed_clip(platform, third, uid, "https://storage.poyo.ai/three.mp4")
    platform.process_story_batches()
    assert client.get(f"/api/v1/generations/{batch['id']}").json()["status"] == "COMPLETED"


def test_retry_middle_scene_invalidates_downstream_and_preserves_parent_video(client, monkeypatch):
    platform, plan, uid, batch, _durations = make_continuity_story(client, monkeypatch, 3)
    first, = chain_generation_ids(platform, batch["id"])
    persist_completed_clip(platform, first, uid, "https://storage.poyo.ai/one.mp4")
    platform.process_story_batches()
    first, second = chain_generation_ids(platform, batch["id"])
    persist_completed_clip(platform, second, uid, "https://storage.poyo.ai/old-two.mp4")
    platform.process_story_batches()
    first, second, old_third = chain_generation_ids(platform, batch["id"])
    persist_completed_clip(platform, old_third, uid, "https://storage.poyo.ai/old-three.mp4")
    platform._record_completed_scene_output(old_third)
    draft = platform.owned(drafts, plan["id"], uid)
    platform._record_scene_continuity(draft, 2, continuity_status="FAILED", continuity_error="native_speech_quality_failed")
    with platform.engine.begin() as db:
        db.execute(update(story_batches).where(story_batches.c.id == batch["id"]).values(
            status="FAILED", error_code="native_speech_quality_failed",
        ))
    failed = client.get(f"/api/v1/generations/{batch['id']}").json()
    assert failed["retryable"] and failed["clip_progress"]["completed"] == 1
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert response.status_code == 202
    assert chain_generation_ids(platform, batch["id"]) == [first]
    third_row = platform.video_project(uid, plan["project_id"])["scenes"][2]
    assert third_row["generation_id"] is None
    assert third_row["continuity_metadata"]["continuity_status"] == "INVALIDATED"
    assert third_row["continuity_metadata"]["parent_generation_id"] is None
    assert third_row["continuity_metadata"]["invalidated_generations"][-1]["generation_id"] == old_third
    platform.process_story_batches()
    _, replacement = chain_generation_ids(platform, batch["id"])
    assert replacement != second
    persist_completed_clip(platform, replacement, uid, "https://storage.poyo.ai/new-two.mp4")
    platform.process_story_batches()
    first_after, second_after, third_after = chain_generation_ids(platform, batch["id"])
    assert first_after == first and second_after == replacement and third_after != old_third
    metadata = platform.video_project(uid, plan["project_id"])["scenes"][2]["continuity_metadata"]
    assert metadata["parent_video_url"] == "https://storage.poyo.ai/new-two.mp4"
    assert metadata["parent_generation_id"] == replacement
    assert platform.store.get(old_third)["status"] == "COMPLETED"


def test_cached_frame_is_not_enough_without_the_parent_video_asset(client, monkeypatch):
    platform, plan, uid, batch, _durations = make_continuity_story(client, monkeypatch, 2)
    first, = chain_generation_ids(platform, batch["id"])
    path = persist_completed_clip(platform, first, uid)
    platform._record_scene_continuity(platform.owned(drafts, plan["id"], uid), 1, continuity_status="PASSED")
    platform._extract_continuity_frame(first)
    path.unlink()
    with pytest.raises(HTTPException, match="continuity_parent_video_missing"):
        platform.generate(uid, plan["id"], True, clip_index=2, continuity_generation_id=first, native_audio=True)
    assert chain_generation_ids(platform, batch["id"]) == [first]


def test_native_merge_cache_changes_with_the_generation_chain(monkeypatch):
    import app.services.platform_story_render as renderer
    first = Platform._story_output_filename("story", ["one", "two"], "native")
    retried = Platform._story_output_filename("story", ["one", "two-retry"], "native")
    assert first != retried
    assert first == Platform._story_output_filename("story", ["one", "two"], "native")
    monkeypatch.setattr(renderer, "NATIVE_REVIEW_PROFILE", "next-boundary-policy")
    assert first != Platform._story_output_filename("story", ["one", "two"], "native")


def test_capability_model_mismatch_does_not_switch_away_from_veo_fast(client, monkeypatch):
    from app.services.video_provider_capabilities import VideoProviderCapabilities

    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    monkeypatch.setenv("POYO_MODEL", "veo3.1-fast")
    monkeypatch.setattr(platform.runtime.client, "call", lambda *_args, **_kwargs:
                        VideoProviderCapabilities.documented_fallback("poyo", "veo3.1-quality").model_dump())
    with pytest.raises(HTTPException, match="video_provider_model_mismatch"):
        platform._continuity_strategy()
    assert platform.provider == "poyo"


def test_retry_does_not_discard_an_in_flight_downstream_generation(client, monkeypatch):
    platform, plan, uid, batch, _durations = make_continuity_story(client, monkeypatch, 3)
    first, = chain_generation_ids(platform, batch["id"])
    persist_completed_clip(platform, first, uid)
    platform.process_story_batches()
    _, second = chain_generation_ids(platform, batch["id"])
    persist_completed_clip(platform, second, uid)
    platform.process_story_batches()
    original_chain = chain_generation_ids(platform, batch["id"])
    platform._record_scene_continuity(platform.owned(drafts, plan["id"], uid), 2,
                                     continuity_status="FAILED", continuity_error="native_speech_quality_failed")
    with platform.engine.begin() as db:
        db.execute(update(story_batches).where(story_batches.c.id == batch["id"]).values(
            status="FAILED", error_code="native_speech_quality_failed",
        ))
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True})
    assert response.status_code == 409
    assert response.json()["detail"].startswith("continuity_downstream_active")
    assert chain_generation_ids(platform, batch["id"]) == original_chain
    assert client.get(f"/api/v1/generations/{batch['id']}").json()["retryable"] is False


def test_cross_origin_write_rejected(client):
    assert (
        client.post(
            "/api/v1/auth/register",
            headers={"Origin": "https://evil.example"},
            json={"email": "one@example.com", "password": "test-password"},
        ).status_code
        == 403
    )


def test_paid_provider_cannot_be_selected_when_server_paid_mode_disabled(client):
    register(client)
    assert (
        client.post(
            "/api/v1/plans", json={"prompt": "A city at sunrise", "provider": "seedance"}
        ).status_code
        == 409
    )


def test_seedance_requires_explicit_server_enablement(tmp_path):
    with pytest.raises(RuntimeError, match="ENABLE_PAID_GENERATION"):
        Platform(
            "sqlite:///" + str(tmp_path / "off.sqlite"),
            tmp_path / "off",
            "http://example.test",
            "key",
            provider="seedance",
        )


def test_poyo_live_configuration_and_paid_confirmation(tmp_path):
    platform = Platform(
        "sqlite:///" + str(tmp_path / "poyo.sqlite"),
        tmp_path / "poyo-assets",
        "http://example.test",
        "key",
        provider="poyo",
        paid_enabled=True,
    )

    class Client:
        def call(self, provider, action, **kwargs):
            assert provider == "poyo"
            if action == "validate":
                return {"execution_request": kwargs["payload"]}
            return {"provider_task_id": "poyo-task-1", "status": "queued"}

    platform.runtime.client = Client()
    credential_calls = []

    def credential(method, payload=None, *, provider=None):
        assert provider in {None, "poyo"}
        credential_calls.append((method, payload))
        return {"configured": True}

    platform._credential_request = credential
    with TestClient(create_app(platform, run_worker=False)) as http:
        register(http)
        config = http.get("/api/v1/config").json()
        assert config["provider"] == "poyo"
        assert config["duration_options"] == [8]
        assert config["local_image_upload"] is True
        assert http.get("/api/v1/provider-credential").json() == {"configured": True}
        assert http.put(
            "/api/v1/provider-credential", json={"api_key": "poyo-secret-key"}
        ).json() == {"configured": True}
        assert "poyo-secret-key" not in str(http.get("/api/v1/provider-credential").json())
        assert (
            http.post(
                "/api/v1/plans", json={"prompt": "A city at sunrise", "duration": 5}
            ).status_code
            == 422
        )
        draft = http.post(
            "/api/v1/plans", json={"prompt": "A city at sunrise", "duration": 8}
        ).json()["id"]
        path = "/api/v1/plans/" + draft + "/generate"
        assert http.post(path, json={"confirm_paid": False}).status_code == 409
        assert http.post(path, json={"confirm_paid": True}).status_code == 202
        assert ("PUT", {"api_key": "poyo-secret-key"}) in credential_calls


def test_live_seedance_confirmation_base64_and_persistence(tmp_path, monkeypatch):
    platform = Platform(
        "sqlite:///" + str(tmp_path / "live.sqlite"),
        tmp_path / "live-assets",
        "http://example.test",
        "key",
        provider="seedance",
        paid_enabled=True,
    )

    class Client:
        def call(self, provider, action, **kwargs):
            assert provider == "seedance"
            if action == "validate":
                self.validated = kwargs["payload"]
                return {"execution_request": kwargs["payload"]}
            if action == "submit":
                return {"provider_task_id": "seedance-task-1", "status": "queued"}
            return {
                "provider": "seedance",
                "provider_task_id": "seedance-task-1",
                "status": "completed",
                "outputs": [{"source_url": "https://result.volces.com/video.mp4"}],
                "usage": {"completion_tokens": 100},
            }

    adapter = Client()
    platform.runtime.client = adapter
    platform.runtime.settings = ExecutionSettings(poll_seconds=0)
    with TestClient(create_app(platform, run_worker=False)) as http:
        register(http)
        image = io.BytesIO()
        Image.new("RGB", (64, 48)).save(image, format="PNG")
        asset = http.post(
            "/api/v1/assets", files={"file": ("frame.png", image.getvalue(), "image/png")}
        ).json()["id"]
        draft = http.post(
            "/api/v1/plans", json={"prompt": "A real sunrise scene", "asset_id": asset}
        ).json()["id"]
        path = "/api/v1/plans/" + draft + "/generate"
        assert http.post(path, json={"confirm_paid": False}).status_code == 409
        response = http.post(path, json={"confirm_paid": True})
        assert response.status_code == 202
        gid = response.json()["id"]
        assert adapter.validated["assets"][0]["source_url"].startswith("data:image/jpeg;base64,")
        platform.runtime.tick(gid)
        job = platform.runtime.tick(gid)
        target = platform.storage / "videos" / (gid + ".mp4")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"mock video bytes")
        monkeypatch.setattr(
            platform,
            "_download_video",
            lambda generation_id, source: (generation_id + ".mp4", target.stat().st_size),
        )
        platform.finalize(job)
        result = http.get("/api/v1/generations/" + gid).json()
        assert result["status"] == "COMPLETED"
        assert result["demo"] is False
        assert http.get(result["video_url"]).content == b"mock video bytes"


def test_output_download_rejects_untrusted_host(tmp_path):
    platform = Platform(
        "sqlite:///" + str(tmp_path / "host.sqlite"),
        tmp_path / "host",
        "http://example.test",
        "key",
    )
    try:
        with pytest.raises(ValueError, match="Untrusted"):
            platform._download_video("generation", "https://evil.example/video.mp4")
    finally:
        platform.close()


def test_output_download_accepts_poyo_cdn(tmp_path, monkeypatch):
    platform = Platform(
        "sqlite:///" + str(tmp_path / "poyo-cdn.sqlite"),
        tmp_path / "poyo-cdn",
        "http://example.test",
        "key",
    )

    class Response:
        headers: ClassVar[dict[str, str]] = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"mock poyo video"

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, source):
            assert method == "GET"
            assert source == "https://cdn.doculator.org/videos/task/output.mp4"
            return Response()

    monkeypatch.delenv("VIDEO_OUTPUT_ALLOWED_HOST_SUFFIXES", raising=False)
    monkeypatch.setattr(
        "app.services.platform.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr("app.services.platform.httpx.Client", Client)
    try:
        filename, size = platform._download_video(
            "generation", "https://cdn.doculator.org/videos/task/output.mp4"
        )
        assert filename == "generation.mp4"
        assert size == len(b"mock poyo video")
        assert (platform.storage / "videos" / filename).read_bytes() == b"mock poyo video"
    finally:
        platform.close()


def test_story_failing_before_any_scene_can_retry_once_the_key_is_added(client, monkeypatch):
    register(client)
    draft = client.post("/api/v1/plans", json={"prompt": "A host explains a recipe"}).json()
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.public_base_url = "https://video.example.test"
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft["id"])).scalar_one()
        payload["story_plan"] = {"clips": [{
            "index": 1, "video_prompt": "The host introduces the meal in the kitchen.", "voice_script": "Xin chao.",
        }]}
        db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=payload))
    configured = {"value": True}
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": configured["value"]})
    monkeypatch.setattr(platform, "_validate_public_backend_target", lambda: None)
    monkeypatch.setattr(platform, "_validate_public_continuity_image", lambda _draft_id: None)
    monkeypatch.setattr(platform, "_story_media_ready", lambda *_args: True)
    batch = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True}).json()
    configured["value"] = False  # the key is missing when the worker submits the first scene
    platform.process_story_batches()
    failed = client.get(f"/api/v1/generations/{batch['id']}").json()
    assert failed["status"] == "FAILED" and "API key" in failed["error"]
    assert failed["clip_progress"]["completed"] == 0
    assert failed["retryable"] is True
    configured["value"] = True
    retried = client.post(f"/api/v1/plans/{draft['id']}/generate", json={"confirm_paid": True})
    assert retried.status_code == 202
    assert client.get(f"/api/v1/generations/{batch['id']}").json()["status"] != "FAILED"
