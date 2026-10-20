"""Native speech validation and timing, without synthesizing replacement audio."""

import secrets
import subprocess
import time

import httpx
from sqlalchemy import insert, select, update

from app.services.native_audio_quality import (
    audio_measurements,
    boundary_speech_issues,
    native_audio_gap_issues,
    native_speech_issues,
    native_trim_window,
    speech_tokens,
)
from app.services.platform_shared import Path, video_quality_reports, video_scenes
from app.services.spoken_text import clean_spoken_text
from app.services.video_continuity import scene_transition

# v4 reads digits in transcripts as Vietnamese words ("95%" matches "chín mươi lăm phần trăm");
# v5 reads printed codes by their letter names ("TK01" matches "tê ca không một").
NATIVE_REVIEW_PROFILE = "continuity-v5"
SPEECH_ISSUE_LABELS = {
    "native_speech_repetition": "đọc lặp từ",
    "native_speech_incomplete": "đọc thiếu hoặc bị cắt",
    "native_speech_mismatch": "đọc sai lời thoại",
    "native_speech_unintelligible": "không nghe rõ lời",
    "native_audio_missing_speech": "không có giọng nói",
}


class PlatformNativeAudioMixin:
    def _native_audio_measurements(self, path: Path) -> dict:
        import imageio_ffmpeg

        try:
            result = subprocess.run([  # noqa: S603 - bundled FFmpeg, argv only, owned local media.
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                "-af", "silencedetect=noise=-45dB:d=0.25,volumedetect", "-f", "null", "-",
            ], capture_output=True, text=True, timeout=60)
            return audio_measurements(result.stderr, self._media_duration(path)) if not result.returncode else {}
        except (OSError, subprocess.TimeoutExpired):
            return {}

    def _native_audio_timing(self, path: Path) -> tuple[list[str], dict]:
        import imageio_ffmpeg

        try:
            result = subprocess.run([  # noqa: S603 - bundled FFmpeg, argv only, owned local media.
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                "-af", "silencedetect=noise=-30dB:d=0.35", "-f", "null", "-",
            ], capture_output=True, text=True, timeout=60)
            return native_audio_gap_issues(result.stderr, self._media_duration(path)) if not result.returncode else ([], {})
        except (OSError, subprocess.TimeoutExpired):
            return [], {}

    def _native_speech_report(self, draft: dict, scene_index: int) -> dict | None:
        """The newest failed speech check of a scene: its issues and what the model actually said."""
        project_id = self._project_id_for_draft(draft["id"])
        if not project_id:
            return None
        with self.engine.connect() as db:
            report = db.execute(select(video_quality_reports).where(
                video_quality_reports.c.project_id == project_id,
                video_quality_reports.c.stage == "NATIVE_SPEECH",
                video_quality_reports.c.scene_index == scene_index,
                video_quality_reports.c.status == "FAIL",
            ).order_by(video_quality_reports.c.created.desc())).mappings().first()
        return dict(report) if report else None

    def _native_speech_failure(self, draft: dict, scene_index: int) -> str:
        """Readable failure keeping the machine prefix, so the user can see what the model actually said."""
        report = self._native_speech_report(draft, scene_index)
        if not report:
            return "native_speech_quality_failed"
        labels = [SPEECH_ISSUE_LABELS[issue] for issue in report["issues"] if issue in SPEECH_ISSUE_LABELS]
        heard = (report["metrics"] or {}).get("transcript") or ""
        message = f"native_speech_quality_failed: Cảnh {scene_index} {', '.join(labels) or 'chưa đạt'}"
        if heard:
            # Show the ending for a cut-off line; mark any shortening so it is not mistaken for the cut.
            if len(heard) > 110:
                heard = "…" + heard[-110:] if "native_speech_incomplete" in report["issues"] else heard[:110] + "…"
            message += f'. Model đọc: "{heard}"'
        return (message + ". Sửa lời thoại hoặc tạo lại cảnh.")[:240]

    def _review_native_scene_audio(self, draft: dict, scene_index: int, generation_id: str, path: Path) -> bool:
        project_id = self._project_id_for_draft(draft["id"])
        if not project_id:
            return True
        clips = (draft["payload"].get("story_plan") or {}).get("clips") or []
        clip = next((c for c in clips if c.get("index") == scene_index), {})
        expected = clean_spoken_text(clip.get("voice_script", ""))
        if not expected:
            return True
        with self.engine.connect() as db:
            reports = db.execute(select(video_quality_reports).where(
                video_quality_reports.c.project_id == project_id,
                video_quality_reports.c.stage == "NATIVE_SPEECH",
            ).order_by(video_quality_reports.c.created.desc())).mappings().all()
            previous_id = db.execute(select(video_scenes.c.generation_id).where(
                video_scenes.c.draft_id == draft["id"], video_scenes.c.scene_index == scene_index - 1,
            )).scalar_one_or_none()
        existing = next((r for r in reports if r["scene_index"] == scene_index), None)
        previous = next((r for r in reports if r["scene_index"] == scene_index - 1
                         and r["metrics"].get("generation_id") == previous_id), None)
        same_clip = bool(existing and existing["metrics"].get("generation_id") == generation_id
                         and existing["metrics"].get("expected") == expected)
        if same_clip and existing["metrics"].get("review_profile") == NATIVE_REVIEW_PROFILE:
            return existing["status"] != "FAIL"
        # A newer checker re-scores the stored transcript instead of transcribing the same clip again.
        cached = existing["metrics"].get("transcript") if same_clip else None
        configured = cached or self.planner_configured or self.planner_credential_status().get("configured")
        transcript, similarity, issues = "", 0.0, ["native_speech_check_unavailable"]
        if configured:
            try:
                if cached:
                    transcript = cached
                else:
                    response = self.http.post("/internal/v1/planner/transcribe-clip", content=path.read_bytes(),
                                              headers={"Content-Type": "video/mp4"}, timeout=150)
                    response.raise_for_status()
                    transcript = response.json()["text"]
                if not isinstance(transcript, str):
                    raise ValueError("Invalid transcript")
                issues, similarity = native_speech_issues(expected, transcript)
                if previous:
                    issues += boundary_speech_issues(clean_spoken_text(previous["metrics"].get("expected", "")), expected,
                                                    previous["metrics"].get("transcript", ""), transcript)
            except (httpx.HTTPError, OSError, KeyError, TypeError, ValueError):
                transcript, similarity, issues = "", 0.0, ["native_speech_check_unavailable"]
        timing_issues, timing_metrics = self._native_audio_timing(path)
        measured = self._native_audio_measurements(path)
        fatal = [issue for issue in issues if issue != "native_speech_check_unavailable"]
        if measured and measured.get("active_audio_seconds", 1) < 0.25:
            fatal.append("native_audio_missing_speech")
        metrics = {"generation_id": generation_id, "review_profile": NATIVE_REVIEW_PROFILE,
                   "expected": expected, "transcript": transcript, "similarity": similarity,
                   **timing_metrics, **measured}
        warnings = list(timing_issues)
        if measured:
            trim_start, trim_end = native_trim_window(measured["duration_seconds"], measured)
            if scene_index > 1 and scene_transition(clip) == "continue":
                # Removing the opening pose breaks first-frame continuation, even if it is silent.
                if trim_start > 0:
                    warnings.append("continuous_scene_leading_silence_preserved")
                trim_start = 0.0
            metrics.update({"trim_start_seconds": trim_start, "trim_end_seconds": trim_end})
        active = measured.get("active_audio_seconds", 0)
        if transcript and active > 0:
            rate = len(speech_tokens(transcript)) / active
            metrics["speech_rate_syllables_per_second"] = round(rate, 3)
            if not 1.5 <= rate <= 5.5:
                warnings.append("native_speech_rate_outlier")
            previous_rate = (previous or {}).get("metrics", {}).get("speech_rate_syllables_per_second")
            if previous_rate and abs(rate / previous_rate - 1) > 0.4:
                warnings.append("native_speech_rate_change")
        previous_volume = (previous or {}).get("metrics", {}).get("active_volume_db")
        if previous_volume is not None and "active_volume_db" in measured:
            delta = measured["active_volume_db"] - previous_volume
            metrics["volume_change_db"] = round(delta, 3)
            if abs(delta) > 4:
                warnings.append("native_audio_volume_change")
        status = "FAIL" if fatal else "WARN" if issues or warnings else "PASS"
        with self.engine.begin() as db:
            if status != "FAIL":
                db.execute(update(video_quality_reports).where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.scene_index == scene_index,
                    video_quality_reports.c.stage == "NATIVE_SPEECH",
                    video_quality_reports.c.status.in_(("FAIL", "WARN")),
                ).values(status="RESOLVED"))
            db.execute(insert(video_quality_reports).values(
                id=secrets.token_hex(16), project_id=project_id, scene_index=scene_index,
                stage="NATIVE_SPEECH", status=status, metrics=metrics,
                issues=list(dict.fromkeys(issues + fatal + warnings)), created=time.time(),
            ))
            if scene_index > 1:
                # ASR and volume/rate statistics do not establish vocal identity.
                db.execute(update(video_quality_reports).where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.scene_index == scene_index,
                    video_quality_reports.c.stage == "NATIVE_VOICE_CONTINUITY",
                    video_quality_reports.c.status == "WARN",
                ).values(status="RESOLVED"))
                db.execute(insert(video_quality_reports).values(
                    id=secrets.token_hex(16), project_id=project_id, scene_index=scene_index,
                    stage="NATIVE_VOICE_CONTINUITY", status="WARN",
                    metrics={"generation_id": generation_id, "voice_identity_verified": False,
                             "accent_verified": False, "voice_reference_supported": False},
                    issues=["native_voice_identity_unverified"], created=time.time(),
                ))
        return status != "FAIL"
