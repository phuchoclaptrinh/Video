import logging
import random
import threading
import time
from dataclasses import dataclass

from app.services.video_execution_client import ExecutionError, VideoExecutionClient
from app.services.video_execution_store import ExecutionStore

logger = logging.getLogger(__name__)
# Providers whose tasks run on one of several API keys, each limited in concurrent generations.
POOLED_PROVIDERS = {"seedvis"}
SLOT_WAIT_SECONDS = 15
SLOT_CACHE_SECONDS = 30


@dataclass(frozen=True)
class ExecutionSettings:
    max_retries: int = 3
    backoff_seconds: float = 5
    poll_seconds: float = 10
    timeout_seconds: float = 900
    lease_seconds: float = 600

    def __post_init__(self):
        if self.max_retries < 0 or min(self.backoff_seconds, self.poll_seconds) < 0:
            raise ValueError("Invalid retry/poll settings")
        if min(self.timeout_seconds, self.lease_seconds) <= 0:
            raise ValueError("Timeout and lease must be positive")


class GenerationOrchestrator:
    def __init__(
        self,
        store: ExecutionStore,
        client: VideoExecutionClient,
        settings: ExecutionSettings | None = None,
    ):
        self.store = store
        self.client = client
        self.settings = settings or ExecutionSettings()
        self._slot_lock = threading.Lock()
        self._slots_cache: tuple[float, list[dict] | None] = (0.0, None)

    def forget_credential_slots(self) -> None:
        self._slots_cache = (0.0, None)

    def credential_slots(self, provider: str) -> list[dict] | None:
        expires, slots = self._slots_cache
        if time.monotonic() < expires:
            return slots
        lookup = getattr(self.client, "credential_slots", None)
        slots = lookup(provider) if lookup else None
        self._slots_cache = (time.monotonic() + SLOT_CACHE_SECONDS, slots)
        return slots

    def slot_usage(self, provider: str, slots: list[dict]) -> dict[str, int]:
        active = self.store.active_by_credential(provider)
        legacy = active.pop(None, 0)
        if slots:
            # Tasks submitted before key pools ran on the default, first key.
            active[slots[0]["id"]] = active.get(slots[0]["id"], 0) + legacy
        return active

    def _pick_credential(self, job: dict) -> str | bool | None:
        """Pool key with a free slot for a new task; False when every key is at its limit."""
        existing = (job.get("result") or {}).get("credential")
        if existing:
            # A resubmission must reuse the account that owns the task idempotency key.
            return existing
        slots = self.credential_slots(job["provider"])
        if not slots:
            return None
        active = self.slot_usage(job["provider"], slots)
        free = [slot for slot in slots if active.get(slot["id"], 0) < slot["slots"]]
        if not free:
            return False
        return min(free, key=lambda slot: active.get(slot["id"], 0) / slot["slots"])["id"]

    @staticmethod
    def _credential(job: dict) -> dict:
        credential = (job.get("result") or {}).get("credential")
        return {"credential": credential} if credential else {}

    def enqueue(self, provider: str, payload: dict) -> dict:
        validated = self.client.call(provider, "validate", payload=payload)
        # The durable QUEUED row is also the outbox; recovery dispatches it after broker failures.
        return self.store.create(provider, validated["execution_request"])

    def tick(self, generation_id: str) -> dict:
        job = self.store.claim(generation_id, self.settings.lease_seconds)
        if job is None:
            return self.store.get(generation_id)
        try:
            self._advance(job)
        except ExecutionError as error:
            self._handle_error(job, error)
        except Exception:
            # Leave the lease and submit intent intact for recovery after unexpected failures.
            logger.error("video_execution_interrupted", extra={"generation_id": generation_id})
            raise
        result = self.store.get(generation_id)
        logger.info(
            "video_execution_transition",
            extra={
                "generation_id": generation_id,
                "provider": result["provider"],
                "provider_task_id": result["provider_task_id"],
                "status": result["status"],
                "attempts": result["attempts"],
            },
        )
        return result

    def reconcile(self, generation_id: str, task_id: str) -> dict:
        """Operator has verified task ownership in the provider console before calling."""
        job = self.store.get(generation_id)
        if job["status"] != "NEEDS_USER_ACTION":
            raise ValueError("reconciliation_requires_needs_user_action")
        self.client.call(job["provider"], "status", task_id=task_id, **self._credential(job))
        result = self.store.reconcile(generation_id, task_id)
        logger.info(
            "video_execution_reconciled",
            extra={
                "generation_id": generation_id,
                "provider_task_id": task_id,
            },
        )
        return result

    def _advance(self, job):
        if job["status"] == "SUBMITTING" and not job["provider_task_id"] and job["provider"] != "seedvis":
            self.store.release(job, status="NEEDS_USER_ACTION", error_code="submit_outcome_unknown")
            return
        # A pooled task still waiting for a free key has not started running at the provider.
        waiting_for_slot = job["provider"] in POOLED_PROVIDERS and job["status"] == "QUEUED" and not job["attempts"]
        if not waiting_for_slot and time.time() - job["run_started_at"] >= self.settings.timeout_seconds:
            self.store.release(
                job,
                status="NEEDS_USER_ACTION" if job["provider_task_id"] or job["status"] == "SUBMITTING" else "FAILED",
                error_code="execution_timeout",
            )
            return
        cancelled = self.store.get(job["generation_id"])["cancel_requested"]
        if cancelled and job["status"] == "SUBMITTING" and not job["provider_task_id"]:
            self.store.release(job, status="NEEDS_USER_ACTION", error_code="submit_outcome_unknown")
            return
        if cancelled and not job["provider_task_id"]:
            self.store.release(job, status="CANCELLED")
            return
        if not job["provider_task_id"]:
            with self._slot_lock:
                if job["provider"] in POOLED_PROVIDERS:
                    credential = self._pick_credential(job)
                    if credential is False:
                        # Every key is busy: stay queued; the run timeout starts once the task is submitted.
                        self.store.release(job, delay=SLOT_WAIT_SECONDS, run_started_at=time.time())
                        return
                    if credential and not self._credential(job):
                        self.store.save(job, result={**(job.get("result") or {}), "credential": credential},
                                        run_started_at=time.time())
                # Commit intent BEFORE any non-idempotent network call.
                if not self.store.begin_submit(job):
                    self.store.release(job, status="CANCELLED")
                    return
            credential = self._credential(job)
            result = self.client.call(job["provider"], "submit", payload=job["payload"], **credential)
            self.store.release(
                job,
                delay=self._poll_delay(result, job["provider"]),
                status={"completed": "POST_PROCESSING", "failed": "FAILED", "cancelled": "CANCELLED"}.get(result.get("status"), "SUBMITTED"),
                provider_task_id=result["provider_task_id"],
                result={**result, **credential},
                attempts=0,
                error_code=result.get("error_code"),
            )
            return
        action = "cancel" if cancelled else "status"
        poll_options = {}
        if job["provider"] == "seedvis" and (job.get("result") or {}).get("poll_url"):
            poll_options["poll_url"] = job["result"]["poll_url"]
        credential = self._credential(job)
        try:
            result = self.client.call(job["provider"], action, task_id=job["provider_task_id"],
                                      **(poll_options if action == "status" else {}), **credential)
        except ExecutionError as error:
            if error.code != "cancel_not_supported_running":
                raise
            result = self.client.call(job["provider"], "status", task_id=job["provider_task_id"],
                                      **poll_options, **credential)
        statuses = {
            "queued": "SUBMITTED",
            "generating": "GENERATING",
            "completed": "POST_PROCESSING",
            "failed": "FAILED",
            "cancelled": "CANCELLED",
        }
        # A completion racing cancellation remains a real, possibly billed output for QA.
        self.store.release(
            job,
            delay=self._poll_delay(result, job["provider"]),
            status=statuses[result["status"]],
            result={**result, **credential},
            attempts=0,
            error_code=result.get("error_code"),
        )

    def _poll_delay(self, result, provider: str | None = None):
        delay = result.get("poll_after_seconds")
        if isinstance(delay, (int, float)) and not isinstance(delay, bool) and 1 <= delay <= 120:
            return delay if provider == "seedvis" else max(self.settings.poll_seconds, delay)
        return self.settings.poll_seconds

    def _handle_error(self, job, error):
        attempts = job["attempts"] + 1
        budget = min(
            self.settings.max_retries,
            job["payload"]["spec"].get("execution_preferences", {}).get("max_attempts", 3) - 1,
        )
        if error.ambiguous:
            self.store.release(job, status="NEEDS_USER_ACTION", error_code=error.code)
        elif error.retryable and attempts <= budget:
            delay = self.settings.backoff_seconds * 2 ** (attempts - 1)
            delay += random.uniform(0, delay * 0.2)  # noqa: S311 - scheduling jitter
            retry_after = error.retry_after_seconds
            if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool) and 0 <= retry_after < float("inf"):
                delay = max(delay, retry_after)
            self.store.release(
                job,
                delay=delay,
                attempts=attempts,
                error_code=error.code,
                status="SUBMITTED" if job["provider_task_id"] else "QUEUED",
            )
        else:
            self.store.release(
                job,
                status="NEEDS_USER_ACTION" if job["provider_task_id"] else "FAILED",
                attempts=attempts,
                error_code=error.code,
            )
