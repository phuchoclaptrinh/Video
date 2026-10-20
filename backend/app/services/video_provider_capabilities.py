"""Conservative internal capabilities; never enable an undocumented paid API."""

import hashlib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class VideoProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    native_audio: bool = Field(default=False, strict=True)
    audio_control: bool = Field(default=False, strict=True)
    first_frame: bool = Field(default=False, strict=True)
    reference_images: bool = Field(default=False, strict=True)
    video_continuation: bool = Field(default=False, strict=True)
    continuation_requires_video_url: bool = Field(default=False, strict=True)
    voice_identity_guaranteed: bool = Field(default=False, strict=True)
    generation_durations: list[int] = Field(default_factory=list)
    reference_image_durations: list[int] = Field(default_factory=list)
    continuation_durations: list[int] = Field(default_factory=list)
    continuation_output: Literal["UNSUPPORTED", "SEGMENT", "CUMULATIVE"] = "UNSUPPORTED"
    reason: str = "no_verified_continuation_contract"
    evidence_url: str | None = None
    capability_source: Literal["ADAPTER", "DOCUMENTED_FALLBACK"] = "ADAPTER"

    @classmethod
    def documented_fallback(cls, provider: str, model: str):
        if provider == "seedvis":
            durations = {"Veo-3.1": [8], "seedance_2.5": [5, 10, 20, 25, 30],
                         "seedance_2.0_fast": [5, 10, 15], "Omni-Flash": [10]}
            return cls(provider=provider, model=model, native_audio=model in {"Veo-3.1", "Omni-Flash"},
                       first_frame=model in {"Veo-3.1", "Omni-Flash"}, reference_images=model in durations,
                       generation_durations=durations.get(model, []),
                       reason="video_continuation_not_documented_for_model" if model == "Veo-3.1" else "native_audio_unverified_model",
                       evidence_url="https://seedvis.com/api/v1/developer-api/docs.md?early_access=1", capability_source="DOCUMENTED_FALLBACK")
        if provider == "poyo" and model in {
            "veo3.1-fast", "veo3.1-quality", "veo3.1-lite",
            "veo3.1-fast-official", "veo3.1-quality-official", "veo3.1-lite-official",
        }:
            official = model.endswith("-official")
            references = model in {"veo3.1-fast", "veo3.1-fast-official", "veo3.1-quality-official"}
            return cls(
                provider=provider, model=model, native_audio=True,
                audio_control=official, first_frame=model != "veo3.1-lite", reference_images=references,
                generation_durations=[4, 6, 8] if official else [8],
                reference_image_durations=[8] if references else [],
                reason="video_continuation_not_documented_for_model",
                evidence_url="https://docs.poyo.ai/api-manual/video-series/veo-3-1" + ("-official" if official else ""),
                capability_source="DOCUMENTED_FALLBACK",
            )
        return cls(
            provider=provider, model=model, first_frame=provider == "seedance",
            native_audio=provider == "seedance" and model.startswith("seedance-1-5-pro"),
            audio_control=provider == "seedance" and model.startswith("seedance-1-5-pro"),
            generation_durations=list(range(4, 13)) if provider in {"seedance", "fake"} else [],
            reason="simulated_provider" if provider == "fake" else "no_verified_continuation_contract_in_adapter",
            capability_source="DOCUMENTED_FALLBACK",
        )


def safe_video_url(value: str | None) -> str:
    if not value:
        return "none"
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    try:
        parsed = urlsplit(value)
        # Signed URLs can contain credentials in both path and query.
        host = parsed.hostname if parsed.scheme in {"https", "http"} else "local-asset"
        return f"{parsed.scheme or 'asset'}://{host}/[redacted]#{digest}"
    except ValueError:
        return "invalid-url"
