"""Story batch recovery, clip sequencing, merge, and final validation."""

from app.services.native_audio_quality import (
    native_audio_gap_issues as _native_audio_gap_issues,  # noqa: F401 - legacy helper alias
)
from app.services.native_audio_quality import (
    native_speech_issues as _native_speech_issues,  # noqa: F401 - legacy helper alias
)
from app.services.native_audio_quality import (
    native_trim_window,
    volume_gain,
)
from app.services.platform_native_audio import NATIVE_REVIEW_PROFILE, PlatformNativeAudioMixin
from app.services.platform_shared import (
    STORY_TTS_PROFILE,
    HTTPException,
    Image,
    Path,
    UnidentifiedImageError,
    assets,
    drafts,
    executions,
    hashlib,
    httpx,
    insert,
    json,
    logger,
    secrets,
    select,
    shutil,
    story_batches,
    subprocess,
    time,
    update,
    video_quality_reports,
    videos,
)
from app.services.video_execution_client import ExecutionError
from app.services.voice_pipeline import story_duration


class PlatformStoryRenderMixin(PlatformNativeAudioMixin):

    @staticmethod
    def _story_output_filename(batch_id: str, generation_ids: list[str], audio_mode: str) -> str:
        if audio_mode != "native":
            return batch_id + ".mp4"
        chain = hashlib.sha256(json.dumps({"generations": generation_ids, "profile": NATIVE_REVIEW_PROFILE},
                                         separators=(",", ":")).encode()).hexdigest()[:16]
        return f"{batch_id}-{chain}.mp4"

    def process_story_batches(self) -> None:
        with self.engine.connect() as db:
            merge_failures = (
                db.execute(
                    select(story_batches).where(
                        story_batches.c.status == "FAILED",
                        story_batches.c.error_code == "story_merge_failed",
                    )
                )
                .mappings()
                .all()
            )
        for batch in merge_failures:
            merged_file = self.storage / "videos" / (batch["filename"] or self._story_output_filename(
                batch["id"], batch["generation_ids"], batch["audio_mode"],
            ))
            if merged_file.is_file() and self._validate_story_video(merged_file):
                with self.engine.begin() as db:
                    db.execute(
                        update(story_batches)
                        .where(story_batches.c.id == batch["id"])
                        .values(
                            status="COMPLETED",
                            filename=merged_file.name,
                            size_bytes=merged_file.stat().st_size,
                            error_code=None,
                        )
                    )

        with self.engine.connect() as db:
            recoverable = (
                db.execute(
                    select(executions).where(
                        executions.c.status == "NEEDS_USER_ACTION",
                        executions.c.error_code == "output_persistence_failed",
                    )
                )
                .mappings()
                .all()
            )
        for job in recoverable:
            with self.engine.connect() as db:
                stored = (
                    db.execute(
                        select(videos).where(videos.c.generation_id == job["generation_id"])
                    )
                    .mappings()
                    .first()
                )
            if stored:
                path = self.storage / "videos" / stored["filename"]
                if path.is_file() and path.stat().st_size == stored["size_bytes"]:
                    try:
                        self.finalize(dict(job))
                    except Exception as exc:
                        logger.debug("[VIDEO] output recovery deferred generation=%s error_type=%s",
                                     job["generation_id"], type(exc).__name__)
                        continue
            else:
                self._retry_output_download(job)

        with self.engine.connect() as db:
            interrupted = (
                db.execute(
                    select(story_batches).where(
                        story_batches.c.status == "FAILED",
                        story_batches.c.error_code == "clip_generation_failed",
                    )
                )
                .mappings()
                .all()
            )
        for batch in interrupted:
            generation_ids = batch["generation_ids"]
            if len(generation_ids) == self._story_clip_count(batch["draft_id"]) and all(
                self.store.get(gid)["status"] == "COMPLETED" for gid in generation_ids
            ):
                with self.engine.begin() as db:
                    db.execute(
                        update(story_batches)
                        .where(story_batches.c.id == batch["id"])
                        .values(status="MERGING", error_code=None)
                    )

        with self.engine.connect() as db:
            rows = (
                db.execute(
                    select(story_batches).where(
                        story_batches.c.status.in_(("GENERATING", "MERGING"))
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            self._process_story_batch(dict(row))

    def _retry_output_download(self, job: dict) -> None:
        """Re-download a completed provider output whose first download failed (CDN not ready, network)."""
        now = time.time()
        outputs = (job.get("result") or {}).get("outputs") or []
        if (
            not outputs or not outputs[0].get("source_url")
            or now - job["created_at"] > 24 * 3600 or job["next_run_at"] > now
        ):
            return
        with self.engine.begin() as db:
            claimed = db.execute(
                update(executions).where(
                    executions.c.generation_id == job["generation_id"],
                    executions.c.status == "NEEDS_USER_ACTION",
                    executions.c.error_code == "output_persistence_failed",
                ).values(status="POST_PROCESSING", error_code=None, next_run_at=now + 120)
            ).rowcount
        if not claimed:
            return
        try:
            self.finalize(self.store.get(job["generation_id"]))
            logger.info("[VIDEO] recovered provider output generation=%s", job["generation_id"])
        except Exception as exc:
            # finalize restores NEEDS_USER_ACTION; next_run_at spaces out the next attempt.
            logger.warning("[VIDEO] output download retry failed generation=%s error_type=%s detail=%s",
                           job["generation_id"], type(exc).__name__, str(exc)[:160])

    def _prepare_native_story_reference(self, draft: dict, fallback_clips: list[dict] | None = None) -> None:
        project_id = self._project_id_for_draft(draft["id"])
        if project_id:
            self.prepare_video_reference(draft["user_id"], project_id)
            return
        anchor = self.storage / "continuity" / f"anchor-{draft['id']}.jpg"
        if anchor.is_file():
            return
        anchor.parent.mkdir(parents=True, exist_ok=True)
        references = draft["payload"].get("spec", {}).get("references", [])
        asset_id = references[0]["asset_id"] if references else None
        if asset_id:
            if not self._create_product_anchor(draft, draft["user_id"], anchor):
                source = self.owned(assets, asset_id, draft["user_id"])
                shutil.copyfile(self.storage / source["filename"], anchor)
            self._prepare_scene_keyframes(draft, draft["user_id"])
            return
        plan = draft["payload"].get("story_plan") or {}
        clips = plan.get("clips") or fallback_clips or []
        prompt = plan.get("reference_image_prompt") or (clips[0]["video_prompt"] if clips else None)
        if not prompt:
            raise HTTPException(422, "story_reference_prompt_missing")
        self.require_image_credits(draft)
        try:
            response = self.http.post(
                "/internal/v1/planner/scene-image",
                json={"prompt": prompt, "aspect_ratio": draft["payload"]["spec"]["output"]["aspect_ratio"], "request_id": draft["id"], **self._image_target(draft)},
                timeout=240,
            )
            response.raise_for_status()
            anchor.write_bytes(response.content)
            with Image.open(anchor) as image:
                image.verify()
        except (httpx.HTTPError, OSError, UnidentifiedImageError):
            anchor.unlink(missing_ok=True)
            raise HTTPException(502, "story_reference_image_failed") from None
        self.charge_image(draft, draft["id"])
        self._prepare_scene_keyframes(draft, draft.get("user_id"))

    def _story_media_ready(self, batch_id: str, draft: dict, audio_mode: str = "narration") -> bool:
        audio = self.storage / "continuity" / f"story-{batch_id}.mp3"
        metadata = self.storage / "continuity" / f"story-{batch_id}.json"
        anchor = self.storage / "continuity" / f"anchor-{draft['id']}.jpg"
        if audio_mode == "native":
            return anchor.is_file()
        project_id = self._project_id_for_draft(draft["id"])
        if not audio.is_file() or not anchor.is_file() or not project_id:
            return False
        narration = " ".join(
            clip["voice_script"].strip()
            for clip in draft["payload"].get("story_plan", {}).get("clips", [])
        )
        try:
            stored_metadata = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if stored_metadata.get("narration_sha256") != hashlib.sha256(
            narration.encode("utf-8")
        ).hexdigest():
            return False
        if stored_metadata.get("tts_profile") != STORY_TTS_PROFILE:
            return False
        target = story_duration(draft["payload"].get("story_plan") or {})
        if abs(self._media_duration(audio) - target) > 0.5:
            return False
        with self.engine.connect() as db:
            status = db.execute(
                select(video_quality_reports.c.status)
                .where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.stage == "AUDIO_PRECHECK",
                )
                .order_by(video_quality_reports.c.created.desc())
                .limit(1)
            ).scalar_one_or_none()
        return status == "PASS"

    def _process_story_batch(self, row: dict) -> None:
        scoped = self._for_batch(row)
        if scoped is not self:
            return scoped._process_story_batch(row)
        generation_ids = row["generation_ids"]
        native_audio = row["audio_mode"] == "native"
        expected_count = self._story_clip_count(row.get("draft_id"))
        if self.provider in {"poyo", "seedance", "seedvis"} and len(generation_ids) < expected_count:
            if not generation_ids:
                draft = None
                try:
                    draft = self.owned(drafts, row["draft_id"], row["user_id"])
                    clips = draft["payload"].get("story_plan", {}).get("clips", [])
                    if self._poyo_public_urls:
                        self._validate_public_backend_target()
                    with self.batch_lock:
                        if not self._story_media_ready(row["id"], draft, row["audio_mode"]):
                            if native_audio:
                                self._prepare_native_story_reference(draft)
                            else:
                                if not self.planner_credential_status()["configured"]:
                                    raise HTTPException(
                                        409,
                                        "planner_credentials_missing: configure the OpenAI planner before rendering a story video",
                                    )
                                self._prepare_story_media(row["id"], draft, clips)
                    if self._poyo_public_urls:
                        self._validate_public_continuity_image(draft["id"])
                    generated = self.generate(
                        row["user_id"],
                        row["draft_id"],
                        confirm_paid=True,
                        clip_index=1,
                        native_audio=native_audio if native_audio or self.provider != "poyo" else None,
                    )
                    with self.engine.begin() as db:
                        db.execute(
                            update(story_batches)
                            .where(story_batches.c.id == row["id"])
                            .values(generation_ids=[generated["id"]])
                        )
                except Exception as exc:
                    logger.exception("Story media preparation failed for batch %s", row["id"])
                    detail = getattr(exc, "detail", None)
                    error_code = str(
                        detail
                        or f"clip_preparation_failed:{exc.code if isinstance(exc, ExecutionError) else type(exc).__name__}"
                    )[:240]
                    if draft is not None:
                        self._record_scene_continuity(
                            draft, 1, continuity_status="FAILED", continuity_error=error_code,
                        )
                    with self.engine.begin() as db:
                        db.execute(
                            update(story_batches)
                            .where(story_batches.c.id == row["id"])
                            .values(status="FAILED", error_code=error_code)
                        )
                return
            last_job = self.store.get(generation_ids[-1])
            if last_job["status"] in {"FAILED", "CANCELLED", "NEEDS_USER_ACTION"}:
                draft = self.owned(drafts, row["draft_id"], row["user_id"])
                self._record_scene_continuity(
                    draft, len(generation_ids), continuity_status="FAILED",
                    continuity_error=last_job.get("error_code") or "clip_generation_failed",
                )
                with self.engine.begin() as db:
                    db.execute(
                        update(story_batches)
                        .where(story_batches.c.id == row["id"])
                        .values(
                            status="FAILED",
                            error_code=last_job.get("error_code") or "clip_generation_failed",
                        )
                    )
                return
            if last_job["status"] != "COMPLETED":
                return
            self._record_completed_scene_output(generation_ids[-1])
            failed_scene = len(generation_ids)
            draft = None
            try:
                draft = self.owned(drafts, row["draft_id"], row["user_id"])
                clips = draft["payload"].get("story_plan", {}).get("clips", [])
                scene_index = len(generation_ids)
                self._record_scene_continuity(draft, scene_index, continuity_status="VALIDATING")
                with self.engine.connect() as db:
                    clip_video = db.execute(select(videos.c.filename).where(
                        videos.c.generation_id == generation_ids[-1], videos.c.user_id == row["user_id"],
                    )).scalar_one_or_none()
                if native_audio and (
                    not clip_video or not self._validate_story_video(self.storage / "videos" / clip_video)
                ):
                    raise HTTPException(422, "native_audio_missing: scene video has no decodable audio")
                if native_audio and not self._review_native_scene_audio(
                    draft, scene_index, generation_ids[-1], self.storage / "videos" / clip_video,
                ):
                    raise HTTPException(422, self._native_speech_failure(draft, scene_index))
                anchor = self.storage / "continuity" / f"anchor-{row['draft_id']}.jpg"
                current_start = (
                    self._extract_video_frame(generation_ids[-1], "start")
                    if anchor.is_file()
                    else None
                )
                if scene_index == 1 and current_start:
                    self._record_scene_boundary_quality(
                        row["draft_id"], scene_index, anchor, current_start, anchor
                    )
                elif scene_index > 1 and current_start:
                    previous_end = self.storage / "continuity" / f"{generation_ids[-2]}.jpg"
                    if not previous_end.is_file():
                        previous_end = self._extract_continuity_frame(generation_ids[-2])
                    self._record_scene_boundary_quality(
                        row["draft_id"], scene_index, previous_end, current_start, anchor
                    )
                if current_start:
                    self._record_media_asset(
                        draft, scene_index, "scene_start_frame", current_start, "image/jpeg"
                    )
                current_end = self._extract_continuity_frame(generation_ids[-1])
                self._record_media_asset(
                    draft, scene_index, "scene_end_frame", current_end, "image/jpeg"
                )
                if clip_video:
                    self._record_media_asset(
                        draft,
                        scene_index,
                        "scene_video",
                        self.storage / "videos" / clip_video,
                        "video/mp4",
                    )
                if self.provider in {"poyo", "seedvis"} and anchor.is_file():
                    previous_end = anchor if scene_index == 1 else (
                        self.storage / "continuity" / f"{generation_ids[-2]}.jpg"
                    )
                    if not previous_end.is_file():
                        previous_end = self._extract_continuity_frame(generation_ids[-2])
                    if not self._review_scene_reference(
                        row["draft_id"], scene_index, anchor, previous_end, current_end
                    ):
                        raise HTTPException(422, "visual_reference_drift")
                if len(clips) < expected_count or not (
                    anchor
                ).is_file():
                    current_end = self._extract_continuity_frame(generation_ids[-1])
                next_index = len(generation_ids) + 1
                self._record_scene_continuity(draft, scene_index, continuity_status="PASSED", continuity_error=None)
                failed_scene = next_index
                generated = self.generate(
                    row["user_id"],
                    row["draft_id"],
                    confirm_paid=True,
                    clip_index=next_index,
                    continuity_generation_id=generation_ids[-1] if next_index > 1 else None,
                    native_audio=native_audio if native_audio or self.provider != "poyo" else None,
                )
                with self.engine.begin() as db:
                    current_ids = db.execute(
                        select(story_batches.c.generation_ids).where(
                            story_batches.c.id == row["id"]
                        )
                    ).scalar_one()
                    if generated["id"] not in current_ids:
                        db.execute(
                            update(story_batches)
                            .where(story_batches.c.id == row["id"])
                            .values(generation_ids=[*current_ids, generated["id"]])
                        )
            except Exception as exc:
                logger.exception("Story clip generation failed for batch %s", row["id"])
                # Keep the exception type so a failure without an HTTP detail is still diagnosable.
                detail = getattr(exc, "detail", None) or f"clip_generation_failed:{type(exc).__name__}"
                if draft is not None:
                    self._record_scene_continuity(
                        draft, failed_scene, continuity_status="FAILED", continuity_error=str(detail or "clip_generation_failed")[:240],
                    )
                with self.engine.begin() as db:
                    db.execute(
                        update(story_batches)
                        .where(story_batches.c.id == row["id"])
                        .values(status="FAILED", error_code=str(detail or "clip_generation_failed")[:240])
                    )
            return
        if len(generation_ids) != expected_count:
            return
        jobs = [self.store.get(gid) for gid in generation_ids]
        if any(job["status"] in {"FAILED", "CANCELLED", "NEEDS_USER_ACTION"} for job in jobs):
            draft = self.owned(drafts, row["draft_id"], row["user_id"])
            self._record_scene_continuity(
                draft, len(generation_ids), continuity_status="FAILED", continuity_error="clip_generation_failed",
            )
            with self.engine.begin() as db:
                db.execute(
                    update(story_batches)
                    .where(story_batches.c.id == row["id"])
                    .values(status="FAILED", error_code="clip_generation_failed")
                )
            return
        if not all(job["status"] == "COMPLETED" for job in jobs):
            return
        self._record_completed_scene_output(generation_ids[-1])
        if native_audio:
            draft = self.owned(drafts, row["draft_id"], row["user_id"])
            self._record_scene_continuity(draft, len(generation_ids), continuity_status="VALIDATING")
            with self.engine.connect() as db:
                clip_video = db.execute(
                    select(videos.c.filename).where(
                        videos.c.generation_id == generation_ids[-1],
                        videos.c.user_id == row["user_id"],
                    )
                ).scalar_one_or_none()
            if not clip_video or not self._validate_story_video(self.storage / "videos" / clip_video):
                self._record_scene_continuity(
                    draft, len(generation_ids), continuity_status="FAILED", continuity_error="native_audio_missing",
                )
                with self.engine.begin() as db:
                    db.execute(update(story_batches).where(story_batches.c.id == row["id"])
                               .values(status="FAILED", error_code="native_audio_missing"))
                return
            if not self._review_native_scene_audio(
                draft, len(generation_ids), generation_ids[-1], self.storage / "videos" / clip_video
            ):
                failure = self._native_speech_failure(draft, len(generation_ids))
                self._record_scene_continuity(
                    draft, len(generation_ids), continuity_status="FAILED", continuity_error=failure,
                )
                with self.engine.begin() as db:
                    db.execute(update(story_batches).where(story_batches.c.id == row["id"])
                               .values(status="FAILED", error_code=failure))
                return
        if len(generation_ids) > 1 and self._project_id_for_draft(row["draft_id"]):
            try:
                previous_end = self.storage / "continuity" / f"{generation_ids[-2]}.jpg"
                if not previous_end.is_file():
                    previous_end = self._extract_continuity_frame(generation_ids[-2])
                current_start = self._extract_video_frame(generation_ids[-1], "start")
                anchor = self.storage / "continuity" / f"anchor-{row['draft_id']}.jpg"
                self._record_scene_boundary_quality(
                    row["draft_id"], len(generation_ids), previous_end, current_start, anchor
                )
            except (OSError, RuntimeError) as exc:
                logger.warning("Could not review final scene boundary for %s: %s", row["id"], exc)
        if self.provider in {"poyo", "seedvis"}:
            anchor = self.storage / "continuity" / f"anchor-{row['draft_id']}.jpg"
            if anchor.is_file():
                try:
                    current_end = self._extract_continuity_frame(generation_ids[-1])
                    previous_end = anchor if len(generation_ids) == 1 else (
                        self.storage / "continuity" / f"{generation_ids[-2]}.jpg"
                    )
                    if not previous_end.is_file():
                        previous_end = self._extract_continuity_frame(generation_ids[-2])
                    if not self._review_scene_reference(
                        row["draft_id"], len(generation_ids), anchor, previous_end, current_end
                    ):
                        draft = self.owned(drafts, row["draft_id"], row["user_id"])
                        self._record_scene_continuity(
                            draft, len(generation_ids), continuity_status="FAILED", continuity_error="visual_reference_drift",
                        )
                        with self.engine.begin() as db:
                            db.execute(
                                update(story_batches)
                                .where(story_batches.c.id == row["id"])
                                .values(status="FAILED", error_code="visual_reference_drift")
                            )
                        return
                except (OSError, RuntimeError) as exc:
                    logger.warning("Could not review final scene reference for %s: %s", row["id"], exc)
        draft = self.owned(drafts, row["draft_id"], row["user_id"])
        self._record_scene_continuity(draft, len(generation_ids), continuity_status="PASSED", continuity_error=None)
        with self.engine.begin() as db:
            db.execute(
                update(story_batches)
                .where(story_batches.c.id == row["id"])
                .values(status="MERGING", error_code=None)
            )
        try:
            filename, size = self._merge_story_videos(
                row["id"], generation_ids, row["ratio"], row["audio_mode"]
            )
            draft = self.owned(drafts, row["draft_id"], row["user_id"])
            output_path = self.storage / "videos" / filename
            self._record_final_quality(row["draft_id"], row["user_id"], output_path)
            self._record_media_asset(draft, None, "final_video", output_path, "video/mp4")
            with self.engine.begin() as db:
                db.execute(
                    update(story_batches)
                    .where(story_batches.c.id == row["id"])
                    .values(status="COMPLETED", filename=filename, size_bytes=size)
                )
        except Exception as exc:
            # Without this the reason (missing clip file, timeout, duration mismatch…) is lost.
            logger.exception("Story merge failed for batch %s", row["id"])
            with self.engine.begin() as db:
                db.execute(
                    update(story_batches)
                    .where(story_batches.c.id == row["id"])
                    .values(
                        status="FAILED",
                        error_code=(
                            "native_audio_missing"
                            if native_audio and str(exc) == "native_audio_missing"
                            else "story_merge_failed"
                        ),
                    )
                )

    def _record_final_quality(self, draft_id: str, uid: str, path: Path) -> None:
        project_id = self._project_id_for_draft(draft_id)
        if not project_id:
            return
        duration = self._media_duration(path)
        with self.engine.connect() as db:
            payload = db.execute(
                select(drafts.c.payload).where(
                    drafts.c.id == draft_id, drafts.c.user_id == uid
                )
            ).scalar_one_or_none() or {}
        clips = (payload.get("story_plan") or {}).get("clips") or []
        target = story_duration({"clips": clips})
        has_audio_video = self._validate_story_video(path)
        issues = ["video_trimmed_to_voice_duration"] if target and target - duration > 0.5 else []
        if not has_audio_video:
            issues.append("final_media_missing_audio_or_video_stream")
        with self.engine.begin() as db:
            db.execute(
                insert(video_quality_reports).values(
                    id=secrets.token_hex(16),
                    project_id=project_id,
                    scene_index=None,
                    stage="FINAL_MEDIA",
                    status="WARN" if issues else "PASS",
                    metrics={
                        "video_duration_seconds": round(duration, 3),
                        "planned_duration_seconds": target,
                        "contains_audio_and_video": has_audio_video,
                    },
                    issues=issues,
                    created=time.time(),
                )
            )

    def _merge_story_videos(
        self, batch_id: str, generation_ids: list[str], ratio: str,
        audio_mode: str = "narration",
    ) -> tuple[str, int]:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("Install backend dependency imageio-ffmpeg") from exc
        with self.engine.connect() as db:
            rows = (
                db.execute(
                    select(videos).where(videos.c.generation_id.in_(generation_ids))
                )
                .mappings()
                .all()
            )
        by_id = {row["generation_id"]: row for row in rows}
        if len(by_id) != len(generation_ids) or not generation_ids:
            raise RuntimeError("A story clip has no persisted video")
        paths = [self.storage / "videos" / by_id[gid]["filename"] for gid in generation_ids]
        if any(not path.is_file() for path in paths):
            raise RuntimeError("A story clip file is missing")
        width, height = (1280, 720) if ratio == "16:9" else (720, 1280)
        inputs = [value for path in paths for value in ("-i", str(path))]
        narration_path = self.storage / "continuity" / f"story-{batch_id}.mp3"
        has_narration = audio_mode == "narration" and narration_path.is_file()
        if audio_mode == "native" and any(not self._validate_story_video(path) for path in paths):
            raise RuntimeError("native_audio_missing")
        narration_duration = self._media_duration(narration_path) if has_narration else 0.0
        narration_trim_start = 0.0
        if has_narration:
            with self.engine.connect() as db:
                batch_draft_id = db.execute(
                    select(story_batches.c.draft_id).where(story_batches.c.id == batch_id)
                ).scalar_one_or_none()
            project_id = self._project_id_for_draft(batch_draft_id) if batch_draft_id else None
            if project_id:
                with self.engine.connect() as db:
                    report = db.execute(
                        select(video_quality_reports.c.metrics)
                        .where(
                            video_quality_reports.c.project_id == project_id,
                            video_quality_reports.c.stage == "AUDIO_PRECHECK",
                        )
                        .order_by(video_quality_reports.c.created.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                if report:
                    narration_duration = min(
                        narration_duration,
                        float(report.get("effective_duration_seconds", narration_duration)),
                    )
                    narration_trim_start = float(report.get("trim_start_seconds", 0.0))
            if narration_duration <= 0 or narration_duration > sum(self._media_duration(path) for path in paths) + 0.5:
                raise RuntimeError("Narration duration does not fit the assembled video")
        if has_narration:
            inputs.extend(("-i", str(narration_path)))
        native_metrics = [self._native_audio_measurements(path) for path in paths] if audio_mode == "native" else []
        durations = [self._media_duration(path) for path in paths] if audio_mode == "native" else []
        windows = [native_trim_window(duration, measured) for duration, measured in zip(durations, native_metrics, strict=True)]
        if audio_mode == "native":
            for index, gid in enumerate(generation_ids):
                retained = self._scene_native_trim(gid)
                if retained:
                    windows[index] = (retained["trim_start_seconds"], retained["trim_end_seconds"])
        with self.engine.connect() as db:
            batch_draft = db.execute(select(story_batches.c.draft_id).where(story_batches.c.id == batch_id)).scalar_one_or_none()
            plan_payload = db.execute(select(drafts.c.payload).where(drafts.c.id == batch_draft)).scalar_one_or_none() if batch_draft else None
        planned_clips = ((plan_payload or {}).get("story_plan") or {}).get("clips", [])
        filters = []
        concat_inputs = []
        for index in range(len(paths)):
            trim_video = trim_audio = ""
            native_gain = ""
            visual_fade = ""
            if audio_mode == "native":
                start, end = windows[index]
                trim_video = f"trim=start={start:.3f}:end={end:.3f},"
                trim_audio = f"atrim=start={start:.3f}:end={end:.3f},"
                native_gain = f"volume={volume_gain(native_metrics[0], native_metrics[index]):.3f}dB,"
                duration = end - start
                transition = planned_clips[index].get("transition_intent", "") if index < len(planned_clips) else ""
                next_transition = planned_clips[index + 1].get("transition_intent", "") if index + 1 < len(planned_clips) else ""
                if index > 0 and transition == "fade_to_black":
                    visual_fade += "fade=t=in:d=0.15,"
                if next_transition == "fade_to_black" and duration > 0.3:
                    visual_fade += f"fade=t=out:st={duration - 0.15:.3f}:d=0.15,"
            filters.append(
                f"[{index}:v:0]{trim_video}setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                f"{visual_fade}format=yuv420p[v{index}]"
            )
            concat_inputs.append(f"[v{index}]")
            if not has_narration:
                filters.append(
                    f"[{index}:a:0]{trim_audio}asetpts=PTS-STARTPTS,aresample=48000,{native_gain}"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[a{index}]"
                )
                concat_inputs.append(f"[a{index}]")
        if has_narration:
            filters.append(
                "".join(concat_inputs) + f"concat=n={len(paths)}:v=1:a=0[joinedv]"
            )
            filters.append(
                f"[joinedv]trim=duration={narration_duration:.3f},setpts=PTS-STARTPTS[outv]"
            )
            voice_index = len(paths)
            filters.append(
                f"[{voice_index}:a:0]aresample=48000,"
                f"atrim=start={narration_trim_start:.3f}:duration={narration_duration:.3f},"
                "asetpts=PTS-STARTPTS,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo[outa]"
            )
        else:
            filters.append(
                "".join(concat_inputs)
                + f"concat=n={len(paths)}:v=1:a=1[outv][outa]"
            )
        output_name = self._story_output_filename(batch_id, generation_ids, audio_mode)
        output = self.storage / "videos" / output_name
        temporary = output.with_suffix(".part.mp4")
        if output.is_file() and self._validate_story_video(output):
            return output_name, output.stat().st_size
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-r",
            "24",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600)
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size < 12:
                logger.error("Story merge FFmpeg failed for %s: %s", batch_id, result.stderr[-2000:])
                raise RuntimeError("FFmpeg could not merge the story clips")
            if not self._validate_story_video(temporary):
                raise RuntimeError("FFmpeg output failed full audio/video decoding")
            temporary.replace(output)
            return output_name, output.stat().st_size
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate_story_video(path: Path) -> bool:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if "Video:" not in probe.stderr or "Audio:" not in probe.stderr:
            return False
        decoded = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        return decoded.returncode == 0 and not decoded.stderr.strip()

    def story_batch_video_path(self, uid: str, batch_id: str) -> Path:
        row = self._story_batch_row(uid, batch_id)
        if row["status"] != "COMPLETED" or not row["filename"]:
            raise HTTPException(409, "Video hoàn chỉnh chưa sẵn sàng")
        path = self.storage / "videos" / row["filename"]
        if not path.is_file():
            raise HTTPException(503, "Không tìm thấy video đã ghép trong storage")
        return path
