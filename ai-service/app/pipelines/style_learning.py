"""Learn a reusable short-form style blueprint from a reference video's frames and transcript."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.pipelines.planner_schema import object_schema, response_content, strict_format, text_schema
from app.pipelines.planner_transport import (
    PLANNER_DEADLINE_SECONDS,
    PlannerRequests,
    generation_params,
)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "style_learning.md"
Rule = Annotated[str, Field(min_length=5, max_length=300)]


class StyleHook(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    technique: str = Field(min_length=3, max_length=300)
    opening_visual: str = Field(min_length=3, max_length=400)
    opening_line: str = Field(max_length=300)
    why_it_works: str = Field(min_length=3, max_length=400)


class StyleBeat(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_seconds: float = Field(ge=0, le=600)
    end_seconds: float = Field(ge=0, le=600)
    beat: str = Field(min_length=2, max_length=120)
    shot: str = Field(max_length=200)
    camera: str = Field(max_length=200)
    on_screen_text: str = Field(max_length=200)
    audio: str = Field(max_length=200)
    purpose: str = Field(max_length=300)


class StyleSpeech(BaseModel):
    """How the reference lines are written, abstracted so it can be reused without copying sentences."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    addressing: str = Field(max_length=400)
    language_register: str = Field(max_length=400)
    sentence_style: str = Field(max_length=500)
    emotion_arc: str = Field(max_length=400)
    transitions: str = Field(max_length=400)
    devices: list[Rule] = Field(max_length=8)
    signature_words: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(max_length=15)
    hook_formula: str = Field(max_length=300)
    cta_formula: str = Field(max_length=300)
    script_rules: list[Rule] = Field(max_length=10)


class StyleBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=10, max_length=800)
    language: str = Field(min_length=2, max_length=40)
    format: str = Field(min_length=2, max_length=120)
    narration_mode: Literal["on_screen_speaker", "voice_over", "music_only"]
    hook: StyleHook
    beats: list[StyleBeat] = Field(min_length=1, max_length=12)
    pacing: str = Field(min_length=5, max_length=400)
    visual_style: str = Field(min_length=5, max_length=500)
    music: str = Field(max_length=300)
    editing: str = Field(max_length=400)
    rules: list[Rule] = Field(min_length=3, max_length=10)
    avoid: list[Rule] = Field(max_length=6)
    speech: StyleSpeech


NARRATION_OVERRIDES = {
    "on_screen_speaker": (
        "Set narration_mode to on_screen_speaker: an adult Vietnamese presenter is on camera the whole time and "
        "talks directly to the viewer with lip-sync, even when the product is for children; children may appear "
        "only as silent extras or not at all."
    ),
    "voice_over": "Set narration_mode to voice_over: the voice is an unseen narrator and nobody on screen speaks.",
    "music_only": (
        "The reference has no speech: set narration_mode to voice_over and keep each spoken line to one short, "
        "punchy sentence in the reference's mood."
    ),
}


def speech_style_instruction(blueprint: dict) -> str:
    """How to write the lines like the reference, without reusing its sentences or its voice."""
    speech = blueprint.get("speech") or {}
    metrics = blueprint.get("speech_metrics") or {}
    if not (speech or metrics):
        return ""
    writing_metrics = {key: metrics[key] for key in (
        "words", "sentences", "avg_words_per_sentence", "questions", "exclamations", "particles", "addressing_terms",
    ) if key in metrics}
    return (
        " WRITING STYLE OF THE LINES (match how the reference lines are written): write every voice_script with "
        "the same self-reference and way of addressing the viewer, the same register and slang level, similar "
        "sentence length and structure, the same kind of transitions between ideas, its rhetorical devices and its "
        "characteristic particles and filler words used about as often, and build the opening and closing lines on "
        "its hook_formula and cta_formula with this video's own content in the slots. Treat the measured writing "
        "metrics as a feel for sentence length and punctuation, not as a quota. This covers wording only: the "
        "voice_bible follows the VOICE DELIVERY POLICY, never the reference speaker's voice. Never reuse a sentence "
        "or distinctive phrase from the reference. Writing style: "
        + json.dumps({"speech": speech, "writing_metrics": writing_metrics}, ensure_ascii=False)
    )


def learned_style_instruction(blueprint: dict) -> str:
    """Planner direction from a learned blueprint; the reference's own words never travel with it."""
    hook = {key: value for key, value in (blueprint.get("hook") or {}).items() if key != "opening_line"}
    compact = {key: blueprint.get(key) for key in (
        "title", "format", "narration_mode", "beats", "pacing", "visual_style", "editing", "rules", "avoid",
    )} | {"hook": hook}
    narration = NARRATION_OVERRIDES.get(blueprint.get("narration_mode"), "")
    return (
        "\nLEARNED STYLE REFERENCE (highest-priority creative direction, learned from a reference video the user "
        "chose for this video). The user wants this video to look and feel like the reference, so it OVERRIDES the "
        "default creative guidance above: the story-over-catalogue preference, the one-detail-once and one-detail-scene "
        "limits, the default hook patterns, the default narration_mode choice, the SALES STORY DIRECTION requirement "
        "that every scene shows the product worn or used, and any CREATIVE FORMAT. Follow the reference instead: its "
        "format and who appears on screen, how the product is held and shown, its beat order, shot sizes, camera "
        "moves and cut rhythm. "
        + narration
        + " Distribute the reference beats across this video's scenes in order and in proportion to time, so each "
        "scene performs the matching beats; a pitch that walks through features in sequence stays a feature "
        "walkthrough, but each feature beat says why it matters to the viewer instead of reading out colors and details "
        "already on screen. Still keep the truth rules, Vietnamese people and the spoken text format: "
        "never invent prices, discounts, sizes, materials or other facts a beat mentions, and fulfil such a beat with "
        "supplied or visible facts or a neutral call to action. Never copy the reference's words, brand, people or "
        "footage. Reference: "
        + json.dumps(compact, ensure_ascii=False)
        + speech_style_instruction(blueprint)
    )


def blueprint_format() -> dict:
    def texts(minimum: int, maximum: int) -> dict:
        return {"type": "array", "items": text_schema(5, 300), "minItems": minimum, "maxItems": maximum}

    hook = object_schema({
        "technique": text_schema(3, 300), "opening_visual": text_schema(3, 400),
        "opening_line": text_schema(0, 300), "why_it_works": text_schema(3, 400),
    })
    speech = object_schema({
        "addressing": text_schema(0, 400), "language_register": text_schema(0, 400),
        "sentence_style": text_schema(0, 500),
        "emotion_arc": text_schema(0, 400), "transitions": text_schema(0, 400),
        "devices": texts(0, 8),
        "signature_words": {"type": "array", "items": text_schema(1, 40), "minItems": 0, "maxItems": 15},
        "hook_formula": text_schema(0, 300), "cta_formula": text_schema(0, 300),
        "script_rules": texts(0, 10),
    })
    beat = object_schema({
        "start_seconds": {"type": "number", "minimum": 0, "maximum": 600},
        "end_seconds": {"type": "number", "minimum": 0, "maximum": 600},
        "beat": text_schema(2, 120), "shot": text_schema(0, 200), "camera": text_schema(0, 200),
        "on_screen_text": text_schema(0, 200), "audio": text_schema(0, 200), "purpose": text_schema(0, 300),
    })
    return strict_format("style_blueprint", object_schema({
        "title": text_schema(2, 80), "summary": text_schema(10, 800), "language": text_schema(2, 40),
        "format": text_schema(2, 120),
        "narration_mode": {"type": "string", "enum": ["on_screen_speaker", "voice_over", "music_only"]},
        "hook": hook,
        "beats": {"type": "array", "items": beat, "minItems": 1, "maxItems": 12},
        "pacing": text_schema(5, 400), "visual_style": text_schema(5, 500),
        "music": text_schema(0, 300), "editing": text_schema(0, 400),
        "rules": texts(3, 10), "avoid": texts(0, 6), "speech": speech,
    }))


async def learn_style(
    *, frames: list[dict], transcript: str, duration_seconds: float, cut_times: list[float],
    audio_present: bool, source_label: str, vault, model: str,
    speech_metrics: dict | None = None,
) -> dict:
    key = vault.get("openai_planner")
    if not key:
        raise ValueError("planner_credentials_missing")
    facts = json.dumps({
        "source": source_label, "duration_seconds": round(duration_seconds, 2),
        "cut_times_seconds": [round(value, 2) for value in cut_times], "audio_present": audio_present,
        "transcript": transcript or "",
        "measured_writing_metrics": speech_metrics or {},
    }, ensure_ascii=False)
    content = [{"type": "text", "text": facts}]
    for frame in frames:
        content.append({"type": "text", "text": f"Frame at {frame['time_seconds']:.1f}s"})
        content.append({"type": "image_url", "image_url": {"url": frame["image_data_url"], "detail": "low"}})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": content},
        ],
        "response_format": blueprint_format(),
        **generation_params(model, temperature=0.3, max_tokens=4000),
    }
    try:
        async with (
            asyncio.timeout(PLANNER_DEADLINE_SECONDS),
            httpx.AsyncClient(
                base_url="https://api.openai.com/v1/",
                headers={"Authorization": "Bearer " + key},
                timeout=httpx.Timeout(90, connect=10, write=30, pool=5),
                follow_redirects=False,
            ) as client,
        ):
            response = await PlannerRequests(client).post(payload, phase="style")
        blueprint = StyleBlueprint.model_validate(json.loads(response_content(response.json())))
        # Measured writing numbers are kept verbatim next to the model's reading.
        return blueprint.model_dump(mode="json") | {"speech_metrics": speech_metrics or {}}
    except httpx.HTTPStatusError as exc:
        reason = {
            401: "planner_invalid_key", 403: "planner_invalid_key",
            429: "planner_quota_or_rate_limit", 404: "planner_model_unavailable",
        }.get(exc.response.status_code, "planner_upstream_error")
        raise RuntimeError(reason) from None
    except (TimeoutError, httpx.TimeoutException):
        raise RuntimeError("planner_timeout") from None
    except httpx.TransportError:
        raise RuntimeError("planner_connection_failed") from None
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("style_schema_mismatch") from None
