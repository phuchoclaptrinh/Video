import json
import unicodedata

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.credential_vault import CredentialVault
from app.core.voice_presets import get_voice_preset, voice_preset_for_request
from app.pipelines.story_duration import (
    duration_policy,
    requested_duration_seconds,
)
from app.pipelines.story_planner import BookStoryPlan, create_book_story_plan, without_speech_rate
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.schemas.provider import VideoProviderCapabilities


@pytest.fixture(autouse=True)
def default_voice_pace(monkeypatch):
    monkeypatch.setenv("VOICE_PACE", "podcast")


@pytest.mark.parametrize("prompt,expected", [
    ("Tạo video KOC dài 20s", 20),
    ("Video dài 20 giây giới thiệu sản phẩm", 20),
    ("Tao video 20 giay", 20),
    ("Create a video lasting 20 seconds", 20),
    ("Tạo video 0,5 phút", 30),
    ("Tạo video 20.5s", 21),
    ("Tạo video 20s, mỗi cảnh 8s", 20),
    ("Mỗi cảnh 8 giây, video 20 giây", 20),
    ("Tạo video 20s, mỗi cảnh dài khoảng 8 giây", 20),
    ("Video 20 giây, cảnh 1 dài 8 giây", 20),
    ("Mỗi cảnh 8 giây", None),
    ("Mỗi clip dài 8 giây", None),
    ("Cảnh 1 dài 8 giây, cảnh 2 dài 8 giây", None),
    ("Hai cảnh riêng: 8s và 12s", None),
    ("KOC nữ 20 tuổi review sách", None),
    ("Sản phẩm giảm giá 20%, video phong cách tự nhiên", None),
    ("Tạo video với độ trễ 20ms", None),
    (unicodedata.normalize("NFD", "Video dài 20 giây"), 20),
])
def test_extracts_total_duration_without_confusing_age_or_per_scene_time(prompt, expected):
    assert requested_duration_seconds(prompt) == expected


@pytest.mark.parametrize("target,allowed,total,count", [
    (20, [8], 24, 3), (8, [8], 8, 1), (32, [8], 32, 4), (40, [8], 40, 5),
    (20, [4, 6, 8], 20, 3), (12, [4, 6, 8], 12, 2), (10, [4, 6, 8], 10, 2),
    (20, list(range(4, 13)), 20, 2), (3, [8], 8, 1),
])
def test_schedule_uses_supported_clips_not_a_single_clip_limit(target, allowed, total, count):
    policy = duration_policy(target, allowed)
    assert policy["requested_duration_seconds"] == target
    assert policy["planned_duration_seconds"] == total and policy["scene_count"] == count
    assert sum(policy["suggested_scene_durations"]) == total
    assert len(policy["suggested_scene_durations"]) == count
    assert all(duration in allowed for duration in policy["suggested_scene_durations"])


@pytest.mark.parametrize("target,allowed", [(41, [8]), (60, [4, 6, 8]), (20, []), (0, [8]), (601, [120])])
def test_unachievable_duration_does_not_silently_become_eight_seconds(target, allowed):
    with pytest.raises(RuntimeError, match=r"^planner_duration_unsupported$"):
        duration_policy(target, allowed)


def story(durations):
    lines = [
        "Hôm nay mình chia sẻ một cuốn sách giúp nhìn lại những thói quen nhỏ mỗi ngày.",
        "Điều mình thích là cách mình có thể bắt đầu bằng một việc đơn giản và vừa sức.",
        "Bạn có thể thử chọn một thói quen phù hợp rồi theo dõi sự thay đổi của bản thân.",
    ]
    clips = [{"index": index, "beat": f"Part {index}", "duration_seconds": duration,
              "video_prompt": "The same seated presenter speaks calmly in the same locked shot.",
              "voice_script": line}
             for index, (duration, line) in enumerate(zip(durations, lines, strict=False), 1)]
    return {"core_value": "A coherent Vietnamese book discussion.", "clips": clips,
            "visual_continuity": "One seated adult presenter, same book and locked camera throughout.",
            "full_voice_script": " ".join(clip["voice_script"] for clip in clips)}


class Vault:
    def get(self, _name):
        return "unit-test-not-real-key"


@pytest.mark.parametrize("voice", [None, "", "   ", {}])
def test_planner_missing_voice_profile_defaults_to_active_preset(voice):
    output = story([8]) | {"voice_bible": voice}
    profile = BookStoryPlan.model_validate(output).voice_bible
    assert profile == get_voice_preset().default_voice_profile
    assert "minimal breathiness" in profile and "female" not in profile
    assert len(profile) <= 600


def mock_planner(monkeypatch, *outcomes):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) > len(outcomes):
            raise AssertionError("Duration repair exceeded its bounded request budget")
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(200, json={"id": "duration-test", "choices": [
            {"message": {"content": json.dumps(outcome, ensure_ascii=False)}}
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("app.pipelines.planner_transport.NETWORK_RETRY_DELAY_SECONDS", 0)
    return calls


@pytest.mark.asyncio
async def test_twenty_second_prompt_on_eight_second_model_gets_three_connected_clips(monkeypatch):
    calls = mock_planner(monkeypatch, story([8, 8, 8]))
    result = await create_book_story_plan("Tạo video review sách 20s", Vault(), "gpt-4o-mini")
    assert result["duration_target_seconds"] == 24 and result["requested_duration_seconds"] == 20
    assert len(result["clips"]) == 3 and len(calls) == 1
    assert '"scene_count": 3' in calls[0]["messages"][0]["content"]
    assert '"planned_duration_seconds": 24' in calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_model_with_shorter_clip_options_can_plan_exactly_twenty_seconds(monkeypatch):
    calls = mock_planner(monkeypatch, story([6, 8, 6]))
    caps = VideoProviderCapabilities(provider="poyo", model="veo3.1-fast-official", generation_durations=[4, 6, 8])
    result = await create_book_story_plan("Tạo video dài 20 giây", Vault(), "gpt-4o-mini", video_capabilities=caps)
    assert result["duration_target_seconds"] == 20 and result["requested_duration_seconds"] == 20
    assert [clip["duration_seconds"] for clip in result["clips"]] == [6, 8, 6]
    assert len(calls) == 1
    system = calls[0]["messages"][0]["content"]
    assert "Hanoi Northern accent" in system and "syllables/s" not in system and "2.6" not in system
    assert "3.2" not in system and "brisk" not in system and "lively" not in system
    assert "No Markdown, emoji" in system
    assert "scene_speech_budgets" not in system and "target_syllables" not in system
    assert "word range" not in system and "about five words per second" in system


@pytest.mark.asyncio
async def test_wrong_initial_duration_is_rejected_without_replanning(monkeypatch):
    calls = mock_planner(monkeypatch, story([8]))
    with pytest.raises(RuntimeError, match=r"^planner_duration_mismatch$"):
        await create_book_story_plan("Tạo video dài 20 giây", Vault(), "gpt-4o-mini")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_network_retry_repeats_same_first_pass_contract_not_a_repair(monkeypatch):
    calls = mock_planner(monkeypatch, httpx.ReadError("reset"), story([8, 8, 8]))
    result = await create_book_story_plan("Tạo video dài 20 giây", Vault(), "gpt-4o-mini")
    assert len(calls) == 2 and calls[0] == calls[1]
    assert result["duration_target_seconds"] == 24
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,explicit,previous,expected", [
    ("Video 20s", 32, 40, 32),
    ("Video 20s", None, 40, 20),
    ("Giữ nội dung nhưng đổi thành giọng miền Nam", None, 20, 20),
])
async def test_duration_precedence_explicit_then_latest_prompt_then_saved_target(monkeypatch, prompt, explicit, previous, expected):
    count = duration_policy(expected, [8])["scene_count"]
    output = story([8] * min(count, 3))
    if count == 4:
        extra = dict(output["clips"][-1], index=4, voice_script="Mình sẽ tiếp tục quan sát những thay đổi nhỏ này mỗi ngày và chia sẻ thêm cùng tất cả các bạn.")
        output["clips"].append(extra)
        output["full_voice_script"] += " " + extra["voice_script"]
    calls = mock_planner(monkeypatch, output)
    result = await create_book_story_plan(prompt, Vault(), "gpt-4o-mini",
                                         project_context={"previous_target_duration_seconds": previous},
                                         target_duration_seconds=explicit)
    assert result["requested_duration_seconds"] == expected and len(calls) == 1


@pytest.mark.asyncio
async def test_no_duration_request_retains_short_story_behavior(monkeypatch):
    calls = mock_planner(monkeypatch, story([8]))
    result = await create_book_story_plan("Một ý tưởng video ngắn", Vault(), "gpt-4o-mini")
    assert result["duration_target_seconds"] == 8 and result["requested_duration_seconds"] is None
    assert len(calls) == 1
    assert result["voice_bible"] == get_voice_preset().default_voice_profile


@pytest.mark.asyncio
@pytest.mark.parametrize("preset_name", ["podcast", "koc"])
async def test_planner_injects_only_selected_voice_policy_without_changing_json_schema(monkeypatch, preset_name):
    monkeypatch.setenv("VOICE_PACE", preset_name)
    calls = mock_planner(monkeypatch, story([8]))
    result = await create_book_story_plan("KOC nữ Việt Nam 18-25 tuổi giới thiệu sách", Vault(), "test-model")
    preset = get_voice_preset()
    system = calls[0]["messages"][0]["content"]
    assert without_speech_rate(preset.default_voice_profile) in system and preset.delivery in system
    assert "syllables/s" not in system
    assert "scene_speech_budgets" not in system and "target_syllables" not in system
    assert "25-30" not in system
    other = get_voice_preset("koc" if preset_name == "podcast" else "podcast")
    assert f"{other.pace_min}-{other.pace_max}" not in system
    assert str(other.pace_min) not in system
    assert result["voice_bible"] == preset.default_voice_profile
    assert set(result) == set(BookStoryPlan.model_fields)
    assert "preset" not in result and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("brief", ["KOC nữ nói nhanh hơn", "Nói chậm, rõ chữ", "Giọng Bắc, tốc độ 1,8-2,2 âm tiết/giây"])
async def test_explicit_speech_request_changes_delivery_not_dialogue_length_quota(monkeypatch, brief):
    calls = mock_planner(monkeypatch, story([8]))
    result = await create_book_story_plan(brief, Vault(), "test-model")
    preset = voice_preset_for_request(brief)
    system = calls[0]["messages"][0]["content"]
    assert "scene_speech_budgets" not in system and "target_syllables" not in system
    assert without_speech_rate(preset.default_voice_profile) in system and "syllables/s" not in system
    assert result["voice_bible"] == preset.default_voice_profile and len(calls) == 1


@pytest.mark.asyncio
async def test_duration_above_five_clip_limit_makes_no_llm_call(monkeypatch):
    calls = mock_planner(monkeypatch)
    with pytest.raises(RuntimeError, match=r"^planner_duration_unsupported$"):
        await create_book_story_plan("Tạo video dài 60 giây", Vault(), "gpt-4o-mini")
    assert calls == []


@pytest.mark.parametrize("target,durations,status,reason", [
    (20, [8, 8, 8], 200, None), (60, [], 422, "planner_duration_unsupported"),
])
def test_internal_endpoint_accepts_total_duration_separate_from_clip_duration(tmp_path, monkeypatch, target, durations, status, reason):
    calls = mock_planner(monkeypatch, story(durations))
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda _self, _name: "unit-test-not-real-key")
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        response = client.post("/internal/v1/planner/story", json={"prompt": "A book video", "target_duration_seconds": target},
                               headers={"X-Internal-Key": "test-secret"})
    assert response.status_code == status
    if reason:
        assert response.json()["detail"] == reason and calls == []
    else:
        assert response.json()["plan"]["duration_target_seconds"] == 24
        assert response.json()["plan"]["requested_duration_seconds"] == 20 and len(calls) == 1
