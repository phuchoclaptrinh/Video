import copy

import pytest

from app.pipelines.dialogue_timing import validate_scene_dialogue

LINE = "Mình mời bạn cùng tìm hiểu đôi giày này để xem có thật sự phù hợp với nhu cầu của mình không."


@pytest.mark.parametrize("duration", [4, 6, 8])
@pytest.mark.parametrize("line", ["Ừ.", "Đừng chọn tai nghe chỉ vì nhìn đẹp.", LINE, " ".join(["mình"] * 75) + "."])
def test_plain_dialogue_is_preserved_without_duration_or_syllable_quota(duration, line):
    clips = [{"index": 1, "duration_seconds": duration, "voice_script": line}]
    before = copy.deepcopy(clips)
    assert validate_scene_dialogue(clips) == before and clips == before


@pytest.mark.parametrize("line", ["", " ", " ".join(["mình"] * 450)])
def test_empty_or_oversized_data_is_rejected_without_rewriting(line):
    clips = [{"index": 1, "duration_seconds": 8, "voice_script": line}]
    before = copy.deepcopy(clips)
    with pytest.raises(RuntimeError, match=r"^planner_spoken_text_invalid$"):
        validate_scene_dialogue(clips)
    assert clips == before


@pytest.mark.parametrize("symbol", ["**", "😊", "[", "/", "…", "\\n", "  ", "20", "\u00d7", "\u00f7"])
def test_spoken_symbols_and_non_spoken_tokens_are_rejected(symbol):
    clips = [{"index": 1, "duration_seconds": 8, "voice_script": LINE.replace("Mình", symbol + "Mình", 1)}]
    with pytest.raises(RuntimeError, match=r"^planner_spoken_text_invalid$"):
        validate_scene_dialogue(clips)


def test_validation_preserves_all_scene_words_without_mutating_input():
    clips = [{"index": 1, "duration_seconds": 8, "voice_script": "Một dòng cũ quá ngắn."},
             {"index": 2, "duration_seconds": 8, "voice_script": LINE}]
    before = copy.deepcopy(clips)
    result = validate_scene_dialogue(clips)
    assert result == before and clips == before
