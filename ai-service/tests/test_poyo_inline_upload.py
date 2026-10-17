import base64

import httpx
import pytest

from app.providers.poyo import PoyoVeoProvider
from app.schemas.provider import ProviderError

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg").decode()


def provider(handler):
    client = httpx.AsyncClient(base_url="https://api.poyo.ai/", transport=httpx.MockTransport(handler))
    adapter = PoyoVeoProvider(client, "veo3.1-fast")
    adapter.set_api_key("poyo-key")
    return adapter


def payload(*urls):
    return {"model": "veo3.1-fast", "input": {"prompt": "A scene", "duration": 8, "image_urls": list(urls)}}


@pytest.mark.asyncio
async def test_inline_images_are_uploaded_to_poyo_storage_once_before_submit():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/common/upload/stream":
            assert b"fake-jpeg" in request.content and request.headers["authorization"] == "Bearer poyo-key"
            return httpx.Response(200, json={"code": 200, "data": {"file_url": "https://storage.poyo.ai/temp/a.jpg"}})
        body = request.content
        assert b"data:image" not in body and b"https://storage.poyo.ai/temp/a.jpg" in body
        return httpx.Response(200, json={"code": 200, "data": {"task_id": "task-1"}})

    adapter = provider(handler)
    first = await adapter.submit(payload(JPEG, "https://cdn.example/b.jpg"))
    assert b"https://cdn.example/b.jpg" in requests[1].content
    await adapter.submit(payload(JPEG))
    assert first.provider_task_id == "task-1"
    paths = [request.url.path for request in requests]
    assert paths == ["/api/common/upload/stream", "/api/generate/submit", "/api/generate/submit"]


@pytest.mark.asyncio
async def test_upload_failure_is_retryable_and_never_submits_paid_task():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(503)

    with pytest.raises(ProviderError) as error:
        await provider(handler).submit(payload(JPEG))
    assert error.value.retryable and not error.value.ambiguous
    assert paths == ["/api/common/upload/stream"]


@pytest.mark.asyncio
async def test_upload_without_https_url_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"code": 200, "data": {"file_url": "http://insecure/a.jpg"}})

    with pytest.raises(ProviderError, match="poyo_upload_missing_url"):
        await provider(handler).submit(payload(JPEG))
