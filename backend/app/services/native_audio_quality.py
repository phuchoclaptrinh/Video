"""Text and signal checks; these do not identify a speaker or their accent."""

import difflib
import math
import re
import unicodedata

DIGIT_WORDS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
SCALE_WORDS = ("", "nghìn", "triệu", "tỷ")
# Speech recognisers write "95%" or "2 đến 6" where scripts spell the words out.
NUMBER_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?!\d)|\d+)(?:[.,](\d+))?(?:\s*(%|k\b|đ\b))?", re.IGNORECASE)
# Transcripts write a code the way it is printed ("TK01", "XL"); scripts spell it with Vietnamese letter names.
CODE_RE = re.compile(r"(?<!\w)(?:(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z\d]{2,12}|[A-Z]{2,6})(?!\w)")
LETTER_NAMES = {
    "a": "a", "b": "bê", "c": "xê", "d": "dê", "e": "e", "f": "ép", "g": "giê", "h": "hát", "i": "i",
    "j": "gi", "k": "ca", "l": "e lờ", "m": "em mờ", "n": "en nờ", "o": "o", "p": "pê", "q": "quy",
    "r": "e rờ", "s": "ét xì", "t": "tê", "u": "u", "v": "vê", "w": "vê kép", "x": "ích xì", "y": "i dài",
    "z": "dét",
}
# Spoken variants of the same number word: lăm/năm, tư/bốn, ngàn/nghìn, lẻ/linh (diacritics already removed).
NUMBER_VARIANTS = {"lam": "nam", "tu": "bon", "ngan": "nghin", "le": "linh"}


def _hundreds_words(value: int, full: bool) -> list[str]:
    hundreds, tens, units = value // 100, value // 10 % 10, value % 10
    words = [DIGIT_WORDS[hundreds], "trăm"] if hundreds or full else []
    if tens:
        words += ["mười"] if tens == 1 else [DIGIT_WORDS[tens], "mươi"]
    elif units and words:
        words.append("linh")
    if units:
        words.append("lăm" if units == 5 and tens else "mốt" if units == 1 and tens > 1 else DIGIT_WORDS[units])
    return words


def vietnamese_number_words(value: int) -> str:
    if value == 0:
        return DIGIT_WORDS[0]
    if value >= 10 ** 12:
        return " ".join(DIGIT_WORDS[int(digit)] for digit in str(value))
    groups, words = [], []
    while value:
        groups.append(value % 1000)
        value //= 1000
    for scale in range(len(groups) - 1, -1, -1):
        if groups[scale]:
            words += [*_hundreds_words(groups[scale], full=scale < len(groups) - 1), SCALE_WORDS[scale]]
    return " ".join(word for word in words if word)


def spell_numbers(text: str) -> str:
    def spoken(match: re.Match) -> str:
        integer, decimals, unit = match.groups()
        words = vietnamese_number_words(int(integer.replace(".", "")))
        if decimals:
            words += " phẩy " + " ".join(DIGIT_WORDS[int(digit)] for digit in decimals)
        suffix = {"%": " phần trăm", "k": " nghìn", "đ": " đồng"}.get((unit or "").lower(), "")
        return f" {words}{suffix} "

    return NUMBER_RE.sub(spoken, text)


def spell_codes(text: str) -> str:
    def spoken(match: re.Match) -> str:
        return " " + " ".join(DIGIT_WORDS[int(char)] if char.isdigit() else LETTER_NAMES[char.lower()]
                              for char in match[0]) + " "

    return CODE_RE.sub(spoken, text)


def speech_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", spell_numbers(spell_codes(text)).lower().replace("đ", "d"))
    tokens = re.findall(r"[a-z0-9]+", "".join(c for c in normalized if not unicodedata.combining(c)))
    return [NUMBER_VARIANTS.get(token, token) for token in tokens]


def native_speech_issues(expected: str, transcript: str) -> tuple[list[str], float]:
    wanted, heard = speech_tokens(expected), speech_tokens(transcript)
    matcher = difflib.SequenceMatcher(None, wanted, heard, autojunk=False)
    similarity = matcher.ratio()
    matched = sum(block.size for block in matcher.get_matching_blocks())
    issues = []
    if len(heard) < max(1, min(3, len(wanted))) or len(heard) < len(wanted) / 3:
        issues.append("native_speech_unintelligible")
    elif similarity < 0.55:
        issues.append("native_speech_mismatch")
    elif wanted and (matched / len(wanted) < 0.8 or (wanted[:len(heard)] == heard and len(wanted) - len(heard) >= 2)):
        issues.append("native_speech_incomplete")
    for size in (2, 3, 4):
        wanted_chunks = [tuple(wanted[i:i + size]) for i in range(len(wanted) - size + 1)]
        heard_chunks = [tuple(heard[i:i + size]) for i in range(len(heard) - size + 1)]
        if any(heard_chunks.count(chunk) >= 2 and heard_chunks.count(chunk) > wanted_chunks.count(chunk)
               for chunk in set(heard_chunks)):
            issues.append("native_speech_repetition")
            break
    return issues, round(similarity, 3)


def boundary_speech_issues(previous_expected: str, expected: str, previous_heard: str, heard: str) -> list[str]:
    before, current = speech_tokens(previous_heard), speech_tokens(heard)
    planned_before, planned_current = speech_tokens(previous_expected), speech_tokens(expected)
    for size in range(min(12, len(before), len(current)), 2, -1):
        if before[-size:] == current[:size] and not (
            planned_before[-size:] == planned_current[:size] == current[:size]
        ):
            return ["native_speech_boundary_repetition"]
    return []


def silence_intervals(stderr: str, duration: float) -> list[tuple[float, float]]:
    intervals, start = [], None
    for kind, value in re.findall(r"silence_(start|end):\s*([0-9.]+)", stderr):
        timestamp = min(duration, float(value))
        if kind == "start":
            start = timestamp
        elif start is not None:
            intervals.append((start, timestamp))
            start = None
    if start is not None:
        intervals.append((start, duration))
    return intervals


def native_audio_gap_issues(stderr: str, duration: float) -> tuple[list[str], dict]:
    intervals = silence_intervals(stderr, duration)
    leading = max((end - begin for begin, end in intervals if begin <= 0.2), default=0.0)
    trailing = max((end - begin for begin, end in intervals if end >= duration - 0.1), default=0.0)
    issues = []
    if leading >= 1.0:
        issues.append("native_audio_leading_silence")
    if trailing >= 1.0:
        issues.append("native_audio_trailing_silence")
    return issues, {"leading_silence_seconds": round(leading, 3), "trailing_silence_seconds": round(trailing, 3)}


def audio_measurements(stderr: str, duration: float) -> dict:
    intervals = silence_intervals(stderr, duration)
    _, metrics = native_audio_gap_issues(stderr, duration)
    active = max(0.0, duration - sum(end - begin for begin, end in intervals))
    metrics.update({"duration_seconds": duration, "active_audio_seconds": round(active, 3)})
    for label, key in (("mean_volume", "mean_volume_db"), ("max_volume", "peak_volume_db")):
        match = re.search(rf"{label}:\s*(-?[0-9.]+)\s*dB", stderr)
        if match:
            metrics[key] = float(match.group(1))
    if active > 0 and duration > 0 and "mean_volume_db" in metrics:
        metrics["active_volume_db"] = round(metrics["mean_volume_db"] - 10 * math.log10(active / duration), 3)
    return metrics


def native_trim_window(duration: float, metrics: dict) -> tuple[float, float]:
    # Remove only measured near-silence, leaving 120ms around speech. Video and
    # audio must use this identical window so mouth movements remain aligned.
    if duration <= 0 or metrics.get("active_audio_seconds", duration) < 0.25:
        return 0.0, duration
    leading, trailing = (float(metrics.get(key, 0)) for key in ("leading_silence_seconds", "trailing_silence_seconds"))
    start = max(0.0, leading - 0.12) if leading >= 0.35 else 0.0
    end = duration - max(0.0, trailing - 0.12) if trailing >= 0.35 else duration
    return (round(start, 3), round(end, 3)) if end - start >= 0.5 else (0.0, duration)


def volume_gain(reference: dict, current: dict) -> float:
    if "active_volume_db" not in reference or "active_volume_db" not in current:
        return 0.0
    gain = max(-6.0, min(6.0, reference["active_volume_db"] - current["active_volume_db"]))
    if "peak_volume_db" in current:
        gain = min(gain, -1.0 - current["peak_volume_db"])
    return round(gain, 3)
