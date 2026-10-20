"""New wording for a scene line the video model could not speak cleanly, before the scene is generated again."""

import json
import re

import httpx

from app.core.credential_vault import CredentialVault
from app.pipelines.dialogue_boundaries import BOUNDARY_POLICY, SPOKEN_CODE_RULE
from app.pipelines.planner_schema import response_content, speech_word_target, spoken_schema
from app.pipelines.planner_transport import generation_params

ISSUE_HINTS = {
    "native_speech_incomplete": "the model dropped words or its speech was cut off before the line ended",
    "native_speech_repetition": "the model repeated words or restarted part of the line",
    "native_speech_mismatch": "the model said different words from the line",
    "native_speech_boundary_repetition": "the model repeated the end of the previous scene",
}

REWRITE_INSTRUCTION = (
    "You edit Vietnamese short-video voice lines. A video model with native speech failed to say one scene's line "
    "cleanly: the report lists what went wrong and what the model actually said. Write a new line for that scene "
    "only, with different wording that is easy to say in one natural take. Keep its meaning, its role in the story "
    "and every fact and exact figure, and keep it flowing from the previous scene's line into the next one. Stay "
    "between min_words and max_words, about target_words; Vietnamese words are separated by spaces, so count them. "
    "Find where the model stumbled in its transcript and rephrase that part: prefer short complete sentences, avoid "
    "tongue twisters, runs of similar syllables, long lists and words the model misread. " + SPOKEN_CODE_RULE
    + BOUNDARY_POLICY + "Use plain Vietnamese words separated by single spaces; only periods, commas, question "
    "marks and exclamation marks directly after a word; no digits, symbols, quotes, emoji or Markdown. Never add "
    "new facts, greetings or a call to action that was not there. Return only JSON: {\"voice_script\": \"...\"}."
)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


async def rewrite_scene_line(
    scenes: list[dict], index: int, issues: list[str], transcript: str, vault: CredentialVault, model: str,
) -> str:
    api_key = vault.get("openai_planner")
    if not api_key:
        raise ValueError("planner_credentials_missing")
    scene = next((item for item in scenes if item["index"] == index), None)
    if scene is None:
        raise ValueError("scene_not_found")
    low, target, high = speech_word_target(scene["duration_seconds"])
    request = {
        "failed_scene": f"scene_{index}",
        "problems": [ISSUE_HINTS.get(issue, issue) for issue in issues] or ["the spoken line did not match"],
        "model_said": transcript,
        "line_to_rewrite": scene["voice_script"],
        "beat": scene.get("beat", ""),
        "purpose": scene.get("purpose", ""),
        "min_words": low, "target_words": target, "max_words": high,
        "whole_script_in_order": [f"scene_{item['index']}: {item['voice_script']}" for item in scenes],
    }
    try:
        async with httpx.AsyncClient(
            base_url="https://api.openai.com/v1/", headers={"Authorization": "Bearer " + api_key},
            timeout=httpx.Timeout(90, connect=10), follow_redirects=False,
        ) as client:
            response = await client.post("chat/completions", json={
                "model": model,
                "messages": [
                    {"role": "system", "content": REWRITE_INSTRUCTION},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                **generation_params(model, temperature=0.7, max_tokens=800),
            })
            response.raise_for_status()
            line = json.loads(response_content(response.json())).get("voice_script")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise RuntimeError("planner_quota_or_rate_limit" if status == 429 else "planner_unavailable") from None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise RuntimeError("planner_line_rewrite_failed") from None
    line = line.strip() if isinstance(line, str) else ""
    # The same pace bounds the planner used, so a rewrite neither gets cut off nor drags.
    if (not re.fullmatch(spoken_schema(scene["duration_seconds"])["pattern"], line)
            or not low <= len(line.split()) <= high or _words(line) == _words(scene["voice_script"])):
        raise RuntimeError("planner_line_rewrite_invalid")
    return line
