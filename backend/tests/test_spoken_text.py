import copy
import unicodedata

import pytest

from app.services.spoken_text import clean_spoken_text, clean_story_dialogue


@pytest.mark.parametrize("raw,expected", [
    ('**Hôm nay** mình chia sẻ “cuốn sách này” 😊...', "Hôm nay mình chia sẻ cuốn sách này."),
    ("# Mở đầu\n- `Mình` thích [cuốn sách này](https://example.com/book)!", "Mở đầu Mình thích cuốn sách này!"),
    ("Mình\u200b thích\u200d nhịp nói rõ ràng ❤️.", "Mình thích nhịp nói rõ ràng."),
    (unicodedata.normalize("NFD", "Giọng Bắc rõ, tự nhiên và đầy đủ dấu."), "Giọng Bắc rõ, tự nhiên và đầy đủ dấu."),
    ("Giá $12.50, giảm 20%, còn 250.000₫.", "Giá 12.50 đô la Mỹ, giảm 20 phần trăm, còn 250.000 đồng."),
    ("Số dư -$5, $-10 và €2,5.", "Số dư âm 5 đô la Mỹ, âm 10 đô la Mỹ và 2,5 euro."),
    ("Âm lượng -2,5, nhiệt độ \u22125°C, tăng 2+3=5.", "Âm lượng âm 2,5, nhiệt độ âm 5 độ C, tăng 2 cộng 3 bằng 5."),
    ("Dùng trong 2\u20133 ngày, từ 5-10 phút.", "Dùng trong 2 đến 3 ngày, từ 5 đến 10 phút."),
    ("Ngày 08/03/2026 và tỷ lệ 2/3.", "Ngày 08 tháng 03 năm 2026 và tỷ lệ 2 trên 3."),
    ("Hẹn vào 08/03/2026.", "Hẹn vào ngày 08 tháng 03 năm 2026."),
    ("Mình & bạn: cùng đọc; không vội…", "Mình và bạn, cùng đọc, không vội."),
    ("Cuốn [Tên sách] trên tay.", "Cuốn Tên sách trên tay."),
    ("Hôm nay&nbsp;mình &#273;ọc sách.", "Hôm nay mình đọc sách."),
    (" , **Mình** vẫn đang kể câu chuyện này, ", "Mình vẫn đang kể câu chuyện này,"),
    (" *** 🎉 ... !!! \u200d ", ""),
    ("% $ & + @ 🎉", ""),
])
def test_clean_spoken_text_preserves_vietnamese_and_spoken_meaning(raw, expected):
    assert clean_spoken_text(raw) == expected
    assert clean_spoken_text(expected) == expected


def test_clean_story_keeps_partition_and_visual_fields_without_mutating_input():
    plan = {"duration_target_seconds": 16, "voice_bible": "Northern adult speaker", "clips": [
        {"index": 1, "duration_seconds": 8, "video_prompt": "Keep [prop] and camera fixed.",
         "voice_script": "**Hôm nay** mình nói rõ ràng 😊."},
        {"index": 2, "duration_seconds": 8, "video_prompt": "Continue the same shot.",
         "voice_script": "Giá $5, giảm 10%."},
    ]}
    plan["full_voice_script"] = " ".join(clip["voice_script"] for clip in plan["clips"])
    original = copy.deepcopy(plan)
    cleaned = clean_story_dialogue(plan)
    assert plan == original
    assert cleaned["full_voice_script"] == "Hôm nay mình nói rõ ràng. Giá 5 đô la Mỹ, giảm 10 phần trăm."
    assert cleaned["full_voice_script"] == " ".join(clip["voice_script"] for clip in cleaned["clips"])
    assert cleaned["duration_target_seconds"] == 16 and cleaned["voice_bible"] == plan["voice_bible"]
    for before, after in zip(original["clips"], cleaned["clips"], strict=True):
        assert before["video_prompt"] == after["video_prompt"] and before["duration_seconds"] == after["duration_seconds"]
    assert clean_story_dialogue(cleaned) == cleaned


def test_symbol_only_scene_fails_before_generation():
    with pytest.raises(ValueError, match="scene_dialogue_empty_after_cleaning"):
        clean_story_dialogue({"clips": [{"index": 1, "voice_script": "*** 🎉 !!!"}]})


def test_removing_brackets_does_not_hide_unresolved_placeholders():
    cleaned = clean_story_dialogue({"clips": [{"index": 1, "voice_script": "Mình giới thiệu [thông tin bổ sung] hôm nay."}]})
    assert cleaned["clips"][0]["voice_script"] == "Mình giới thiệu thông tin bổ sung hôm nay."
    assert cleaned["clips"][0]["unresolved_voice_placeholder"] is True
    assert clean_story_dialogue(cleaned) == cleaned
