import re

import httpx


class ExecutionError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, ambiguous: bool = False,
                 retry_after_seconds: float | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after_seconds = retry_after_seconds


class VideoExecutionClient:
    def __init__(self, client: httpx.Client):
        self.client = client

    def credential_slots(self, provider: str) -> list[dict] | None:
        """Pool keys with their concurrent-task limits; None when the provider has no key pool."""
        try:
            response = self.client.get(f"/internal/v1/video/{provider}/credentials", timeout=5)
            response.raise_for_status()
            keys = response.json()["keys"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
        return [
            {"id": key["id"], "slots": key["slots"]} for key in keys
            if isinstance(key, dict) and isinstance(key.get("id"), str) and re.fullmatch(r"[a-z0-9]{4,16}", key["id"])
            and isinstance(key.get("slots"), int) and key["slots"] > 0
        ] if isinstance(keys, list) else None

    def call(self, provider: str, action: str, payload=None, task_id=None, model=None, poll_url=None,
             credential=None):
        if not re.fullmatch(r"[\w-]{1,32}", provider):
            raise ExecutionError("invalid_provider")
        if task_id is not None and not re.fullmatch(r"[\w.-]{1,128}", task_id):
            raise ExecutionError("invalid_task_id")
        if credential is not None and not re.fullmatch(r"[a-z0-9]{4,16}", credential):
            raise ExecutionError("invalid_credential")
        path = f"/internal/v1/video/{provider}"
        method = "POST"
        if action == "capabilities":
            path += "/capabilities"
            method = "GET"
        elif action == "validate":
            path += "/validate"
        else:
            path += "/tasks"
            if task_id:
                path += "/" + task_id
            if action == "status":
                method = "GET"
            elif action == "cancel":
                path += "/cancel"
        submitting = action == "submit"
        try:
            options = {"timeout": 3} if action == "capabilities" else {}
            if action == "capabilities" and model is not None:
                options["params"] = {"model": model}
            if action == "status" and poll_url is not None:
                options["params"] = {"poll_url": poll_url}
            if credential is not None and action in {"submit", "status", "cancel"}:
                options["params"] = {**options.get("params", {}), "credential": credential}
            response = self.client.request(method, path, json=payload, **options)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ExecutionError("ai_connection_error", retryable=True) from None
        except httpx.TransportError:
            raise ExecutionError(
                "ai_transport_error", retryable=not submitting, ambiguous=submitting
            ) from None
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            raise ExecutionError(
                "invalid_ai_response", ambiguous=submitting, retryable=not submitting
            ) from None
        if response.is_error:
            # Structured errors from our internal service explicitly classify submit uncertainty.
            if all(key in data for key in ("code", "retryable", "ambiguous")):
                raise ExecutionError(
                    data["code"],
                    retryable=data["retryable"] is True,
                    ambiguous=data["ambiguous"] is True,
                    retry_after_seconds=data.get("retry_after_seconds"),
                )
            raise ExecutionError(
                "ai_http_error",
                ambiguous=submitting and response.status_code >= 500,
                retryable=not submitting and response.status_code >= 500,
            )
        if action == "validate" and not isinstance(data.get("execution_request"), dict):
            raise ExecutionError("invalid_validation_response")
        if action == "capabilities":
            return data
        if action != "validate" and (
            data.get("provider") != provider
            or not isinstance(data.get("provider_task_id"), str)
            or not re.fullmatch(r"[\w.-]{1,128}", data["provider_task_id"])
            or data.get("status")
            not in {"queued", "generating", "completed", "failed", "cancelled"}
            or (task_id and data["provider_task_id"] != task_id)
            or (data["status"] == "completed" and not data.get("outputs"))
        ):
            raise ExecutionError("invalid_ai_response", ambiguous=submitting)
        return data
