# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import copy
import json

import httpx
from sqlalchemy import insert, select, update

from app.services.platform import chat_messages, drafts, story_batches, story_plan_versions
from app.services.platform_auto_retry import REWORD_FAILURE_LIMIT
from tests.test_platform import client, register  # noqa: F401 - shared fixture

FAILED = "native_speech_quality_failed: Cảnh 1 đọc lặp từ"
OLD = "Bảng size của bộ Takit tê ca không một ghi rõ từng mốc cân nặng cho mẹ chọn."
NEW = "Bộ Takit có bảng size ghi rõ từng mốc cân nặng, mẹ nhìn một lần là chọn được."
SECOND = "Chất vải mềm mát nên bé mặc cả ngày vẫn thoải mái."


def failed_story(client, monkeypatch, issues):
    register(client)
    platform = client.platform
    plan = client.post("/api/v1/plans", json={"prompt": "Giới thiệu bộ quần áo trẻ em Takit"}).json()
    story = {"clips": [
        {"index": 1, "beat": "Chọn size", "video_prompt": "A mother checks a size chart.", "voice_script": OLD},
        {"index": 2, "beat": "Chất vải", "video_prompt": "A child plays in the outfit.", "voice_script": SECOND},
    ]}
    plan["story_plan"] = story
    with platform.engine.begin() as db:
        payload = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        db.execute(update(drafts).where(drafts.c.id == plan["id"]).values(payload=payload | {"story_plan": story}))
        version = db.execute(select(story_plan_versions).where(story_plan_versions.c.draft_id == plan["id"])).mappings().one()
        db.execute(update(story_plan_versions).where(story_plan_versions.c.id == version["id"])
                   .values(payload=copy.deepcopy(version["payload"]) | {"story_plan": story}, status="APPROVED"))
    assert client.post("/api/v1/chat/video-events", json={"prompt": "Bộ Takit", "plan": plan}).status_code == 201
    owner = client.get("/api/v1/me").json()["id"]
    with platform.engine.begin() as db:
        db.execute(insert(story_batches).values(
            id="b" * 32, user_id=owner, title="Takit", draft_id=plan["id"], ratio="9:16",
            generation_ids=["scene-1"], status="FAILED", audio_mode="native", error_code=FAILED, created=1,
        ))
    monkeypatch.setattr(platform, "_failed_story_scene_index", lambda *_: 1)
    monkeypatch.setattr(platform, "_native_speech_report", lambda *_: {
        "issues": issues, "metrics": {"transcript": "Bảng của bộ Takit TK0 sửa lại, TK0"}})
    sent = []

    def planner(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"voice_script": NEW})

    platform.http.close()
    platform.http = httpx.Client(base_url="http://example.test", transport=httpx.MockTransport(planner))
    with platform.engine.connect() as db:
        row = dict(db.execute(select(story_batches)).mappings().one())
    return platform, plan, row, sent


def lines(platform, plan):
    with platform.engine.connect() as db:
        draft = db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()
        version = db.execute(select(story_plan_versions.c.payload)
                             .where(story_plan_versions.c.draft_id == plan["id"])).scalar_one()
        card = json.loads(db.execute(select(chat_messages.c.content)
                                     .where(chat_messages.c.role == "assistant")).scalar_one())
    return [[clip["voice_script"] for clip in source["story_plan"]["clips"]] for source in (draft, version, card["plan"])]


def test_speech_failure_gets_a_new_line_everywhere_the_plan_is_shown(client, monkeypatch):
    platform, plan, row, sent = failed_story(client, monkeypatch, ["native_speech_repetition"])
    assert platform._reword_failed_dialogue(row) is True
    assert lines(platform, plan) == [[NEW, SECOND]] * 3
    assert sent[0]["scene_index"] == 1 and sent[0]["issues"] == ["native_speech_repetition"]
    assert sent[0]["transcript"].startswith("Bảng của bộ Takit TK0")
    assert [scene["voice_script"] for scene in sent[0]["scenes"]] == [OLD, SECOND]


def test_audio_failures_keep_their_line_and_a_failed_rewrite_skips_the_render(client, monkeypatch):
    platform, plan, row, sent = failed_story(client, monkeypatch, ["native_audio_missing_speech"])
    assert platform._reword_failed_dialogue(row) is None and not sent
    monkeypatch.setattr(platform, "_native_speech_report", lambda *_: {"issues": ["native_speech_incomplete"], "metrics": {}})
    platform.http.close()
    platform.http = httpx.Client(base_url="http://example.test",
                                 transport=httpx.MockTransport(lambda _: httpx.Response(502, json={})))
    assert platform._reword_failed_dialogue(row) is False
    assert lines(platform, plan) == [[OLD, SECOND]] * 3

    rendered = []
    monkeypatch.setattr(platform, "present_story_batch", lambda _row: {"retryable": True})
    monkeypatch.setattr(platform, "generate", lambda *args, **kwargs: rendered.append(kwargs))

    def run_due_retry():
        with platform.engine.begin() as db:
            db.execute(update(story_batches).values(retry_after=1))
        platform.auto_retry_failed_stories()
        with platform.engine.connect() as db:
            return db.execute(select(story_batches.c.auto_retries, story_batches.c.retry_after)).one()

    # A failed rewrite never renders the old words and does not use up one of the three attempts.
    for _ in range(REWORD_FAILURE_LIMIT):
        attempts, retry_after = run_due_retry()
        assert not rendered and attempts == 0 and retry_after > 1
    assert run_due_retry()[0] == 1 and not rendered

    # Once a new line is written, the render runs on its own with the consent given at the start.
    platform.http.close()
    platform.http = httpx.Client(base_url="http://example.test",
                                 transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"voice_script": NEW})))
    for attempt in (2, 3):
        assert run_due_retry()[0] == attempt
    assert rendered == [{"confirm_paid": True}] * 2
    assert lines(platform, plan) == [[NEW, SECOND]] * 3
    run_due_retry()
    assert len(rendered) == 2  # three attempts at most


def test_printed_codes_in_a_transcript_match_their_spelled_line():
    from app.services.native_audio_quality import native_speech_issues, speech_tokens

    assert speech_tokens("Bộ Takit TK01, size XL") == speech_tokens("Bộ Takit tê ca không một, size ích xì e lờ")
    assert native_speech_issues("Bộ Takit tê ca không một mặc rất mát.", "Bộ Takit TK01 mặc rất mát.") == ([], 1.0)
    assert speech_tokens("Năm 2024") == ["nam", "hai", "nghin", "khong", "tram", "hai", "muoi", "bon"]
    issues, _ = native_speech_issues(OLD, "Bảng size của bộ Takit TK0 sửa lại, TK0. Bảng size của bộ Takit TK0")
    assert "native_speech_repetition" in issues
