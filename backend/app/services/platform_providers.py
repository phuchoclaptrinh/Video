"""Provider selection is immutable per draft, not mutable application state."""

import copy
import os
import time

import httpx
from fastapi import HTTPException
from sqlalchemy import insert, select, update

from app.services.platform_shared import (
    drafts,
    platform_settings,
    story_plan_versions,
    video_projects,
)

IMAGE_PROVIDER_KEY = "image_provider"


class PlatformProvidersMixin:
    def _default_video_model(self, provider: str | None = None) -> str:
        provider = provider or self.provider
        return {
            "poyo": os.getenv("POYO_MODEL", "veo3.1-fast"),
            "seedvis": os.getenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1"),
            "seedance": os.getenv("SEEDANCE_MODEL", "seedance-1-5-pro-251215"),
            "fake": "fake",
        }[provider]

    def image_default(self) -> dict:
        """Image provider and model for new drafts: the admin's choice, else the server environment."""
        with self.engine.connect() as db:
            saved = db.execute(
                select(platform_settings.c.value).where(platform_settings.c.key == IMAGE_PROVIDER_KEY)
            ).scalar_one_or_none()
        if saved:
            return {"provider": saved["provider"], "model": saved["model"]}
        provider = os.getenv("IMAGE_PROVIDER", "openai")
        return {
            "provider": provider,
            "model": os.getenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2")
            if provider == "seedvis"
            else os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare"),
        }

    def set_image_default(self, provider: str, model: str) -> dict:
        item = next(
            (p for p in self.provider_catalog(refresh=True)["image_providers"] if p["id"] == provider), None
        )
        found = next((m for m in item["models"] if m["id"] == model), None) if item else None
        if found is None:
            raise HTTPException(422, "unsupported_image_provider_model")
        if found.get("available") is False:
            raise HTTPException(409, f"Model {model} chưa khả dụng trên {item['label']}. Hãy chọn model khác.")
        value = {"provider": provider, "model": model}
        with self.engine.begin() as db:
            updated = db.execute(
                update(platform_settings).where(platform_settings.c.key == IMAGE_PROVIDER_KEY)
                .values(value=value, updated=time.time())
            ).rowcount
            if not updated:
                db.execute(insert(platform_settings).values(key=IMAGE_PROVIDER_KEY, value=value, updated=time.time()))
        return value

    def provider_catalog(self, *, refresh: bool = False) -> dict:
        try:
            response = self.http.get(
                "/internal/v1/providers",
                timeout=15,
                **({"params": {"refresh": "true"}} if refresh else {}),
            )
            response.raise_for_status()
            result = response.json()
            for provider in result["video_providers"]:
                provider["enabled"] = provider["id"] == "fake" or self.paid_enabled
                for model in provider["models"]:
                    model["local_image_upload"] = model["image_input"]
                    model["continuity_enabled"] = model["image_input"]
            if not isinstance(result["image_providers"], list):
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(
                503, "Chưa tải được danh sách provider từ AI service. Hãy kiểm tra AI service."
            ) from None

    def _plan_target(self, uid, body) -> dict:
        previous = {}
        if body.project_id:
            project = self.owned(video_projects, body.project_id, uid)
            with self.engine.connect() as db:
                payload = (
                    db.execute(
                        select(story_plan_versions.c.payload).where(
                            story_plan_versions.c.project_id == project["id"],
                            story_plan_versions.c.version == project["current_plan_version"],
                        )
                    ).scalar_one_or_none()
                    or {}
                )
            previous = payload.get("render_target", {})
        provider = body.provider or previous.get("provider") or self.provider
        image_default = self.image_default()
        if not self.credit_exempt(uid):
            # Members always render images with the provider the admin picked.
            # Campaign items arrive as a SimpleNamespace, so copy generically rather than via pydantic.
            body = copy.copy(body)
            body.image_provider = body.image_model = None
            previous = {key: value for key, value in previous.items() if not key.startswith("image_")}
        image_provider = body.image_provider or previous.get("image_provider") or image_default["provider"]
        target = {
            "provider": provider,
            "video_model": body.video_model
            or (previous.get("video_model") if previous.get("provider") == provider else None)
            or self._default_video_model(provider),
            "image_provider": image_provider,
            "image_model": body.image_model
            or (
                previous.get("image_model")
                if previous.get("image_provider") == image_provider
                else None
            )
            or (
                image_default["model"]
                if image_default["provider"] == image_provider
                else os.getenv("SEEDVIS_IMAGE_MODEL", "gpt-image-2")
                if image_provider == "seedvis"
                else os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
            ),
        }
        if provider != "fake" and not self.paid_enabled:
            raise HTTPException(
                409, "Máy chủ chưa bật ENABLE_PAID_GENERATION cho provider có tính phí."
            )
        if any(
            getattr(body, field) is not None
            for field in ("provider", "video_model", "image_provider", "image_model")
        ):
            catalog = self.provider_catalog()
            for kind, selected_provider, selected_model in (
                ("video", provider, target["video_model"]),
                ("image", image_provider, target["image_model"]),
            ):
                item = next(
                    (p for p in catalog[kind + "_providers"] if p["id"] == selected_provider), None
                )
                if item is None or not any(m["id"] == selected_model for m in item["models"]):
                    raise HTTPException(422, f"unsupported_{kind}_provider_model")
                model = next(m for m in item["models"] if m["id"] == selected_model)
                needs_model = kind == "video" or (
                    (body.use_planner or body.require_planner) and not body.asset_id
                )
                if needs_model and model.get("available") is False:
                    raise HTTPException(
                        409,
                        f"Model {selected_model} chưa khả dụng trên {selected_provider}. Hãy cập nhật danh sách model hoặc chọn model khác.",
                    )
        self.require_visible_model(uid, provider, target["video_model"])
        return target

    def _with_render_target(self, target: dict):
        if getattr(self, "render_target", None) == target:
            return self
        scoped = copy.copy(self)
        scoped.render_target = dict(target)
        scoped.provider = target["provider"]
        return scoped

    def _for_draft(self, draft: dict):
        target = draft["payload"].get("render_target")
        return self._with_render_target(target) if target else self

    def _image_target(self, draft: dict) -> dict:
        target = draft["payload"].get("render_target") or {}
        return (
            {"provider": target["image_provider"], "model": target["image_model"]} if target else {}
        )

    def _for_batch(self, row: dict):
        if not row.get("draft_id"):
            return self
        return self._for_draft(self.owned(drafts, row["draft_id"], row["user_id"]))
