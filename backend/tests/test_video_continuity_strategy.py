import copy
import json

import pytest

from app.core.voice_presets import get_voice_preset
from app.services.native_audio_quality import (
    boundary_speech_issues,
    native_speech_issues,
    native_trim_window,
    volume_gain,
)
from app.services.video_continuity import (
    ContinuityRequest,
    VideoContinuityStrategy,
    build_scene_prompt,
    voice_instruction,
)
from app.services.video_provider_capabilities import VideoProviderCapabilities, safe_video_url


@pytest.mark.parametrize("provider,model,mode", [
    ("poyo", "veo3.1-fast", "image_references"),
    ("poyo", "veo3.1-quality", "first_frame"),
    ("seedance", "", "first_frame"),
])
def test_continuation_uses_only_supported_reference_inputs(provider, model, mode):
    strategy = VideoContinuityStrategy(provider, model)
    request = strategy.build_continuation_request("story", "completed-parent")
    assert request.mode == mode
    assert request.parent_generation_id == "completed-parent"
    assert request.references[-1].identifier == "completed-parent"
    assert request.native_continuation is False
    assert request.voice_reference_supported is False
    initial = strategy.build_initial_request("story", True)
    assert [r.identifier for r in initial.references] == ["anchor-story"]


def test_continuation_rejects_unfinished_or_unrelated_parent():
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast")
    parent = {"generation_id": "parent", "status": "GENERATING",
              "payload": {"spec": {"request": {"user_id": "owner"}}}}
    with pytest.raises(ValueError, match="not_completed"):
        strategy.validate_continuation(parent, user_id="owner", expected_parent_id="parent")
    with pytest.raises(ValueError, match="mismatch"):
        strategy.validate_continuation(parent | {"status": "COMPLETED"}, user_id="someone-else", expected_parent_id="parent")
    with pytest.raises(ValueError, match="required"):
        strategy.build_continuation_request("story", "")


def test_continuous_dialogue_has_one_voice_description_and_only_current_line():
    first = {"index": 1, "video_prompt": "The presenter holds the same product at chest height.",
             "voice_script": "Hôm nay mình sẽ cùng các bạn tìm hiểu"}
    second = {"index": 2, "video_prompt": "The same presenter keeps the product visible and continues.",
              "voice_script": "vì sao thiết kế này lại tiện lợi khi dùng mỗi ngày."}
    plan = {"voice_bible": {"language": "Vietnamese", "accent": "Northern", "timbre": "warm"},
            "clips": [first, second]}
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast")
    opening = build_scene_prompt(plan, first, strategy.build_initial_request("draft", True), native_audio=True)
    continuation = build_scene_prompt(plan, second, strategy.build_continuation_request("draft", "parent"), native_audio=True)
    voice_line = next(line for line in opening.splitlines() if line.startswith("VOICE DESCRIPTION"))
    assert voice_line in continuation
    assert "CONTINUE THE PREVIOUS VIDEO" not in continuation
    assert first["voice_script"] not in continuation
    assert "PREVIOUS DIALOGUE" not in continuation
    assert "VOICE MATCH RULES" not in continuation
    assert f'says this Vietnamese line exactly once, word for word: "{second["voice_script"]}"' in continuation


@pytest.mark.parametrize("voice", [
    "Vietnamese; female 25-30; Hanoi accent; mid-high pitch; warm, smooth, lightly breathy; close dry mic.",
    {"language": "Vietnamese", "accent": "Southern", "gender": "male", "age": "adult 35-40",
     "pitch": "comfortable low-mid", "timbre": "warm", "resonance": "balanced chest",
     "texture": "smooth", "breathiness": "restrained", "nasality": "low",
     "speaking_style": "conversation", "pace": "steady", "emotion": "calm",
     "intonation": "gentle endings", "pronunciation": "native tones", "volume": "moderate",
     "recording": "close dry mic"},
])
def test_three_scenes_share_identical_detailed_voice_without_recasting(voice):
    clips = [{"index": index, "video_prompt": "The same presenter holds the book and speaks naturally.",
              "voice_script": f"Đây là phần chia sẻ thứ {index} trong cùng một bài nói.",
              "voice_bible": "Different scene-local voice that must not be selected."} for index in range(1, 4)]
    plan = {"voice_bible": voice, "clips": clips}
    before = copy.deepcopy(plan)
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast")
    prompts = [build_scene_prompt(plan, clip, strategy.build_initial_request("draft", True) if clip["index"] == 1
                                 else strategy.build_continuation_request("draft", f"parent-{clip['index'] - 1}"),
                                 native_audio=True) for clip in clips]
    voice_line = "VOICE DESCRIPTION (not dialogue): " + voice_instruction(voice)
    for clip, prompt in zip(clips, prompts, strict=True):
        assert voice_line in prompt and prompt.count("VOICE DESCRIPTION") == 1
        assert "VOICE MATCH RULES" not in prompt and "Different scene-local voice" not in prompt
        assert f"says this Vietnamese line exactly once, word for word: {json.dumps(clip['voice_script'], ensure_ascii=False)}." in prompt
        assert "Generate picture and native audio together" in prompt
    assert plan == before


@pytest.mark.parametrize("value", [None, "", "   ", {}])
@pytest.mark.parametrize("preset_name", ["podcast", "koc"])
def test_missing_voice_defaults_to_active_preset_without_forcing_gender(value, preset_name, monkeypatch):
    monkeypatch.setenv("VOICE_PACE", preset_name)
    profile = voice_instruction(value)
    assert profile == get_voice_preset().default_voice_profile and len(profile) <= 600
    assert all(detail in profile for detail in ("pitch", "resonance", "texture", "breathiness", "nasality", "tones", "microphone"))
    assert "female" not in profile and "Hanoi Northern accent" in profile
    assert "minimal breathiness" in profile


def test_native_dialogue_is_clean_without_overriding_legacy_voice_profile(monkeypatch):
    monkeypatch.setenv("VOICE_PACE", "koc")
    first = {"index": 1, "video_prompt": "The presenter stays still and talks to the camera.",
             "voice_script": "**Hôm nay** mình chia sẻ “cuốn sách này” 😊..."}
    second = {"index": 2, "video_prompt": "The same presenter continues in the same shot.",
              "voice_script": "Mình & bạn cùng đọc, giá $5 giảm 10%."}
    plan = {"voice_bible": "Vietnamese; Hanoi Northern accent; warm adult; moderate pace.", "clips": [first, second]}
    prompts = [build_scene_prompt(plan, clip, ContinuityRequest("none"), native_audio=True) for clip in plan["clips"]]
    for prompt in prompts:
        assert plan["voice_bible"] in prompt
        assert "3.2" not in prompt and "takes priority over" not in prompt and "lively" not in prompt
        assert "synchronize lips with native speech" in prompt
        assert "global voice pace" not in prompt and "syllables/s" not in prompt
        assert "😊" not in prompt and "**" not in prompt and "“" not in prompt
    assert 'says this Vietnamese line exactly once, word for word: "Hôm nay mình chia sẻ cuốn sách này."' in prompts[0]
    assert '"Mình và bạn cùng đọc, giá 5 đô la Mỹ giảm 10 phần trăm."' in prompts[1]
    assert '"Hôm nay mình chia sẻ cuốn sách này."' not in prompts[1]


@pytest.mark.parametrize("preset_name", ["podcast", "koc"])
def test_all_native_scenes_have_one_voice_pace_source_and_identical_instruction(monkeypatch, preset_name):
    monkeypatch.setenv("VOICE_PACE", preset_name)
    clips = [{"index": index, "video_prompt": "The presenter faces the camera holding the same book.",
              "voice_script": "Mình tiếp tục chia sẻ về cuốn sách này hôm nay."} for index in (1, 2, 3)]
    prompts = [build_scene_prompt({"clips": clips}, clip, ContinuityRequest("none"), native_audio=True) for clip in clips]
    profile = get_voice_preset().default_voice_profile
    instruction = "VOICE DESCRIPTION (not dialogue): " + profile
    for prompt in prompts:
        assert instruction in prompt and "syllables/s" not in prompt
        assert "takes priority over" not in prompt
        remainder = prompt.replace(instruction, "")
        assert all(word not in remainder.casefold() for word in ("brisk", "lively", "confident", "3.2", "2.6"))
        if preset_name == "podcast":
            assert all(word not in prompt.casefold() for word in ("brisk", "lively", "3.2", "takes priority over"))
        assert len(prompt) <= 4000


def test_podcast_scene_two_keeps_explicit_healing_profile_without_another_delivery(monkeypatch):
    monkeypatch.setenv("VOICE_PACE", "podcast")
    profile = "Vietnamese; female adult 18-25; Hanoi accent; gentle deep healing podcast tone; slow-moderate pace, calm pauses."
    clip = {"index": 2, "video_prompt": "The same host faces the camera holding the same book.",
            "voice_script": "Một trang sách giúp mình nhìn lại những điều nhỏ bé trong ngày."}
    prompt = build_scene_prompt({"voice_bible": profile, "clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)
    assert profile in prompt and "VOICE MATCH RULES" not in prompt
    assert all(value not in prompt for value in ("brisk", "lively", "3.2", "takes priority over"))
    assert "no reading down or page-turning during speech" in prompt


def test_legacy_voice_over_schema_length_is_never_silently_truncated():
    profile = "Preserve this full explicit speaker profile. " * 18 + "KEEP THIS LAST VOICE DETAIL."
    clip = {"index": 1, "video_prompt": "The host stays in the same locked shot.", "voice_script": "Đây là lời thoại đầy đủ cho cảnh này."}
    prompt = build_scene_prompt({"voice_bible": profile}, clip, ContinuityRequest("none"), native_audio=True)
    assert len(profile) > 600 and profile in prompt and len(prompt) <= 4000
    with pytest.raises(ValueError, match="continuity_prompt_too_long"):
        build_scene_prompt({"voice_bible": profile * 8}, clip, ContinuityRequest("none"), native_audio=True)
def test_native_dialogue_cannot_be_empty_after_symbol_removal():
    clip = {"index": 1, "video_prompt": "The presenter faces the camera.", "voice_script": "*** 😊 !!!"}
    with pytest.raises(ValueError, match="native_audio_requires_scene_dialogue"):
        build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)


@pytest.mark.parametrize("voice,unresolved", [("Mình chia sẻ [thông tin bổ sung] hôm nay.", False),
                                             ("Mình chia sẻ thông tin bổ sung hôm nay.", True)])
def test_unresolved_dialogue_is_not_sent_as_native_speech_after_bracket_removal(voice, unresolved):
    clip = {"index": 1, "video_prompt": "The same presenter faces the camera.",
            "voice_script": voice, "unresolved_voice_placeholder": unresolved}
    with pytest.raises(ValueError, match="native_audio_unresolved_scene_dialogue"):
        build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)


@pytest.mark.parametrize("mode", ["first_frame", "reference"])
def test_long_prompt_shortens_supporting_context_not_voice_or_dialogue(mode):
    profile = "warm detailed voice " * 29 + "clear dry mic."
    spoken = ("Speak this complete sentence with long proper names. " * 10)[:500]
    first = {"index": 1, "voice_script": "Previous scene dialogue. " * 18}
    clip = {"index": 2, "video_prompt": "Scene action description " * 60,
            "voice_script": spoken, "start_state": "Starting state. " * 45,
            "end_state": "Ending state. " * 45, "transition_intent": "Continue the same action. " * 10}
    plan = {"voice_bible": profile, "visual_bible": "Visual description. " * 80,
            "clips": [first, clip, {"index": 3}]}
    request = VideoContinuityStrategy("poyo", "veo3.1-fast", reference_mode=mode).build_continuation_request("draft", "parent")
    prompt = build_scene_prompt(plan, clip, request, native_audio=True)
    assert len(prompt) <= 4000
    assert "VOICE DESCRIPTION (not dialogue): " + profile in prompt
    assert "VOICE MATCH RULES" not in prompt and "PREVIOUS DIALOGUE" not in prompt
    assert json.dumps(spoken.strip(), ensure_ascii=False) in prompt
    assert "Visual context:" in prompt and "Scene action: " + clip["video_prompt"].strip() in prompt
    assert "Ending state:" in prompt and "Transition intent:" in prompt


def test_held_product_scene_forbids_floating_props_only_when_something_is_held():
    held = {"index": 1, "video_prompt": "The same Vietnamese woman holds two outfits on hangers and smiles.",
            "voice_script": "Bộ này mặc Tết là xinh nhất luôn các mẹ ơi."}
    free = {"index": 1, "video_prompt": "The same Vietnamese boy runs across the yard laughing.",
            "voice_script": "Con chạy cả buổi mà vẫn thoải mái."}
    vietnamese = {**free, "end_state": "Người dẫn giữ bộ đồ trước thân."}
    prompts = [build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)
               for clip in (held, free, vietnamese)]
    assert "never floating; a holding hand never lets go to gesture" in prompts[0]
    assert "never floating" not in prompts[1]
    assert "never floating" in prompts[2]


def test_digits_in_a_transcript_match_numbers_spelled_out_in_the_script():
    expected = "Chất vải gồm chín mươi lăm phần trăm cotton, giá một trăm năm mươi chín nghìn đồng, bé từ hai đến sáu tuổi."
    heard = "chất vải gồm 95% cotton, giá 159.000đ, bé từ 2 đến 6 tuổi."
    assert native_speech_issues(expected, heard) == ([], 1.0)
    assert native_speech_issues(expected, "chất vải gồm 95% cotton, giá 159k")[0] == ["native_speech_incomplete"]


def test_silent_video_does_not_get_voice_identity_or_spoken_instructions():
    clip = {"index": 1, "video_prompt": "The subject stays in a stable locked shot.", "voice_script": "A complete spoken line."}
    prompt = build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=False)
    assert "VOICE DESCRIPTION" not in prompt and "VOICE MATCH RULES" not in prompt
    assert "Vietnamese line exactly once" not in prompt and "Do not generate audio" in prompt


def test_planned_transition_does_not_request_a_locked_camera_at_the_same_time():
    clip = {"index": 2, "video_prompt": "The camera deliberately moves to a closer shot.",
            "voice_script": "và nhìn rõ hơn thiết kế này", "transition_intent": "intentional_camera_transition"}
    request = VideoContinuityStrategy("poyo", "veo3.1-fast").build_continuation_request("draft", "parent")
    prompt = build_scene_prompt({"clips": [clip]}, clip, request, native_audio=True)
    assert "Follow only the planned shot or camera transition" in prompt
    assert "Hold the existing camera framing" not in prompt


def test_boundary_repeated_phrase_fails_but_planned_repetition_is_allowed():
    before = "Hôm nay mình muốn chia sẻ cuốn sách này"
    current = "vì nội dung giúp mình suy nghĩ chậm lại"
    assert boundary_speech_issues(before, current, before, "cuốn sách này " + current)
    assert boundary_speech_issues(before, "cuốn sách này rất hay", before, "cuốn sách này rất hay") == []
    assert native_speech_issues("Một câu thoại phải được nói đủ đến cuối cùng", "Một câu thoại phải được nói đủ")[0] == ["native_speech_incomplete"]


def test_connected_clauses_are_not_boundary_repetition():
    before = "Mình cứ lặng lẽ làm cho xong mọi việc, cho đến khi"
    current = "đọc cuốn sách này mình muốn dành thời gian lắng nghe chính mình."
    assert boundary_speech_issues(before, current, before, current) == []
    assert boundary_speech_issues(before, current, before, "cho đến khi " + current)


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_native_three_scene_prompts_use_natural_delivery_without_hard_timing(duration):
    profile = get_voice_preset("podcast").default_voice_profile.ljust(599, " ") + "."
    clips = [{"index": index, "duration_seconds": duration,
              "voice_script": line, "video_prompt": "The presenter holds the same book at chest height and looks into the camera in a stable medium shot."}
             for index, line in enumerate(("Mình muốn kể câu chuyện này cùng bạn,",
                                           "cho đến khi chúng ta nhìn lại chính mình,",
                                           "và dành thêm thời gian cho điều quan trọng."), 1)]
    plan = {"voice_bible": profile, "visual_bible": "Same presenter and room. " * 70, "clips": clips}
    before = copy.deepcopy(plan)
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast")
    prompts = [build_scene_prompt(plan, clip, strategy.build_initial_request("draft", True) if clip["index"] == 1
                                 else strategy.build_continuation_request("draft", f"parent-{clip['index'] - 1}"),
                                 native_audio=True) for clip in clips]
    for clip, prompt in zip(clips, prompts, strict=True):
        assert len(profile) == 600 and profile in prompt and len(prompt) <= 4000
        assert f"one {duration}-second shot" in prompt
        assert "expressive not flat" in prompt
        assert "within 0.2 seconds" not in prompt and "seconds into the" not in prompt
        assert "keep the physical state stable" in prompt
        assert json.dumps(clip["voice_script"], ensure_ascii=False) in prompt
        assert "unfinished intonation" not in prompt and "do not drop the pitch" not in prompt
        assert "continues a sentence already in progress" not in prompt
    assert plan == before


@pytest.mark.parametrize("previous_ending", [".", "!", "?", ","])
@pytest.mark.parametrize("current_ending", [".", "!", "?", ","])
def test_native_intonation_is_natural_without_forced_continuation(previous_ending, current_ending):
    clips = [
        {"index": 1, "duration_seconds": 8, "voice_script": "Mình muốn tìm hiểu chiếc tai nghe này" + previous_ending},
        {"index": 2, "duration_seconds": 8, "voice_script": "Cùng xem nó phù hợp với nhu cầu nào" + current_ending,
         "video_prompt": "The same presenter faces the camera and holds the same headphones steadily."},
        {"index": 3, "duration_seconds": 8, "voice_script": "Bạn hãy chia sẻ nhu cầu của mình nhé."},
    ]
    plan = {"voice_bible": get_voice_preset("podcast").default_voice_profile, "clips": clips}
    before = copy.deepcopy(plan)
    request = VideoContinuityStrategy("poyo", "veo3.1-fast").build_continuation_request("draft", "parent")
    prompt = build_scene_prompt(plan, clips[1], request, native_audio=True)
    assert "first part of a sentence" not in prompt
    assert "unfinished intonation" not in prompt and "start mid-sentence" not in prompt
    assert json.dumps(clips[1]["voice_script"], ensure_ascii=False) in prompt
    assert clips[0]["voice_script"] not in prompt and clips[2]["voice_script"] not in prompt
    assert "while speech continues" not in prompt and "while still speaking" not in prompt
    assert prompt.count("VOICE DESCRIPTION") == 1 and plan == before


def test_detailed_visual_direction_is_not_silently_cut_to_four_hundred_characters():
    action = (
        "An eye-level medium shot of the same adult Vietnamese presenter in the same recording room. "
        "She wears the unchanged blouse and holds the unbranded headphones at chest height. "
        "Her face is unobstructed. One soft practical lamp illuminates the desk and natural window light "
        "illuminates her face. She angles one ear cushion slightly toward the lens without lifting the "
        "headphones higher or turning away. The desk, microphone, hair and background remain in their "
        "previous positions. The ear cushion stays clearly visible at the end."
    )
    clip = {"index": 1, "duration_seconds": 8, "video_prompt": action,
            "voice_script": "Đừng chọn tai nghe chỉ vì nhìn đẹp. Hãy thử cảm giác khi đeo trước nhé."}
    prompt = build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)
    assert len(action) > 400 and "Scene action: " + action in prompt
    assert prompt.count("Scene action:") == 1 and prompt.count("Audio:") == 1
    assert len(prompt) <= 4000


def test_oversized_prompt_is_rejected_instead_of_cutting_scene_action():
    clip = {"index": 1, "video_prompt": "A visual detail that must not be cut. " * 110,
            "voice_script": "Đừng chọn tai nghe chỉ vì nhìn đẹp."}
    with pytest.raises(ValueError, match="continuity_prompt_too_long"):
        build_scene_prompt({"clips": [clip]}, clip, ContinuityRequest("none"), native_audio=True)


@pytest.mark.parametrize("transition", ["hard_cut", "match_cut", "fade_to_black"])
def test_new_shot_uses_planned_start_instead_of_previous_frame_composition(transition):
    clip = {"index": 2, "transition_intent": transition,
            "video_prompt": "A closer angle shows the presenter at a desk under the requested warmer light.",
            "start_state": "Presenter seated at the desk, product on its stand.",
            "end_state": "Product stays on the stand.", "voice_script": "Cùng xem chi tiết này nhé."}
    plan = {"clips": [{"index": 1, "end_state": "Standing beside a window."}, clip]}
    request = VideoContinuityStrategy("poyo", "veo3.1-fast").build_continuation_request("story", "parent")
    prompt = build_scene_prompt(plan, clip, request, native_audio=True)
    assert "Starting state: " + clip["start_state"] in prompt
    assert "previous-shot context, not the new opening composition" in prompt
    assert "changes to framing, setting or lighting" in prompt
    assert "Hold the existing camera" not in prompt
    assert "Follow the visible pose" not in prompt
    assert "standing beside a window" not in prompt.lower()
    assert ("The editor adds the fade" in prompt) == (transition == "fade_to_black")


@pytest.mark.parametrize("transition", ["hard_cut", "match_cut", "fade_to_black"])
def test_cut_cannot_use_previous_frame_as_exact_new_opening(transition):
    clip = {"index": 2, "transition_intent": transition, "video_prompt": "A planned new angle.",
            "voice_script": "Cùng xem chi tiết này nhé."}
    request = VideoContinuityStrategy("poyo", "veo3.1-quality").build_continuation_request("story", "parent")
    with pytest.raises(ValueError, match="scene_cut_requires_new_opening_image_or_reference_mode"):
        build_scene_prompt({"clips": [clip]}, clip, request, native_audio=True)


def test_initial_reference_shot_does_not_invent_a_previous_scene():
    clip = {"index": 1, "transition_intent": "hard_cut",
            "video_prompt": "The presenter is seated at the desk, holding headphones at chest height.",
            "voice_script": "Đừng chọn tai nghe chỉ vì nhìn đẹp."}
    request = VideoContinuityStrategy("poyo", "veo3.1-fast", reference_mode="reference").build_initial_request("story", True)
    prompt = build_scene_prompt({"clips": [clip]}, clip, request, native_audio=True)
    assert "final reference guides the opening pose" in prompt
    assert "previous-shot context" not in prompt and "supplied previous-frame image" not in prompt


def test_planned_camera_move_starts_at_previous_frame_without_locking_whole_shot():
    clip = {"index": 2, "transition_intent": "intentional_camera_transition",
            "video_prompt": "Slowly dolly closer to the product while the window light fades as requested.",
            "voice_script": "Cùng xem chi tiết này nhé."}
    request = VideoContinuityStrategy("poyo", "veo3.1-quality").build_continuation_request("story", "parent")
    prompt = build_scene_prompt({"clips": [clip]}, clip, request, native_audio=True)
    assert "exact opening composition" in prompt
    assert "Follow the visible pose" in prompt and clip["video_prompt"] in prompt
    assert "explicitly planned visual changes" in prompt
    assert "Hold the existing camera" not in prompt
    assert "Preserve its visible subject, wardrobe, props, setting and lighting" not in prompt


def test_trim_keeps_speech_padding_and_does_not_erase_empty_clip():
    assert native_trim_window(8, {"active_audio_seconds": 6, "leading_silence_seconds": 0.5,
                                  "trailing_silence_seconds": 1.5}) == (0.38, 6.62)
    assert native_trim_window(8, {"active_audio_seconds": 0, "leading_silence_seconds": 8}) == (0, 8)
    assert native_trim_window(8, {"active_audio_seconds": 7.8, "trailing_silence_seconds": 0.2}) == (0, 8)


def test_volume_adjustment_limits_gain_and_prevents_clipping():
    assert volume_gain({"active_volume_db": -18}, {"active_volume_db": -24, "peak_volume_db": -2}) == 1
    assert volume_gain({"active_volume_db": -18}, {"active_volume_db": -8}) == -6


def test_capability_flag_cannot_enable_a_fake_extension_api():
    caps = VideoProviderCapabilities(
        provider="poyo", model="veo3.1-fast", native_audio=True, first_frame=True,
        video_continuation=True, continuation_requires_video_url=True,
        continuation_durations=[7], continuation_output="CUMULATIVE",
    )
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast", provider_capabilities=caps)
    with pytest.raises(ValueError, match="native_continuation_adapter_not_implemented"):
        strategy.build_continuation_request("story", "parent", parent_video_url="https://provider.example/clip.mp4")


def test_source_urls_are_preserved_in_request_but_redacted_in_logs():
    url = "https://user:secret@storage.poyo.ai/private-token/video.mp4?signature=secret-token"
    request = VideoContinuityStrategy("poyo", "veo3.1-fast").build_continuation_request(
        "story", "parent", parent_video_url=url, parent_asset_path="videos/parent.mp4",
    )
    assert request.parent_video_url == url
    assert request.parent_asset_path == "videos/parent.mp4"
    logged = safe_video_url(url)
    assert "storage.poyo.ai" in logged
    assert "secret" not in logged and "private-token" not in logged
    assert logged != safe_video_url("https://storage.poyo.ai/a-different-video.mp4")


@pytest.mark.parametrize("duration,mode", [(4, "first_frame"), (6, "first_frame"), (8, "image_references")])
def test_official_reference_inputs_respect_duration_capability(duration, mode):
    strategy = VideoContinuityStrategy("poyo", "veo3.1-fast-official", reference_mode="reference")
    for request in (
        strategy.build_initial_request("story", True, duration),
        strategy.build_continuation_request("story", "parent", duration_seconds=duration),
    ):
        assert request.mode == mode
        assert request.references[-1].role == "FIRST_FRAME"
        assert any(ref.role == "CHARACTER_REFERENCE" for ref in request.references) == (duration == 8)
