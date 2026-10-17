"""Generation job delivery, output persistence, and download validation."""

from app.services.platform_shared import (
    HTTPException,
    Path,
    drafts,
    executions,
    httpx,
    insert,
    ipaddress,
    os,
    select,
    socket,
    story_batches,
    time,
    update,
    urlparse,
    videos,
)


class PlatformExecutionMixin:
    def jobs(self, uid):
        with self.engine.connect() as db:
            rows = (
                db.execute(
                    select(drafts)
                    .where(drafts.c.user_id == uid, drafts.c.generation_id.is_not(None))
                    .order_by(drafts.c.created.desc())
                    .limit(100)
                )
                .mappings()
                .all()
            )
        with self.engine.connect() as db:
            batches = (
                db.execute(
                    select(story_batches)
                    .where(story_batches.c.user_id == uid)
                    .order_by(story_batches.c.created.desc())
                )
                .mappings()
                .all()
            )
        batch_generations = {
            gid
            for batch in batches
            if batch["status"] != "FAILED"
            for gid in batch["generation_ids"]
        }
        regular = [
            self.present(row) for row in rows if row["generation_id"] not in batch_generations
        ]
        combined = [self.present_story_batch(dict(batch)) for batch in batches]
        return sorted(regular + combined, key=lambda item: item["created"], reverse=True)

    def job(self, uid, gid):
        with self.engine.connect() as db:
            batch = db.execute(
                select(story_batches.c.id).where(
                    story_batches.c.id == gid, story_batches.c.user_id == uid
                )
            ).first()
        if batch:
            return self.story_batch(uid, gid)
        with self.engine.connect() as db:
            row = (
                db.execute(
                    select(drafts).where(drafts.c.user_id == uid, drafts.c.generation_id == gid)
                )
                .mappings()
                .first()
            )
        if not row:
            raise HTTPException(404, "Không tìm thấy video")
        return self.present(row)

    def video_path(self, uid: str, generation_id: str) -> Path:
        job = self.job(uid, generation_id)
        if job.get("story_batch"):
            return self.story_batch_video_path(uid, generation_id)
        if job["status"] != "COMPLETED":
            raise HTTPException(409, "Video chưa hoàn thành")
        if job["demo"]:
            return Path(__file__).resolve().parents[2] / "fixtures" / "demo.mp4"
        with self.engine.connect() as db:
            row = (
                db.execute(
                    select(videos).where(
                        videos.c.generation_id == generation_id, videos.c.user_id == uid
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(503, "Video chưa được lưu vào storage")
        path = self.storage / "videos" / row["filename"]
        if not path.is_file():
            raise HTTPException(503, "Không tìm thấy file video trong storage")
        return path

    def finalize(self, job: dict) -> None:
        if job["provider"] == "fake":
            return
        try:
            with self.engine.connect() as db:
                stored = (
                    db.execute(
                        select(videos).where(videos.c.generation_id == job["generation_id"])
                    )
                    .mappings()
                    .first()
                )
            if stored:
                stored_path = self.storage / "videos" / stored["filename"]
                if (
                    stored_path.is_file()
                    and stored_path.stat().st_size == stored["size_bytes"]
                    and stored["size_bytes"] >= 12
                ):
                    with self.engine.begin() as db:
                        db.execute(
                            update(executions)
                            .where(
                                executions.c.generation_id == job["generation_id"],
                                executions.c.status.in_(
                                    ["POST_PROCESSING", "NEEDS_USER_ACTION"]
                                ),
                            )
                            .values(status="COMPLETED", error_code=None)
                        )
                    self._record_completed_scene_output(job["generation_id"])
                    return
            source = job["result"]["outputs"][0]["source_url"]
            filename, size = self._download_video(job["generation_id"], source)
            with self.engine.begin() as db:
                owner = db.execute(
                    select(drafts.c.user_id).where(drafts.c.generation_id == job["generation_id"])
                ).scalar_one()
                existing = db.execute(
                    select(videos.c.generation_id).where(
                        videos.c.generation_id == job["generation_id"]
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.execute(
                        insert(videos).values(
                            generation_id=job["generation_id"],
                            user_id=owner,
                            filename=filename,
                            size_bytes=size,
                            created=time.time(),
                        )
                    )
                db.execute(
                    update(executions)
                    .where(
                        executions.c.generation_id == job["generation_id"],
                        executions.c.status == "POST_PROCESSING",
                    )
                    .values(status="COMPLETED")
                )
            self._record_completed_scene_output(job["generation_id"])
        except Exception:
            with self.engine.begin() as db:
                db.execute(
                    update(executions)
                    .where(
                        executions.c.generation_id == job["generation_id"],
                        executions.c.status == "POST_PROCESSING",
                    )
                    .values(status="NEEDS_USER_ACTION", error_code="output_persistence_failed")
                )
            raise

    def _download_video(self, generation_id: str, source: str) -> tuple[str, int]:
        parsed = urlparse(source)
        allowed = tuple(
            part.strip().lower()
            for part in os.getenv(
                "VIDEO_OUTPUT_ALLOWED_HOST_SUFFIXES",
                ".bytepluses.com,.byteplus.com,.volces.com,.poyo.ai,.doculator.org,.seedvis.com",
            ).split(",")
            if part.strip()
        )
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in allowed)
        ):
            raise ValueError("Untrusted provider output host")
        for answer in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(answer[4][0])
            if not address.is_global:
                raise ValueError("Provider output resolved to a non-public address")
        target_dir = self.storage / "videos"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = generation_id + ".mp4"
        target = target_dir / filename
        temporary = target.with_suffix(".part")
        maximum = int(os.getenv("VIDEO_OUTPUT_MAX_BYTES", str(500 * 1024 * 1024)))
        # A just-completed output can briefly 404/5xx on the CDN; retry before giving up.
        for delay in (2, 5, 0):
            try:
                return self._stream_video(source, target, temporary, maximum, filename)
            except httpx.HTTPError:
                if not delay:
                    raise
                time.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _stream_video(source: str, target, temporary, maximum: int, filename: str) -> tuple[str, int]:
        size = 0
        try:
            with (
                httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client,
                client.stream("GET", source) as response,
            ):
                response.raise_for_status()
                if not response.headers.get("content-type", "").lower().startswith("video/"):
                    raise ValueError("Provider output is not video")
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > maximum:
                            raise ValueError("Provider output exceeds configured limit")
                        stream.write(chunk)
            if size < 12:
                raise ValueError("Provider output is empty")
            temporary.replace(target)
            return filename, size
        finally:
            if temporary.exists():
                temporary.unlink()
