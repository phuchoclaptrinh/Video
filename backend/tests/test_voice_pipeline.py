from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.services.continuity_frames import FrameCandidate, choose_continuity_frame
from app.services.voice_pipeline import (
    MasterVoicePreparation,
    PlannerMasterVoice,
    SceneAudioWindow,
    VoiceMode,
    choose_voice_mode,
    story_duration,
    validate_story_timing,
)


def test_default_voice_mode_requires_all_real_dependencies():
    assert choose_voice_mode(1, None, tts=True).effective == VoiceMode.NATIVE_VEO
    assert choose_voice_mode(3, None, tts=True).effective == VoiceMode.NATIVE_VEO
    assert choose_voice_mode(3, None, tts=True, alignment=True, lip_sync=True, audio_control=True).effective == VoiceMode.CONSISTENT_VOICE
    decision = choose_voice_mode(3, "consistent_voice", tts=True)
    assert decision.effective == VoiceMode.NATIVE_VEO
    assert set(decision.blockers) == {"speech_alignment_unavailable", "lip_sync_unavailable", "provider_audio_disable_unsupported"}
    assert decision.metadata()["voice_identity_guaranteed"] is False


def test_missing_lip_sync_fails_before_charging_master_tts():
    tts = Mock()
    with pytest.raises(ValueError, match="infrastructure_incomplete"):
        MasterVoicePreparation(tts).prepare({"clips": [{"index": 1, "voice_script": "Hello world"}]})
    tts.synthesize.assert_not_called()


def test_master_voice_is_generated_once_and_uses_measured_windows():
    tts, aligner, lip_sync = Mock(), Mock(), Mock()
    tts.synthesize.return_value = b"one-master-recording"
    aligner.align.return_value = [SceneAudioWindow(1, 0, 5740), SceneAudioWindow(2, 5740, 11000)]
    plan = {"full_voice_script": "First clause. Then the next clause.", "clips": [
        {"index": 1, "voice_script": "First clause."}, {"index": 2, "voice_script": "Then the next clause."},
    ]}
    audio, windows = MasterVoicePreparation(tts, aligner, lip_sync).prepare(plan)
    tts.synthesize.assert_called_once_with(plan["full_voice_script"])
    assert audio == b"one-master-recording" and windows[1].audio_start_ms == 5740


def test_existing_tts_boundary_requests_wav_not_aac_segments():
    http = Mock()
    http.post.return_value.content = b"RIFF\x24\x00\x00\x00WAVEdata"
    assert PlannerMasterVoice(http).synthesize("One complete speech.").startswith(b"RIFF")
    assert http.post.call_args.kwargs["json"] == {"text": "One complete speech.", "response_format": "wav"}


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_timing_accepts_provider_capability(duration):
    validate_story_timing({"clips": [{"duration_seconds": duration, "voice_script": "one clause"}]}, [4, 6, 8])


@pytest.mark.parametrize("duration", [5, 7, 10, True])
def test_timing_rejects_duration_outside_provider_capability(duration):
    with pytest.raises(ValueError, match="unsupported_scene_duration"):
        validate_story_timing({"clips": [{"duration_seconds": duration}]}, [4, 6, 8])


def test_full_speech_must_partition_without_duplicated_boundary():
    plan = {"full_voice_script": "First clause. Next clause.", "clips": [
        {"voice_script": "First clause.", "duration_seconds": 6},
        {"voice_script": "clause. Next clause.", "duration_seconds": 8},
    ]}
    with pytest.raises(ValueError, match="full_voice_script_mismatch"):
        validate_story_timing(plan, [4, 6, 8])
    assert story_duration(plan) == 14


@pytest.mark.parametrize("start,end", [(5000, 9000), (6000, None), (True, 7000), (6000, 16000)])
def test_scene_audio_windows_reject_overlap_unpaired_or_oversized_timing(start, end):
    plan = {"clips": [
        {"duration_seconds": 6, "audio_start_ms": 0, "audio_end_ms": 5740},
        {"duration_seconds": 8, "audio_start_ms": start, "audio_end_ms": end},
    ]}
    with pytest.raises(ValueError, match="audio_window"):
        validate_story_timing(plan, [4, 6, 8])


def test_frame_rank_prefers_sharp_low_motion_not_blurred_tail(tmp_path):
    sharp = Image.new("L", (320, 180), "white")
    drawing = ImageDraw.Draw(sharp)
    for x in range(0, 320, 12):
        drawing.rectangle((x, 20, x + 6, 160), fill="black")
    candidates = []
    for index, frame in enumerate((sharp, sharp, sharp.filter(ImageFilter.GaussianBlur(9)))):
        path = tmp_path / f"frame-{index}.png"
        frame.save(path)
        candidates.append(FrameCandidate(path, 7.7 + index * 0.1))
    selected, metrics = choose_continuity_frame(candidates)
    assert selected.path == candidates[0].path
    assert metrics["face_pose_verified"] is False
