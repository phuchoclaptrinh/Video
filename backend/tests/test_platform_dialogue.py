import copy
import json

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select, update

from app.services.platform import Platform
from app.services.platform_shared import (
    chat_messages,
    drafts,
    story_plan_versions,
    video_projects,
    video_scenes,
)
from app.services.video_continuity import VideoContinuityStrategy
from app.services.video_execution_store import executions

LINE = "Mình mời bạn cùng tìm hiểu đôi giày này để xem có thật sự phù hợp với nhu cầu của mình không."


@pytest.fixture
def saved_story(tmp_path, request):
    line = getattr(request, "param", LINE)
    platform = Platform("sqlite:///" + str(tmp_path / "platform.sqlite"), tmp_path / "assets", "https://example.test", "test-key")
    platform.paid_enabled = True
    clips = [{"index": index, "duration_seconds": 8, "voice_script": line,
              "video_prompt": "The presenter holds the same shoes in the same workshop.",
              "start_state": "Shoes held at chest height.", "end_state": "Shoes held at chest height."}
             for index in (1, 2, 3)]
    payload = {"spec": {"request": {"request_id": "story", "user_id": "user", "tenant_id": "user"},
                        "intent": {"operation": "TEXT_GENERATION", "goal": "Introduce shoes"},
                        "output": {"duration_seconds": 8, "aspect_ratio": "16:9", "resolution_preference": "720p"},
                        "story": {"shots": []}, "references": []},
               "assets": [], "preflight_decision": "ALLOW", "policy_decision_id": "test",
               "story_plan": {"voice_bible": "Vietnamese; adult woman; Hanoi accent; warm clear mid pitch; pace 2.6-3.0 syllables/s.",
                              "full_voice_script": " ".join(clip["voice_script"] for clip in clips), "clips": clips}}
    draft = {"id": "story", "user_id": "user", "title": "KOC giới thiệu giày tại xưởng", "payload": payload, "created": 1}
    event = {"type": "video_generation", "plan": {"id": "story", "story_plan": payload["story_plan"]}}
    with platform.engine.begin() as db:
        db.execute(insert(drafts).values(**draft))
        db.execute(insert(video_projects).values(id="project", user_id="user", conversation_id="conversation",
                   brief=draft["title"], status="GENERATING", current_plan_version=1, context_snapshot={}, created=1, updated=1))
        db.execute(insert(story_plan_versions).values(id="version", project_id="project", version=1, draft_id="story",
                   payload=copy.deepcopy(payload), status="APPROVED", created=1))
        for clip in clips:
            db.execute(insert(video_scenes).values(id=f"scene-{clip['index']}", project_id="project", plan_version=1,
                       scene_index=clip["index"], draft_id="story", payload=clip, status="PLANNED", created=1, updated=1))
        db.execute(insert(chat_messages).values(id="message", user_id="user", conversation_id="conversation", role="assistant",
                   content=json.dumps(event), created=1))
    try:
        yield platform, draft
    finally:
        platform.close()


def mock_internal_http(platform, handler):
    platform.http.close()
    platform.http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))


def saved_snapshot(platform):
    with platform.engine.connect() as db:
        return (
            db.execute(select(drafts.c.payload).where(drafts.c.id == "story")).scalar_one(),
            db.execute(select(story_plan_versions.c.payload)).scalar_one(),
            db.execute(select(chat_messages.c.content)).scalar_one(),
            list(db.execute(select(video_scenes.c.payload)).scalars()),
        )


def block_planner_calls(platform):
    def handler(_request):
        raise AssertionError("Approved dialogue must not trigger an LLM edit")
    mock_internal_http(platform, handler)


@pytest.mark.parametrize("saved_story", [LINE, "Ừ.", "Hãy cùng mình khám phá nhé!", LINE + " Một đoạn thêm nữa.", " ".join(["mình"] * 75) + "."], indirect=True)
def test_generation_checks_dialogue_then_enqueues_without_edits(saved_story, monkeypatch):
    platform, draft = saved_story
    platform.provider = "poyo"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_continuity_strategy", lambda: VideoContinuityStrategy("poyo", "veo3.1-fast"))

    class ExecutionClient:
        def call(self, _provider, action, **kwargs):
            assert action == "validate"
            return {"execution_request": kwargs["payload"]}

    platform.runtime.client = ExecutionClient()
    block_planner_calls(platform)
    before = saved_snapshot(platform)
    generated = platform.generate("user", "story", confirm_paid=True, clip_index=1, native_audio=True)
    job = platform.store.get(generated["id"])
    prompt = job["payload"]["spec"]["intent"]["goal"]
    assert draft["payload"]["story_plan"]["clips"][0]["voice_script"] in prompt
    assert prompt.count("VOICE DESCRIPTION") == 1
    assert "VOICE MATCH RULES" not in prompt and "CONTINUE THE PREVIOUS VIDEO" not in prompt
    assert job["status"] == "QUEUED" and saved_snapshot(platform) == before
    again = platform.generate("user", "story", confirm_paid=True, clip_index=1, native_audio=True)
    assert again["id"] == generated["id"]


def test_invalid_plan_stops_before_execution_validation_or_paid_enqueue(saved_story, monkeypatch):
    platform, draft = saved_story
    draft["payload"]["story_plan"]["clips"][0]["voice_script"] = "*** 😊 !!!"
    with platform.engine.begin() as db:
        db.execute(update(drafts).where(drafts.c.id == "story").values(payload=draft["payload"]))
    platform.provider = "poyo"
    monkeypatch.setattr(platform, "provider_credential_status", lambda: {"configured": True})
    monkeypatch.setattr(platform, "_continuity_strategy", lambda: VideoContinuityStrategy("poyo", "veo3.1-fast"))

    class ExecutionClient:
        def call(self, *_args, **_kwargs):
            raise AssertionError("Invalid dialogue must stop before enqueue")

    platform.runtime.client = ExecutionClient()
    block_planner_calls(platform)
    with pytest.raises(HTTPException) as error:
        platform.generate("user", "story", confirm_paid=True, clip_index=1, native_audio=True)
    assert error.value.status_code == 422
    with platform.engine.connect() as db:
        assert db.execute(select(executions.c.generation_id)).first() is None
