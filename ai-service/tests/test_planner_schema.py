import copy
import json
import re

import httpx
import pytest

from app.core.voice_presets import get_voice_preset
from app.pipelines.planner_schema import (
    grounding_response_format,
    speech_word_target,
    spoken_schema,
    story_response_format,
    unpack_scenes,
)
from app.pipelines.story_duration import duration_policy
from app.pipelines.story_planner import BookStoryPlan, create_book_story_plan, voice_words

LINE = "Mình mời bạn cùng tìm hiểu đôi giày này để xem có thật sự phù hợp với nhu cầu của mình không."
NATURAL_LINES = {
    4: "Chiếc tai nghe này có phù hợp với bạn không?",
    6: "Cùng mình tìm hiểu chiếc tai nghe này qua những nhu cầu sử dụng hằng ngày.",
    8: "Tai nghe này phù hợp ra sao khi nghe nhạc và chơi game, mình sẽ cùng bạn tìm hiểu theo nhu cầu nhé.",
}


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_schema_only_enforces_clean_text_not_syllable_quota(duration):
    pattern = spoken_schema(duration)["pattern"]
    for count in (1, 2, 8, 18, 19, 23, 24, 30, 51, 75):
        line = " ".join(["mình"] * count) + "."
        assert re.fullmatch(pattern, line)
    valid = NATURAL_LINES[8]
    for invalid in ("**" + valid, valid + " 😊", valid.replace("mình", "20", 1), valid.replace(" ", "\n", 1)):
        assert not re.fullmatch(pattern, invalid)


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_schema_describes_natural_complete_speech_instead_of_target_count(duration):
    schema = spoken_schema(duration)
    assert re.fullmatch(schema["pattern"], NATURAL_LINES[duration])
    assert "never filler" in schema["description"]


@pytest.mark.parametrize("duration,expected", [(4, (16, 18, 20)), (8, (33, 37, 40)), (10, (41, 46, 50))])
def test_planner_schema_caps_tiktok_pace_at_five_words_per_second(duration, expected):
    low, target, high = expected
    assert speech_word_target(duration) == expected
    schema = spoken_schema(duration, paced=True)
    assert f"about {target} words, between {low} and {high} and never more than {high}" in schema["description"]
    assert re.fullmatch(schema["pattern"], " ".join(["mình"] * low) + ".")
    assert re.fullmatch(schema["pattern"], " ".join(["mình"] * high) + ".")
    # Loose ceiling so the model never has to glue words to fit; far longer lines are still rejected.
    assert re.fullmatch(schema["pattern"], " ".join(["mình"] * (high + 1)) + ".")
    assert not re.fullmatch(schema["pattern"], " ".join(["mình"] * round(target * 1.4 + 1)) + ".")
    # Validation, grounding and TTS narration keep the uncapped clean-text check.
    assert re.fullmatch(spoken_schema(duration)["pattern"], " ".join(["mình"] * 120) + ".")


def assert_strict_objects(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for value in schema["properties"].values():
            assert_strict_objects(value)
    for alternative in schema.get("anyOf", []):
        assert_strict_objects(alternative)
    for definition in schema.get("$defs", {}).values():
        assert_strict_objects(definition)


def resolve(schema, root):
    return root["$defs"][schema["$ref"].split("/")[-1]] if "$ref" in schema else schema


@pytest.mark.parametrize("durations,expected", [([8], [8, 8, 8]), ([4, 6, 8], [8, 6, 6])])
def test_requested_timeline_is_bound_to_each_key_before_generation(durations, expected):
    preset = get_voice_preset("podcast")
    format = story_response_format(durations, preset, duration_policy(20, durations))
    assert format["type"] == "json_schema" and format["json_schema"]["strict"] is True
    schema = format["json_schema"]["schema"]
    assert_strict_objects(schema)
    scenes = schema["properties"]["scenes"]["properties"]
    assert list(scenes) == ["scene_1", "scene_2", "scene_3"]
    for scene, duration in zip(scenes.values(), expected, strict=True):
        scene = resolve(scene, schema)
        assert scene["properties"]["duration_seconds"]["enum"] == [duration]
        assert scene["properties"]["voice_script"] == spoken_schema(duration, paced=True)
    assert "full_voice_script" not in schema["properties"] and "duration_target_seconds" not in schema["properties"]


def test_no_requested_total_allows_one_to_five_scenes_with_supported_durations():
    schema = story_response_format([4, 6, 8], get_voice_preset("koc"), None)["json_schema"]["schema"]
    assert_strict_objects(schema)
    choices = schema["properties"]["scenes"]["anyOf"]
    assert [len(choice["required"]) for choice in choices] == [1, 2, 3, 4, 5]
    branches = choices[0]["properties"]["scene_1"]["anyOf"]
    assert [resolve(branch, schema)["properties"]["duration_seconds"]["enum"] for branch in branches] == [[4], [6], [8]]
    assert len(json.dumps(schema)) < 10000


def test_grounding_contract_uses_saved_duration_without_speech_quota():
    clips = [{"index": 1, "duration_seconds": 6}, {"index": 2, "duration_seconds": 8}]
    schema = grounding_response_format(clips)["json_schema"]["schema"]
    assert_strict_objects(schema)
    for clip in clips:
        assert schema["properties"]["scenes"]["properties"][f"scene_{clip['index']}"]["properties"]["voice_script"] == spoken_schema(clip["duration_seconds"])


@pytest.mark.parametrize("scenes", [[], {}, {"scene_2": {}}, {"scene_1": None}, {"scene_1": {}, "scene_3": {}}])
def test_bad_scene_keys_are_rejected_without_synthesizing_dialogue(scenes):
    with pytest.raises(ValueError):
        unpack_scenes({"scenes": scenes})


def output():
    return {
        "video_type": "product_review", "book_title": "", "audience": "Người xem quan tâm giày",
        "core_value": "Cùng tìm hiểu đôi giày phù hợp với nhu cầu cá nhân.",
        "visual_continuity": "Same adult presenter, shoes, wardrobe and locked camera in the same workshop.",
        "visual_bible": "Same adult presenter, shoes, wardrobe and locked camera in the same workshop.",
        "voice_bible": "Vietnamese adult woman; Hanoi accent; warm natural timbre; reflective clear conversation; pace 2.6-3.0 syllables/s.",
        "reference_image_prompt": "An adult Vietnamese presenter holds unbranded shoes in a realistic workshop.",
        "scenes": {"scene_1": {"beat": "Giới thiệu", "purpose": "Quan sát đôi giày",
            "video_prompt": "The same adult presenter holds the same shoes at chest height, looks into the camera and speaks with stable hands.",
            "voice_script": LINE, "start_state": "Shoes at chest height.", "end_state": "Shoes at chest height.",
            "transition_intent": "continue", "duration_seconds": 8}},
    }


class Vault:
    def get(self, _name):
        return "unit-test-not-real-key"


def mock_response(monkeypatch, payload):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert len(calls) == 1, "Planner must not rewrite its first-pass output"
        return httpx.Response(200, json=payload)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls


@pytest.mark.asyncio
async def test_keyed_first_pass_is_converted_without_extra_request_or_changed_words(monkeypatch):
    original = output()
    before = copy.deepcopy(original)
    calls = mock_response(monkeypatch, {"id": "initial-plan", "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(original, ensure_ascii=False)},
    }]})
    result = await create_book_story_plan("Tạo video giày tám giây", Vault(), "gpt-4o-mini")
    assert result["clips"][0]["voice_script"] == result["full_voice_script"] == LINE
    assert result["duration_target_seconds"] == 8 and set(result) == set(BookStoryPlan.model_fields)
    assert original == before and len(calls) == 1
    assert voice_words(result["full_voice_script"]) == voice_words(LINE)
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    pattern = calls[0]["response_format"]["json_schema"]["schema"]["properties"]["voice_bible"]["pattern"]
    assert re.fullmatch(pattern, original["voice_bible"])
    assert re.fullmatch(pattern, original["voice_bible"].replace("; pace 2.6-3.0 syllables/s.", "."))


@pytest.mark.asyncio
async def test_complete_headphone_script_keeps_phrases_and_sentence_endings_in_one_request(monkeypatch):
    lines = [
        "Hôm nay mình muốn giới thiệu chiếc tai nghe này đến những bạn thường nghe nhạc hoặc chơi game mỗi ngày.",
        NATURAL_LINES[8],
        "Bạn thích nghe nhạc hay chơi game, hãy chia sẻ nhu cầu để mình cùng bạn tìm chiếc tai nghe phù hợp.",
    ]
    original = output()
    template = original["scenes"]["scene_1"]
    original["scenes"] = {f"scene_{index}": template | {"voice_script": line} for index, line in enumerate(lines, 1)}
    before = copy.deepcopy(original)
    calls = mock_response(monkeypatch, {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(original, ensure_ascii=False),
    }}]})
    result = await create_book_story_plan("Tạo video giới thiệu tai nghe 24s", Vault(), "gpt-4o-mini")
    assert [clip["voice_script"] for clip in result["clips"]] == lines
    assert result["full_voice_script"] == " ".join(lines) and result["duration_target_seconds"] == 24
    assert len(lines[1].split()) == 23 and "chơi game," in result["clips"][1]["voice_script"]
    assert len(calls) == 1 and original == before


LONG_LINE = (
    "Mẹ đang chọn đồ Tết cho bé mà cứ nhìn áo rồi lại nhìn quần, vẫn khó tưởng tượng mặc lên sẽ ra sao? "
    "Đây nè, cho bé mặc trọn bộ là thấy cách phối rõ hơn liền. Bé trai từ hai đến sáu tuổi có thể cân nhắc "
    "bộ raglan tay ngắn này, với hình gấu ngộ nghĩnh giữa áo."
)
SHORT_LINE = (
    "Mẹ cứ nhìn áo rồi lại nhìn quần mà khó hình dung? Đây nè, bé mặc trọn bộ là thấy cách phối liền. "
    "Bé trai hai đến sáu tuổi mặc bộ raglan hình gấu này là xinh lắm."
)


def planner_sequence(monkeypatch, responses):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return responses[len(calls) - 1]
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls


def first_pass_with(line):
    plan = output()
    plan["scenes"]["scene_1"]["voice_script"] = line
    return httpx.Response(200, json={"id": "initial-plan", "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(plan, ensure_ascii=False)},
    }]})


@pytest.mark.asyncio
async def test_line_over_its_clip_maximum_is_shortened_in_one_extra_request(monkeypatch):
    assert len(LONG_LINE.split()) > speech_word_target(8)[2] >= len(SHORT_LINE.split())
    calls = planner_sequence(monkeypatch, [first_pass_with(LONG_LINE), httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps({"scene_1": SHORT_LINE}, ensure_ascii=False)},
    }]})])
    result = await create_book_story_plan("Tạo video bộ đồ tám giây", Vault(), "gpt-4o-mini")
    assert result["clips"][0]["voice_script"] == result["full_voice_script"] == SHORT_LINE
    assert len(calls) == 2
    request = json.loads(calls[1]["messages"][1]["content"])
    assert request["lines_to_shorten"][0]["max_words"] == speech_word_target(8)[2]
    assert calls[1]["response_format"]["json_schema"]["schema"]["required"] == ["scene_1"]


@pytest.mark.asyncio
async def test_failed_shortening_keeps_the_first_pass_instead_of_failing_the_plan(monkeypatch):
    calls = planner_sequence(monkeypatch, [first_pass_with(LONG_LINE), httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": None, "refusal": "No"},
    }]})])
    result = await create_book_story_plan("Tạo video bộ đồ tám giây", Vault(), "gpt-4o-mini")
    assert result["clips"][0]["voice_script"] == LONG_LINE and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,reason", [
    ({"finish_reason": "stop", "message": {"content": None, "refusal": "No"}}, "planner_refused"),
    ({"finish_reason": "length", "message": {"content": "{}"}}, "planner_output_truncated"),
    ({"finish_reason": "content_filter", "message": {"content": None}}, "planner_refused"),
    ({"finish_reason": "stop", "message": {"content": None}}, "planner_empty_output"),
])
async def test_incomplete_or_refused_first_pass_is_not_repaired(monkeypatch, choice, reason):
    calls = mock_response(monkeypatch, {"choices": [choice]})
    with pytest.raises(RuntimeError, match="^" + reason + "$"):
        await create_book_story_plan("Tạo video giới thiệu giày", Vault(), "gpt-4o-mini")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {"choices": [None]}, {"choices": [{"message": None}]}])
async def test_malformed_provider_envelope_is_classified_without_an_edit(monkeypatch, payload):
    calls = mock_response(monkeypatch, payload)
    with pytest.raises(RuntimeError, match=r"^planner_invalid_response$"):
        await create_book_story_plan("Tạo video giới thiệu giày", Vault(), "gpt-4o-mini")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("durations", [[8], [4, 6, 8]])
async def test_requested_total_and_each_scene_budget_are_written_in_one_pass(monkeypatch, durations):
    from app.schemas.provider import VideoProviderCapabilities
    original = output()
    schedule = duration_policy(20, durations)["suggested_scene_durations"]
    original["scenes"] = {f"scene_{index}": original["scenes"]["scene_1"] | {
        "duration_seconds": duration,
        "voice_script": NATURAL_LINES[duration],
    } for index, duration in enumerate(schedule, 1)}
    calls = mock_response(monkeypatch, {"choices": [{"message": {"content": json.dumps(original, ensure_ascii=False)}}]})
    result = await create_book_story_plan("Video giày 20s", Vault(), "gpt-4o-mini", video_capabilities=(
        VideoProviderCapabilities(provider="poyo", model="veo3.1-fast-official", generation_durations=durations)
    ))
    assert len(calls) == 1 and result["duration_target_seconds"] == sum(schedule)
    assert [clip["duration_seconds"] for clip in result["clips"]] == schedule
    assert result["full_voice_script"] == " ".join(clip["voice_script"] for clip in result["clips"])
