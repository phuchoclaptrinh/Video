"""Separate native model speech from an explicitly provisioned master-voice pipeline."""

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Protocol


class VoiceMode(StrEnum):
    NATIVE_VEO = "native_veo"
    CONSISTENT_VOICE = "consistent_voice"


@dataclass(frozen=True)
class SceneAudioWindow:
    scene_index: int
    audio_start_ms: int
    audio_end_ms: int

    def __post_init__(self):
        if self.scene_index < 1 or not 0 <= self.audio_start_ms < self.audio_end_ms:
            raise ValueError("invalid_scene_audio_window")


class MasterVoiceSynthesizer(Protocol):
    def synthesize(self, script: str) -> bytes:
        """Return one WAV/PCM recording for the complete script."""
        ...


class SpeechAligner(Protocol):
    def align(self, audio: bytes, clips: list[dict]) -> list[SceneAudioWindow]: ...


class LipSyncAdapter(Protocol):
    def synchronize(self, video: Path, audio: bytes, window: SceneAudioWindow) -> Path: ...


class PlannerMasterVoice:
    """Reuse the existing authenticated TTS service, without a new vendor."""

    def __init__(self, http):
        self.http = http

    def synthesize(self, script: str) -> bytes:
        if not 2 <= len(script) <= 4096:
            raise ValueError("master_voice_script_length_invalid")
        response = self.http.post("/internal/v1/planner/voice", json={
            "text": script, "response_format": "wav",
        }, timeout=240)
        response.raise_for_status()
        audio = response.content
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("master_voice_requires_wav")
        return audio


@dataclass(frozen=True)
class VoiceDecision:
    requested: VoiceMode
    effective: VoiceMode
    blockers: tuple[str, ...] = ()

    def metadata(self) -> dict:
        return {"requested_voice_mode": self.requested.value, "voice_mode": self.effective.value,
                "voice_mode_limitations": list(self.blockers), "voice_identity_guaranteed": False}


def choose_voice_mode(scene_count: int, requested: str | None, *, tts: bool,
                      alignment: bool = False, lip_sync: bool = False, audio_control: bool = False) -> VoiceDecision:
    ready = tts and alignment and lip_sync and audio_control
    desired = VoiceMode(requested) if requested else (
        VoiceMode.CONSISTENT_VOICE if scene_count > 1 and ready else VoiceMode.NATIVE_VEO
    )
    if desired == VoiceMode.CONSISTENT_VOICE and not ready:
        blockers = tuple(name for name, available in (
            ("master_tts_unavailable", tts), ("speech_alignment_unavailable", alignment),
            ("lip_sync_unavailable", lip_sync), ("provider_audio_disable_unsupported", audio_control),
        ) if not available)
        return VoiceDecision(desired, VoiceMode.NATIVE_VEO, blockers)
    return VoiceDecision(desired, desired)


def joined_voice_script(plan: dict) -> str:
    return " ".join(clip.get("voice_script", "").strip() for clip in plan.get("clips", []))


def story_duration(plan: dict) -> int:
    return sum(clip.get("duration_seconds", 8) for clip in plan.get("clips", []))


def validate_story_timing(plan: dict, allowed: list[int]) -> None:
    clips = plan.get("clips", [])
    previous_end = 0
    for clip in clips:
        duration = clip.get("duration_seconds", 8)
        if type(duration) is not int or (allowed and duration not in allowed):
            raise ValueError("unsupported_scene_duration")
        start, end = clip.get("audio_start_ms"), clip.get("audio_end_ms")
        if start is not None or end is not None:
            if type(start) is not int or type(end) is not int or not previous_end <= start < end:
                raise ValueError("invalid_scene_audio_window")
            if end - start > duration * 1000:
                raise ValueError("scene_audio_window_exceeds_duration")
            previous_end = end
    full = plan.get("full_voice_script")
    if full and full.split() != joined_voice_script(plan).split():
        raise ValueError("full_voice_script_mismatch")


class MasterVoicePreparation:
    """Fail before paid TTS unless real alignment and lip-sync dependencies exist."""

    def __init__(self, tts: MasterVoiceSynthesizer, aligner: SpeechAligner | None = None,
                 lip_sync: LipSyncAdapter | None = None):
        self.tts, self.aligner, self.lip_sync = tts, aligner, lip_sync

    def prepare(self, plan: dict) -> tuple[bytes, list[SceneAudioWindow]]:
        if self.aligner is None or self.lip_sync is None:
            raise ValueError("consistent_voice_infrastructure_incomplete")
        script = joined_voice_script(plan)
        if plan.get("full_voice_script", script).split() != script.split():
            raise ValueError("full_voice_script_mismatch")
        audio = self.tts.synthesize(script)
        windows = self.aligner.align(audio, plan["clips"])
        if len(windows) != len(plan["clips"]) or any(
            window.scene_index != clip["index"] for window, clip in zip(windows, plan["clips"], strict=True)
        ) or any(a.audio_end_ms > b.audio_start_ms for a, b in pairwise(windows)):
            raise ValueError("master_voice_alignment_invalid")
        return audio, windows
