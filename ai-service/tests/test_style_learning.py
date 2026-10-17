import json

import httpx
import pytest

from app.pipelines import style_learning
from app.pipelines.story_planner import create_book_story_plan
from app.pipelines.style_learning import (
    blueprint_format,
    learn_style,
    learned_style_instruction,
)
from tests.test_story_duration import Vault, mock_planner, story

BLUEPRINT = {
    "title": "Mở bằng tay đang làm", "summary": "Hook cận tay, cắt nhanh, kết bằng câu hỏi.",
    "language": "Tiếng Việt", "format": "POV", "narration_mode": "voice_over",
    "hook": {"technique": "Cận tay giữa hành động", "opening_visual": "Bàn tay mở hộp",
             "opening_line": "Câu mở đầu nguyên văn của video gốc", "why_it_works": "Gây tò mò"},
    "beats": [{"start_seconds": 0, "end_seconds": 1.5, "beat": "Hook", "shot": "Cận", "camera": "Cầm tay",
               "on_screen_text": "", "audio": "Giọng dẫn", "purpose": "Gây tò mò"}],
    "pacing": "Cắt mỗi giây ở phần mở đầu", "visual_style": "Sáng, ấm",
    "music": "", "editing": "Cắt nhảy",
    "rules": ["Open on a close-up of hands mid-action.", "Cut every second during the hook.",
              "End on a question to the viewer."],
    "avoid": ["Do not open on a static product shot."],
    "speech": {
        "addressing": "Tự xưng mình, gọi người xem là mấy mẹ", "language_register": "Thân mật, nhiều khẩu ngữ",
        "sentence_style": "Câu ngắn khoảng tám từ",
        "emotion_arc": "Hào hứng rồi chốt", "transitions": "Dùng từ nối ngắn như mà, thêm nữa",
        "devices": ["Rhetorical question in the hook."], "signature_words": ["nè", "luôn"],
        "hook_formula": "[gọi đối tượng] + [nỗi đau] + câu hỏi?", "cta_formula": "[lợi ích] + [bấm giỏ hàng] + nha",
        "script_rules": ["Address the viewer as mấy mẹ in the first sentence."],
    },
}


def assert_strict(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        for value in schema["properties"].values():
            assert_strict(value)
    if schema.get("type") == "array":
        assert_strict(schema["items"])


def test_blueprint_schema_is_strict():
    assert_strict(blueprint_format()["json_schema"]["schema"])


def test_learned_style_carries_technique_but_never_the_reference_words():
    instruction = learned_style_instruction(BLUEPRINT)
    assert "Open on a close-up of hands mid-action." in instruction and "Never copy the reference's words" in instruction
    assert "Câu mở đầu nguyên văn" not in instruction
    # The learned style must outrank the default creative rules that previously won.
    assert "OVERRIDES the default creative guidance" in instruction and "never invent prices" in instruction
    presenter = learned_style_instruction(BLUEPRINT | {"narration_mode": "on_screen_speaker"})
    assert "even when the product is for children" in presenter


@pytest.mark.asyncio
async def test_learn_style_sends_frames_with_timestamps_and_validates_output(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(BLUEPRINT)},
                                                      "finish_reason": "stop"}]})

    original = httpx.AsyncClient

    def client(*args, **kwargs):
        return original(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(style_learning.httpx, "AsyncClient", client)
    frame = "data:image/jpeg;base64," + "A" * 200
    result = await learn_style(
        frames=[{"time_seconds": 0.2, "image_data_url": frame}, {"time_seconds": 1.7, "image_data_url": frame}],
        transcript="Lời thoại", duration_seconds=3.0, cut_times=[1.5], audio_present=True,
        source_label="viral.mp4", vault=Vault(), model="gpt-4o-mini",
    )
    assert result["rules"] == BLUEPRINT["rules"]
    content = sent[0]["messages"][1]["content"]
    assert json.loads(content[0]["text"])["cut_times_seconds"] == [1.5]
    assert [part["text"] for part in content if part["type"] == "text"][1:] == ["Frame at 0.2s", "Frame at 1.7s"]
    assert sum(part["type"] == "image_url" for part in content) == 2


@pytest.mark.asyncio
async def test_planner_follows_learned_style_instead_of_a_random_format(monkeypatch):
    calls = mock_planner(monkeypatch, story([8]))
    result = await create_book_story_plan("Quảng cáo hộp quà", Vault(), "test-model",
                                          project_context={"style_reference": BLUEPRINT})
    system = calls[0]["messages"][0]["content"]
    assert "LEARNED STYLE REFERENCE" in system and "CREATIVE FORMAT FOR THIS PLAN" not in system
    assert "Câu mở đầu nguyên văn" not in json.dumps(calls[0]["messages"], ensure_ascii=False)
    assert result["creative_format"] == "learned_style"


def test_writing_style_reaches_dialogue_but_never_the_voice():
    blueprint = BLUEPRINT | {"speech_metrics": {"avg_words_per_sentence": 7.5, "particles": {"nè": 3},
                                                "words_per_second": 4.6}}
    instruction = learned_style_instruction(blueprint)
    assert "WRITING STYLE OF THE LINES" in instruction and "gọi người xem là mấy mẹ" in instruction
    assert '"avg_words_per_sentence": 7.5' in instruction and '"nè": 3' in instruction
    # Delivery measurements and the reference voice never steer the new video.
    assert "words_per_second" not in instruction and "never the reference speaker's voice" in instruction
    assert "Câu mở đầu nguyên văn" not in instruction and "Never reuse a sentence" in instruction
