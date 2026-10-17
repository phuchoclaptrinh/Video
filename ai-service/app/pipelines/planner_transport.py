"""Bounded recovery for planner requests, never for paid video submissions."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import httpx

PLANNER_DEADLINE_SECONDS = 120
NETWORK_RETRY_DELAY_SECONDS = 0.5
logger = logging.getLogger(__name__)
REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt-6")


def generation_params(model: str, *, temperature: float, max_tokens: int) -> dict:
    """Reasoning models reject temperature/max_tokens and spend output tokens on hidden reasoning."""
    if model.startswith(REASONING_MODEL_PREFIXES):
        return {
            "max_completion_tokens": max_tokens + 8000,
            "reasoning_effort": os.getenv("OPENAI_REASONING_EFFORT", "low"),
        }
    return {"temperature": temperature, "max_tokens": max_tokens}


@dataclass
class PlannerRequests:
    client: httpx.AsyncClient
    retries_remaining: int = 1

    async def post(self, payload: dict, *, phase: str, read_timeout: float = 90) -> httpx.Response:
        request_timeout = httpx.Timeout(
            read_timeout, connect=min(read_timeout, 10), write=min(read_timeout, 10), pool=min(read_timeout, 5),
        )
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                response = await self.client.post("chat/completions", json=payload, timeout=request_timeout)
                response.raise_for_status()
                logger.info(
                    "Planner request complete; phase=%s attempt=%s elapsed_ms=%s status=%s",
                    phase, attempt, round((time.monotonic() - started) * 1000), response.status_code,
                )
                return response
            except (httpx.NetworkError, httpx.RemoteProtocolError, httpx.TimeoutException) as exc:
                retry = self.retries_remaining > 0
                logger.warning(
                    "Planner transport failure; phase=%s attempt=%s error_type=%s elapsed_ms=%s retry=%s",
                    phase, attempt, type(exc).__name__, round((time.monotonic() - started) * 1000), retry,
                )
                if not retry:
                    raise
                # All planning phases share one network retry budget.
                self.retries_remaining -= 1
                await asyncio.sleep(NETWORK_RETRY_DELAY_SECONDS)
