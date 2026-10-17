"""Per-account credits: pricing, balance checks, charges, automatic render refunds and admin top-ups."""

import logging
import math

from sqlalchemy import func

from app.services.platform_shared import (
    HTTPException,
    IntegrityError,
    credit_ledger,
    drafts,
    executions,
    insert,
    platform_settings,
    secrets,
    select,
    time,
    update,
    users,
)

logger = logging.getLogger(__name__)

PRICING_KEY = "credit_pricing"
DEFAULT_PRICING = {
    # Credits given once to every new account (and to accounts that existed before credits).
    "signup_bonus": 100,
    # Video credits for each block of `seconds` in a generated clip, so a fixed-length model is
    # priced per clip (40 credits / 8s) and a variable one per block (20 credits / 5s).
    # "provider:model" overrides the default.
    "video_clip": {"default": {"credits": 40, "seconds": 8}},
    "plan": 2,  # one AI script, including each script of a campaign
    "campaign": 2,  # the campaign strategy call
    "image": 1,  # one reference or scene keyframe image
    # "provider:model" keys members cannot see or choose; admins still can.
    "hidden_models": [],
}
KIND_LABELS = {
    "signup": "Tặng tài khoản mới",
    "admin": "Admin điều chỉnh",
    "render": "Render cảnh video",
    "refund": "Hoàn credit cảnh lỗi",
    "plan": "Lập kịch bản",
    "campaign": "Lập chiến lược chiến dịch",
    "image": "Tạo ảnh tham chiếu",
}
NO_VIDEO = ("FAILED", "CANCELLED")
RENDER_REF = "render:"
# Clip length each admin price was entered for, used when upgrading per-second prices.
PRICE_SECONDS = {
    "poyo:veo3.1-fast": 8, "seedvis:Veo-3.1": 8, "seedvis:Omni-Flash": 10,
    "seedvis:seedance_2.0_fast": 5, "seedvis:seedance_2.5": 5,
}


class InsufficientCredits(HTTPException):
    def __init__(self, needed: int, balance: int):
        super().__init__(402, f"Không đủ credit: cần {needed} credit, tài khoản còn {balance} credit.")


def _clip_price(value) -> dict:
    return {"credits": max(0, int(value["credits"])), "seconds": min(60, max(1, int(value["seconds"])))}


def _clean_pricing(value: dict) -> dict:
    pricing = {**DEFAULT_PRICING, **{key: value[key] for key in DEFAULT_PRICING if key in value}}
    if "video_clip" not in value and isinstance(value.get("video_second"), dict):
        # Prices saved before clip pricing were entered as credits for one whole clip.
        pricing["video_clip"] = {
            key: {"credits": credits, "seconds": PRICE_SECONDS.get(key, 8)}
            for key, credits in value["video_second"].items()
        }
    clips = {**DEFAULT_PRICING["video_clip"], **(pricing.get("video_clip") or {})}
    pricing["video_clip"] = {str(key): _clip_price(price) for key, price in clips.items()}
    for key in ("signup_bonus", "plan", "campaign", "image"):
        pricing[key] = max(0, int(pricing[key]))
    pricing["hidden_models"] = sorted({str(key) for key in pricing.get("hidden_models") or []})
    return pricing


class PlatformCreditsMixin:
    # Pricing -------------------------------------------------------------------------------
    def credit_pricing(self) -> dict:
        with self.engine.connect() as db:
            value = db.execute(
                select(platform_settings.c.value).where(platform_settings.c.key == PRICING_KEY)
            ).scalar_one_or_none()
        return _clean_pricing(value or {})

    def set_credit_pricing(self, value: dict) -> dict:
        pricing = _clean_pricing(value)
        with self.engine.begin() as db:
            updated = db.execute(
                update(platform_settings).where(platform_settings.c.key == PRICING_KEY)
                .values(value=pricing, updated=time.time())
            ).rowcount
            if not updated:
                db.execute(insert(platform_settings).values(key=PRICING_KEY, value=pricing, updated=time.time()))
        return pricing

    def clip_cost(self, provider: str, model: str | None, seconds: int, pricing: dict | None = None) -> int:
        """Credits for one generated clip: the model price for every started block of its seconds."""
        if provider == "fake":
            return 0  # demo renders cost nothing
        prices = (pricing or self.credit_pricing())["video_clip"]
        price = prices.get(f"{provider}:{model}", prices["default"])
        return price["credits"] * max(1, math.ceil(seconds / price["seconds"]))

    def story_render_estimate(self, draft: dict) -> int:
        """Credits for a full story render: every scene clip plus the reference images it needs."""
        target = draft["payload"].get("render_target") or {}
        provider = target.get("provider") or self.provider
        if provider == "fake":
            return 0
        pricing = self.credit_pricing()
        clips = (draft["payload"].get("story_plan") or {}).get("clips") or []
        default_seconds = draft["payload"]["spec"]["output"]["duration_seconds"]
        video = sum(
            self.clip_cost(provider, target.get("video_model"), clip.get("duration_seconds") or default_seconds, pricing)
            for clip in clips
        )
        # One opening anchor plus at most one keyframe per later scene.
        return video + len(clips) * pricing["image"]

    # Model visibility ----------------------------------------------------------------------
    def visible_catalog(self, catalog: dict, uid: str) -> dict:
        """Members only see the video models the admin has opened to them."""
        if self.credit_exempt(uid):
            return catalog
        hidden = set(self.credit_pricing()["hidden_models"])
        providers = []
        for provider in catalog["video_providers"]:
            models = [model for model in provider["models"] if f"{provider['id']}:{model['id']}" not in hidden]
            if models:
                providers.append({**provider, "models": models})
        return {**catalog, "video_providers": providers}

    def require_visible_model(self, uid: str, provider: str, model: str) -> None:
        if f"{provider}:{model}" in self.credit_pricing()["hidden_models"] and not self.credit_exempt(uid):
            raise HTTPException(409, "Model này hiện chưa mở cho tài khoản của bạn. Hãy chọn model khác.")

    # Balance -------------------------------------------------------------------------------
    def credit_exempt(self, uid: str) -> bool:
        with self.engine.connect() as db:
            row = db.execute(select(users.c.email, users.c.is_admin).where(users.c.id == uid)).mappings().first()
        # Same rule as admin access: a local install without ADMIN_EMAILS is never charged.
        return self.is_admin(dict(row) if row else {"email": "", "is_admin": 0})

    def credit_balance(self, uid: str) -> int:
        with self.engine.connect() as db:
            return db.execute(select(users.c.credits).where(users.c.id == uid)).scalar_one_or_none() or 0

    def require_credits(self, uid: str, amount: int) -> None:
        if amount <= 0 or self.credit_exempt(uid):
            return
        balance = self.credit_balance(uid)
        if balance < amount:
            raise InsufficientCredits(amount, balance)

    def _apply_credits(self, db, uid: str, delta: int, kind: str, ref: str | None, note: str | None = None,
                       actor_id: str | None = None, settled: bool = True) -> int:
        db.execute(update(users).where(users.c.id == uid).values(credits=users.c.credits + delta))
        balance = db.execute(select(users.c.credits).where(users.c.id == uid)).scalar_one()
        db.execute(insert(credit_ledger).values(
            id=secrets.token_hex(16), user_id=uid, delta=delta, balance=balance, kind=kind, ref=ref,
            note=note, actor_id=actor_id, settled=int(settled), created=time.time(),
        ))
        return balance

    def charge_credits(self, uid: str, amount: int, kind: str, ref: str, note: str | None = None,
                       *, settled: bool = True) -> None:
        """Deduct once per ref; admins are never charged. A concurrent race may dip slightly below zero."""
        if amount <= 0 or self.credit_exempt(uid):
            return
        try:
            with self.engine.begin() as db:
                self._apply_credits(db, uid, -amount, kind, ref, note, settled=settled)
        except IntegrityError:
            pass  # this ref was already charged

    def _draft_owner(self, draft: dict) -> str | None:
        if draft.get("user_id"):
            return draft["user_id"]
        with self.engine.connect() as db:
            return db.execute(select(drafts.c.user_id).where(drafts.c.id == draft.get("id"))).scalar_one_or_none()

    def require_image_credits(self, draft: dict) -> None:
        if uid := self._draft_owner(draft):
            self.require_credits(uid, self.credit_pricing()["image"])

    def charge_image(self, draft: dict, request_id: str) -> None:
        # Charged after the image exists; every generated image is a separate provider call.
        if uid := self._draft_owner(draft):
            self.charge_credits(uid, self.credit_pricing()["image"], "image",
                                f"image:{request_id}:{secrets.token_hex(4)}", (draft.get("title") or "")[:80])

    def grant_signup_credits(self, db, uid: str) -> None:
        bonus = self.credit_pricing()["signup_bonus"]
        if bonus:
            self._apply_credits(db, uid, bonus, "signup", f"signup:{uid}")

    def credit_summary(self, uid: str, limit: int = 50) -> dict:
        with self.engine.connect() as db:
            rows = db.execute(
                select(credit_ledger).where(credit_ledger.c.user_id == uid)
                .order_by(credit_ledger.c.created.desc()).limit(limit)
            ).mappings().all()
        pricing = self.credit_pricing()
        exempt = self.credit_exempt(uid)
        clips = {key: price for key, price in pricing["video_clip"].items()
                 if exempt or key not in pricing["hidden_models"]}
        return {
            "balance": self.credit_balance(uid),
            "exempt": exempt,
            "pricing": {"video_clip": clips} | {key: pricing[key] for key in ("plan", "campaign", "image")},
            "history": [
                {
                    "id": row["id"], "delta": row["delta"], "balance": row["balance"], "kind": row["kind"],
                    "label": KIND_LABELS.get(row["kind"], row["kind"]), "note": row["note"],
                    "pending": not row["settled"], "created": row["created"],
                }
                for row in rows
            ],
        }

    # Admin ---------------------------------------------------------------------------------
    def admin_adjust_credits(self, admin: dict, uid: str, delta: int, note: str) -> dict:
        if not delta:
            raise HTTPException(422, "Số credit điều chỉnh phải khác 0")
        with self.engine.begin() as db:
            if not db.execute(select(users.c.id).where(users.c.id == uid)).first():
                raise HTTPException(404, "Không tìm thấy người dùng")
            balance = db.execute(select(users.c.credits).where(users.c.id == uid)).scalar_one()
            if balance + delta < 0:
                raise HTTPException(409, f"Tài khoản chỉ còn {balance} credit, không trừ được {-delta}")
            self._apply_credits(db, uid, delta, "admin", None, note.strip() or None, admin["id"])
        return self.credit_summary(uid)

    # Render refunds ------------------------------------------------------------------------
    def settle_render_credits(self, limit: int = 50) -> None:
        """Refund scenes that ended without a video; keep charges for scenes that produced one."""
        with self.engine.connect() as db:
            rows = db.execute(
                select(credit_ledger.c.id, credit_ledger.c.user_id, credit_ledger.c.delta,
                       credit_ledger.c.ref, executions.c.status)
                .join(executions, executions.c.generation_id == func.substr(credit_ledger.c.ref, len(RENDER_REF) + 1))
                .where(credit_ledger.c.kind == "render", credit_ledger.c.settled == 0,
                       executions.c.status.in_(("COMPLETED", *NO_VIDEO)))
                .limit(limit)
            ).mappings().all()
        for row in rows:
            try:
                with self.engine.begin() as db:
                    claimed = db.execute(
                        update(credit_ledger)
                        .where(credit_ledger.c.id == row["id"], credit_ledger.c.settled == 0)
                        .values(settled=1)
                    ).rowcount
                    if claimed and row["status"] in NO_VIDEO:
                        self._apply_credits(db, row["user_id"], -row["delta"], "refund",
                                            "refund:" + row["ref"], "Cảnh không tạo ra video")
            except IntegrityError:
                logger.warning("[CREDITS] refund already recorded ref=%s", row["ref"])
