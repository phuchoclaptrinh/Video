"""Person 3 execution journal, independent of platform generation/user tables."""

import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

TERMINAL = {"POST_PROCESSING", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "NEEDS_USER_ACTION"}
# Statuses during which a task holds one of the provider account's concurrent-generation slots.
SLOT_STATUSES = ("SUBMITTING", "SUBMITTED", "GENERATING")
metadata = MetaData()
executions = Table(
    "video_execution_jobs",
    metadata,
    Column("generation_id", String(64), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("provider_task_id", String(128)),
    Column("result", JSON),
    Column("error_code", String(128)),
    Column("attempts", Integer, nullable=False, default=0),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("run_started_at", Float, nullable=False),
    Column("next_run_at", Float, nullable=False, index=True),
    Column("lease_until", Float, nullable=False, default=0),
    Column("lease_token", String(32)),
)


class ExecutionStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def initialize(self):
        metadata.create_all(self.engine)

    def create(self, provider: str, payload: dict) -> dict:
        context = payload["spec"]["request"]
        key = json.dumps([context["tenant_id"], context["request_id"]])
        generation_id = hashlib.sha256(key.encode()).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps([provider, payload], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        now = time.time()
        try:
            with self.engine.begin() as db:
                db.execute(
                    insert(executions).values(
                        generation_id=generation_id,
                        fingerprint=fingerprint,
                        provider=provider,
                        payload=payload,
                        status="QUEUED",
                        created_at=now,
                        run_started_at=now,
                        next_run_at=now,
                    )
                )
        except IntegrityError:
            existing = self.get(generation_id)
            if existing["fingerprint"] != fingerprint:
                raise ValueError("idempotency_conflict") from None
        return self.get(generation_id)

    def get(self, generation_id: str) -> dict:
        with self.engine.connect() as db:
            row = (
                db.execute(select(executions).where(executions.c.generation_id == generation_id))
                .mappings()
                .first()
            )
        if row is None:
            raise KeyError(generation_id)
        return dict(row)

    def claim(self, generation_id: str, lease_seconds: float) -> dict | None:
        now = time.time()
        token = uuid4().hex
        with self.engine.begin() as db:
            changed = db.execute(
                update(executions)
                .where(
                    executions.c.generation_id == generation_id,
                    executions.c.status.not_in(TERMINAL),
                    executions.c.lease_until <= now,
                    executions.c.next_run_at <= now,
                )
                .values(lease_until=now + lease_seconds, lease_token=token)
            )
            if not changed.rowcount:
                return None
        return self.get(generation_id)

    def save(self, job: dict, **values) -> None:
        with self.engine.begin() as db:
            result = db.execute(
                update(executions)
                .where(
                    executions.c.generation_id == job["generation_id"],
                    executions.c.lease_token == job["lease_token"],
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise RuntimeError("execution_lease_lost")
        job.update(values)

    def begin_submit(self, job: dict) -> bool:
        with self.engine.begin() as db:
            result = db.execute(
                update(executions)
                .where(
                    executions.c.generation_id == job["generation_id"],
                    executions.c.lease_token == job["lease_token"],
                    executions.c.cancel_requested.is_(False),
                )
                .values(status="SUBMITTING")
            )
        if result.rowcount:
            job["status"] = "SUBMITTING"
            return True
        if self.get(job["generation_id"])["lease_token"] != job["lease_token"]:
            raise RuntimeError("execution_lease_lost")
        return False

    def release(self, job: dict, delay: float = 0, **values):
        self.save(job, **values, lease_until=0, lease_token=None, next_run_at=time.time() + delay)

    def request_cancel(self, generation_id: str) -> dict:
        with self.engine.begin() as db:
            db.execute(
                update(executions)
                .where(
                    executions.c.generation_id == generation_id,
                    executions.c.status.not_in(TERMINAL),
                )
                .values(cancel_requested=True, next_run_at=time.time())
            )
        return self.get(generation_id)

    def active_by_credential(self, provider: str) -> dict[str | None, int]:
        """Tasks occupying a provider slot, counted per pool key (None: submitted before key pools)."""
        with self.engine.connect() as db:
            rows = db.execute(select(executions.c.result).where(
                executions.c.provider == provider,
                executions.c.status.in_(SLOT_STATUSES),
            )).scalars().all()
        counts: dict[str | None, int] = {}
        for result in rows:
            credential = (result or {}).get("credential")
            counts[credential] = counts.get(credential, 0) + 1
        return counts

    def due(self, limit: int = 100) -> list[str]:
        now = time.time()
        with self.engine.connect() as db:
            return list(
                db.execute(
                    select(executions.c.generation_id)
                    .where(
                        and_(
                            executions.c.status.not_in(TERMINAL),
                            executions.c.next_run_at <= now,
                            executions.c.lease_until <= now,
                        )
                    )
                    .order_by(executions.c.next_run_at)
                    .limit(limit)
                ).scalars()
            )

    def reconcile(self, generation_id: str, task_id: str) -> dict:
        job = self.get(generation_id)
        if job["status"] != "NEEDS_USER_ACTION":
            raise ValueError("reconciliation_requires_needs_user_action")
        if job["provider_task_id"] and job["provider_task_id"] != task_id:
            raise ValueError("cannot_replace_existing_provider_task")
        with self.engine.begin() as db:
            changed = db.execute(
                update(executions)
                .where(
                    executions.c.generation_id == generation_id,
                    executions.c.status == "NEEDS_USER_ACTION",
                    executions.c.provider_task_id == job["provider_task_id"],
                )
                .values(
                    provider_task_id=task_id,
                    status="SUBMITTED",
                    attempts=0,
                    error_code=None,
                    lease_until=0,
                    lease_token=None,
                    run_started_at=time.time(),
                    next_run_at=time.time(),
                )
            )
            if not changed.rowcount:
                raise ValueError("reconciliation_conflict")
        return self.get(generation_id)
