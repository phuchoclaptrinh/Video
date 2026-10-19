"""Admin console: usage statistics, every user's videos and queue, and account management."""

import os
import secrets
import sqlite3
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import func

from app.services.platform_shared import (
    HTTPException,
    conversations,
    delete,
    drafts,
    executions,
    password_hash,
    select,
    sessions,
    story_batches,
    update,
    users,
)

DAY = 86400
ACTIVE_BATCH = ("GENERATING", "QUEUED", "MERGING")
DONE_SCENE = ("COMPLETED", "POST_PROCESSING")


def admin_emails() -> set[str]:
    return {email.strip().lower() for email in os.getenv("ADMIN_EMAILS", "").split(",") if email.strip()}


def error_kind(code: str | None) -> str:
    # Readable failures keep a machine prefix before the first colon.
    return (code or "").split(":", 1)[0].strip()


class PlatformAdminMixin:
    def is_admin(self, user: dict) -> bool:
        configured = admin_emails()
        if not configured:
            # Local single-user installs keep full access until ADMIN_EMAILS is set.
            return True
        return user["email"].lower() in configured or bool(user.get("is_admin"))

    def database_snapshot(self) -> Path:
        """A consistent copy of the SQLite database, safe to take while the app keeps writing."""
        url = self.engine.url
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise HTTPException(409, "Chỉ tải được bản sao lưu khi dùng database SQLite")
        target = self.storage / "backups" / f"snapshot-{secrets.token_hex(8)}.sqlite3"
        target.parent.mkdir(parents=True, exist_ok=True)
        source, copy = sqlite3.connect(url.database), sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        return target

    def require_admin(self, user: dict) -> dict:
        if not self.is_admin(user):
            raise HTTPException(403, "Chỉ quản trị viên mới dùng được chức năng này")
        return user

    def admin_overview(self) -> dict:
        now = time.time()
        with self.engine.connect() as db:
            user_count = db.execute(select(func.count()).select_from(users)).scalar_one()
            batches = db.execute(select(story_batches.c.status, story_batches.c.error_code, story_batches.c.created)
                                 .where(story_batches.c.created >= now - 30 * DAY)).all()
            scenes = db.execute(select(executions.c.provider, executions.c.status, executions.c.created_at)
                                .where(executions.c.created_at >= now - 30 * DAY)).all()
            queued = db.execute(select(func.count()).select_from(executions)
                                .where(executions.c.status == "QUEUED")).scalar_one()
            running = db.execute(select(func.count()).select_from(executions)
                                 .where(executions.c.status.in_(("SUBMITTING", "SUBMITTED", "GENERATING")))).scalar_one()
        week = [row for row in batches if row.created >= now - 7 * DAY]
        completed = sum(row.status == "COMPLETED" for row in week)
        failed = sum(row.status == "FAILED" for row in week)
        daily = []
        for offset in range(13, -1, -1):
            start = time.mktime((*time.localtime(now - offset * DAY)[:3], 0, 0, 0, 0, 0, -1))
            rows = [row for row in batches if start <= row.created < start + DAY]
            daily.append({
                "date": time.strftime("%d/%m", time.localtime(start)),
                "completed": sum(row.status == "COMPLETED" for row in rows),
                "failed": sum(row.status == "FAILED" for row in rows),
                "other": sum(row.status not in {"COMPLETED", "FAILED"} for row in rows),
            })
        errors = Counter(error_kind(row.error_code) for row in batches if row.status == "FAILED" and row.error_code)
        providers = Counter(row.provider for row in scenes if row.status in DONE_SCENE and row.created_at >= now - 7 * DAY)
        slots = self.runtime.credential_slots("seedvis") or []
        usage = self.runtime.slot_usage("seedvis", slots) if slots else {}
        return {
            "users": user_count,
            "videos_today": sum(row.created >= now - DAY for row in batches),
            "videos_week": len(week),
            "completed_week": completed,
            "failed_week": failed,
            "failure_rate_week": round(failed / (completed + failed), 3) if completed + failed else 0,
            "active_videos": sum(row.status in ACTIVE_BATCH for row in batches),
            "scenes_week": sum(providers.values()),
            "scenes_by_provider": dict(providers),
            "running_scenes": running,
            "queued_scenes": queued,
            "seedvis_slots": sum(slot["slots"] for slot in slots),
            "seedvis_active": sum(usage.get(slot["id"], 0) for slot in slots),
            "daily": daily,
            "top_errors": [{"code": code, "count": count} for code, count in errors.most_common(6)],
        }

    def _emails(self) -> dict[str, str]:
        with self.engine.connect() as db:
            return {row.id: row.email for row in db.execute(select(users.c.id, users.c.email))}

    def admin_videos(self, status: str = "active", limit: int = 100) -> list[dict]:
        query = select(story_batches).order_by(story_batches.c.created.desc()).limit(limit)
        if status == "active":
            query = query.where(story_batches.c.status.in_(ACTIVE_BATCH))
        elif status in {"FAILED", "COMPLETED", "CANCELLED"}:
            query = query.where(story_batches.c.status == status)
        with self.engine.connect() as db:
            rows = db.execute(query).mappings().all()
            generation_ids = [gid for row in rows for gid in row["generation_ids"] or []]
            jobs = {job.generation_id: job for job in db.execute(
                select(executions.c.generation_id, executions.c.status, executions.c.result)
                .where(executions.c.generation_id.in_(generation_ids))
            )} if generation_ids else {}
            plans = {row.id: row.payload for row in db.execute(
                select(drafts.c.id, drafts.c.payload).where(drafts.c.id.in_([row["draft_id"] for row in rows if row["draft_id"]]))
            )}
        emails = self._emails()
        videos = []
        for row in rows:
            ids = row["generation_ids"] or []
            clips = ((plans.get(row["draft_id"]) or {}).get("story_plan") or {}).get("clips") or []
            current = jobs.get(ids[-1]) if ids else None
            failed_scene = next((jobs[gid] for gid in reversed(ids) if gid in jobs and jobs[gid].status == "FAILED"), None)
            videos.append({
                "id": row["id"], "title": row["title"][:160], "user": emails.get(row["user_id"], "?"),
                "status": row["status"], "error": row["error_code"], "created": row["created"],
                "scenes_done": sum(jobs[gid].status in DONE_SCENE for gid in ids if gid in jobs),
                "scenes_total": len(clips) or len(ids),
                "scene_status": current.status if current and row["status"] in ACTIVE_BATCH else None,
                "credential": ((current.result or {}).get("credential") if current else None),
                "has_video": bool(row["filename"]),
                # The provider's own words, e.g. a safety-filter rejection; admins only.
                "error_detail": ((failed_scene.result or {}).get("error_detail") if failed_scene else None),
                "auto_retries": row["auto_retries"],
            })
        return videos

    def admin_queue(self) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(select(executions).where(executions.c.status.in_(
                ("QUEUED", "SUBMITTING", "SUBMITTED", "GENERATING")
            )).order_by(executions.c.created_at)).mappings().all()
        emails = self._emails()
        now = time.time()
        return [{
            "id": row["generation_id"], "provider": row["provider"], "status": row["status"],
            "user": emails.get(row["payload"]["spec"]["request"].get("user_id"), "?"),
            "credential": (row["result"] or {}).get("credential"), "attempts": row["attempts"],
            "error": row["error_code"], "waiting_seconds": round(now - row["created_at"]),
            "cancel_requested": bool(row["cancel_requested"]),
        } for row in rows]

    def admin_cancel_video(self, batch_id: str) -> dict:
        with self.engine.begin() as db:
            row = db.execute(select(story_batches).where(story_batches.c.id == batch_id)).mappings().first()
            if not row:
                raise HTTPException(404, "Không tìm thấy video")
            if row["status"] not in ACTIVE_BATCH:
                raise HTTPException(409, "Video này không còn chạy")
            db.execute(update(story_batches).where(story_batches.c.id == batch_id)
                       .values(status="CANCELLED", error_code="cancelled_by_admin"))
        for gid in row["generation_ids"] or []:
            self.store.request_cancel(gid)
        return {"id": batch_id, "status": "CANCELLED"}

    def admin_users(self) -> list[dict]:
        configured = admin_emails()
        with self.engine.connect() as db:
            rows = db.execute(select(users.c.id, users.c.email, users.c.is_admin, users.c.disabled, users.c.created,
                                     users.c.credits)
                              .order_by(users.c.email)).all()
            projects = dict(db.execute(select(conversations.c.user_id, func.count())
                                       .group_by(conversations.c.user_id)).all())
            videos = dict(db.execute(select(story_batches.c.user_id, func.count())
                                     .where(story_batches.c.status == "COMPLETED")
                                     .group_by(story_batches.c.user_id)).all())
            seen = dict(db.execute(select(sessions.c.user_id, func.max(sessions.c.expires))
                                   .group_by(sessions.c.user_id)).all())
        return [{
            "id": row.id, "email": row.email, "created": row.created,
            "is_admin": self.is_admin({"email": row.email, "is_admin": row.is_admin}),
            "admin_from_env": row.email.lower() in configured, "disabled": bool(row.disabled),
            "credits": row.credits,
            "projects": projects.get(row.id, 0), "videos": videos.get(row.id, 0),
            "last_seen": seen[row.id] - DAY if seen.get(row.id) else None,
        } for row in rows]

    def admin_create_user(self, email: str, password: str) -> dict:
        with self.engine.begin() as db:
            uid = self.create_account(db, email.strip().lower(), password)
        return next(user for user in self.admin_users() if user["id"] == uid)

    def admin_set_password(self, user_id: str, password: str) -> dict:
        with self.engine.begin() as db:
            if not db.execute(update(users).where(users.c.id == user_id).values(password=password_hash(password))).rowcount:
                raise HTTPException(404, "Không tìm thấy người dùng")
            # Old sessions end, so whoever held the previous password is signed out.
            db.execute(delete(sessions).where(sessions.c.user_id == user_id))
        return next(user for user in self.admin_users() if user["id"] == user_id)

    def admin_update_user(self, admin: dict, user_id: str, *, is_admin: bool | None, disabled: bool | None) -> dict:
        with self.engine.connect() as db:
            row = db.execute(select(users.c.id, users.c.email).where(users.c.id == user_id)).first()
        if not row:
            raise HTTPException(404, "Không tìm thấy người dùng")
        if user_id == admin["id"] and (disabled or is_admin is False):
            raise HTTPException(409, "Không thể tự khóa hoặc tự bỏ quyền admin của chính mình")
        if is_admin is False and row.email.lower() in admin_emails():
            raise HTTPException(409, "Admin khai báo trong ADMIN_EMAILS chỉ đổi được trong file .env")
        values = {key: int(value) for key, value in (("is_admin", is_admin), ("disabled", disabled)) if value is not None}
        with self.engine.begin() as db:
            if values:
                db.execute(update(users).where(users.c.id == user_id).values(**values))
            if disabled:
                db.execute(delete(sessions).where(sessions.c.user_id == user_id))
        return next(user for user in self.admin_users() if user["id"] == user_id)
