import asyncio
import json

import httpx
import pytest

from app.pipelines.planner_transport import PlannerRequests


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr("app.pipelines.planner_transport.NETWORK_RETRY_DELAY_SECONDS", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_transient_failure_retries_same_payload_once(error_type, caplog):
    calls = []
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "private prompt"}]}

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise error_type("private transport detail", request=request)
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(
        base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer private-key"},
    ) as client:
        requests = PlannerRequests(client)
        assert (await requests.post(payload, phase="story")).status_code == 200
        assert requests.retries_remaining == 0
    assert len(calls) == 2
    assert all(json.loads(call.content) == payload for call in calls)
    assert "error_type=" + error_type.__name__ in caplog.text and "retry=True" in caplog.text
    assert all(secret not in caplog.text for secret in ("private-key", "private prompt", "private transport detail"))


@pytest.mark.asyncio
async def test_read_error_mid_response_body_retries_and_closes_stream():
    calls = []

    class InterruptedBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"choices":'
            raise httpx.ReadError("connection reset during body read")

        async def aclose(self):
            self.closed = True

    body = InterruptedBody()

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=body)
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)) as client:
        response = await PlannerRequests(client).post({}, phase="story")
    assert response.json() == {"choices": []}
    assert len(calls) == 2 and body.closed


@pytest.mark.asyncio
async def test_story_and_dialogue_repair_share_one_network_retry():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) != 2:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)) as client:
        requests = PlannerRequests(client)
        await requests.post({"story": True}, phase="story")
        with pytest.raises(httpx.ReadError):
            await requests.post({"repair": True}, phase="dialogue_repair", read_timeout=30)
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
async def test_http_errors_are_not_network_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "upstream"})

    async with httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)) as client:
        requests = PlannerRequests(client)
        with pytest.raises(httpx.HTTPStatusError):
            await requests.post({}, phase="story")
        assert requests.retries_remaining == 1
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("read_timeout", [90, 30])
async def test_per_request_timeout_preserves_short_connection_limits(read_timeout):
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)) as client:
        await PlannerRequests(client).post({}, phase="story", read_timeout=read_timeout)
    assert timeouts == [{"connect": 10, "read": read_timeout, "write": 10, "pool": 5}]


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    calls = []

    async def handler(request):
        calls.append(request)
        raise asyncio.CancelledError()

    async with httpx.AsyncClient(base_url="https://api.openai.com/v1/", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await PlannerRequests(client).post({}, phase="story")
    assert len(calls) == 1
