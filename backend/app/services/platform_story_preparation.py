"""Story batch setup, narration preparation, and media quality checks."""

from app.services.platform_shared import (
    STORY_PLACEHOLDER_RE,
    STORY_TTS_PROFILE,
    HTTPException,
    Path,
    chat_messages,
    drafts,
    hashlib,
    httpx,
    insert,
    json,
    logger,
    re,
    secrets,
    select,
    story_batches,
    story_plan_versions,
    subprocess,
    time,
    update,
    video_media_assets,
    video_quality_reports,
    video_scenes,
    videos,
)
from app.services.platform_auto_retry import AUTO_RETRY_LIMIT, member_error_message, story_error_kind
from app.services.video_continuity import is_scene_quality_error, voice_instruction
from app.services.voice_pipeline import (
    choose_voice_mode,
    joined_voice_script,
    story_duration,
    validate_story_timing,
)


class PlatformStoryPreparationMixin:
    def present(self, draft):
        scoped = self._for_draft(draft)
        if scoped is not self:
            return scoped.present(draft)
        job = self.store.get(draft["generation_id"])
        with self.engine.connect() as db:
            stored = (
                db.execute(select(videos).where(videos.c.generation_id == job["generation_id"]))
                .mappings()
                .first()
            )
        demo_done = (
            job["provider"] == "fake"
            and job["status"] == "POST_PROCESSING"
            and bool((job["result"] or {}).get("outputs"))
        )
        done = demo_done or (job["status"] == "COMPLETED" and stored is not None)
        return {
            "id": job["generation_id"],
            "draft_id": draft["id"],
            "prompt": draft["title"],
            "created": draft["created"],
            "status": "COMPLETED" if done else job["status"],
            "ratio": draft["payload"]["spec"]["output"]["aspect_ratio"],
            "duration": (None if job["provider"] == "seedvis" and not self._continuity_strategy().capabilities.generation_durations
                         else draft["payload"]["spec"]["output"]["duration_seconds"]),
            "demo": job["provider"] == "fake",
            "provider": job["provider"],
            "model": job["payload"].get("model"),
            "retryable": self._is_unbilled_scene_failure(job),
            "error": job["error_code"],
            "cancel_requested": job["cancel_requested"],
            "video_url": f"/api/v1/generations/{job['generation_id']}/video" if done else None,
            **draft["payload"].get("voice_policy", {}),
        }

    def generate_story_batch(self, uid: str, draft: dict, confirm_paid: bool, voice_mode: str | None = None) -> dict:
        scoped = self._for_draft(draft)
        if scoped is not self:
            return scoped.generate_story_batch(uid, draft, confirm_paid, voice_mode)
        capabilities = self._continuity_strategy().capabilities
        try:
            validate_story_timing(draft["payload"]["story_plan"], capabilities.generation_durations)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        batch_id = hashlib.sha256(f"{draft['id']}:complete-story".encode()).hexdigest()[:32]
        with self.engine.connect() as db:
            started = db.execute(select(story_batches.c.id).where(story_batches.c.id == batch_id)).first()
        if not started:
            # Scenes are charged one by one in the background, so check the whole video up front.
            self.require_credits(uid, self.story_render_estimate(draft))
        with self.batch_lock:
            self._resume_rescored_native_scene(draft, batch_id)
            with self.engine.begin() as db:
                existing = (
                    db.execute(select(story_batches).where(story_batches.c.id == batch_id))
                    .mappings()
                    .first()
                )
                is_new = existing is None
                if is_new:
                    decision = choose_voice_mode(len(draft["payload"]["story_plan"]["clips"]), voice_mode,
                                                 tts=self.planner_configured, audio_control=capabilities.audio_control)
                    draft["payload"]["voice_policy"] = decision.metadata()
                    draft["payload"]["story_plan"]["full_voice_script"] = joined_voice_script(draft["payload"]["story_plan"])
                    db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=draft["payload"]))
                    if decision.blockers:
                        logger.warning("[VIDEO] story=%s requested_voice_mode=%s voice_mode=%s limitations=%s",
                                       draft["id"], decision.requested.value, decision.effective.value, decision.blockers)
                elif voice_mode and voice_mode != draft["payload"].get("voice_policy", {}).get("requested_voice_mode", "native_veo"):
                    raise HTTPException(409, "voice_mode_change_requires_new_plan")
                native_supported = self._continuity_strategy().native_audio_supported
                if is_new and self.provider != "fake" and not native_supported:
                    raise HTTPException(
                        409, "native_audio_unsupported_model: choose a video model with native audio; external TTS is not substituted",
                    )
                generation_ids = existing["generation_ids"] if existing else []
                # Nothing reached the video provider yet, so preparation can always run again
                # (missing key, credits, hidden model, reference or voice problems).
                retry_preparation = bool(
                    existing and existing["status"] == "FAILED" and not generation_ids
                )
                retry_unbilled_clip = False
                retry_bad_scene = False
                failed_scene_index = len(generation_ids)
                if existing and existing["status"] == "FAILED" and generation_ids:
                    failed_scene_index = self._failed_story_scene_index(draft["id"], generation_ids)
                    previous_jobs = [self.store.get(identifier) for identifier in generation_ids[:failed_scene_index - 1]]
                    failed_job = self.store.get(generation_ids[failed_scene_index - 1])
                    downstream_ready = all(self.store.get(gid)["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
                                           for gid in generation_ids[failed_scene_index:])
                    retry_unbilled_clip = (
                        downstream_ready
                        and all(job["status"] == "COMPLETED" for job in previous_jobs)
                        and self._is_unbilled_scene_failure(failed_job)
                    )
                    retry_bad_scene = (
                        downstream_ready
                        and is_scene_quality_error(existing["error_code"])
                        and all(job["status"] == "COMPLETED" for job in previous_jobs)
                        and failed_job["status"] == "COMPLETED"
                    )
                    if not downstream_ready:
                        raise HTTPException(409, "continuity_downstream_active: wait for downstream tasks to finish before retry")
                can_retry_batch = retry_preparation or retry_unbilled_clip or retry_bad_scene
                pending = draft["payload"].get("story_plan", {}).get("clips", [])
                first_pending = failed_scene_index if retry_unbilled_clip or retry_bad_scene else 1
                unresolved = [
                    clip["index"] for clip in pending
                    if clip["index"] >= first_pending
                    and (clip.get("unresolved_voice_placeholder") or any(
                        STORY_PLACEHOLDER_RE.search(clip.get(field, ""))
                        for field in ("voice_script", "video_prompt")
                    ))
                ]
                if unresolved:
                    scenes = ", ".join(str(index) for index in unresolved)
                    raise HTTPException(
                        409,
                        f"Lời thoại còn chỗ trống ở cảnh {scenes}; hãy để AI đọc ảnh tham chiếu trước khi tạo video",
                    )
                if can_retry_batch:
                    retry_ids = generation_ids[:failed_scene_index - 1] if retry_unbilled_clip or retry_bad_scene else generation_ids
                    if retry_bad_scene or retry_unbilled_clip:
                        self._invalidate_scene_chain(db, draft, failed_scene_index)
                    db.execute(
                        update(story_batches)
                        .where(story_batches.c.id == batch_id)
                        .values(status="GENERATING", error_code=None, generation_ids=retry_ids,
                                filename=None, size_bytes=None)
                    )
                    existing = None
                if is_new:
                    db.execute(
                        insert(story_batches).values(
                            id=batch_id,
                            user_id=uid,
                            title=draft["title"],
                            draft_id=draft["id"],
                            ratio=draft["payload"]["spec"]["output"]["aspect_ratio"],
                            generation_ids=[],
                            status="GENERATING",
                            audio_mode="native" if native_supported else "narration",
                            created=time.time(),
                        )
                )
            if is_new:
                for clip in pending:
                    self._record_scene_continuity(
                        draft, clip["index"], continuity_status="PENDING" if clip["index"] == 1 else "WAITING_FOR_PARENT",
                        continuity_error=None, parent_generation_id=None, continuation_generation_id=None,
                    )
            if (is_new or can_retry_batch) and self.provider == "fake":
                try:
                    clips = draft["payload"]["story_plan"]["clips"]
                    if self.provider != "fake":
                        planner_ready = self.planner_credential_status()["configured"]
                        media_ready = self._story_media_ready(batch_id, draft)
                        if not media_ready and not planner_ready:
                            raise HTTPException(
                                409,
                                "planner_credentials_missing: configure the OpenAI planner before rendering a story video",
                            )
                    else:
                        media_ready = True
                    if self.provider != "fake" and not media_ready:
                        self._prepare_story_media(batch_id, draft, clips)
                    initial_clips = clips if self.provider == "fake" else []
                    for clip in initial_clips:
                        generated = self.generate(uid, draft["id"], confirm_paid, clip["index"])
                        with self.engine.begin() as db:
                            row = db.execute(
                                select(story_batches.c.generation_ids).where(
                                    story_batches.c.id == batch_id
                                )
                            ).scalar_one()
                            if generated["id"] not in row:
                                db.execute(
                                    update(story_batches)
                                    .where(story_batches.c.id == batch_id)
                                    .values(generation_ids=[*row, generated["id"]])
                                )
                except Exception:
                    with self.engine.begin() as db:
                        db.execute(
                            update(story_batches)
                            .where(story_batches.c.id == batch_id)
                            .values(status="FAILED", error_code="clip_submission_failed")
                        )
                    raise
        return self.story_batch(uid, batch_id)

    def _resume_rescored_native_scene(self, draft: dict, batch_id: str) -> None:
        """Keep a clip that only an older speech checker rejected, instead of paying to regenerate it."""
        with self.engine.connect() as db:
            batch = db.execute(select(story_batches).where(story_batches.c.id == batch_id)).mappings().first()
        if not batch or batch["status"] != "FAILED" or not batch["generation_ids"]:
            return
        if not str(batch["error_code"] or "").startswith("native_speech_quality_failed"):
            return
        generation_ids = batch["generation_ids"]
        scene_index = self._failed_story_scene_index(draft["id"], generation_ids)
        generation_id = generation_ids[-1]
        if scene_index != len(generation_ids) or self.store.get(generation_id)["status"] != "COMPLETED":
            return
        with self.engine.connect() as db:
            filename = db.execute(select(videos.c.filename).where(
                videos.c.generation_id == generation_id, videos.c.user_id == batch["user_id"],
            )).scalar_one_or_none()
        if not filename or not self._review_native_scene_audio(
            draft, scene_index, generation_id, self.storage / "videos" / filename
        ):
            return
        self._record_scene_continuity(draft, scene_index, continuity_status="VALIDATING", continuity_error=None)
        with self.engine.begin() as db:
            db.execute(update(story_batches).where(
                story_batches.c.id == batch_id, story_batches.c.status == "FAILED",
            ).values(status="GENERATING", error_code=None))
        logger.info("[VIDEO] story=%s scene=%s kept after speech re-review", draft["id"], scene_index)

    @staticmethod
    def _is_unbilled_scene_failure(job: dict) -> bool:
        if job.get("provider") == "seedvis":
            # Seedvis refunds a rejected task (e.g. its safety filter) and says so in the message.
            detail = str((job.get("result") or {}).get("error_detail") or "").lower()
            return job.get("status") == "FAILED" and (
                job.get("error_code") == "seedvis_not_started"
                or "hoàn lại" in detail
                or "refund" in detail
            )
        return PlatformStoryPreparationMixin._is_unbilled_poyo_failure(job)

    @staticmethod
    def _is_unbilled_poyo_failure(job: dict) -> bool:
        result = job.get("result") or {}
        usage = result.get("usage") or {}
        credits = usage.get("credits")
        return (
            job.get("provider") == "poyo"
            and job.get("status") == "FAILED"
            and job.get("error_code") == "poyo_generation_failed"
            and isinstance(credits, (int, float))
            and not isinstance(credits, bool)
            and credits == 0
        )

    def _prepare_story_media(self, batch_id: str, draft: dict, clips: list[dict]) -> None:
        directory = self.storage / "continuity"
        directory.mkdir(parents=True, exist_ok=True)
        # Same reference flow as native-audio providers (worn product anchor, per-scene keyframes),
        # done before narration because reference preparation may revise the scene dialogue.
        self._prepare_native_story_reference(draft, clips)
        narration = " ".join(clip["voice_script"].strip() for clip in clips)
        narration_hash = hashlib.sha256(narration.encode("utf-8")).hexdigest()
        voice = voice_instruction((draft["payload"].get("story_plan") or {}).get("voice_bible"))
        voice_hash = hashlib.sha256(voice.encode("utf-8")).hexdigest()
        if len(narration) > 4096:
            raise HTTPException(422, "Lời dẫn dài quá giới hạn TTS; hãy rút gọn kịch bản")
        audio_path = directory / f"story-{batch_id}.mp3"
        metadata_path = directory / f"story-{batch_id}.json"
        try:
            stored_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored_metadata = {}
        planner_configured = self.planner_credential_status()["configured"]
        if (
            not audio_path.is_file()
            or stored_metadata.get("narration_sha256") != narration_hash
            or stored_metadata.get("tts_profile") != STORY_TTS_PROFILE
            or stored_metadata.get("voice_profile_sha256") != voice_hash
        ):
            try:
                response = self.http.post(
                    "/internal/v1/planner/voice", json={"text": narration, "voice_bible": voice}, timeout=240
                )
                response.raise_for_status()
            except httpx.HTTPError:
                raise HTTPException(502, "Không tạo được giọng đọc liên tục từ OpenAI") from None
            audio_path.write_bytes(response.content)
        self._compact_story_audio(audio_path)
        audio_duration = self._media_duration(audio_path)
        target_duration = story_duration({"clips": clips})
        if audio_duration <= 0:
            raise HTTPException(
                422,
                f"voice_timeline_mismatch: lời đọc dài {audio_duration:.2f}s, "
                f"trong khi video dài tối đa {target_duration}s",
            )
        for _adjustment in range(2):
            if abs(audio_duration - target_duration) <= 0.5 or not planner_configured:
                break
            try:
                response = self.http.post(
                    "/internal/v1/planner/expand-narration",
                    json={
                        "clips": clips,
                        "target_duration_seconds": target_duration,
                        "current_duration_seconds": audio_duration,
                        "voice_bible": voice,
                    },
                    timeout=120,
                )
                response.raise_for_status()
                adjusted = response.json()["clips"]
                scripts = {item["index"]: item["voice_script"] for item in adjusted}
                if set(scripts) != {clip["index"] for clip in clips}:
                    raise ValueError("adjusted narration scene mismatch")
                for clip in clips:
                    clip["voice_script"] = scripts[clip["index"]]
                self._persist_expanded_story_narration(draft, clips)
                narration = " ".join(clip["voice_script"].strip() for clip in clips)
                if len(narration) > 4096:
                    raise HTTPException(422, "Adjusted narration exceeds the TTS input limit")
                response = self.http.post(
                    "/internal/v1/planner/voice", json={"text": narration, "voice_bible": voice}, timeout=240
                )
                response.raise_for_status()
                audio_path.write_bytes(response.content)
                self._compact_story_audio(audio_path)
                audio_duration = self._media_duration(audio_path)
                if audio_duration <= 0:
                    raise HTTPException(422, "voice_track_empty: cannot measure adjusted narration")
            except HTTPException:
                raise
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                raise HTTPException(
                    502,
                    "voice_timeline_mismatch: AI could not adjust narration to fit the planned scenes; revise the script and try again",
                ) from None
        stretch = audio_duration / target_duration
        if not 0.90 <= stretch <= 1.10:
            self._record_audio_quality(draft, batch_id, audio_path, audio_duration, target_duration)
            raise HTTPException(
                422,
                f"voice_timeline_mismatch: narration is {audio_duration:.2f}s after silence removal "
                f"but the video timeline is {target_duration}s after two automatic adjustments",
            )
        if abs(audio_duration - target_duration) > 0.25:
            self._retime_story_audio(audio_path, stretch)
            audio_duration = self._media_duration(audio_path)
        if audio_duration <= 0 or abs(audio_duration - target_duration) > 0.5:
            self._record_audio_quality(draft, batch_id, audio_path, audio_duration, target_duration)
            raise HTTPException(422, "voice_timeline_mismatch: narration could not be matched to the video timeline")
        effective_audio = self._record_audio_quality(
            draft, batch_id, audio_path, audio_duration, target_duration
        )
        if effective_audio <= 0:
            raise HTTPException(422, "voice_track_empty: không tìm thấy lời đọc trong âm thanh")
        audio_issues = self._story_audio_quality_issues(draft)
        if audio_issues:
            raise HTTPException(422, f"audio_quality_failed:{','.join(audio_issues)}")
        metadata_path.write_text(
            json.dumps({
                "narration_sha256": hashlib.sha256(narration.encode("utf-8")).hexdigest(),
                "tts_profile": STORY_TTS_PROFILE,
                "voice_profile_sha256": voice_hash,
            }),
            encoding="utf-8",
        )
        anchor_path = directory / f"anchor-{draft['id']}.jpg"
        if not anchor_path.is_file():
            raise HTTPException(502, "Không tạo được ảnh tham chiếu chung cho video")
        self._record_media_asset(draft, None, "shared_reference_image", anchor_path, "image/jpeg")
        self._record_media_asset(draft, None, "continuous_narration", audio_path, "audio/mpeg")

    def _persist_expanded_story_narration(self, draft: dict, clips: list[dict]) -> None:
        plan = draft["payload"].get("story_plan") or {}
        plan_clips = {clip["index"]: clip for clip in plan.get("clips", [])}
        for clip in clips:
            if clip["index"] in plan_clips:
                plan_clips[clip["index"]]["voice_script"] = clip["voice_script"]
        plan["full_voice_script"] = joined_voice_script(plan)
        with self.engine.begin() as db:
            db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=draft["payload"]))
            versions = db.execute(
                select(story_plan_versions.c.id, story_plan_versions.c.payload).where(
                    story_plan_versions.c.draft_id == draft["id"]
                )
            ).mappings().all()
            for version in versions:
                version_payload = version["payload"]
                version_plan = version_payload.get("story_plan") or {}
                version_clips = {clip["index"]: clip for clip in version_plan.get("clips", [])}
                for clip in clips:
                    if clip["index"] in version_clips:
                        version_clips[clip["index"]]["voice_script"] = clip["voice_script"]
                version_plan["full_voice_script"] = joined_voice_script(version_plan)
                db.execute(
                    update(story_plan_versions)
                    .where(story_plan_versions.c.id == version["id"])
                    .values(payload=version_payload)
                )
            for clip in clips:
                db.execute(
                    update(video_scenes)
                    .where(
                        video_scenes.c.draft_id == draft["id"],
                        video_scenes.c.scene_index == clip["index"],
                    )
                    .values(payload=clip, updated=time.time())
                )
            messages = db.execute(
                select(chat_messages.c.id, chat_messages.c.content).where(
                    chat_messages.c.user_id == draft["user_id"],
                    chat_messages.c.role == "assistant",
                )
            ).mappings().all()
            for message in messages:
                try:
                    event = json.loads(message["content"])
                    event_plan = event.get("plan") or {}
                    event_story = event_plan.get("story_plan") or {}
                    if event.get("type") != "video_generation" or event_plan.get("id") != draft["id"]:
                        continue
                    event_clips = {clip["index"]: clip for clip in event_story.get("clips", [])}
                    for clip in clips:
                        if clip["index"] in event_clips:
                            event_clips[clip["index"]]["voice_script"] = clip["voice_script"]
                    event_story["full_voice_script"] = joined_voice_script(event_story)
                    db.execute(
                        update(chat_messages)
                        .where(chat_messages.c.id == message["id"])
                        .values(content=json.dumps(event, ensure_ascii=False))
                    )
                except (TypeError, ValueError, KeyError):
                    continue

    @staticmethod
    def _media_duration(path: Path) -> float:
        import imageio_ffmpeg

        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not match:
            return 0.0
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    @staticmethod
    def _run_story_audio_filter(path: Path, filter_spec: str, suffix: str) -> None:
        import imageio_ffmpeg

        output = path.with_name(f"{path.stem}{suffix}{path.suffix}")
        try:
            result = subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(path),
                    "-af", filter_spec, "-c:a", "libmp3lame", "-q:a", "3", str(output),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode != 0 or not output.is_file() or output.stat().st_size < 1000:
                raise RuntimeError("ffmpeg audio filter failed")
            output.replace(path)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            output.unlink(missing_ok=True)
            raise HTTPException(502, "Could not process narration audio for the video timeline") from None

    @classmethod
    def _compact_story_audio(cls, path: Path) -> None:
        cls._run_story_audio_filter(
            path,
            "silenceremove=stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB:stop_silence=0.2",
            ".compact",
        )

    @classmethod
    def _retime_story_audio(cls, path: Path, stretch: float) -> None:
        cls._run_story_audio_filter(path, f"atempo={stretch:.5f}", ".timed")

    def _project_id_for_draft(self, draft_id: str) -> str | None:
        with self.engine.connect() as db:
            return db.execute(
                select(story_plan_versions.c.project_id).where(
                    story_plan_versions.c.draft_id == draft_id
                )
            ).scalar_one_or_none()

    def _record_media_asset(
        self, draft: dict, scene_index: int | None, kind: str, path: Path, mime_type: str
    ) -> None:
        project_id = self._project_id_for_draft(draft["id"])
        if not project_id or not path.is_file():
            return
        hasher = hashlib.sha256()
        with path.open("rb") as media_file:
            for chunk in iter(lambda: media_file.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        relative_path = path.relative_to(self.storage).as_posix()
        with self.engine.begin() as db:
            existing = db.execute(
                select(video_media_assets.c.id).where(
                    video_media_assets.c.project_id == project_id,
                    video_media_assets.c.kind == kind,
                    video_media_assets.c.path == relative_path,
                )
            ).first()
            if existing:
                return
            db.execute(
                insert(video_media_assets).values(
                    id=secrets.token_hex(16),
                    project_id=project_id,
                    scene_index=scene_index,
                    kind=kind,
                    path=relative_path,
                    mime_type=mime_type,
                    sha256=digest,
                    metadata={"size_bytes": path.stat().st_size},
                    created=time.time(),
                )
            )

    def _record_audio_quality(
        self, draft: dict, batch_id: str, path: Path, duration: float, target: float
    ) -> float:
        import imageio_ffmpeg

        project_id = self._project_id_for_draft(draft["id"])
        if not project_id:
            return duration
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-i", str(path),
                "-af", "silencedetect=noise=-48dB:d=0.7",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stderr + result.stdout
        starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", output)]
        ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", output)]
        leading = ends[0] if starts and starts[0] <= 0.05 and ends else 0.0
        trailing = max(0.0, duration - starts[-1]) if starts and len(starts) > len(ends) else 0.0
        effective_duration = max(0.0, duration - trailing - leading)
        long_pauses = []
        for index, start in enumerate(starts):
            end = ends[index] if index < len(ends) else duration
            pause = end - start
            if pause >= 0.7 and start > 0.05 and end < duration - 0.05:
                long_pauses.append({"start_seconds": round(start, 3), "duration_seconds": round(pause, 3)})
        issues = []
        if target - duration > 0.5:
            issues.append("voice_shorter_than_video_timeline")
        if leading > 0.25:
            issues.append("leading_silence")
        if trailing > 0.25:
            issues.append("trailing_silence")
        if long_pauses:
            issues.append("long_internal_pause")
        with self.engine.begin() as db:
            previous = db.execute(
                select(video_quality_reports.c.id)
                .where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.stage == "AUDIO_PRECHECK",
                )
                .order_by(video_quality_reports.c.created.desc())
                .limit(1)
            ).scalar_one_or_none()
            values = {
                "status": "WARN" if issues else "PASS",
                "metrics": {
                    "audio_duration_seconds": round(duration, 3),
                    "effective_duration_seconds": round(effective_duration, 3),
                    "trim_start_seconds": round(leading, 3),
                    "target_duration_seconds": target,
                    "leading_silence_seconds": round(leading, 3),
                    "trailing_silence_seconds": round(trailing, 3),
                    "long_internal_pauses": long_pauses,
                },
                "issues": issues,
                "created": time.time(),
            }
            if previous:
                db.execute(
                    update(video_quality_reports)
                    .where(video_quality_reports.c.id == previous)
                    .values(**values)
                )
            else:
                db.execute(
                    insert(video_quality_reports).values(
                        id=secrets.token_hex(16),
                        project_id=project_id,
                        scene_index=None,
                        stage="AUDIO_PRECHECK",
                        **values,
                    )
                )
        return effective_duration

    def _story_audio_quality_issues(self, draft: dict) -> list[str]:
        project_id = self._project_id_for_draft(draft["id"])
        if not project_id:
            return []
        with self.engine.connect() as db:
            issues = db.execute(
                select(video_quality_reports.c.issues)
                .where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.stage == "AUDIO_PRECHECK",
                )
                .order_by(video_quality_reports.c.created.desc())
                .limit(1)
            ).scalar_one_or_none()
        return list(issues or [])

    def _story_batch_row(self, uid: str, batch_id: str) -> dict:
        with self.engine.connect() as db:
            row = (
                db.execute(
                    select(story_batches).where(
                        story_batches.c.id == batch_id, story_batches.c.user_id == uid
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "Không tìm thấy video")
        return dict(row)

    def present_story_batch(self, row: dict) -> dict:
        jobs = [self.store.get(gid) for gid in row["generation_ids"]]
        failed_index = self._failed_story_scene_index(row["draft_id"], row["generation_ids"]) if jobs and row["status"] == "FAILED" else len(jobs)
        failed_job = jobs[failed_index - 1] if jobs else None
        previous_jobs = jobs[:failed_index - 1]
        downstream_ready = all(job["status"] in {"COMPLETED", "FAILED", "CANCELLED"} for job in jobs[failed_index:])
        statuses = [job["status"] for job in jobs]
        complete = sum(status == "COMPLETED" for status in statuses)
        clip_count = self._story_clip_count(row.get("draft_id"))
        status = row["status"]
        # Failed before any scene was submitted: preparing again bills no video.
        preparation_error = not row["generation_ids"]
        retryable_provider_failure = bool(
            jobs
            and downstream_ready
            and all(job["status"] == "COMPLETED" for job in previous_jobs)
            and self._is_unbilled_scene_failure(failed_job)
        )
        retryable_bad_scene = bool(
            is_scene_quality_error(row["error_code"])
            and jobs
            and downstream_ready
            and all(job["status"] == "COMPLETED" for job in previous_jobs)
            and failed_job["status"] == "COMPLETED"
        )
        if retryable_bad_scene:
            complete = failed_index - 1
        elif status == "FAILED" and jobs and (is_scene_quality_error(row["error_code"]) or failed_job["status"] != "COMPLETED"):
            complete = min(complete, failed_index - 1)
        error = row["error_code"]
        if failed_job and (failed_job.get("result") or {}).get("error_detail"):
            error = f"{error}: {failed_job['result']['error_detail']}"[:400]
        with self.engine.connect() as db:
            root_payload = db.execute(select(drafts.c.payload).where(drafts.c.id == row.get("draft_id"))).scalar_one_or_none() or {}
        planned_clips = root_payload.get("story_plan", {}).get("clips", [])
        duration = sum(clip.get("duration_seconds", 8) for clip in planned_clips) if planned_clips else clip_count * 8
        retryable = status == "FAILED" and (preparation_error or retryable_provider_failure or retryable_bad_scene)
        attempts = row.get("auto_retries") or 0
        retry_pending = bool(self.story_retry_kind(row, retryable)) and attempts < AUTO_RETRY_LIMIT
        error_kind = story_error_kind(error) if status == "FAILED" else None
        # Admins see codes and provider messages; members get a plain notice.
        technical = self.credit_exempt(row["user_id"])
        if status == "COMPLETED" and row.get("filename"):
            measured_duration = self._media_duration(self.storage / "videos" / row["filename"])
            if measured_duration > 0:
                duration = round(measured_duration, 3)
        return {
            "id": row["id"],
            "draft_id": None,
            "prompt": row["title"],
            "created": row["created"],
            "status": status,
            "ratio": row["ratio"],
            "duration": duration,
            "demo": False,
            "provider": root_payload.get("render_target", {}).get("provider", jobs[0]["provider"] if jobs else self.provider),
            "model": root_payload.get("render_target", {}).get("video_model"),
            "error": error if technical else member_error_message(
                error_kind, pending=retry_pending, attempts=attempts, error=error,
            ),
            "error_code": error if technical else None,
            "error_kind": error_kind,
            "auto_retry": {"attempts": attempts, "limit": AUTO_RETRY_LIMIT, "pending": retry_pending},
            "cancel_requested": False,
            "video_url": f"/api/v1/story-batches/{row['id']}/video"
            if status == "COMPLETED"
            else None,
            "story_batch": True,
            "audio_mode": row["audio_mode"],
            **root_payload.get("voice_policy", {}),
            "clip_progress": {"completed": complete, "total": clip_count},
            "retryable": retryable,
        }

    def _story_clip_count(self, draft_id: str | None) -> int:
        if not draft_id:
            return 5
        with self.engine.connect() as db:
            payload = db.execute(
                select(drafts.c.payload).where(drafts.c.id == draft_id)
            ).scalar_one_or_none()
        clips = ((payload or {}).get("story_plan") or {}).get("clips") or []
        return min(5, max(1, len(clips))) if clips else 5

    def story_batch(self, uid: str, batch_id: str) -> dict:
        return self.present_story_batch(self._story_batch_row(uid, batch_id))

    def retry_story_batch_merge(self, uid: str, batch_id: str) -> dict:
        row = self._story_batch_row(uid, batch_id)
        if row["error_code"] != "story_merge_failed":
            raise HTTPException(409, "Batch này không có lỗi ghép để thử lại")
        for generation_id in row["generation_ids"]:
            job = self.store.get(generation_id)
            if job["status"] == "NEEDS_USER_ACTION" and job["error_code"] == "output_persistence_failed":
                with self.engine.connect() as db:
                    stored = (
                        db.execute(
                            select(videos).where(videos.c.generation_id == generation_id)
                        )
                        .mappings()
                        .first()
                    )
                if stored:
                    path = self.storage / "videos" / stored["filename"]
                    if path.is_file() and path.stat().st_size == stored["size_bytes"]:
                        self.finalize(job)
            if self.store.get(generation_id)["status"] != "COMPLETED":
                raise HTTPException(409, "Chưa đủ clip hoàn tất để ghép lại")
        with self.engine.begin() as db:
            db.execute(
                update(story_batches)
                .where(story_batches.c.id == batch_id)
                .values(status="MERGING", error_code=None)
            )
        self._process_story_batch(row)
        return self.story_batch(uid, batch_id)
