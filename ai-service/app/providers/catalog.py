"""Installed models with Seedvis availability from its read-only models endpoint."""

import os

from app.providers.poyo_veo_compiler import PoyoVeoCompiler
from app.providers.seedvis_models import IMAGE_MODELS, VIDEO_MODELS
from app.schemas.provider import ProviderError


async def provider_catalog(registry, vault, *, refresh: bool = False):
    videos = []
    seedvis_available = None
    seedvis_error = None
    if "seedvis" in registry.providers and registry.get("seedvis").credential_configured:
        try:
            seedvis_available = {
                model["id"]
                for model in await registry.get("seedvis").available_models(refresh=refresh)
            }
        except ProviderError as exc:
            seedvis_error = exc.code
    for name, adapter in registry.providers.items():
        default = adapter.continuity_capabilities().model
        models = (
            sorted(PoyoVeoCompiler.models)
            if name == "poyo"
            else list(VIDEO_MODELS)
            if name == "seedvis"
            else [default]
        )
        items = []
        for model in models:
            capabilities = registry.get(name, model).continuity_capabilities()
            ratios = (
                list(VIDEO_MODELS[model].ratios)
                if name == "seedvis"
                else ["16:9", "9:16"]
                if name == "poyo"
                else ["16:9", "9:16", "1:1"]
            )
            items.append(
                {
                    "id": model,
                    "label": VIDEO_MODELS[model].label if name == "seedvis" else model,
                    "native_audio": capabilities.native_audio,
                    "story_supported": name == "fake"
                    or bool(
                        capabilities.native_audio
                        and capabilities.generation_durations
                        and (capabilities.first_frame or capabilities.reference_images)
                    ),
                    "duration_options": capabilities.generation_durations,
                    "ratio_options": ratios,
                    "image_input": capabilities.first_frame or capabilities.reference_images,
                    "available": model in seedvis_available
                    if name == "seedvis" and seedvis_available is not None
                    else None,
                }
            )
        videos.append(
            {
                "id": name,
                "label": {
                    "poyo": "PoYo",
                    "seedvis": "Seedvis",
                    "fake": "Demo",
                    "seedance": "Seedance",
                }.get(name, name),
                "configured": getattr(adapter, "credential_configured", True),
                "models": items,
                **({"catalog_error": seedvis_error} if name == "seedvis" and seedvis_error else {}),
            }
        )
    image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
    images = [
        {
            "id": "openai",
            "label": "OpenAI",
            "configured": bool(vault.get("openai_planner")),
            "models": [{"id": image_model, "label": image_model}],
        }
    ]
    if "seedvis" in registry.providers:
        images.append(
            {
                "id": "seedvis",
                "label": "Seedvis",
                "configured": registry.get("seedvis").credential_configured,
                "models": [
                    {
                        "id": model,
                        "label": label,
                        "available": model in seedvis_available
                        if seedvis_available is not None
                        else None,
                    }
                    for model, label in IMAGE_MODELS.items()
                ],
                **({"catalog_error": seedvis_error} if seedvis_error else {}),
            }
        )
    return {"video_providers": videos, "image_providers": images}
