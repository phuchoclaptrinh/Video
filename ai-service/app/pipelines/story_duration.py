"""Resolve total video length independently of a provider's per-clip duration."""

import re
import unicodedata
from decimal import ROUND_CEILING, Decimal

_DURATION = re.compile(
    r"(?<![\w.,])(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>gi\u00e2y|giay|seconds?|secs?|s|ph\u00fat|phut|minutes?|mins?)(?!\w)",
)
_VIDEO_PREFIX = re.compile(
    r"(?:\bvideo|\bclip|th\u1eddi\s+l\u01b0\u1ee3ng|thoi\s+luong|"
    r"\bd\u00e0i|\bdai|\bduration|\blength|\blasting)"
    r"\s*(?:(?:kho\u1ea3ng|khoang|about|around|of|l\u00e0|la)\s*)?[:=]?\s*$",
)
_SCENE_PREFIX = re.compile(
    r"(?:(?:m\u1ed7i|moi|t\u1eebng|tung|each)\s+(?:c\u1ea3nh|canh|scene|clip)|"
    r"\b(?:c\u1ea3nh|canh|scene)\s*\d*)\s*"
    r"(?:(?:d\u00e0i|dai|lasting|kho\u1ea3ng|khoang|about)\s*)*[:=]?\s*$",
)


def requested_duration_seconds(prompt: str) -> int | None:
    text = unicodedata.normalize("NFC", prompt).casefold()
    candidates, preferred = set(), set()
    for match in _DURATION.finditer(text):
        prefix = text[max(0, match.start() - 60):match.start()]
        if _SCENE_PREFIX.search(prefix):
            continue
        amount = Decimal(match["amount"].replace(",", "."))
        if match["unit"].startswith(("ph", "min")):
            amount *= 60
        seconds = int(amount.to_integral_value(rounding=ROUND_CEILING))
        if seconds <= 0:
            continue
        candidates.add(seconds)
        if _VIDEO_PREFIX.search(prefix):
            preferred.add(seconds)
    choices = preferred or candidates
    return next(iter(choices)) if len(choices) == 1 else None


def duration_policy(target: int, allowed: list[int]) -> dict:
    options = sorted({value for value in allowed if type(value) is int and 0 < value <= 120})
    if type(target) is not int or not 0 < target <= 600 or not options or target > 5 * max(options):
        raise RuntimeError("planner_duration_unsupported")
    # Keep one balanced candidate per total/count; at most five paid clips.
    states = {0: ()}
    candidates = []
    for count in range(1, 6):
        following = {}
        for total, sequence in states.items():
            for value in options:
                length = total + value
                schedule = tuple(sorted((*sequence, value), reverse=True))
                previous = following.get(length)
                if previous is None or (max(schedule) - min(schedule), schedule) < (max(previous) - min(previous), previous):
                    following[length] = schedule
        candidates.extend((total - target, count, schedule) for total, schedule in following.items() if total >= target)
        states = following
    _, count, schedule = min(candidates)
    return {
        "requested_duration_seconds": target,
        "planned_duration_seconds": sum(schedule),
        "scene_count": count,
        "suggested_scene_durations": list(schedule),
    }
