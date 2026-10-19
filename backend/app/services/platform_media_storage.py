"""Videos kept in object storage (R2) so the volume only holds the database and recent working files.

Uploaded images and continuity files are copied there as a backup but stay on the volume.
"""

import mimetypes

from app.services.platform_shared import Path, logger, os, shutil, time


class PlatformMediaStorageMixin:
    objects = None

    def _video_file(self, filename: str) -> Path:
        """Local path of a stored video, fetched back from object storage when it was moved off the disk."""
        path = self.storage / "videos" / filename
        if path.is_file():
            # Eviction goes by last use, so a clip being merged or reviewed is not removed under it.
            os.utime(path)
        elif self.objects:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.objects.download(f"videos/{filename}", path)
            except Exception as exc:
                logger.warning("Could not restore video %s from object storage: %s", filename, exc)
        return path

    def _video_available(self, filename: str) -> bool:
        if (self.storage / "videos" / filename).is_file():
            return True
        if not self.objects:
            return False
        try:
            return self.objects.size(f"videos/{filename}") is not None
        except Exception as exc:
            logger.warning("Could not check video %s in object storage: %s", filename, exc)
            return False

    def media_url(self, path: Path, media_type: str, filename: str | None = None) -> str:
        """Short-lived link to the object storage copy of a file that is no longer on the volume."""
        return self.objects.url(path.relative_to(self.storage).as_posix(), media_type, filename)

    def media_work(self):
        interval = max(30, int(os.getenv("R2_SYNC_SECONDS", "300")))
        while True:
            try:
                self.sync_media()
            except Exception:
                logger.exception("Video sync to object storage failed; will retry")
            if self.stop.wait(interval):
                return

    def sync_media(self) -> None:
        """Upload finished videos, then drop local copies that are safely stored and not in recent use."""
        directory = self.storage / "videos"
        if not self.objects:
            return
        keep = float(os.getenv("R2_KEEP_LOCAL_HOURS", "24")) * 3600
        uploaded = getattr(self, "_uploaded_videos", None)
        if uploaded is None:
            uploaded = self._uploaded_videos = {}
        now = time.time()
        files = []
        for path in directory.glob("*.mp4"):
            if ".part" in path.name:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        for _, size, path in sorted(files):
            if self.stop.is_set():
                return
            relative = f"videos/{path.name}"
            if uploaded.get(path.name) != size:
                if self.objects.size(relative) != size:
                    self.objects.upload(path, relative, "video/mp4")
                    if self.objects.size(relative) != size:
                        logger.warning("Uploaded video %s does not match its local size", path.name)
                        continue
                uploaded[path.name] = size
        usage = shutil.disk_usage(self.storage)
        # Below this much free space, older local copies go even before their keep period ends.
        minimum_free = max(int(os.getenv("R2_MIN_FREE_MB", "100")) * 1024 * 1024, usage.total // 5)
        free = usage.free
        for modified, size, path in sorted(files):
            if uploaded.get(path.name) != size:
                continue
            idle = now - modified
            if idle < keep and (free >= minimum_free or idle < 1800):
                continue
            try:
                if path.stat().st_mtime != modified:
                    continue
                path.unlink()
            except FileNotFoundError:
                continue
            free += size
            logger.info("Moved video %s to object storage only", path.name)
        self._back_up_files(now)

    def _back_up_files(self, now: float) -> None:
        """Copy uploaded images and continuity files to object storage; they stay on the volume too."""
        backed_up = getattr(self, "_backed_up_files", None)
        if backed_up is None:
            backed_up = self._backed_up_files = {}
        continuity = self.storage / "continuity"
        paths = [*self.storage.glob("*.jpg"), *(continuity.iterdir() if continuity.is_dir() else ())]
        for path in paths:
            if self.stop.is_set():
                return
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            # Writers stage files under a random name for a moment; skip anything that fresh.
            if not path.is_file() or now - stat.st_mtime < 60:
                continue
            relative = path.relative_to(self.storage).as_posix()
            marker = (stat.st_size, stat.st_mtime)
            if backed_up.get(relative) == marker:
                continue
            remote = self.objects.stat(relative)
            # Anchors and keyframes are rewritten in place when regenerated, so a newer local file wins.
            if remote is None or remote[0] != stat.st_size or remote[1] < stat.st_mtime:
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.objects.upload(path, relative, content_type)
            backed_up[relative] = marker
