import json

import httpx
import pytest

from app.core.voice_presets import get_voice_preset
from app.pipelines.reference_grounding import GroundedClip, ground_book_story
from app.pipelines.story_planner import voice_words


@pytest.mark.asyncio
@pytest.mark.parametrize("preset_name", ["podcast", "koc"])
async def test_ground_book_story_sends_image_and_keeps_scene_order(monkeypatch, preset_name):
    preset = get_voice_preset(preset_name)
    voice = preset.default_voice_profile
    monkeypatch.setenv("VOICE_PACE", "koc" if preset_name == "podcast" else "podcast")
    calls = []

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        narration = "Mình đang cầm cuốn Những điều nhỏ bé tạo nên hạnh phúc trên chuyến xe hôm nay và chia sẻ cùng mọi người"
        answer = {
            "book_title": "Những điều nhỏ bé tạo nên hạnh phúc",
            "clips": [{
                "index": 1,
                "video_prompt": "The same host holds the same book inside the same car.",
                "voice_script": narration + ".",
            }],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.pipelines.reference_grounding.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await ground_book_story(
        "data:image/jpeg;base64,YWJj",
        {"voice_bible": voice,
         "clips": [{"index": 1, "beat": "intro", "duration_seconds": 6, "video_prompt": "Host holds a book in a car.",
                    "voice_script": "Cuốn [Tên sách] là gì?"}]},
        "Giới thiệu sách trong ô tô", "test-key", "gpt-4o-mini",
    )
    assert result["book_title"] == "Những điều nhỏ bé tạo nên hạnh phúc"
    assert calls[0]["messages"][1]["content"][1]["image_url"]["detail"] == "high"
    assert calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;")
    context = json.loads(calls[0]["messages"][1]["content"][0]["text"])
    assert context["voice_bible"] == voice
    assert context["scenes"][0]["duration_seconds"] == 6
    assert "speech_budget" not in context["scenes"][0]
    system = calls[0]["messages"][0]["content"]
    assert voice in system and "no Markdown" in system
    assert "no word or syllable quota" in system
    assert "preserve the existing hook" in system.replace("\n", " ")
    other = get_voice_preset("koc" if preset_name == "podcast" else "podcast")
    assert f"{other.pace_min}-{other.pace_max}" not in system


@pytest.mark.parametrize("count", [1, 2, 12, 24, 28, 36, 42, 50, 75])
def test_grounding_supports_speech_for_four_six_eight_and_twelve_second_scenes(count):
    clip = GroundedClip(index=1, video_prompt="The same host holds the book in the same room.", voice_script=" ".join(["sách"] * count))
    assert len(clip.voice_script.split()) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("unfilled", ["[Tên sách]", "Tên sách"])
async def test_ground_book_story_rejects_unfilled_placeholder(monkeypatch, unfilled):
    async def handler(request):
        answer = {"book_title": "", "clips": [{
            "index": 1,
            "video_prompt": "The same host holds a book inside the same car.",
            "voice_script": f"Mình sẽ giới thiệu cuốn {unfilled} cùng mọi người hôm nay.",
        }]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.pipelines.reference_grounding.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(RuntimeError, match="reference_grounding_placeholders"):
        await ground_book_story(
            "data:image/jpeg;base64,YWJj",
            {"clips": [{"index": 1, "beat": "intro", "video_prompt": "Host holds a book in a car.",
                        "voice_script": "Cuốn [Tên sách] là gì?"}]},
            "Giới thiệu sách trong ô tô", "test-key", "gpt-4o-mini",
        )


@pytest.mark.asyncio
async def test_grounding_keeps_short_dialogue_without_a_quota_or_second_edit(monkeypatch):
    calls = []
    visual = "The same host holds the same book inside the same parked car."
    def handler(request):
        calls.append(json.loads(request.content))
        assert len(calls) == 1, "Grounding must not trigger an extra dialogue edit"
        answer = {"book_title": "", "scenes": {"scene_1": {
            "video_prompt": visual, "voice_script": "Hãy cùng mình khám phá nhé!",
        }}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    result = await ground_book_story(
        "data:image/jpeg;base64,YWJj", {"voice_bible": get_voice_preset("podcast").default_voice_profile,
        "clips": [{"index": 1, "duration_seconds": 8, "video_prompt": visual, "voice_script": "Giới thiệu cuốn sách này."}]},
        "Giới thiệu cuốn sách trên tay", "test-key", "test-model",
    )
    assert result["clips"][0]["voice_script"] == "Hãy cùng mình khám phá nhé!"
    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_grounding_preserves_complete_three_scene_dialogue_without_extra_request(monkeypatch):
    lines = [
        "Có những ngày mình thấy mệt mỏi mà chẳng hiểu vì sao, cứ lặng lẽ làm cho xong mọi việc trong ngày.",
        "Cho đến khi đọc cuốn sách này mình muốn dành thời gian nhìn lại những điều đã diễn ra trong ngày.",
        "Và lắng nghe chính mình nhiều hơn một chút để hiểu điều gì đang cần được thay đổi trong cuộc sống.",
    ]
    clips = [{"index": index, "duration_seconds": 8,
              "video_prompt": "The same host holds the same book with body stable while speaking.",
              "voice_script": line} for index, line in enumerate(lines, 1)]
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        answer = {"book_title": "", "clips": [{k: v for k, v in clip.items() if k != "duration_seconds"} for clip in clips]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    result = await ground_book_story("data:image/jpeg;base64,YWJj", {
        "voice_bible": get_voice_preset("podcast").default_voice_profile, "clips": clips,
    }, "Chia sẻ về cuốn sách trên tay", "test-key", "test-model")
    assert len(calls) == 1 and "Prefer a complete thought" in calls[0]["messages"][0]["content"]
    assert [clip["voice_script"][-1] for clip in result["clips"]] == [".", ".", "."]
    assert result["clips"][1]["voice_script"].startswith("Cho đến khi")
    assert result["clips"][2]["voice_script"].startswith("Và ")
    assert voice_words(" ".join(clip["voice_script"] for clip in result["clips"])) == voice_words(" ".join(lines))
