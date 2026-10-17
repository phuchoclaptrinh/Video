"""Formatting-only scene joins, shared by planning and dialogue edits."""

import copy
import re

BOUNDARY_POLICY = (
    "Prefer a complete thought in each scene. A scene may end with normal sentence punctuation; "
    "visual continuity does not require an unfinished sentence. Use a comma only when a complete "
    "clause naturally continues in the next scene. Never split a compound or tightly connected "
    "phrase, proper name, or number and unit across scenes. Keep ch\u01a1i game, tai nghe, "
    "tr\u1ea3i nghi\u1ec7m and th\u00fa v\u1ecb intact. Never end a line with an incomplete phrase such as "
    "tr\u1ea3i nghi\u1ec7m ch\u01a1i or th\u00fa just to meet a count. Compose shorter complete wording instead "
    "of chopping words or padding. No fresh greetings, repeated introductions or duplicated boundary words. "
)

# Lowercase only recognizable clause openings; unknown names stay untouched.
_CLAUSE_OPENINGS = (
    "v\u00e0", "nh\u01b0ng", "v\u00ec", "n\u00ean", "\u0111\u1ec3", "khi", "cho \u0111\u1ebfn khi",
    "m\u00ecnh", "ch\u00fang ta", "b\u1ea1n", "cu\u1ed1n s\u00e1ch", "c\u00f3 nh\u1eefng", "c\u1ee9",
)


def normalize_dialogue_boundaries(clips: list[dict], editable_indices: set[int] | None = None) -> list[dict]:
    normalized = copy.deepcopy(clips)
    if len(normalized) < 2:
        return normalized
    last_index = max(clip["index"] for clip in normalized)
    for clip in normalized:
        if editable_indices is not None and clip["index"] not in editable_indices:
            continue
        line = clip["voice_script"].strip()
        if clip["index"] == last_index and not line.endswith((".", "?", "!")):
            line = line.rstrip(",") + "."
        previous = next((item for item in normalized if item["index"] == clip["index"] - 1), {})
        if previous.get("voice_script", "").rstrip().endswith(","):
            first = re.match(r"[^\W\d_]+", line)
            lower = line.casefold()
            if first and not first[0].isupper() and any(
                lower == opening or lower.startswith(opening + " ") for opening in _CLAUSE_OPENINGS
            ):
                line = line[:1].lower() + line[1:]
        clip["voice_script"] = line
    return normalized
