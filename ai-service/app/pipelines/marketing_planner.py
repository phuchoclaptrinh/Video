"""One bounded vision request for campaign strategy and distinct creative briefs."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.pipelines.dialogue_boundaries import SPOKEN_CODE_RULE
from app.pipelines.planner_schema import object_schema, response_content, strict_format, text_schema
from app.pipelines.planner_transport import (
    PLANNER_DEADLINE_SECONDS,
    PlannerRequests,
    generation_params,
)
from app.pipelines.visual_input import image_parts

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "marketing_campaign_planner.md"
Detail = Annotated[str, Field(min_length=1, max_length=600)]


class CampaignStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_summary: str = Field(min_length=5, max_length=600)
    audience: str = Field(min_length=2, max_length=300)
    objective: str = Field(min_length=2, max_length=300)
    positioning: str = Field(min_length=5, max_length=500)
    observed_details: list[Detail] = Field(max_length=20)
    needs_confirmation: list[Detail] = Field(max_length=20)
    content_pillars: list[Detail] = Field(min_length=1, max_length=6)


class CampaignVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=100)
    angle: str = Field(min_length=5, max_length=400)
    hook: str = Field(min_length=5, max_length=300)
    cta: str = Field(min_length=2, max_length=200)
    caption: str = Field(min_length=2, max_length=500)
    visual_direction: str = Field(min_length=10, max_length=600)
    channel: Literal["tiktok", "facebook", "instagram", "youtube"]
    funnel_stage: Literal["awareness", "consideration", "conversion"]
    publish_day: int = Field(ge=1, le=90, strict=True)


def campaign_format(count: int) -> dict:
    def texts(maximum):
        return {"type": "array", "items": text_schema(1, 600), "maxItems": maximum}

    strategy = object_schema(
        {
            "product_summary": text_schema(5, 600),
            "audience": text_schema(2, 300),
            "objective": text_schema(2, 300),
            "positioning": text_schema(5, 500),
            "observed_details": texts(20),
            "needs_confirmation": texts(20),
            "content_pillars": texts(6) | {"minItems": 1},
        }
    )
    variant = object_schema(
        {
            "title": text_schema(2, 100),
            "angle": text_schema(5, 400),
            "hook": text_schema(5, 300),
            "cta": text_schema(2, 200),
            "caption": text_schema(2, 500),
            "visual_direction": text_schema(10, 600),
            "channel": {"type": "string", "enum": ["tiktok", "facebook", "instagram", "youtube"]},
            "funnel_stage": {
                "type": "string",
                "enum": ["awareness", "consideration", "conversion"],
            },
            "publish_day": {"type": "integer", "minimum": 1, "maximum": 90},
        }
    )
    return strict_format(
        "marketing_campaign",
        object_schema(
            {
                "strategy": strategy,
                "variants": object_schema(
                    {f"video_{index}": variant for index in range(1, count + 1)}
                ),
            }
        ),
    )


async def create_marketing_campaign(
    prompt: str,
    vault,
    model: str,
    script_count: int,
    images: list[str],
    project_context: dict | None = None,
) -> dict:
    if not 2 <= script_count <= 10 or len(images) > 6:
        raise ValueError("campaign_input_invalid")
    key = vault.get("openai_planner")
    if not key:
        raise ValueError("planner_credentials_missing")
    text = json.dumps(
        {"brief": prompt, "script_count": script_count, "project_context": project_context or {}},
        ensure_ascii=False,
    )
    payload = {
        "model": model,
        **generation_params(model, temperature=0.6, max_tokens=7000),
        "messages": [
            {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")
             + "\nSpoken hooks follow this rule for codes: " + SPOKEN_CODE_RULE},
            {
                "role": "user",
                "content": [{"type": "text", "text": text}, *image_parts(images)]
                if images
                else text,
            },
        ],
        "response_format": campaign_format(script_count),
    }
    try:
        async with (
            asyncio.timeout(PLANNER_DEADLINE_SECONDS),
            httpx.AsyncClient(
                base_url="https://api.openai.com/v1/",
                headers={"Authorization": "Bearer " + key},
                timeout=httpx.Timeout(90, connect=10, write=15, pool=5),
                follow_redirects=False,
            ) as client,
        ):
            response = await PlannerRequests(client).post(payload, phase="campaign")
        decoded = json.loads(response_content(response.json()))
        strategy = CampaignStrategy.model_validate(decoded["strategy"]).model_dump(mode="json")
        variants = decoded["variants"]
        expected = [f"video_{index}" for index in range(1, script_count + 1)]
        if not isinstance(variants, dict) or set(variants) != set(expected):
            raise ValueError("campaign_variant_count_mismatch")
        items = [
            CampaignVariant.model_validate(variants[key]).model_dump(mode="json")
            for key in expected
        ]
        if any(
            len({" ".join(item[field].casefold().split()) for item in items}) != len(items)
            for field in ("angle", "hook")
        ):
            raise ValueError("campaign_duplicate_angles")
        return {"strategy": strategy, "variants": items}
    except httpx.HTTPStatusError as exc:
        reason = {
            401: "planner_invalid_key",
            403: "planner_invalid_key",
            429: "planner_quota_or_rate_limit",
            404: "planner_model_unavailable",
        }.get(exc.response.status_code, "planner_upstream_error")
        raise RuntimeError(reason) from None
    except (TimeoutError, httpx.TimeoutException):
        raise RuntimeError("planner_timeout") from None
    except httpx.TransportError:
        raise RuntimeError("planner_connection_failed") from None
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("campaign_schema_mismatch") from None
