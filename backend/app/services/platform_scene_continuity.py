"""Dependency validation and persistent scene continuity provenance."""

import hashlib
import os
import time

from fastapi import HTTPException
from sqlalchemy import select, update

from app.services.platform_shared import (
    drafts,
    logger,
    story_batches,
    video_quality_reports,
    video_scenes,
    videos,
)
from app.services.video_continuity import VideoContinuityStrategy, voice_instruction
from app.services.video_execution_client import ExecutionError
from app.services.video_provider_capabilities import VideoProviderCapabilities, safe_video_url


class PlatformSceneContinuityMixin:
    def _scene_native_trim(self, generation_id: str) -> dict:
        with self.engine.connect() as db:
            scene = db.execute(select(video_scenes).where(video_scenes.c.generation_id == generation_id)).mappings().first()
            if not scene:
                return {}
            reports = db.execute(select(video_quality_reports.c.metrics).where(
                video_quality_reports.c.project_id == scene["project_id"],
                video_quality_reports.c.scene_index == scene["scene_index"],
                video_quality_reports.c.stage == "NATIVE_SPEECH",
            ).order_by(video_quality_reports.c.created.desc())).scalars().all()
        metrics = dict(next((m for m in reports if m.get("generation_id") == generation_id and "trim_end_seconds" in m), {}))
        selected_end = (scene["continuity_metadata"] or {}).get("composition_end_seconds")
        if metrics and selected_end is not None:
            metrics["trim_end_seconds"] = max(metrics["trim_end_seconds"], min(selected_end, metrics.get("duration_seconds", selected_end)))
        return metrics

    def _continuity_strategy(self) -> VideoContinuityStrategy:
        model = getattr(self, "render_target", {}).get("video_model") or (
            os.getenv("POYO_MODEL", "veo3.1-fast") if self.provider == "poyo"
            else os.getenv("SEEDVIS_VIDEO_MODEL", "Veo-3.1") if self.provider == "seedvis"
            else "fake" if self.provider == "fake"
            else os.getenv("SEEDANCE_MODEL", "seedance-1-5-pro-251215")
        )
        cache = getattr(self, "_video_capability_cache", {})
        key = (self.provider, model)
        if key not in cache:
            try:
                capabilities = VideoProviderCapabilities.model_validate(
                    self.runtime.client.call(self.provider, "capabilities", **({"model": model} if getattr(self, "render_target", None) else {})),
                )
            except (ExecutionError, AttributeError, TypeError, ValueError):
                capabilities = VideoProviderCapabilities.documented_fallback(self.provider, model)
                logger.warning("[VIDEO] provider=%s model=%s capability_source=DOCUMENTED_FALLBACK", self.provider, model)
            if capabilities.provider != self.provider or capabilities.model != model:
                raise HTTPException(409, "video_provider_model_mismatch: restart backend and AI service with the same model")
            cache[key] = capabilities
            self._video_capability_cache = cache
        return VideoContinuityStrategy(self.provider, model, os.getenv("POYO_CONTINUITY_MODE", "adaptive"), cache[key])

    def _completed_scene_output(self, generation_id: str, uid: str) -> dict:
        job = self.store.get(generation_id)
        if job["status"] != "COMPLETED" or job["payload"]["spec"]["request"]["user_id"] != uid:
            raise HTTPException(409, "continuity_parent_not_completed")
        with self.engine.connect() as db:
            video = db.execute(select(videos).where(
                videos.c.generation_id == generation_id, videos.c.user_id == uid,
            )).mappings().first()
        if not video or not (self.storage / "videos" / video["filename"]).is_file():
            raise HTTPException(409, "continuity_parent_video_missing")
        outputs = (job.get("result") or {}).get("outputs") or []
        source = next((item.get("source_url") for item in outputs if item.get("source_url")), None)
        return {
            "generation_id": generation_id, "provider_task_id": job.get("provider_task_id"),
            "output_video_url": source or f"/api/v1/generations/{generation_id}/video",
            "output_url_kind": "PROVIDER" if source else "AUTHENTICATED_LOCAL",
            "output_asset_path": "videos/" + video["filename"],
        }

    def _record_completed_scene_output(self, generation_id: str) -> None:
        with self.engine.connect() as db:
            scene = db.execute(select(video_scenes).where(video_scenes.c.generation_id == generation_id)).mappings().first()
        if not scene:
            return
        job = self.store.get(generation_id)
        if job["provider"] == "fake":
            return
        draft = self.owned(drafts, scene["draft_id"], job["payload"]["spec"]["request"]["user_id"])
        scoped = self._for_draft(draft)
        if scoped is not self:
            return scoped._record_completed_scene_output(generation_id)
        try:
            output = self._completed_scene_output(generation_id, draft["user_id"])
        except HTTPException:
            # The render gate handles missing media; recording provenance must not bypass it.
            logger.warning("[VIDEO] scene=%s output_asset_missing generation=%s", scene["scene_index"], generation_id)
            return
        old = scene["continuity_metadata"] or {}
        output["native_audio"] = old.get("native_audio", job["provider"] == "poyo" or bool(
            job["payload"]["spec"].get("output", {}).get("generate_audio"),
        ))
        if old.get("generation_id") != generation_id or old.get("duration_seconds") is None:
            output["duration_seconds"] = self._media_duration(self.storage / output["output_asset_path"]) or None
        self._record_scene_continuity(draft, scene["scene_index"], **output)
        if old.get("output_video_url") != output["output_video_url"]:
            logger.info("[VIDEO] story=%s scene=%s completed generation=%s output_video_url=%s",
                        draft["id"], scene["scene_index"], generation_id, safe_video_url(output["output_video_url"]))

    def _validate_scene_parent(self, draft: dict, scene_index: int, parent_id: str | None) -> dict | None:
        strategy = self._continuity_strategy()
        if scene_index <= 1 or not strategy.requires_parent:
            return
        if not parent_id:
            raise HTTPException(409, "continuity_parent_required")
        previous_draft_id = hashlib.sha256(f"{draft['id']}:clip:{scene_index - 1}".encode()).hexdigest()[:32]
        with self.engine.connect() as db:
            expected = db.execute(select(drafts.c.generation_id).where(
                drafts.c.id == previous_draft_id, drafts.c.user_id == draft["user_id"],
            )).scalar_one_or_none()
            if expected is None:
                legacy_ids = db.execute(select(story_batches.c.generation_ids).where(
                    story_batches.c.draft_id == draft["id"], story_batches.c.user_id == draft["user_id"],
                )).scalar_one_or_none() or []
                if len(legacy_ids) >= scene_index - 1:
                    expected = legacy_ids[scene_index - 2]
            previous_scene = db.execute(select(video_scenes.c.continuity_metadata).where(
                video_scenes.c.draft_id == draft["id"], video_scenes.c.scene_index == scene_index - 1,
            )).scalar_one_or_none()
        try:
            strategy.validate_continuation(self.store.get(parent_id), user_id=draft["user_id"], expected_parent_id=expected)
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else "continuity_parent_missing") from None
        if (previous_scene or {}).get("continuity_status") in {"FAILED", "INVALIDATED"}:
            raise HTTPException(409, "continuity_parent_failed_validation")
        if (previous_scene or {}).get("continuity_status") in {"GENERATING", "VALIDATING", "WAITING_FOR_PARENT", "PENDING"}:
            raise HTTPException(409, "continuity_parent_validation_pending")
        output = self._completed_scene_output(parent_id, draft["user_id"])
        frame = self.storage / "continuity" / f"{parent_id}.jpg"
        if not frame.is_file():
            strategy.extract_continuation_reference(self, parent_id)
        return output

    def _record_scene_continuity(self, draft: dict, scene_index: int, **values) -> None:
        capabilities = self._continuity_strategy().capabilities
        with self.engine.begin() as db:
            rows = db.execute(select(video_scenes).where(
                video_scenes.c.draft_id == draft["id"],
            ).order_by(video_scenes.c.scene_index)).mappings().all()
            position = next((i for i, row in enumerate(rows) if row["scene_index"] == scene_index), None)
            if position is None:
                return
            row = rows[position]
            clip = next((c for c in (draft["payload"].get("story_plan") or {}).get("clips", [])
                         if c["index"] == scene_index), {})
            metadata = dict(row["continuity_metadata"] or {})
            metadata.update({
                "previous_scene_id": rows[position - 1]["id"] if position else None,
                "next_scene_id": rows[position + 1]["id"] if position + 1 < len(rows) else None,
                "start_state": clip.get("start_state", ""), "end_state": clip.get("end_state", ""),
                "voice_script": clip.get("voice_script", ""),
                "voice_bible": voice_instruction((draft["payload"].get("story_plan") or {}).get("voice_bible")),
                "voice_identity_verified": False, "accent_verified": False,
            })
            retry = values.pop("retry", False)
            metadata["retry_count"] = metadata.get("retry_count", 0) + int(retry)
            metadata.update(values)
            metadata.setdefault("provider_capabilities", capabilities.model_dump(mode="json"))
            status = values.get("continuity_status")
            if status:
                metadata["validation_status"] = status
                if status == "PASSED":
                    metadata["continuity_status"] = "BEST_EFFORT" if capabilities.first_frame else "UNSUPPORTED"
            metadata["voice_continuity_status"] = (
                "FAILED" if metadata.get("continuity_status") == "FAILED"
                else "BEST_EFFORT" if metadata.get("native_audio", capabilities.native_audio) else "UNSUPPORTED"
            )
            metadata["voice_continuity_error"] = (
                metadata.get("continuity_error") if metadata["voice_continuity_status"] == "FAILED"
                else "voice_identity_not_guaranteed_by_provider" if metadata["voice_continuity_status"] == "BEST_EFFORT"
                else "native_audio_not_supported"
            )
            db.execute(update(video_scenes).where(video_scenes.c.id == row["id"]).values(
                continuity_metadata=metadata, updated=time.time(),
            ))

    def _failed_story_scene_index(self, draft_id: str, generation_ids: list[str]) -> int:
        if not generation_ids:
            return 1
        with self.engine.connect() as db:
            scenes = db.execute(select(video_scenes).where(video_scenes.c.draft_id == draft_id)).mappings().all()
        for index, gid in enumerate(generation_ids, 1):
            metadata = next((row["continuity_metadata"] or {} for row in scenes
                             if row["scene_index"] == index and row["generation_id"] == gid), {})
            if self.store.get(gid)["status"] in {"FAILED", "CANCELLED", "NEEDS_USER_ACTION"} or (
                metadata.get("validation_status") == "FAILED" or metadata.get("continuity_status") == "FAILED"
            ):
                return index
        return len(generation_ids)

    def _invalidate_scene_chain(self, db, draft: dict, scene_index: int) -> None:
        rows = db.execute(select(video_scenes).where(
            video_scenes.c.draft_id == draft["id"], video_scenes.c.scene_index >= scene_index,
        )).mappings().all()
        for row in rows:
            metadata = dict(row["continuity_metadata"] or {})
            history = list(metadata.get("invalidated_generations") or [])
            if row["generation_id"]:
                history.append({key: metadata.get(key) for key in (
                    "generation_id", "parent_generation_id", "parent_video_url", "output_video_url", "output_asset_path",
                )} | {"generation_id": row["generation_id"], "reason": "parent_chain_retried"})
            metadata.update({
                "generation_id": None, "output_video_url": None, "output_asset_path": None,
                "duration_seconds": None, "continuation_generation_id": None,
                "composition_end_seconds": None, "continuity_frame_time_seconds": None,
                "continuity_status": "PENDING" if row["scene_index"] == scene_index else "INVALIDATED",
                "validation_status": "PENDING" if row["scene_index"] == scene_index else "INVALIDATED",
                "continuity_error": "parent_chain_retried", "voice_continuity_status": "FAILED",
                "voice_continuity_error": "parent_chain_retried", "invalidated_generations": history,
            })
            if row["scene_index"] > scene_index:
                metadata.update(parent_generation_id=None, parent_video_url=None, parent_asset_path=None)
            db.execute(update(video_scenes).where(video_scenes.c.id == row["id"]).values(
                generation_id=None, status="PLANNED", continuity_metadata=metadata, updated=time.time(),
            ))
            db.execute(update(video_quality_reports).where(
                video_quality_reports.c.project_id == row["project_id"],
                video_quality_reports.c.scene_index == row["scene_index"],
                video_quality_reports.c.status.in_(("FAIL", "WARN", "PASS")),
            ).values(status="INVALIDATED"))
        clips = (draft["payload"].get("story_plan") or {}).get("clips", [])
        for clip in clips:
            if clip["index"] >= scene_index:
                child_id = hashlib.sha256(f"{draft['id']}:clip:{clip['index']}".encode()).hexdigest()[:32]
                db.execute(update(drafts).where(drafts.c.id == child_id, drafts.c.user_id == draft["user_id"]).values(generation_id=None))
        project_ids = {row["project_id"] for row in rows}
        if project_ids:
            db.execute(update(video_quality_reports).where(
                video_quality_reports.c.project_id.in_(project_ids),
                video_quality_reports.c.scene_index.is_(None),
                video_quality_reports.c.stage == "FINAL_MEDIA",
                video_quality_reports.c.status.in_(("FAIL", "WARN", "PASS")),
            ).values(status="INVALIDATED"))
        logger.info("[VIDEO] story=%s invalidated_from_scene=%s reason=parent_chain_retried", draft["id"], scene_index)
