from app.services.video_continuity import ContinuityRequest, build_scene_prompt

CLIP = {"index": 1, "video_prompt": "A Vietnamese boy runs into the hallway in the new outfit.",
        "voice_script": "Con nhà bạn cũng lắc đầu mỗi lần thay đồ đúng không?"}


def test_voice_over_keeps_on_screen_children_silent():
    plan = {"narration_mode": "voice_over", "clips": [CLIP]}
    prompt = build_scene_prompt(plan, CLIP, ContinuityRequest("none"), native_audio=True)
    assert "unseen off-screen narrator" in prompt and "nobody on screen speaks" in prompt
    assert "synchronize lips" not in prompt


def test_on_screen_speaker_keeps_lip_sync_and_first_scene_opens_in_motion():
    plan = {"narration_mode": "on_screen_speaker", "clips": [CLIP]}
    prompt = build_scene_prompt(plan, CLIP, ContinuityRequest("none"), native_audio=True)
    assert "synchronize lips" in prompt and "off-screen narrator" not in prompt
    assert "Hook opening: action is already under way" in prompt
    later = dict(CLIP, index=2)
    assert "Hook opening" not in build_scene_prompt({"clips": [CLIP, later]}, later, ContinuityRequest("none"), native_audio=True)
