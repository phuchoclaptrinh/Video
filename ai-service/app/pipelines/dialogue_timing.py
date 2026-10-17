"""Validate plain spoken text without quotas, LLM calls or dialogue rewrites."""

import logging
import re

from app.pipelines.dialogue_boundaries import normalize_dialogue_boundaries
from app.pipelines.planner_schema import MAX_SPOKEN_CHARS, spoken_schema

logger = logging.getLogger(__name__)


def validate_scene_dialogue(
    clips: list[dict], *,
    response_id: str | None = None,
) -> list[dict]:
    if any(not 2 <= len(clip["voice_script"]) <= MAX_SPOKEN_CHARS
           or not re.fullmatch(spoken_schema(clip.get("duration_seconds", 8))["pattern"], clip["voice_script"])
           for clip in clips):
        logger.warning("Planner spoken text format mismatch; response_id=%s", response_id)
        raise RuntimeError("planner_spoken_text_invalid")
    return normalize_dialogue_boundaries(clips)
