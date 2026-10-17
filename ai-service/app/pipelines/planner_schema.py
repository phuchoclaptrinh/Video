"""First-pass output contracts for scene duration and plain spoken dialogue."""

import os

from app.core.voice_presets import VoicePreset


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def text_schema(minimum: int = 0, maximum: int = 3000) -> dict:
    return {"type": "string", "pattern": rf"^[\s\S]{{{minimum},{maximum}}}$"}


# Safety cap against runaway output only; there is no maximum dialogue length.
MAX_SPOKEN_CHARS = 2000


def speech_word_target(duration: int) -> tuple[int, int, int]:
    """TikTok pace capped at about five words per second (one Vietnamese syllable per word).

    Generated clips read up to roughly that many words before the clip ends; longer lines lose their ending.
    """
    rate = float(os.getenv("DIALOGUE_WORDS_PER_SECOND", "5"))
    maximum = round(duration * rate)
    return round(maximum * 0.82), round(maximum * 0.92), maximum


def spoken_schema(duration: int, paced: bool = False) -> dict:
    word = r"[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u1E00-\u1EFF]+[,.?!]?"
    if not paced:
        # Shared clean-text check (validation, grounding, TTS narration): no pace target.
        return {"type": "string", "minLength": 2, "maxLength": MAX_SPOKEN_CHARS,
                "pattern": rf"^{word}(?: {word})*$",
                "description": (
                    f"Gripping, complete Vietnamese speech for this {duration}-second scene. Use vivid detail, "
                    "a reason why or rising tension, never filler. Keep whole phrases and a clear link to the narrative.")}
    low, target, high = speech_word_target(duration)
    return {"type": "string", "minLength": 2, "maxLength": MAX_SPOKEN_CHARS,
            # Only a loose ceiling is enforced: a grammar-forced minimum loops until the output limit, and a
            # tight ceiling makes the model glue words together. The described target sets the density.
            "pattern": rf"^{word}(?: {word}){{0,{round(target * 1.4) - 1}}}$",
            "description": (
                f"Gripping, complete Vietnamese speech for this {duration}-second scene at TikTok pace: about "
                f"{target} words, between {low} and {high} and never more than {high}, because a longer line is cut "
                "off at the end of the clip and a shorter one is spoken too slowly. Count the words before answering. "
                "Use vivid detail, a reason why or a direct call-out to the viewer, never filler. Keep whole phrases "
                "and a clear link to the narrative.")}


def scene_schema(durations: list[int]) -> dict:
    def variant(duration):
        return object_schema({
            "beat": text_schema(2, 80) | {"description": "Specific story beat, not a generic scene label."},
            "purpose": text_schema(0, 240) | {"description": (
                "What the viewer learns or feels here, and how this advances the preceding beat."
            )},
            "video_prompt": text_schema(80, 3000) | {"description": (
                "One concise English visual direction: framing, subject, action, environment, lighting. "
                "Show this scene's specific idea. No dialogue, voice profile, speech timing or repeated rule lists."
            )},
            "voice_script": spoken_schema(duration, paced=True),
            "start_state": text_schema(0, 500) | {"description": "Opening physical pose, props and composition only."},
            "end_state": text_schema(0, 500) | {"description": "Ending physical pose, props and composition only."},
            "transition_intent": {"type": "string", "enum": [
                "continue", "hard_cut", "match_cut", "intentional_camera_transition", "fade_to_black",
            ]},
            "duration_seconds": {"type": "integer", "enum": [duration]},
        })
    variants = [variant(duration) for duration in durations]
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def story_response_format(durations: list[int], preset: VoicePreset, policy: dict | None) -> dict:
    definitions = {f"scene_{duration}s": scene_schema([duration]) for duration in durations}

    def reference(options):
        variants = [{"$ref": f"#/$defs/scene_{duration}s"} for duration in options]
        return variants[0] if len(variants) == 1 else {"anyOf": variants}

    if policy:
        scenes = object_schema({f"scene_{index}": reference([duration])
                                for index, duration in enumerate(policy["suggested_scene_durations"], 1)})
    else:
        scenes = {"anyOf": [object_schema({f"scene_{index}": reference(durations)
                                          for index in range(1, count + 1)}) for count in range(1, 6)]}
    schema = object_schema({
        "video_type": text_schema(1, 80), "book_title": text_schema(0, 200),
        "audience": text_schema(1, 300), "core_value": text_schema(10, 1000),
        "visual_continuity": text_schema(40, 1200), "visual_bible": text_schema(40, 1600),
        "voice_bible": text_schema(40, 600),
        "narration_mode": {"type": "string", "enum": ["on_screen_speaker", "voice_over"], "description": (
            "voice_over when the voice is an off-screen narrator: children on screen, product or b-roll "
            "shots, or POV formats; on_screen_speaker only when an adult on screen talks to camera."
        )},
        "reference_image_prompt": text_schema(30, 3000), "scenes": scenes,
    })
    schema["$defs"] = definitions
    return strict_format("video_story", schema)


def grounding_response_format(clips: list[dict]) -> dict:
    scenes = {f"scene_{clip['index']}": object_schema({
        "video_prompt": text_schema(30, 3000),
        "voice_script": spoken_schema(clip.get("duration_seconds", 8)),
    }) for clip in clips}
    return strict_format("reference_story", object_schema({
        "book_title": text_schema(0, 200), "scenes": object_schema(scenes),
    }))


def speech_fit_response_format(clips: list[dict]) -> dict:
    """Shortening pass for lines over their clip's maximum, under the same paced contract as the first pass."""
    return strict_format("scene_speech_fit", object_schema({
        f"scene_{clip['index']}": spoken_schema(clip["duration_seconds"], paced=True) for clip in clips
    }))


def strict_format(name: str, schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


def unpack_scenes(decoded: dict) -> dict:
    """Convert keyed model output to the public clip list without rewriting any words."""
    if "scenes" not in decoded or "clips" in decoded:
        return decoded
    scenes = decoded["scenes"]
    if not isinstance(scenes, dict) or not 1 <= len(scenes) <= 5:
        raise ValueError("Planner scenes must be a keyed object")
    keys = [f"scene_{index}" for index in range(1, len(scenes) + 1)]
    if set(scenes) != set(keys) or any(not isinstance(scenes[key], dict) for key in keys):
        raise ValueError("Planner scene keys must be sequential")
    return {key: value for key, value in decoded.items() if key != "scenes"} | {
        "clips": [scenes[key] | {"index": index} for index, key in enumerate(keys, 1)],
    }


def response_content(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Planner response must be an object")
    choice = payload["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("Planner response must contain a message object")
    message = choice["message"]
    if message.get("refusal") or choice.get("finish_reason") == "content_filter":
        raise RuntimeError("planner_refused")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("planner_output_truncated")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("planner_empty_output")
    return content
