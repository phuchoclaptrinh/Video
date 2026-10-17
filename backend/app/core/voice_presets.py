"""Minimal mirror of ai-service voice presets; parity is verified in tests."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VoicePreset:
    name: str
    pace_min: float
    pace_max: float
    delivery: str

    @property
    def default_voice_profile(self) -> str:
        return (
            "Vietnamese; adult speaker; Hanoi Northern accent, clear tones and consonants; "
            "natural pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; "
            f"{self.delivery}; "
            "stable volume, close dry microphone."
        )

VOICE_PRESETS = {
    "podcast": VoicePreset(
        "podcast", 2.6, 3.0,
        "warm, gentle, reflective conversation, clear enunciation, short natural clause pauses; "
        "no drawn-out vowels, whispering or theatrical delivery",
    ),
    "koc": VoicePreset(
        "koc", 3.2, 3.6,
        "upbeat short-form creator, quick confident pace, smiling tone, punchy stress on key words, "
        "lively pitch, rising hook questions; never monotone, drawn-out or whispered",
    ),
}


def get_voice_preset(name: str | None = None) -> VoicePreset:
    selected = (name if name is not None else os.getenv("VOICE_PACE", "podcast")).strip().casefold()
    if selected not in VOICE_PRESETS:
        raise ValueError("VOICE_PACE must be podcast or koc")
    return VOICE_PRESETS[selected]
