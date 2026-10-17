import copy
import json

import pytest

from app.pipelines.dialogue_boundaries import normalize_dialogue_boundaries
from app.pipelines.story_planner import PROMPT_PATH, BookStoryPlan, parse_story_plan, voice_words


def story():
    lines = [
        "Co nhung ngay minh thay met moi va cu lam cho xong moi viec.",
        "Cho \u0111\u1ebfn khi doc cuon sach nay minh moi muon dung lai.",
        "V\u00e0 lang nghe chinh minh nhieu hon mot chut.",
    ]
    return {
        "core_value": "One reflective continuous podcast story.",
        "visual_continuity": "The same adult presenter holds the same book in a locked shot.",
        "full_voice_script": " ".join(lines),
        "clips": [{"index": i, "beat": "Reflection", "duration_seconds": 8,
                   "video_prompt": "The same presenter speaks with body and props stable throughout.",
                   "voice_script": line} for i, line in enumerate(lines, 1)],
    }


def test_parse_preserves_complete_sentences_without_words_or_schema_changes():
    original = story()
    before = copy.deepcopy(original)

    plan = parse_story_plan(json.dumps(original), "test", [8])
    assert original == before
    assert all(clip.voice_script == before["clips"][index]["voice_script"] for index, clip in enumerate(plan.clips))
    assert plan.clips[1].voice_script.startswith("Cho \u0111\u1ebfn khi")
    assert plan.clips[2].voice_script.startswith("V\u00e0 ") and plan.clips[2].voice_script.endswith(".")
    assert voice_words(plan.full_voice_script) == voice_words(original["full_voice_script"])
    assert plan.full_voice_script == " ".join(clip.voice_script for clip in plan.clips)
    assert BookStoryPlan.model_validate(plan.model_dump()).full_voice_script == plan.full_voice_script
    assert set(plan.model_dump()) == set(BookStoryPlan.model_fields)
    assert all(clip.video_prompt == before["clips"][i]["video_prompt"] for i, clip in enumerate(plan.clips))


@pytest.mark.parametrize("opening", ["H\u00e0 N\u1ed9i", "Mai", "OpenAI", "AI", "V\u00c0"])
def test_uncertain_proper_names_and_acronyms_keep_their_case(opening):
    clips = [{"index": 1, "voice_script": "One complete clause!"},
             {"index": 2, "voice_script": opening + " la phan tiep theo."}]
    normalized = normalize_dialogue_boundaries(clips)
    assert normalized[0]["voice_script"] == "One complete clause!"
    assert normalized[1]["voice_script"].startswith(opening)


def test_single_scene_is_unchanged_and_selected_edit_never_changes_neighbors():
    clips = story()["clips"]
    assert normalize_dialogue_boundaries(clips[:1]) == clips[:1]
    result = normalize_dialogue_boundaries(clips, {2})
    assert result[0] == clips[0] and result[2] == clips[2]
    assert result[1]["voice_script"] == clips[1]["voice_script"]
    assert normalize_dialogue_boundaries(result, {2}) == result


def test_planner_prompt_prioritizes_complete_meaning_over_count():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    assert "Preserve complete sentence endings" in prompt and "word range" not in prompt
    assert "Every non-final" not in prompt and "speech budget" not in prompt
    assert "Do not cut a phrase" in prompt and "hook must be paid off" in prompt
    assert "one useful insight, demonstration, explanation or consequence" in prompt
    assert "visual-only" in prompt and "Do not embed dialogue" in prompt
    assert "at a scene boundary" in prompt


def test_a_real_clause_join_keeps_comma_and_lowercases_only_its_continuation():
    clips = [{"index": 1, "voice_script": "Mình muốn tìm hiểu chiếc tai nghe này cùng các bạn,"},
             {"index": 2, "voice_script": "Và xem nó phù hợp với nhu cầu nào."}]
    result = normalize_dialogue_boundaries(clips)
    assert result[0] == clips[0] and result[1]["voice_script"].startswith("và ")
    assert clips[1]["voice_script"].startswith("Và ")


def test_completed_thought_keeps_punctuation_and_next_sentence_case():
    clips = [{"index": 1, "voice_script": "Chiếc tai nghe này dùng khi chơi game."},
             {"index": 2, "voice_script": "Mình sẽ cùng bạn tìm hiểu thêm nhé!"}]
    assert normalize_dialogue_boundaries(clips) == clips
