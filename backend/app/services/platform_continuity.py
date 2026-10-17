"""Scene reference images, continuity checks, and boundary reports."""

import shutil
import tempfile

from app.services.continuity_frames import FrameCandidate, choose_continuity_frame
from app.services.platform_shared import (
    HTTPException,
    Image,
    ImageOps,
    Path,
    assets,
    base64,
    drafts,
    hashlib,
    hmac,
    httpx,
    insert,
    secrets,
    select,
    subprocess,
    time,
    update,
    video_quality_reports,
    video_scenes,
    videos,
)


class PlatformContinuityMixin:
    @property
    def _poyo_public_urls(self) -> bool:
        # With an HTTPS tunnel PoYo fetches signed backend URLs; otherwise images travel
        # inline and the AI service uploads them to PoYo storage before submission.
        return self.provider == "poyo" and self.public_base_url.startswith("https://")

    def continuity_reference_url(self, kind: str, reference_id: str) -> str:
        if not self.public_base_url.startswith("https://"):
            raise HTTPException(
                409,
                "Continuity reference requires PUBLIC_BASE_URL to be a publicly reachable HTTPS URL.",
            )
        expires = int(time.time()) + 86400
        message = f"{kind}:{reference_id}:{expires}".encode()
        signature = hmac.new(self.signing_key, message, hashlib.sha256).hexdigest()
        return (
            f"{self.public_base_url}/api/v1/continuity-reference/"
            f"{kind}/{reference_id}/{expires}/{signature}"
        )

    def _validate_public_continuity_image(self, draft_id: str) -> None:
        image_url = self.continuity_reference_url("scene", f"anchor-{draft_id}")
        try:
            with httpx.Client(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = client.get(image_url)
                response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/jpeg") or not response.content.startswith(b"\xff\xd8"):
                raise ValueError("public URL did not return a JPEG reference image")
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                503,
                "continuity_reference_unreachable: PoYo cannot access the reference image. Start the HTTPS tunnel and update PUBLIC_BASE_URL, then retry.",
            ) from None

    def _validate_public_backend_target(self) -> None:
        try:
            with httpx.Client(timeout=12, follow_redirects=True, trust_env=False) as client:
                response = client.get(f"{self.public_base_url}/openapi.json")
                response.raise_for_status()
                paths = response.json().get("paths", {})
        except (httpx.HTTPError, ValueError, AttributeError):
            paths = {}
        if not any("continuity-reference" in path for path in paths):
            raise HTTPException(
                503,
                "continuity_reference_unreachable: PUBLIC_BASE_URL must point to the VO3 backend, not the AI service. Route the tunnel to backend port 8100, then retry.",
            )

    def _continuity_source(self, kind: str, reference_id: str) -> str:
        if self._poyo_public_urls:
            return self.continuity_reference_url(kind, reference_id)
        if kind in {"character", "style", "keyframe"}:
            path = self.storage / "continuity" / f"{kind}-{reference_id}.jpg"
            if not path.is_file():
                raise HTTPException(404, "Continuity reference image is missing")
            return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()
        if kind not in {"scene", "frame"}:
            raise HTTPException(422, "Unsupported continuity reference")
        path = self.storage / "continuity" / f"{reference_id}.jpg"
        if not path.is_file():
            raise HTTPException(404, "Continuity reference frame is missing")
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()

    def _ensure_character_reference_images(self, draft_id: str, anchor: Path) -> None:
        directory = self.storage / "continuity"
        character_path = directory / f"character-{draft_id}.jpg"
        style_path = directory / f"style-{draft_id}.jpg"
        anchor_mtime = anchor.stat().st_mtime_ns
        if (
            character_path.is_file() and style_path.is_file()
            and character_path.stat().st_mtime_ns >= anchor_mtime
            and style_path.stat().st_mtime_ns >= anchor_mtime
        ):
            return
        with Image.open(anchor) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            character_crop = image.crop((
                round(width * 0.24),
                round(height * 0.02),
                round(width * 0.76),
                round(height * 0.76),
            ))
            style_crop = image.crop((
                round(width * 0.12),
                round(height * 0.02),
                round(width * 0.88),
                round(height * 0.98),
            ))
            for crop, path in ((character_crop, character_path), (style_crop, style_path)):
                temporary = path.with_name(f"{path.stem}-{secrets.token_hex(4)}.jpg")
                try:
                    crop.save(temporary, "JPEG", quality=92, optimize=True)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)

    def continuity_reference_path(
        self, kind: str, reference_id: str, expires: int, signature: str
    ) -> Path:
        if kind not in {"asset", "frame", "scene", "character", "style", "keyframe"} or expires < int(time.time()):
            raise HTTPException(404, "Reference image not found")
        message = f"{kind}:{reference_id}:{expires}".encode()
        expected = hmac.new(self.signing_key, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(404, "Reference image not found")
        if kind == "asset":
            with self.engine.connect() as db:
                row = db.execute(select(assets).where(assets.c.id == reference_id)).mappings().first()
            path = self.storage / row["filename"] if row else None
        elif kind == "frame":
            path = self.storage / "continuity" / f"{reference_id}.jpg"
        elif kind in {"character", "style", "keyframe"}:
            path = self.storage / "continuity" / f"{kind}-{reference_id}.jpg"
        else:
            path = self.storage / "continuity" / f"{reference_id}.jpg"
        if path is None or not path.is_file():
            raise HTTPException(404, "Reference image not found")
        return path

    def _extract_continuity_frame(self, generation_id: str) -> Path:
        import imageio_ffmpeg

        with self.engine.connect() as db:
            video = (
                db.execute(select(videos).where(videos.c.generation_id == generation_id))
                .mappings()
                .first()
            )
        if not video:
            raise RuntimeError("Completed clip is missing from storage")
        source = self.storage / "videos" / video["filename"]
        target_dir = self.storage / "continuity"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{generation_id}.jpg"
        with self.engine.connect() as db:
            scene = db.execute(select(video_scenes).where(video_scenes.c.generation_id == generation_id)).mappings().first()
        metadata = (scene["continuity_metadata"] or {}) if scene else {}
        if metadata.get("composition_end_seconds") and target.is_file():
            return target
        trimmed = self._scene_native_trim(generation_id)
        if trimmed and scene and trimmed.get("trailing_silence_seconds", 0) >= 0.35:
            safe_end = trimmed["trim_end_seconds"]
            maximum = min(trimmed.get("duration_seconds", safe_end), safe_end + 0.2)
            with tempfile.TemporaryDirectory(prefix="continuity-", dir=target_dir) as temporary:
                candidates = []
                for offset in (0.0, 0.1, 0.2):
                    end = min(maximum, safe_end + offset)
                    timestamp = max(0.0, end - 1 / 24)
                    path = Path(temporary) / f"frame-{len(candidates)}.jpg"
                    result = subprocess.run([
                        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss", f"{timestamp:.4f}",
                        "-i", str(source), "-frames:v", "1", "-q:v", "2", str(path),
                    ], capture_output=True, text=True, timeout=90)
                    if not result.returncode and path.is_file() and path.stat().st_size >= 1000:
                        candidates.append(FrameCandidate(path, timestamp))
                    if end >= maximum:
                        break
                if candidates:
                    chosen, measurements = choose_continuity_frame(candidates)
                    shutil.copyfile(chosen.path, target)
                    metadata.update(measurements, continuity_frame_time_seconds=chosen.timestamp,
                                    composition_end_seconds=max(safe_end, chosen.timestamp + 1 / 24),
                                    frame_selection_method="SAFE_SILENT_TAIL")
                    with self.engine.begin() as db:
                        db.execute(update(video_scenes).where(video_scenes.c.id == scene["id"])
                                   .values(continuity_metadata=metadata, updated=time.time()))
                    return target
        attempts = [(("-sseof", "-0.5"), ("-vf", "reverse"))]
        if trimmed:
            # Speech can run to the container end while the video stream stops a frame earlier
            # (Omni Flash); a seek past the last video frame yields nothing, so fall back to it.
            attempts.insert(0, (("-ss", f"{max(0, trimmed['trim_end_seconds'] - 1 / 24):.4f}"), ()))
        for seek, reverse in attempts:
            result = subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(),
                    "-y",
                    *seek,
                    "-i",
                    str(source),
                    *reverse,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if not result.returncode and target.is_file() and target.stat().st_size >= 1000:
                return target
            target.unlink(missing_ok=True)
        raise RuntimeError("Could not extract the previous clip's final frame")

    def _extract_video_frame(self, generation_id: str, edge: str) -> Path:
        import imageio_ffmpeg

        if edge not in {"start", "end"}:
            raise ValueError("Unsupported frame edge")
        if edge == "end":
            return self._extract_continuity_frame(generation_id)
        with self.engine.connect() as db:
            video = db.execute(
                select(videos).where(videos.c.generation_id == generation_id)
            ).mappings().first()
        if not video:
            raise RuntimeError("Completed clip is missing from storage")
        source = self.storage / "videos" / video["filename"]
        directory = self.storage / "continuity"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"frame-{generation_id}-{edge}.jpg"
        trimmed = self._scene_native_trim(generation_id)
        if trimmed:
            timestamp = trimmed["trim_start_seconds"] if edge == "start" else max(0, trimmed["trim_end_seconds"] - 1 / 24)
            seek, reverse = ("-ss", f"{timestamp:.4f}"), ()
        else:
            seek = () if edge == "start" else ("-sseof", "-0.5")
            reverse = () if edge == "start" else ("-vf", "reverse")
        result = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", *seek, "-i", str(source),
                *reverse,
                "-frames:v", "1", "-q:v", "2", str(target),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode or not target.is_file() or target.stat().st_size < 1000:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Could not extract the {edge} frame for continuity review")
        return target

    def _record_scene_boundary_quality(
        self,
        draft_id: str,
        scene_index: int,
        previous_end: Path,
        current_start: Path,
        anchor: Path | None,
    ) -> None:
        from PIL import ImageChops, ImageStat

        project_id = self._project_id_for_draft(draft_id)
        if not project_id:
            return

        def normalized(path: Path):
            with Image.open(path) as image:
                return image.convert("RGB").resize((64, 64))

        current = normalized(current_start)
        boundary_delta = self._scene_boundary_delta(previous_end, current_start)
        anchor_delta = None
        if anchor and anchor.is_file():
            anchor_delta = round(
                ImageStat.Stat(
                    ImageChops.difference(normalized(anchor), current).convert("L")
                ).mean[0],
                3,
            )

        issues = ["large_boundary_pixel_change"] if boundary_delta > 34 else []
        if anchor_delta is not None and anchor_delta >= 50:
            issues.append("large_anchor_visual_change")
        with self.engine.begin() as db:
            db.execute(
                insert(video_quality_reports).values(
                    id=secrets.token_hex(16),
                    project_id=project_id,
                    scene_index=scene_index,
                    stage="SCENE_BOUNDARY",
                    status="WARN" if issues else "PASS",
                    metrics={
                        "previous_end_to_current_start_mean_pixel_delta": boundary_delta,
                        "current_start_to_shared_anchor_mean_pixel_delta": anchor_delta,
                    },
                    issues=issues,
                    created=time.time(),
                )
            )

    def _review_scene_reference(
        self, draft_id: str, scene_index: int, anchor: Path,
        previous_end: Path, current_end: Path,
    ) -> bool:
        project_id = self._project_id_for_draft(draft_id)
        if not project_id or not anchor.is_file():
            return True
        with self.engine.connect() as db:
            payload = db.execute(select(drafts.c.payload).where(drafts.c.id == draft_id)).scalar_one()
        clips = (payload.get("story_plan") or {}).get("clips", [])
        planned_shot_change = scene_index > 1 and any(
            1 < clip.get("index", 0) <= scene_index and clip.get("transition_intent") in {
                "hard_cut", "match_cut", "intentional_camera_transition", "fade_to_black",
            } for clip in clips
        )
        current_delta = self._scene_boundary_delta(anchor, current_end)
        previous_delta = (
            self._scene_boundary_delta(anchor, previous_end) if scene_index > 1 else 0.0
        )
        increase = round(current_delta - previous_delta, 3)
        drifted = (
            not planned_shot_change and current_delta >= 50
            and (increase >= 18 if scene_index > 1 else current_delta >= 65)
        )
        # Whole-frame pixel differences cannot verify a face after a planned shot change.
        issues = ["visual_identity_unverified_planned_transition"] if planned_shot_change else ["visual_reference_drift"] if drifted else (
            ["large_anchor_visual_change"] if current_delta >= 50 else []
        )
        with self.engine.begin() as db:
            if not issues:
                db.execute(
                    update(video_quality_reports)
                    .where(
                        video_quality_reports.c.project_id == project_id,
                        video_quality_reports.c.scene_index == scene_index,
                        video_quality_reports.c.stage == "SCENE_REFERENCE",
                        video_quality_reports.c.status.in_(("FAIL", "WARN")),
                    )
                    .values(status="RESOLVED")
                )
            db.execute(
                insert(video_quality_reports).values(
                    id=secrets.token_hex(16), project_id=project_id,
                    scene_index=scene_index, stage="SCENE_REFERENCE",
                    status="FAIL" if drifted else "WARN" if issues else "PASS",
                    metrics={
                        "anchor_to_scene_end_mean_pixel_delta": current_delta,
                        "previous_anchor_to_end_mean_pixel_delta": previous_delta,
                        "increase_from_previous_scene": increase,
                        "planned_shot_change": planned_shot_change,
                        "face_identity_verified": False,
                    },
                    issues=issues, created=time.time(),
                )
            )
        return not drifted

    @staticmethod
    def _scene_boundary_delta(previous_end: Path, current_start: Path) -> float:
        from PIL import ImageChops, ImageStat

        with Image.open(previous_end) as previous_image, Image.open(current_start) as current_image:
            previous = previous_image.convert("RGB").resize((64, 64))
            current = current_image.convert("RGB").resize((64, 64))
        return round(
            ImageStat.Stat(ImageChops.difference(previous, current).convert("L")).mean[0], 3
        )
