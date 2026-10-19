"""Automatic retries for failed story renders, and the error text each viewer is allowed to see.

A story that failed in a way the system already knows how to redo (preparation, an unbilled or
refunded provider rejection, a scene that failed quality checks, a failed merge) is retried by the
worker without asking the user, a few times with growing pauses. Members see a plain notice; the
error codes and provider messages stay visible to admins.
"""

import logging

from app.services.platform_shared import select, story_batches, time, update

logger = logging.getLogger(__name__)

AUTO_RETRY_LIMIT = 3
# Pause before attempt 1, 2 and 3; gives a provider or a just-added key time to recover.
AUTO_RETRY_DELAYS = (30, 120, 300)
RECHECK_SECONDS = 30

REJECTED_MARKERS = (
    "bộ lọc an toàn", "content does not comply", "platform regulations",
    "prominent person", "protected likeness", "celebrity",
)
QUALITY_MARKERS = ("native_speech_quality_failed", "native_audio_missing", "visual_reference_drift")


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
                else:
                    # The user consented to the paid render when starting it; retries reuse that.
                    self.generate(row["user_id"], row["draft_id"], confirm_paid=True)
            except Exception:
                logger.exception("Auto retry failed for batch %s", row["id"])
