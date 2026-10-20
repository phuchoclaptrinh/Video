"""Automatic retries for failed story renders, and the error text each viewer is allowed to see.

A story that failed in a way the system already knows how to redo (preparation, an unbilled or
refunded provider rejection, a scene that failed quality checks, a failed merge) is retried by the
worker without asking the user, a few times with growing pauses. Members see a plain notice; the
error codes and provider messages stay visible to admins. A scene whose line the video model could not
speak cleanly gets a newly worded line first, since sending the same words again fails the same way.
"""

import json
import logging

from app.services.platform_shared import (
    chat_messages,
    drafts,
    select,
    story_batches,
    story_plan_versions,
    time,
    update,
    video_projects,
)

logger = logging.getLogger(__name__)

AUTO_RETRY_LIMIT = 3
# Pause before attempt 1, 2 and 3; gives a provider or a just-added key time to recover.
AUTO_RETRY_DELAYS = (30, 120, 300)
RECHECK_SECONDS = 30
# A rewrite that could not be produced (planner down, invalid line) is tried again after this pause without
# using up one of the render attempts, up to this many times per story.
REWORD_RETRY_SECONDS = 60
REWORD_FAILURE_LIMIT = 3

REJECTED_MARKERS = (
    "bộ lọc an toàn", "content does not comply", "platform regulations",
    "prominent person", "protected likeness", "celebrity",
)
QUALITY_MARKERS = ("native_speech_quality_failed", "native_audio_missing", "visual_reference_drift")
# Speech-check results that point at the words themselves rather than at missing or silent audio.
DIALOGUE_ISSUES = {
    "native_speech_incomplete", "native_speech_repetition", "native_speech_mismatch",
    "native_speech_boundary_repetition",
}


def story_error_kind(error: str | None) -> str | None:
    text = (error or "").lower()
    if not text:
        return None
    if text.startswith("không đủ credit"):
        return "credits"
    if text.startswith("story_merge_failed"):
        return "merge"
    if any(marker in text for marker in REJECTED_MARKERS):
        return "rejected"
    if text.startswith(QUALITY_MARKERS):
        return "quality"
    return "system"


def member_error_message(kind: str | None, *, pending: bool, attempts: int, error: str | None) -> str | None:
    """A notice for members: what happened in plain words, never codes or provider messages."""
    if not kind:
        return None
    if kind == "credits":
        return error  # already a plain sentence the member can act on
    reason = {
        "merge": "Các cảnh đã tạo xong nhưng bước ghép video bị lỗi.",
        "rejected": "Nhà cung cấp video từ chối nội dung của một cảnh (ảnh hoặc mô tả).",
        "quality": "Một cảnh chưa đạt chất lượng giọng nói hoặc hình ảnh.",
        "system": "Hệ thống tạo video gặp lỗi.",
    }[kind]
    if pending:
        return f"{reason} Hệ thống đang tự thử lại (lần {attempts + 1}/{AUTO_RETRY_LIMIT})."
    if kind == "rejected":
        return f"{reason} Hãy lập kịch bản khác hoặc dùng ảnh sản phẩm không có người rồi thử lại."
    return f"{reason} Lỗi đã được ghi nhận cho quản trị viên; bạn có thể thử lại sau."


class PlatformAutoRetryMixin:
    def story_retry_kind(self, row: dict, retryable: bool) -> str | None:
        if row["status"] != "FAILED":
            return None
        if row.get("error_code") == "story_merge_failed":
            return "merge"
        return "story" if retryable else None

    def auto_retry_failed_stories(self) -> None:
        now = time.time()
        checked = self.__dict__.setdefault("_auto_retry_checked", {})
        with self.engine.connect() as db:
            rows = db.execute(
                select(story_batches).where(
                    story_batches.c.status == "FAILED", story_batches.c.auto_retries < AUTO_RETRY_LIMIT,
                )
            ).mappings().all()
        for row in map(dict, rows):
            # Not retryable yet (e.g. later scenes still running): look again only now and then.
            if row["retry_after"] is None and now - checked.get(row["id"], 0) < RECHECK_SECONDS:
                continue
            checked[row["id"]] = now
            try:
                kind = self.story_retry_kind(row, self.present_story_batch(row)["retryable"])
            except Exception:
                logger.exception("Auto retry could not inspect batch %s", row["id"])
                continue
            if not kind:
                continue
            if row["retry_after"] is None:
                delay = AUTO_RETRY_DELAYS[min(row["auto_retries"], len(AUTO_RETRY_DELAYS) - 1)]
                with self.engine.begin() as db:
                    db.execute(update(story_batches).where(story_batches.c.id == row["id"])
                               .values(retry_after=now + delay))
                continue
            if row["retry_after"] > now:
                continue
            with self.engine.begin() as db:
                claimed = db.execute(
                    update(story_batches)
                    .where(story_batches.c.id == row["id"], story_batches.c.status == "FAILED",
                           story_batches.c.retry_after == row["retry_after"])
                    .values(auto_retries=story_batches.c.auto_retries + 1, retry_after=None)
                ).rowcount
            if not claimed:
                continue
            logger.info("[VIDEO] auto retry %s/%s batch=%s kind=%s error=%s",
                        row["auto_retries"] + 1, AUTO_RETRY_LIMIT, row["id"], kind, row["error_code"])
            try:
                if kind == "merge":
                    self.retry_story_batch_merge(row["user_id"], row["id"])
                elif self._reword_failed_dialogue(row) is False:
                    # Never render the same words again; give the attempt back and reword a little later.
                    failures = self.__dict__.setdefault("_reword_failures", {})
                    failures[row["id"]] = failures.get(row["id"], 0) + 1
                    if failures[row["id"]] <= REWORD_FAILURE_LIMIT:
                        with self.engine.begin() as db:
                            db.execute(update(story_batches).where(story_batches.c.id == row["id"]).values(
                                auto_retries=story_batches.c.auto_retries - 1,
                                retry_after=time.time() + REWORD_RETRY_SECONDS,
                            ))
                    continue
                else:
                    # The user consented to the paid render when starting it; retries reuse that.
                    self.generate(row["user_id"], row["draft_id"], confirm_paid=True)
            except Exception:
                logger.exception("Auto retry failed for batch %s", row["id"])

    def _reword_failed_dialogue(self, row: dict) -> bool | None:
        """New wording for a scene that failed on its words; None when the failure was not about them."""
        if not (row.get("error_code") or "").startswith("native_speech_quality_failed") or not row.get("draft_id"):
            return None
        draft = self.owned(drafts, row["draft_id"], row["user_id"])
        scene_index = self._failed_story_scene_index(draft["id"], row["generation_ids"])
        report = self._native_speech_report(draft, scene_index)
        if not report or not DIALOGUE_ISSUES.intersection(report["issues"] or []):
            return None
        plan = draft["payload"].get("story_plan") or {}
        default_seconds = draft["payload"]["spec"]["output"]["duration_seconds"]
        scenes = [{
            "index": clip["index"], "duration_seconds": clip.get("duration_seconds") or default_seconds,
            "voice_script": clip["voice_script"], "beat": clip.get("beat", ""), "purpose": clip.get("purpose", ""),
        } for clip in plan.get("clips") or []]
        clip = next((item for item in plan.get("clips") or [] if item["index"] == scene_index), None)
        if clip is None:
            return None
        try:
            response = self.http.post("/internal/v1/planner/rewrite-scene-line", json={
                "scenes": scenes, "scene_index": scene_index, "issues": report["issues"],
                "transcript": (report["metrics"] or {}).get("transcript") or "",
            }, timeout=150)
            response.raise_for_status()
            line = response.json()["voice_script"]
            self.replace_scene_dialogue(row["user_id"], draft["id"], scene_index, clip["video_prompt"], line)
        except Exception as exc:
            logger.warning("[VIDEO] could not reword scene %s of batch %s: %s", scene_index, row["id"], exc)
            return False
        logger.info("[VIDEO] reworded scene %s of batch %s after %s", scene_index, row["id"], report["issues"])
        return True

    def replace_scene_dialogue(self, uid: str, draft_id: str, scene_index: int, video_prompt: str,
                               voice_script: str) -> None:
        """Store a new scene line everywhere the plan is kept, including the chat card that shows it."""
        project_id = self._project_id_for_draft(draft_id)
        with self.batch_lock, self.engine.begin() as db:
            draft = db.execute(select(drafts).where(drafts.c.id == draft_id, drafts.c.user_id == uid)).mappings().one()
            project = db.execute(select(video_projects).where(video_projects.c.id == project_id)).mappings().first()
            version = db.execute(select(story_plan_versions).where(
                story_plan_versions.c.project_id == project_id,
                story_plan_versions.c.version == project["current_plan_version"],
                story_plan_versions.c.draft_id == draft_id,
            )).mappings().first() if project else None
            story_plan = self._store_scene_revision(
                db, dict(draft), dict(version) if version else None, scene_index, video_prompt, voice_script,
            )
            if not project or not project["conversation_id"]:
                return
            messages = db.execute(select(chat_messages.c.id, chat_messages.c.content).where(
                chat_messages.c.conversation_id == project["conversation_id"],
                chat_messages.c.user_id == uid, chat_messages.c.role == "assistant",
            )).all()
            for identifier, content in messages:
                try:
                    event = json.loads(content)
                except (TypeError, ValueError):
                    continue
                if (isinstance(event, dict) and event.get("type") == "video_generation"
                        and isinstance(event.get("plan"), dict) and event["plan"].get("id") == draft_id):
                    event["plan"]["story_plan"] = story_plan
                    db.execute(update(chat_messages).where(chat_messages.c.id == identifier)
                               .values(content=json.dumps(event, ensure_ascii=False)))
