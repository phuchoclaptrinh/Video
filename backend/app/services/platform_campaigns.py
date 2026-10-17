"""Durable marketing strategy/script jobs, separate from the paid video queue."""

import copy
import hashlib
import json
import logging
import secrets
import time
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.services.platform_shared import (
    assets,
    chat_messages,
    conversations,
    drafts,
    marketing_campaigns,
    story_plan_versions,
)

logger = logging.getLogger(__name__)
ACTIVE = ("QUEUED", "DRAFTING", "WORKING")


class PlatformCampaignsMixin:
    def create_campaign(self, uid: str, body) -> dict:
        if not body.confirm_planning_costs:
            raise HTTPException(409, "Hãy xác nhận phí LLM trước khi lập chiến dịch nhiều kịch bản")
        if body.project_id:
            raise HTTPException(
                422, "Chiến dịch tạo các bản nháp mới, không thay thế một video project"
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                body.model_dump(exclude={"request_id", "confirm_planning_costs"}), sort_keys=True
            ).encode()
        ).hexdigest()
        identifier = body.request_id or secrets.token_hex(16)
        with self.engine.connect() as db:
            existing = (
                db.execute(
                    select(marketing_campaigns).where(marketing_campaigns.c.id == identifier)
                )
                .mappings()
                .first()
            )
        if existing:
            return self._deduplicated_campaign(uid, identifier, fingerprint)
        target = self._plan_target(uid, body)
        scoped = self._with_render_target(target)
        capabilities = scoped._continuity_strategy().capabilities
        if target["provider"] != "fake" and (
            not capabilities.native_audio or not capabilities.generation_durations
        ):
            raise HTTPException(
                422,
                "Chiến dịch nhiều cảnh có thoại cần model hỗ trợ native audio và thời lượng cố định",
            )
        if target["provider"] != "fake" and not (
            capabilities.first_frame or capabilities.reference_images
        ):
            raise HTTPException(422, "Model không hỗ trợ ảnh tham chiếu cho kịch bản nhiều cảnh")
        if (
            capabilities.generation_durations
            and body.duration not in capabilities.generation_durations
        ):
            raise HTTPException(422, "Thời lượng cảnh không được model hỗ trợ")
        if (
            capabilities.generation_durations
            and body.target_duration_seconds
            and body.target_duration_seconds > 5 * max(capabilities.generation_durations)
        ):
            raise HTTPException(422, "Thời lượng video vượt giới hạn 5 cảnh của model")
        if target["provider"] == "poyo" and body.ratio not in {"16:9", "9:16"}:
            raise HTTPException(422, "Model này chỉ hỗ trợ tỷ lệ 16:9 hoặc 9:16")
        if (
            target["provider"] == "seedvis"
            and capabilities.model == "Veo-3.1"
            and body.ratio not in {"16:9", "9:16"}
        ):
            raise HTTPException(422, "Model này chỉ hỗ trợ tỷ lệ 16:9 hoặc 9:16")
        if not scoped.planner_credential_status()["configured"]:
            raise HTTPException(409, "Hãy cấu hình OpenAI planner trước khi lập chiến dịch")
        pricing = self.credit_pricing()
        self.require_credits(uid, pricing["campaign"] + body.script_count * pricing["plan"])
        owned_assets = [self.owned(assets, aid, uid) for aid in body.asset_ids]
        # Validate unreadable assets before enqueueing an LLM job, without retaining base64.
        for asset in owned_assets:
            self._planner_image_data(asset)
        context = self._planner_context(uid, body.conversation_id)
        inputs = (
            body.model_dump(exclude={"script_count", "confirm_planning_costs", "request_id"})
            | target
        )
        inputs["_request_fingerprint"] = fingerprint
        inputs.update(require_planner=True, use_planner=True, planning_goal="sales")
        now = time.time()
        conversation_id = body.conversation_id or secrets.token_hex(16)
        inputs["conversation_id"] = conversation_id
        try:
            with self.engine.begin() as db:
                if not body.conversation_id:
                    db.execute(
                        insert(conversations).values(
                            id=conversation_id,
                            user_id=uid,
                            title="Chiến dịch · " + body.prompt[:45],
                            created=now,
                            updated=now,
                        )
                    )
                db.execute(
                    insert(marketing_campaigns).values(
                        id=identifier,
                        user_id=uid,
                        conversation_id=conversation_id,
                        brief=body.prompt,
                        script_count=body.script_count,
                        inputs=inputs,
                        context_snapshot=context,
                        items=[],
                        strategy=None,
                        status="QUEUED",
                        cancel_requested=0,
                        created=now,
                        updated=now,
                    )
                )
        except IntegrityError:
            return self._deduplicated_campaign(uid, identifier, fingerprint)
        return self.campaign(uid, identifier)

    def _deduplicated_campaign(self, uid, identifier, fingerprint):
        existing = self.owned(marketing_campaigns, identifier, uid)
        if existing["inputs"].get("_request_fingerprint") != fingerprint:
            raise HTTPException(409, "Mã yêu cầu đã được dùng cho nội dung chiến dịch khác")
        return self._present_campaign(existing)

    def campaign(self, uid: str, identifier: str) -> dict:
        return self._present_campaign(self.owned(marketing_campaigns, identifier, uid))

    def campaign_list(self, uid: str, conversation_id: str | None = None) -> list[dict]:
        if conversation_id:
            self.owned(conversations, conversation_id, uid)
        query = select(marketing_campaigns).where(marketing_campaigns.c.user_id == uid)
        if conversation_id:
            query = query.where(marketing_campaigns.c.conversation_id == conversation_id)
        with self.engine.connect() as db:
            rows = (
                db.execute(query.order_by(marketing_campaigns.c.created.desc()).limit(50))
                .mappings()
                .all()
            )
        return [self._present_campaign(dict(row)) for row in rows]

    def _present_campaign(self, row: dict) -> dict:
        return {
            key: row[key]
            for key in (
                "id",
                "conversation_id",
                "brief",
                "script_count",
                "strategy",
                "items",
                "status",
                "error",
                "created",
                "updated",
            )
        } | {
            "input_asset_ids": row["inputs"]["asset_ids"],
            "render_target": {
                key: row["inputs"][key]
                for key in ("provider", "video_model", "image_provider", "image_model")
            },
            "completed": sum(bool(item.get("plan")) for item in row["items"]),
            "cancel_requested": bool(row["cancel_requested"]),
        }

    def cancel_campaign(self, uid: str, identifier: str) -> dict:
        self.owned(marketing_campaigns, identifier, uid)
        with self.engine.begin() as db:
            db.execute(
                update(marketing_campaigns)
                .where(
                    marketing_campaigns.c.id == identifier,
                    marketing_campaigns.c.user_id == uid,
                    marketing_campaigns.c.status.in_(ACTIVE),
                )
                .values(cancel_requested=1, updated=time.time())
            )
            row = dict(
                db.execute(
                    select(marketing_campaigns).where(marketing_campaigns.c.id == identifier)
                )
                .mappings()
                .one()
            )
            if row["status"] in ("QUEUED", "DRAFTING"):
                items = copy.deepcopy(row["items"])
                for item in items:
                    if item["status"] == "PENDING":
                        item["status"] = "CANCELLED"
                db.execute(
                    update(marketing_campaigns)
                    .where(marketing_campaigns.c.id == identifier)
                    .values(status="CANCELLED", items=items)
                )
        return self.campaign(uid, identifier)

    def retry_campaign(self, uid: str, identifier: str, confirmed: bool) -> dict:
        row = self.owned(marketing_campaigns, identifier, uid)
        if not confirmed:
            raise HTTPException(409, "Thử lại kịch bản có thể phát sinh phí LLM; hãy xác nhận")
        if row["status"] not in ("FAILED", "PARTIAL", "CANCELLED"):
            raise HTTPException(409, "Chiến dịch không có phần thất bại cần thử lại")
        items = copy.deepcopy(row["items"])
        for item in items:
            if not item.get("plan"):
                item.update(status="PENDING", error=None)
        if row["strategy"] and not any(item["status"] == "PENDING" for item in items):
            raise HTTPException(409, "Tất cả kịch bản đã hoàn thành")
        with self.engine.begin() as db:
            changed = db.execute(
                update(marketing_campaigns)
                .where(
                    marketing_campaigns.c.id == identifier,
                    marketing_campaigns.c.status == row["status"],
                )
                .values(
                    status="DRAFTING" if row["strategy"] else "QUEUED",
                    items=items,
                    error=None,
                    cancel_requested=0,
                    updated=time.time(),
                )
            )
            if not changed.rowcount:
                raise HTTPException(409, "Chiến dịch đang được xử lý")
        return self.campaign(uid, identifier)

    def campaign_work(self):
        while not self.stop.wait(1):
            try:
                self.process_campaigns()
            except Exception:
                logger.exception("Campaign worker failed; durable state retained")

    def process_campaigns(self):
        if not self.campaign_lock.acquire(blocking=False):
            return
        try:
            self._process_next_campaign()
        finally:
            self.campaign_lock.release()

    def _process_next_campaign(self):
        with self.engine.begin() as db:
            interrupted = (
                db.execute(
                    select(marketing_campaigns).where(
                        marketing_campaigns.c.status == "WORKING",
                        marketing_campaigns.c.updated < time.time() - 300,
                    )
                )
                .mappings()
                .all()
            )
            for stale in interrupted:
                items = copy.deepcopy(stale["items"])
                for item in items:
                    if item["status"] == "RUNNING":
                        item.update(
                            status="FAILED", error="Tác vụ bị gián đoạn; cần xác nhận thử lại"
                        )
                db.execute(
                    update(marketing_campaigns)
                    .where(
                        marketing_campaigns.c.id == stale["id"],
                        marketing_campaigns.c.status == "WORKING",
                        marketing_campaigns.c.updated == stale["updated"],
                    )
                    .values(
                        status="CANCELLED"
                        if stale["cancel_requested"]
                        else "PARTIAL"
                        if any(item.get("plan") for item in items)
                        else "FAILED",
                        items=items,
                        error="Tác vụ bị gián đoạn. Không tự gọi lại LLM; hãy kiểm tra và thử lại phần còn thiếu.",
                        updated=time.time(),
                    )
                )
            row = (
                db.execute(
                    select(marketing_campaigns)
                    .where(marketing_campaigns.c.status.in_(("QUEUED", "DRAFTING")))
                    .order_by(marketing_campaigns.c.updated)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return
            row = copy.deepcopy(dict(row))
            changed = db.execute(
                update(marketing_campaigns)
                .where(
                    marketing_campaigns.c.id == row["id"],
                    marketing_campaigns.c.status == row["status"],
                )
                .values(status="WORKING", updated=time.time())
            )
            if not changed.rowcount:
                return
        try:
            if row["cancel_requested"]:
                self._finish_campaign(row["id"], status="CANCELLED")
            elif row["status"] == "QUEUED":
                self._plan_campaign_strategy(row)
            else:
                self._plan_campaign_item(row)
        except Exception as exc:
            logger.warning(
                "Campaign planning failed; campaign=%s error_type=%s", row["id"], type(exc).__name__
            )
            self._finish_campaign(row["id"], status="FAILED", error=self._campaign_error(exc))

    def _finish_campaign(self, identifier: str, **values):
        with self.engine.begin() as db:
            row = (
                db.execute(
                    select(marketing_campaigns).where(marketing_campaigns.c.id == identifier)
                )
                .mappings()
                .one()
            )
            if row["status"] != "WORKING":
                return
            if row["cancel_requested"]:
                values["status"] = "CANCELLED"
                for item in values.get("items", []):
                    if item["status"] == "PENDING":
                        item["status"] = "CANCELLED"
            db.execute(
                update(marketing_campaigns)
                .where(
                    marketing_campaigns.c.id == identifier,
                    marketing_campaigns.c.status == "WORKING",
                )
                .values(**values, updated=time.time())
            )

    def _plan_campaign_strategy(self, row):
        images = [
            self._planner_image_data(self.owned(assets, aid, row["user_id"]))
            for aid in row["inputs"]["asset_ids"]
        ]
        response = self.http.post(
            "/internal/v1/planner/campaign",
            json={
                "prompt": row["brief"],
                "script_count": row["script_count"],
                "image_data_urls": images,
                "project_context": row["context_snapshot"],
            },
        )
        response.raise_for_status()
        self.charge_credits(row["user_id"], self.credit_pricing()["campaign"], "campaign",
                            "campaign:" + row["id"], row["brief"][:80])
        result = response.json()
        variants = result["variants"]
        if (
            not isinstance(result["strategy"], dict)
            or not isinstance(variants, list)
            or len(variants) != row["script_count"]
        ):
            raise ValueError("campaign_schema_mismatch")
        required = (
            "title",
            "angle",
            "hook",
            "cta",
            "channel",
            "funnel_stage",
            "publish_day",
            "visual_direction",
            "caption",
        )
        if any(
            not isinstance(item, dict) or any(key not in item for key in required)
            for item in variants
        ):
            raise ValueError("campaign_schema_mismatch")
        items = [
            item
            | {"index": index, "status": "PENDING", "error": None, "plan": None, "message_id": None}
            for index, item in enumerate(variants, 1)
        ]
        self._finish_campaign(
            row["id"], strategy=result["strategy"], items=items, status="DRAFTING", error=None
        )

    def _existing_campaign_plan(self, row, index):
        with self.engine.connect() as db:
            draft = (
                db.execute(
                    select(drafts)
                    .where(
                        drafts.c.user_id == row["user_id"],
                        drafts.c.payload["marketing_campaign"]["campaign_id"].as_string()
                        == row["id"],
                        drafts.c.payload["marketing_campaign"]["variant_index"].as_integer()
                        == index,
                    )
                    .order_by(drafts.c.created.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not draft:
                return None
            version = (
                db.execute(
                    select(story_plan_versions).where(story_plan_versions.c.draft_id == draft["id"])
                )
                .mappings()
                .one()
            )
        payload = draft["payload"]
        return {
            "id": draft["id"],
            "project_id": version["project_id"],
            "plan_version": version["version"],
            "spec": payload["spec"],
            "story_plan": payload["story_plan"],
            "render_target": payload["render_target"],
            "mode": "demo" if payload["render_target"]["provider"] == "fake" else "live",
            "input_asset_ids": payload["input_asset_ids"],
            "planning_goal": "sales",
        }

    def _plan_campaign_item(self, row):
        items = copy.deepcopy(row["items"])
        item = next((item for item in items if item["status"] == "PENDING"), None)
        if item is None:
            self._finish_campaign(
                row["id"],
                status="COMPLETED" if all(item.get("plan") for item in items) else "PARTIAL",
            )
            return
        item["status"] = "RUNNING"
        with self.engine.begin() as db:
            db.execute(
                update(marketing_campaigns)
                .where(
                    marketing_campaigns.c.id == row["id"], marketing_campaigns.c.status == "WORKING"
                )
                .values(items=items)
            )
        try:
            context = {
                "campaign_id": row["id"],
                "variant_index": item["index"],
                "strategy": row["strategy"],
                "creative_brief": {
                    key: item[key]
                    for key in (
                        "title",
                        "angle",
                        "hook",
                        "cta",
                        "visual_direction",
                        "funnel_stage",
                        "channel",
                    )
                },
            }
            plan = self._existing_campaign_plan(row, item["index"])
            if plan is None:
                body = SimpleNamespace(
                    **(row["inputs"] | {"prompt": row["brief"], "marketing_context": context})
                )
                plan = self.plan(row["user_id"], body)
            with self.engine.connect() as db:
                messages = db.execute(
                    select(chat_messages.c.id, chat_messages.c.content).where(
                        chat_messages.c.conversation_id == row["conversation_id"],
                        chat_messages.c.user_id == row["user_id"],
                        chat_messages.c.role == "assistant",
                    )
                ).all()
            message_id = None
            for identifier, content in messages:
                try:
                    event = json.loads(content)
                    event_plan = event.get("plan") if isinstance(event, dict) else None
                    if (
                        isinstance(event, dict)
                        and event.get("type") == "video_generation"
                        and isinstance(event_plan, dict)
                        and event_plan.get("id") == plan["id"]
                    ):
                        message_id = identifier
                        break
                except (ValueError, TypeError):
                    continue
            if not message_id:
                saved = self.save_video_event(
                    row["user_id"], row["conversation_id"], item["title"], plan
                )
                message_id = next(
                    message["id"] for message in saved["messages"] if message["role"] == "assistant"
                )
            item.update(status="DRAFT", plan=plan, message_id=message_id, error=None)
        except Exception as exc:
            logger.exception(
                "Campaign script failed; campaign=%s variant=%s", row["id"], item["index"]
            )
            item.update(status="FAILED", error=self._campaign_error(exc))
        pending = any(item["status"] == "PENDING" for item in items)
        status = (
            "DRAFTING"
            if pending
            else "COMPLETED"
            if all(item.get("plan") for item in items)
            else "PARTIAL"
        )
        self._finish_campaign(row["id"], items=items, status=status, error=None)

    @staticmethod
    def _campaign_error(exc):
        if isinstance(exc, HTTPException):
            return str(exc.detail)[:500]
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                reason = exc.response.json().get("detail")
            except (ValueError, AttributeError):
                reason = None
            if not isinstance(reason, str):
                reason = None
            messages = {
                "planner_invalid_key": "OpenAI từ chối API key. Hãy kiểm tra key planner.",
                "planner_quota_or_rate_limit": "OpenAI hết quota hoặc đang giới hạn yêu cầu. Hãy kiểm tra billing và usage.",
                "planner_model_unavailable": "Model planner chưa khả dụng với tài khoản này.",
                "planner_timeout": "AI phản hồi quá lâu. Kế hoạch đã lưu; có thể thử lại phần thiếu.",
                "planner_connection_failed": "Kết nối OpenAI bị gián đoạn. Hãy kiểm tra mạng và thử lại.",
                "campaign_schema_mismatch": "AI trả kế hoạch thiếu trường hoặc lặp góc bán hàng. Chưa tự điền hoặc tạo video.",
            }
            if reason in messages:
                return messages[reason]
            return "Không lập được kế hoạch marketing. Hãy kiểm tra key, quota và thử lại."
        return "Không hoàn tất được bước lập kịch bản. Phần đã hoàn thành vẫn được giữ."
