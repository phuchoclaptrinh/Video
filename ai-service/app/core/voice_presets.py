"""Voice configuration; the backend mirror is checked for parity in tests."""

import os
import re
import unicodedata
from dataclasses import dataclass, replace


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


_RATE_RE = re.compile(
    r"(?<![\w.,])(?P<minimum>\d+(?:[.,]\d+)?)"
    r"(?:\s*[-\u2013\u2014]\s*(?P<maximum>\d+(?:[.,]\d+)?))?\s*"
    r"(?:\u00e2m\s+ti\u1ebft|am\s+tiet|syllables?)\s*"
    r"(?:/|per\s+|m\u1ed7i\s+|tr\u00ean\s+)(?:gi\u00e2y|giay|seconds?|secs?|s)(?!\w)",
    re.IGNORECASE,
)
_PACE_REQUEST_RE = re.compile(
    r"(?P<slow>(?:n\u00f3i|noi|\u0111\u1ecdc|doc|nh\u1ecbp\s+n\u00f3i|t\u1ed1c\s+\u0111\u1ed9\s+n\u00f3i)\s+"
    r"(?:th\u1eadt\s+|r\u1ea5t\s+|h\u01a1i\s+)?(?:ch\u1eadm|cham)\b|"
    r"(?:speak|talk|speech|speaking\s+pace)\s+(?:slowly|slow|slower)\b|"
    r"(?:gi\u1ecdng|giong|voice|delivery|phong\s+c\u00e1ch)\s+podcast\b)|"
    r"(?P<fast>(?:n\u00f3i|noi|\u0111\u1ecdc|doc|nh\u1ecbp\s+n\u00f3i|t\u1ed1c\s+\u0111\u1ed9\s+n\u00f3i)\s+"
    r"(?:th\u1eadt\s+|r\u1ea5t\s+|h\u01a1i\s+)?nhanh\b|"
    r"(?:speak|talk|speech|speaking\s+pace)\s+(?:quickly|fast|faster|brisk)\b|"
    r"(?:gi\u1ecdng|giong|voice|delivery|phong\s+c\u00e1ch)\s+koc\b)",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"(?:kh\u00f4ng|khong|\u0111\u1eebng|not|don't)\s+(?:mu\u1ed1n\s+|want\s+)?$", re.IGNORECASE)


def _numeric_pace(text: str) -> tuple[float, float] | None:
    match = _RATE_RE.search(text) or re.search(
        r"\bpace\s*[:=]\s*(?P<minimum>\d+(?:[.,]\d+)?)"
        r"(?:\s*[-\u2013\u2014]\s*(?P<maximum>\d+(?:[.,]\d+)?))?(?=\s*(?:;|$))",
        text, re.IGNORECASE,
    )
    if not match:
        return None
    minimum = float(match["minimum"].replace(",", "."))
    maximum = float((match["maximum"] or match["minimum"]).replace(",", "."))
    return (minimum, maximum) if 0.5 <= minimum <= maximum <= 8 else None


def voice_preset_for_request(prompt: str) -> VoicePreset:
    """Only explicit current speech preferences override VOICE_PACE, not a KOC role."""
    preset = get_voice_preset()
    text = unicodedata.normalize("NFC", prompt)
    for match in _PACE_REQUEST_RE.finditer(text):
        if not _NEGATED.search(text[max(0, match.start() - 30):match.start()]):
            preset = get_voice_preset("podcast" if match["slow"] else "koc")
    pace = _numeric_pace(text)
    return replace(preset, pace_min=pace[0], pace_max=pace[1]) if pace else preset


def tts_voice_instructions(voice_bible: str = "") -> str:
    profile = voice_bible.strip() or get_voice_preset().default_voice_profile
    return (
        f"GLOBAL VOICE INSTRUCTION (not dialogue): {profile}\n"
        "Read only the supplied text, following this single profile and natural clause pauses. "
        "Keep the same vocal identity throughout. Do not read these instructions aloud."
    )
