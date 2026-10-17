"""Learn reusable short-form styles from reference videos; only the analysis is kept."""

import re
import shutil
import tempfile

from app.services.platform_shared import (
    HTTPException,
    Path,
    base64,
    delete,
    httpx,
    insert,
    logger,
    secrets,
    select,
    style_references,
    subprocess,
    time,
    urlparse,
)

MAX_REFERENCE_BYTES = 50 * 1024 * 1024
MAX_REFERENCE_SECONDS = 180
MAX_STYLE_FRAMES = 14
# Platform pages only: the server never fetches arbitrary user-supplied hosts.
# Writing-style markers counted in the transcript; counts describe the style, they are not targets.
SPEECH_PARTICLES = ("nè", "nha", "nhé", "nhá", "luôn", "á", "nghen", "hen", "ha", "hông", "ạ", "đó", "đấy", "nhỉ", "ta")
ADDRESSING_TERMS = (
    "mình", "bạn", "các bạn", "mọi người", "cả nhà", "mấy mẹ", "các mẹ", "chị em", "các chị", "anh em",
    "tụi mình", "bọn mình", "em", "chị", "anh", "tôi", "tớ", "cậu", "bà con", "quý vị", "mấy bà", "các bà",
)
REFERENCE_URL_HOSTS = (
    "tiktok.com", "instagram.com", "facebook.com", "fb.watch", "youtube.com", "youtu.be",
)


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def style_frame_times(duration: float, cuts: list[float], limit: int = MAX_STYLE_FRAMES) -> list[float]:
    """Sample the hook densely, every cut once, and the rest evenly, without near-duplicates."""
    if duration <= 0:
        return []
    candidates = [0.2, min(1.0, duration * 0.1)]
    candidates += [min(duration - 0.05, cut + 0.2) for cut in cuts]
    candidates += [duration * index / 9 for index in range(1, 9)]
    chosen: list[float] = []
    for value in sorted(max(0.0, min(duration - 0.05, value)) for value in candidates):
        if all(abs(value - kept) >= 0.5 for kept in chosen):
            chosen.append(round(value, 2))
    if len(chosen) <= limit:
        return chosen
    step = (len(chosen) - 1) / (limit - 1)
    return [chosen[round(index * step)] for index in range(limit)]


def speech_metrics(transcript: str) -> dict:
    """Measured description of how the reference lines are written; wording only, never delivery."""
    text = transcript.strip()
    if not text:
        return {}
    lower = text.casefold()
    words = re.findall(r"[^\W\d_]+", lower)
    sentences = [part for part in re.split(r"[.!?…]+", text) if part.strip()]

    def counts(terms):
        found = {term: len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", lower)) for term in terms}
        return {term: count for term, count in sorted(found.items(), key=lambda item: -item[1]) if count}

    return {
        "words": len(words),
        "sentences": len(sentences),
        "avg_words_per_sentence": round(len(words) / max(1, len(sentences)), 1),
        "questions": text.count("?"),
        "exclamations": text.count("!"),
        "particles": counts(SPEECH_PARTICLES),
        "addressing_terms": counts(ADDRESSING_TERMS),
    }


class PlatformStyleLearningMixin:
    def style_references(self, uid: str) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                select(style_references).where(style_references.c.user_id == uid)
                .order_by(style_references.c.created.desc())
            ).mappings().all()
        return [dict(row) for row in rows]

    def style_blueprint(self, uid: str, reference_id: str) -> dict:
        return self.owned(style_references, reference_id, uid)["blueprint"]

    def delete_style_reference(self, uid: str, reference_id: str) -> None:
        self.owned(style_references, reference_id, uid)
        with self.engine.begin() as db:
            db.execute(delete(style_references).where(
                style_references.c.id == reference_id, style_references.c.user_id == uid,
            ))

    def learn_style(self, uid: str, *, data: bytes | None, filename: str, url: str | None, title: str | None) -> dict:
        if not self.planner_credential_status()["configured"]:
            raise HTTPException(409, "Hãy cấu hình OpenAI planner trước khi học phong cách")
        directory = Path(tempfile.mkdtemp(prefix="style-", dir=self._style_workspace()))
        try:
            if data is not None:
                source = directory / "reference.mp4"
                source.write_bytes(data)
                label = filename or "video tải lên"
            elif url:
                source, label = self._download_reference(url, directory), url
            else:
                raise HTTPException(422, "Hãy tải video lên hoặc dán link video")
            duration = self._media_duration(source)
            if duration <= 0:
                raise HTTPException(422, "Không đọc được video tham khảo")
            if duration > MAX_REFERENCE_SECONDS:
                raise HTTPException(422, "Video tham khảo tối đa 3 phút")
            cuts = self._detect_cuts(source)
            frames = self._style_frames(source, style_frame_times(duration, cuts), directory)
            if not frames:
                raise HTTPException(422, "Không trích được khung hình từ video tham khảo")
            audio_present, transcript = self._reference_transcript(source, directory)
            metrics = speech_metrics(transcript)
            try:
                response = self.http.post("/internal/v1/planner/learn-style", json={
                    "frames": frames, "transcript": transcript, "duration_seconds": duration,
                    "cut_times": cuts[:300], "audio_present": audio_present, "source_label": label[:300],
                    "speech_metrics": metrics,
                }, timeout=240)
                response.raise_for_status()
                blueprint = response.json()["blueprint"]
            except httpx.HTTPStatusError as exc:
                try:
                    reason = exc.response.json().get("detail")
                except ValueError:
                    reason = None
                raise HTTPException(502, f"Không phân tích được phong cách: {reason or 'lỗi dịch vụ AI'}") from None
            except (httpx.HTTPError, KeyError, ValueError):
                raise HTTPException(502, "Không phân tích được phong cách. Hãy thử lại.") from None
        finally:
            # The reference video itself is never retained; only the technique analysis is stored.
            shutil.rmtree(directory, ignore_errors=True)
        row = {
            "id": secrets.token_hex(16), "user_id": uid,
            "title": (title or blueprint.get("title") or "Phong cách mới").strip()[:80],
            "source": label[:300], "blueprint": blueprint, "created": time.time(),
        }
        with self.engine.begin() as db:
            db.execute(insert(style_references).values(**row))
        return row

    def _style_workspace(self) -> Path:
        directory = self.storage / "style-learning"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _download_reference(url: str, directory: Path) -> Path:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not any(host == item or host.endswith("." + item) for item in REFERENCE_URL_HOSTS):
            raise HTTPException(422, "Chỉ nhận link TikTok, Instagram, Facebook hoặc YouTube; hoặc tải video lên")
        try:
            import yt_dlp
        except ImportError:
            raise HTTPException(501, "Máy chủ chưa cài yt-dlp để tải link. Hãy tải video lên trực tiếp") from None
        options = {
            "format": "mp4/best", "noplaylist": True, "max_filesize": MAX_REFERENCE_BYTES,
            "outtmpl": str(directory / "reference.%(ext)s"), "quiet": True, "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([url])
        except Exception:
            logger.warning("[STYLE] reference download failed host=%s", host)
            raise HTTPException(502, "Không tải được video từ link này. Hãy tải video lên trực tiếp") from None
        files = sorted(directory.glob("reference.*"))
        if not files or files[0].stat().st_size > MAX_REFERENCE_BYTES:
            raise HTTPException(422, "Video tham khảo tối đa 50 MB")
        return files[0]

    @staticmethod
    def _detect_cuts(source: Path) -> list[float]:
        result = subprocess.run([
            _ffmpeg(), "-hide_banner", "-nostats", "-i", str(source), "-an",
            "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=120)
        cuts: list[float] = []
        for value in sorted(float(item) for item in re.findall(r"pts_time:([\d.]+)", result.stderr)):
            # A flash or dissolve fires on neighbouring frames; one transition is one cut.
            if not cuts or value - cuts[-1] >= 0.3:
                cuts.append(round(value, 2))
        return cuts

    @staticmethod
    def _style_frames(source: Path, times: list[float], directory: Path) -> list[dict]:
        frames = []
        for index, moment in enumerate(times):
            target = directory / f"frame-{index}.jpg"
            subprocess.run([
                _ffmpeg(), "-y", "-ss", f"{moment:.2f}", "-i", str(source), "-frames:v", "1",
                "-vf", "scale=480:-2", "-q:v", "4", str(target),
            ], capture_output=True, text=True, timeout=60)
            if target.is_file() and target.stat().st_size > 500:
                frames.append({
                    "time_seconds": moment,
                    "image_data_url": "data:image/jpeg;base64," + base64.b64encode(target.read_bytes()).decode(),
                })
        return frames

    def _reference_transcript(self, source: Path, directory: Path) -> tuple[bool, str]:
        audio = directory / "audio.mp4"
        result = subprocess.run([
            _ffmpeg(), "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "aac", "-b:a", "48k", "-f", "mp4", str(audio),
        ], capture_output=True, text=True, timeout=120)
        if result.returncode or not audio.is_file() or audio.stat().st_size < 1000:
            return False, ""
        try:
            response = self.http.post("/internal/v1/planner/transcribe-clip", content=audio.read_bytes(),
                                      headers={"Content-Type": "video/mp4"}, timeout=150)
            response.raise_for_status()
            return True, str(response.json().get("text") or "")[:8000]
        except (httpx.HTTPError, ValueError):
            # Style can still be learned from frames and cuts when speech recognition is unavailable.
            return True, ""
