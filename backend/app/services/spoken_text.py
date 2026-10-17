"""Plain spoken Vietnamese for persistence, native generation and ASR expectations."""

import copy
import html
import re
import unicodedata

_CURRENCIES = {"$": "đô la Mỹ", "€": "euro", "£": "bảng Anh", "₫": "đồng"}
_SYMBOL_WORDS = {"%": "phần trăm", "&": "và", "+": "cộng", "=": "bằng", "@": "a còng"}
_NUMBER = r"\d+(?:[.,]\d+)*"
STORY_PLACEHOLDER_RE = re.compile(
    r"\[[^\]\n]{2,100}\]|\b(?:tên|ten)\s+(?:cuốn\s+)?(?:sách|sach)\b",
    re.IGNORECASE,
)


def clean_spoken_text(value: str) -> str:
    text = unicodedata.normalize("NFC", html.unescape(value))
    if not any(unicodedata.category(char)[0] in "LN" for char in text):
        return ""
    # Strip presentation syntax before verbalizing symbols that carry meaning.
    text = re.sub(r"\[([^\]\n]+)\]\(https?://[^)\s]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:[-*•]\s+|\d+[.)]\s+|#{1,6}\s+)", "", text)
    text = re.sub(r"\*{1,3}|`+|_+", "", text)
    text = text.replace("\u2212", "-")
    text = re.sub(r"(?<=\d)\s*[-\u2013\u2014]\s*(?=\d)", " đến ", text)
    text = re.sub(
        rf"([$€£₫])\s*(-?)\s*({_NUMBER})",
        lambda m: f"{'âm ' if m[2] else ''}{m[3]} {_CURRENCIES[m[1]]}", text,
    )
    text = re.sub(r"(?<!\w)-\s*(?=\d)", "âm ", text)
    text = re.sub(r"°\s*C\b", " độ C", text, flags=re.IGNORECASE)
    text = re.sub(r"°\s*F\b", " độ F", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*([<>])\s*(?=\d)", lambda m: " nhỏ hơn " if m[1] == "<" else " lớn hơn ", text)
    text = re.sub(
        r"\b(?:(ngày)\s+)?(\d{1,2})/(\d{1,2})/(\d{4})\b",
        lambda m: f"{m[1] or 'ngày'} {m[2]} tháng {m[3]} năm {m[4]}", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", " trên ", text)
    for symbol, words in (_CURRENCIES | _SYMBOL_WORDS).items():
        text = text.replace(symbol, f" {words} ")
    text = re.sub(r"\.{2,}|…", ".", text)
    text = re.sub(r"[:;\u2013\u2014]", ",", text)
    characters = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf" or char in ("\ufe0e", "\ufe0f") or 0xE0100 <= ord(char) <= 0xE01EF:
            continue
        characters.append(char if category[0] in "LMN" or char in ".,?!" else " ")
    text = " ".join("".join(characters).split())
    text = re.sub(r"\s+([.,?!])", r"\1", text)
    text = re.sub(r",[\s,]*(?=[.?!])", "", text)
    text = re.sub(r"([.,?!])\1+", r"\1", text)
    text = text.lstrip(" ,").rstrip()
    if not any(unicodedata.category(char)[0] in "LN" for char in text):
        return ""
    return text


def clean_story_dialogue(plan: dict) -> dict:
    """Call only after validating the original full-script/scene partition."""
    cleaned = copy.deepcopy(plan)
    for clip in cleaned["clips"]:
        if STORY_PLACEHOLDER_RE.search(clip["voice_script"]):
            clip["unresolved_voice_placeholder"] = True
        clip["voice_script"] = clean_spoken_text(clip["voice_script"])
        if not clip["voice_script"]:
            raise ValueError("scene_dialogue_empty_after_cleaning")
    cleaned["full_voice_script"] = " ".join(clip["voice_script"] for clip in cleaned["clips"])
    return cleaned
