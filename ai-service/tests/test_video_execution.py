import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.pipelines.story_planner import BookStoryPlan, create_book_story_plan
from app.pipelines.video_api import create_app
from app.providers.fake import FakeVideoProvider
from app.providers.poyo import PoyoVeoProvider
from app.providers.poyo_veo_compiler import PoyoVeoCompiler
from app.providers.registry import ProviderRegistry
from app.providers.seedance import SeedanceProvider
from app.providers.seedance_compiler import SeedanceCompiler
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError, VideoProviderCapabilities


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", [True, False])
async def test_official_audio_flag_survives_validate_and_actual_http_submit(audio):
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "data": {"task_id": "official-test", "status": "not_started"}})
    async with httpx.AsyncClient(base_url="https://api.poyo.ai/", transport=httpx.MockTransport(handler)) as transport:
        adapter = PoyoVeoProvider(transport, "veo3.1-fast-official")
        adapter.set_api_key("test-key-not-real")
        body = request_body()
        body["spec"]["output"] = {"duration_seconds": 6, "generate_audio": audio}
        with TestClient(create_app(ProviderRegistry([adapter]), "test-secret")) as client:
            client.headers["X-Internal-Key"] = "test-secret"
            canonical = client.post("/internal/v1/video/poyo/validate", json=body).json()["execution_request"]
            assert canonical["spec"]["output"]["generate_audio"] is audio
            assert client.post("/internal/v1/video/poyo/tasks", json=canonical).status_code == 202
        assert sent[0]["input"]["sound"] is audio and sent[0]["input"]["duration"] == 6


def test_regular_fast_explicit_audio_off_is_rejected_not_silently_ignored():
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8, "generate_audio": False}
    with pytest.raises(ProviderError, match="native_audio_disable_unsupported_model"):
        PoyoVeoCompiler("veo3.1-fast").compile(ExecutionRequest.model_validate(body))


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_official_uses_verified_duration_options(duration):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": duration}
    assert PoyoVeoCompiler("veo3.1-fast-official").compile(ExecutionRequest.model_validate(body))["input"]["duration"] == duration


@pytest.mark.parametrize("duration", [5, 7, 10])
def test_official_rejects_other_durations(duration):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": duration}
    with pytest.raises(ProviderError, match="unsupported_capability"):
        PoyoVeoCompiler("veo3.1-fast-official").compile(ExecutionRequest.model_validate(body))


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_official_three_image_reference_mode_is_eight_seconds_only(duration):
    body = request_body()
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["output"] = {"duration_seconds": duration}
    body["spec"]["references"] = [{"asset_id": str(index), "role": role} for index, role in enumerate(
        ("CHARACTER_REFERENCE", "STYLE_REFERENCE", "FIRST_FRAME"),
    )]
    body["assets"] = [{"asset_id": str(index), "mime_type": "image/jpeg", "source_url": f"https://example.com/{index}.jpg",
                       "moderation_status": "APPROVED"} for index in range(3)]
    compiler = PoyoVeoCompiler("veo3.1-fast-official")
    if duration == 8:
        assert compiler.compile(ExecutionRequest.model_validate(body))["input"]["generation_type"] == "reference"
    else:
        with pytest.raises(ProviderError, match="unsupported_capability"):
            compiler.compile(ExecutionRequest.model_validate(body))


def test_planner_measured_audio_windows_cannot_overlap():
    clips = [{"index": index, "beat": "Continuation", "video_prompt": "The same presenter speaks in a stable locked shot.",
              "voice_script": f"This is the next clause number {index}.", "duration_seconds": 6,
              "audio_start_ms": start, "audio_end_ms": end} for index, (start, end) in enumerate(((0, 5740), (5000, 10000)), 1)]
    with pytest.raises(ValidationError, match="overlap"):
        BookStoryPlan.model_validate({"core_value": "A continuous meaningful story", "clips": clips})


@pytest.mark.asyncio
async def test_planner_uses_adapter_durations_and_preserves_six_eight_six(monkeypatch):
    clips = [{"index": index, "beat": "Continuation", "video_prompt": "The same presenter speaks in a stable locked shot.",
              "voice_script": f"Đây là phần nội dung thứ {('một', 'hai', 'ba')[index - 1]} trong bài nói cùng các bạn hôm nay." + (
                  " Mình cùng tìm hiểu thêm nhé." if duration == 8 else ""), "duration_seconds": duration}
             for index, duration in enumerate((6, 8, 6), 1)]
    output = {"core_value": "A continuous meaningful video story.", "clips": clips,
              "visual_continuity": "The same presenter in the same room and wardrobe throughout.",
              "full_voice_script": " ".join(clip["voice_script"] for clip in clips)}
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    class Vault:
        def get(self, _name):
            return "test-not-real-key"
    caps = VideoProviderCapabilities(provider="poyo", model="veo3.1-fast-official", generation_durations=[4, 6, 8])
    plan = await create_book_story_plan("Một bài nói liên tục", Vault(), "gpt-4o-mini", video_capabilities=caps)
    assert [clip["duration_seconds"] for clip in plan["clips"]] == [6, 8, 6]
    assert plan["duration_target_seconds"] == 20
    expected = " ".join(clip["voice_script"] for clip in clips)
    assert plan["full_voice_script"] == expected
    assert len(captured) == 1
    assert '"generation_durations": [4, 6, 8]' in captured[0]["messages"][0]["content"]
    with pytest.raises(ValidationError, match="Scene duration"):
        BookStoryPlan.model_validate(output, context={"generation_durations": [8]})
    output["full_voice_script"] = "Duplicated or mismatched dialogue."
    with pytest.raises(ValidationError, match="partition"):
        BookStoryPlan.model_validate(output)


def request_body():
    return {
        "spec": {
            "request": {"request_id": "r1", "user_id": "u1", "tenant_id": "t1"},
            "intent": {"operation": "TEXT_GENERATION", "goal": "A city at sunrise"},
        },
        "preflight_decision": "ALLOW",
        "policy_decision_id": "p1",
    }


def test_compiler_and_contract():
    body = request_body()
    compiler = SeedanceCompiler("test-model")
    payload = compiler.compile(ExecutionRequest.model_validate(body))
    assert payload["model"] == "test-model"
    assert payload["duration"] == 5
    assert "sunrise" in payload["content"][0]["text"]
    body["spec"]["seedance_prompt"] = "not canonical"
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(body)


def test_seedance_native_audio_is_explicit_and_legacy_requests_stay_silent():
    body = request_body()
    compiler = SeedanceCompiler("seedance-1-5-pro-251215")
    assert compiler.compile(ExecutionRequest.model_validate(body))["generate_audio"] is False
    body["spec"]["output"] = {"duration_seconds": 8, "generate_audio": True}
    assert compiler.compile(ExecutionRequest.model_validate(body))["generate_audio"] is True
    with pytest.raises(ProviderError, match="native_audio_unsupported_model"):
        SeedanceCompiler("seedance-1-0-pro").compile(ExecutionRequest.model_validate(body))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b["spec"]["intent"].update(goal="   "),
        lambda b: b.update(preflight_decision="BLOCK"),
        lambda b: b["spec"].update(output={"duration_seconds": "5"}),
        lambda b: b["spec"]["intent"].update(operation="REFERENCE_GENERATION"),
    ],
)
def test_invalid_contract(mutation):
    body = request_body()
    mutation(body)
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(body)


def test_image_compiler_and_unsupported_role():
    body = request_body()
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [{"asset_id": "a1", "role": "FIRST_FRAME"}]
    body["assets"] = [
        {
            "asset_id": "a1",
            "mime_type": "image/png",
            "source_url": "https://example.com/image.png",
            "moderation_status": "APPROVED",
        }
    ]
    compiler = SeedanceCompiler("test")
    payload = compiler.compile(ExecutionRequest.model_validate(body))
    assert payload["content"][1]["role"] == "first_frame"
    body["spec"]["references"][0]["role"] = "CHARACTER_REFERENCE"
    with pytest.raises(ProviderError, match="unsupported_capability"):
        compiler.compile(ExecutionRequest.model_validate(body))


def test_image_compiler_accepts_local_jpeg_data_uri():
    body = request_body()
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [{"asset_id": "a1", "role": "FIRST_FRAME"}]
    body["assets"] = [
        {
            "asset_id": "a1",
            "mime_type": "image/jpeg",
            "source_url": "data:image/jpeg;base64,/9j/AA==",
            "moderation_status": "APPROVED",
        }
    ]
    payload = SeedanceCompiler("test").compile(ExecutionRequest.model_validate(body))
    assert payload["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_asset_source_rejects_insecure_url():
    body = request_body()
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [{"asset_id": "a1", "role": "FIRST_FRAME"}]
    body["assets"] = [
        {
            "asset_id": "a1",
            "mime_type": "image/jpeg",
            "source_url": "http://localhost/private.jpg",
            "moderation_status": "APPROVED",
        }
    ]
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(body)


def test_poyo_veo_compiler_uses_documented_contract():
    body = request_body()
    body["spec"]["output"] = {
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "resolution_preference": "720p",
    }
    payload = PoyoVeoCompiler("veo3.1-fast").compile(ExecutionRequest.model_validate(body))
    assert payload == {
        "model": "veo3.1-fast",
        "input": {
            "prompt": "A city at sunrise\nDo not add text overlays.",
            "duration": 8,
                "aspect_ratio": "9:16",
                "resolution": "720p",
        },
    }


def test_story_planner_accepts_legacy_per_clip_image_prompts_as_shared_anchor():
    line = "A complete Vietnamese narration line with enough words to comfortably fill this short scene."
    old_response = {
        "book_title": "Example",
        "core_value": "A concise idea from the book.",
        "visual_continuity": "Same host, clothes, studio, camera, light and set throughout every shot.",
        "clips": [
            {
                "index": 1,
                "beat": "opening",
                "image_prompt": "One shared portrait prompt from an older planner response.",
                "video_prompt": "A detailed visual prompt for animating the first scene in the story.",
                "voice_script": line,
            }
        ],
    }
    plan = BookStoryPlan.model_validate(old_response)
    assert plan.reference_image_prompt == old_response["clips"][0]["image_prompt"]


def test_poyo_lite_rejects_first_frame_reference():
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8}
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [{"asset_id": "end-frame-1", "role": "FIRST_FRAME"}]
    body["assets"] = [
        {
            "asset_id": "end-frame-1",
            "mime_type": "image/jpeg",
            "source_url": "https://video.example.test/frame.jpg",
            "moderation_status": "APPROVED",
        }
    ]
    with pytest.raises(ProviderError, match="model_does_not_support_image_input"):
        PoyoVeoCompiler("veo3.1-lite").compile(ExecutionRequest.model_validate(body))


def test_planner_keeps_dialogue_fragments_and_serializes_global_voice_instruction():
    response = {
        "core_value": "One connected speech across two clips.",
        "visual_continuity": "The same presenter, room, camera, clothing and props across every frame.",
        "voice_bible": {"language": "Vietnamese", "accent": "Northern", "gender": "female", "age": "25-30",
                        "pitch": "mid-high", "timbre": "warm", "resonance": "light chest", "texture": "smooth",
                        "breathiness": "slight", "nasality": "low", "pace": "steady", "intonation": "gentle endings",
                        "volume": "moderate", "recording": "close dry mic"},
        "reference_image_prompt": "A realistic presenter facing the camera in a stable medium shot.",
        "clips": [
            {"index": 1, "beat": "opening", "video_prompt": "The presenter begins speaking in the same locked shot.",
             "voice_script": "Hôm nay mình sẽ cùng các bạn tìm hiểu"},
            {"index": 2, "beat": "continuation", "video_prompt": "The same presenter continues the speech without changing posture.",
             "voice_script": "vì sao thiết kế này lại hữu ích."},
        ],
    }
    plan = BookStoryPlan.model_validate(response).model_dump()
    assert isinstance(plan["voice_bible"], str)
    assert "accent: Northern" in plan["voice_bible"]
    for field, value in response["voice_bible"].items():
        assert f"{field}: {value}" in plan["voice_bible"]
    assert plan["clips"][0]["voice_script"] == response["clips"][0]["voice_script"]
    assert plan["clips"][1]["voice_script"] == response["clips"][1]["voice_script"]


@pytest.mark.asyncio
async def test_detailed_global_voice_survives_actual_poyo_http_requests_for_three_scenes():
    profile = (
        "Vietnamese; female adult 25-30; Hanoi Northern accent; mid-high pitch, warm clear timbre, "
        "light chest resonance, smooth texture, slight breathiness, low nasality; friendly conversation, "
        "steady cadence, gentle endings, clear tones; moderate volume, close dry mic."
    )
    voice_line = "GLOBAL VOICE INSTRUCTION (unchanged for every scene): " + profile
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "data": {"task_id": f"voice-test-{len(sent)}", "status": "not_started"}})

    async with httpx.AsyncClient(base_url="https://api.poyo.ai/", transport=httpx.MockTransport(handler)) as transport:
        adapter = PoyoVeoProvider(transport, "veo3.1-fast")
        adapter.set_api_key("test-key-not-real")
        with TestClient(create_app(ProviderRegistry([adapter]), "test-secret")) as client:
            for index in range(1, 4):
                body = request_body()
                body["spec"]["request"]["request_id"] = f"voice-request-{index}"
                body["spec"]["intent"]["goal"] = voice_line + f"\nGenerate picture and native audio together. Scene {index} dialogue only."
                body["spec"]["output"] = {"duration_seconds": 8, "generate_audio": True}
                canonical = client.post("/internal/v1/video/poyo/validate", json=body,
                                        headers={"X-Internal-Key": "test-secret"})
                assert canonical.status_code == 200
                submitted = client.post("/internal/v1/video/poyo/tasks", json=canonical.json()["execution_request"],
                                        headers={"X-Internal-Key": "test-secret"})
                assert submitted.status_code == 202
    assert len(sent) == 3
    assert all(voice_line in payload["input"]["prompt"] for payload in sent)
    assert all("Generate picture and native audio together" in payload["input"]["prompt"] for payload in sent)
    assert adapter.continuity_capabilities().voice_identity_guaranteed is False


@pytest.mark.parametrize("model", ["veo3.1-fast", "veo3.1-quality"])
def test_poyo_single_start_image_does_not_use_reference_mode(model):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8}
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [{"asset_id": "previous-end", "role": "FIRST_FRAME"}]
    body["assets"] = [{
        "asset_id": "previous-end", "mime_type": "image/jpeg",
        "source_url": "https://video.example.test/previous-end.jpg",
        "moderation_status": "APPROVED",
    }]
    payload = PoyoVeoCompiler(model).compile(ExecutionRequest.model_validate(body))
    assert payload["input"]["image_urls"] == ["https://video.example.test/previous-end.jpg"]
    assert "generation_type" not in payload["input"]


@pytest.mark.parametrize("model", ["veo3.1-fast", "veo3.1-fast-official", "veo3.1-quality-official"])
def test_three_images_cannot_silently_turn_last_frame_into_an_ingredient(model):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8}
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [
        {"asset_id": "anchor", "role": "CHARACTER_REFERENCE"},
        {"asset_id": "previous-start", "role": "FIRST_FRAME"},
        {"asset_id": "previous-end", "role": "LAST_FRAME"},
    ]
    body["assets"] = [
        {
            "asset_id": asset_id,
            "mime_type": "image/jpeg",
            "source_url": f"https://video.example.test/{asset_id}.jpg",
            "moderation_status": "APPROVED",
        }
        for asset_id in ("anchor", "previous-start", "previous-end")
    ]
    request = ExecutionRequest.model_validate(body)
    compiler = PoyoVeoCompiler(model)
    assert not compiler.capabilities.supports(request.spec, images_enabled=True, model=model)
    with pytest.raises(ProviderError, match="unsupported_capability"):
        compiler.compile(request)


@pytest.mark.parametrize("model", ["veo3.1-fast", "veo3.1-quality", "veo3.1-fast-official"])
def test_two_images_preserve_order_and_real_first_last_frame_mode(model):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8}
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [
        {"asset_id": "start", "role": "FIRST_FRAME"},
        {"asset_id": "end", "role": "LAST_FRAME"},
    ]
    body["assets"] = [
        {"asset_id": asset_id, "mime_type": "image/jpeg",
         "source_url": f"https://video.example.test/{asset_id}.jpg", "moderation_status": "APPROVED"}
        for asset_id in ("end", "start")
    ]
    request = ExecutionRequest.model_validate(body)
    payload = PoyoVeoCompiler(model).compile(request)
    assert payload["input"]["generation_type"] == "frame"
    assert payload["input"]["image_urls"] == ["https://video.example.test/start.jpg", "https://video.example.test/end.jpg"]
    assert not PoyoVeoCompiler("veo3.1-lite").capabilities.supports(request.spec, images_enabled=True)


def test_poyo_fast_compiles_character_style_and_start_references():
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": 8}
    body["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
    body["spec"]["references"] = [
        {"asset_id": "character", "role": "CHARACTER_REFERENCE"},
        {"asset_id": "style", "role": "STYLE_REFERENCE"},
        {"asset_id": "start", "role": "FIRST_FRAME"},
    ]
    body["assets"] = [
        {
            "asset_id": asset_id,
            "mime_type": "image/jpeg",
            "source_url": f"https://video.example.test/{asset_id}.jpg",
            "moderation_status": "APPROVED",
        }
        for asset_id in ("character", "style", "start")
    ]
    payload = PoyoVeoCompiler("veo3.1-fast").compile(ExecutionRequest.model_validate(body))
    assert payload["input"]["generation_type"] == "reference"
    assert payload["input"]["image_urls"] == [
        f"https://video.example.test/{asset_id}.jpg"
        for asset_id in ("character", "style", "start")
    ]


@pytest.mark.parametrize("duration", [5, 10])
def test_poyo_veo_rejects_non_eight_second_video(duration):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": duration}
    with pytest.raises(ProviderError, match="unsupported_capability"):
        PoyoVeoCompiler("veo3.1-fast").compile(ExecutionRequest.model_validate(body))


@pytest.mark.parametrize("duration", [1, 3, 13, 120])
def test_capabilities_reject_duration(duration):
    body = request_body()
    body["spec"]["output"] = {"duration_seconds": duration}
    with pytest.raises(ProviderError):
        SeedanceCompiler("test").compile(ExecutionRequest.model_validate(body))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,code,retryable,ambiguous",
    [
        ("POST", 400, False, False),
        ("POST", 401, False, False),
        ("POST", 429, True, False),
        ("POST", 503, False, True),
        ("GET", 503, True, False),
    ],
)
async def test_provider_error_classification(method, code, retryable, ambiguous):
    async with httpx.AsyncClient(
        base_url="https://example.com/",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(code, json={"secret": "do-not-expose"})
        ),
    ) as client:
        provider = SeedanceProvider(client, "test")
        with pytest.raises(ProviderError) as caught:
            await provider._request(method, "tasks")
        assert caught.value.retryable == retryable
        assert caught.value.ambiguous == ambiguous
        assert "do-not-expose" not in str(caught.value)


@pytest.mark.asyncio
async def test_provider_submit_poll_and_running_cancel():
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(200, json={"id": "task1"})
        return httpx.Response(200, json={"id": "task1", "status": "running"})

    async with httpx.AsyncClient(
        base_url="https://example.com/", transport=httpx.MockTransport(handler)
    ) as client:
        provider = SeedanceProvider(client, "test")
        assert (await provider.submit({})).provider_task_id == "task1"
        assert (await provider.get_status("task1")).status == "generating"
        with pytest.raises(ProviderError, match="cancel_not_supported_running"):
            await provider.cancel("task1")
        assert "DELETE" not in calls


@pytest.mark.asyncio
async def test_poyo_submit_poll_and_cancel_behavior():
    state = {"status": "running"}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"task_id": "task-poyo-1", "status": "not_started"},
                },
            )
        files = (
            [{"file_url": "https://storage.poyo.ai/generated/video.mp4", "file_type": "video"}]
            if state["status"] == "finished"
            else []
        )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "task_id": "task-poyo-1",
                    "status": state["status"],
                    "credits_amount": 36,
                    "files": files,
                    "error_message": None,
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.poyo.ai/", transport=httpx.MockTransport(handler)
    ) as client:
        provider = PoyoVeoProvider(client, "veo3.1-fast")
        provider.set_api_key("test-poyo-key")
        assert (await provider.submit({})).provider_task_id == "task-poyo-1"
        assert (await provider.get_status("task-poyo-1")).status == "generating"
        with pytest.raises(ProviderError, match="cancel_not_supported_running"):
            await provider.cancel("task-poyo-1")
        state["status"] = "finished"
        result = await provider.get_status("task-poyo-1")
        assert result.status == "completed"
        assert str(result.outputs[0].source_url).startswith("https://storage.poyo.ai/")
        assert result.usage["credits"] == 36


@pytest.mark.asyncio
async def test_submit_timeout_and_invalid_response_are_ambiguous():
    def timeout(_):
        raise httpx.ReadTimeout("sensitive-url")

    for handler in [timeout, lambda _: httpx.Response(200, json={})]:
        async with httpx.AsyncClient(
            base_url="https://example.com/", transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ProviderError) as caught:
                await SeedanceProvider(client, "test").submit({})
            assert caught.value.ambiguous


@pytest.mark.asyncio
async def test_fake_survives_restart_and_cancel(tmp_path):
    path = str(tmp_path / "fake.sqlite")
    provider = FakeVideoProvider(path, delay=100)
    task = await provider.submit({})
    other = FakeVideoProvider(path, delay=100)
    assert (await other.get_status(task.provider_task_id)).status == "generating"
    assert (await other.cancel(task.provider_task_id)).status == "cancelled"
    assert (await provider.cancel(task.provider_task_id)).status == "cancelled"


def test_internal_api_auth_and_success(tmp_path):
    registry = ProviderRegistry([FakeVideoProvider(str(tmp_path / "fake.sqlite"), delay=0)])
    with TestClient(create_app(registry, "test-secret")) as client:
        assert client.get("/health").json() == {"status": "ok", "providers": ["fake"]}
        path = "/internal/v1/video/fake/tasks"
        assert client.post(path, json=request_body()).status_code == 401
        client.headers["X-Internal-Key"] = "test-secret"
        response = client.post(path, json=request_body())
        assert response.status_code == 202
        result = client.get(path + "/" + response.json()["provider_task_id"]).json()
        assert result["status"] == "completed"
        assert result["usage"]["estimated_cost"] is None
        assert result["outputs"]
        body = request_body()
        body["preflight_decision"] = "BLOCK"
        assert client.post(path, json=body).status_code == 422


def test_validation_preserves_legacy_audio_shape_and_native_opt_in(tmp_path):
    provider = FakeVideoProvider(str(tmp_path / "fake.sqlite"), delay=0)
    provider.compiler = SeedanceCompiler("seedance-1-5-pro-251215")
    registry = ProviderRegistry([provider])
    with TestClient(create_app(registry, "test-secret")) as client:
        client.headers["X-Internal-Key"] = "test-secret"
        path = "/internal/v1/video/fake/validate"
        body = request_body()
        response = client.post(path, json=body)
        assert response.status_code == 200
        assert "generate_audio" not in response.json()["execution_request"]["spec"]["output"]
        body["spec"]["output"] = {"generate_audio": True}
        response = client.post(path, json=body)
        assert response.status_code == 200
        assert response.json()["execution_request"]["spec"]["output"]["generate_audio"] is True


@pytest.mark.asyncio
async def test_poyo_fast_capabilities_are_authenticated_and_do_not_invent_extension_fields():
    def forbid_network(_request):
        raise AssertionError("Capability queries must not create paid provider tasks")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbid_network)) as transport:
        adapter = PoyoVeoProvider(transport, "veo3.1-fast")
        with TestClient(create_app(ProviderRegistry([adapter]), "test-secret")) as client:
            path = "/internal/v1/video/poyo/capabilities"
            assert client.get(path).status_code == 401
            response = client.get(path, headers={"X-Internal-Key": "test-secret"})
            assert response.status_code == 200
            caps = response.json()
            assert caps["model"] == "veo3.1-fast"
            assert caps["native_audio"] is True
            assert caps["video_continuation"] is False
            assert caps["voice_identity_guaranteed"] is False
            assert caps["generation_durations"] == [8]
            assert caps["continuation_durations"] == []
            assert caps["continuation_output"] == "UNSUPPORTED"
            body = request_body()
            body["spec"]["output"] = {"duration_seconds": 8, "generate_audio": True}
            payload = adapter.compile(ExecutionRequest.model_validate(body))
            assert payload["model"] == "veo3.1-fast"
            assert set(payload["input"]) == {"prompt", "duration", "aspect_ratio", "resolution"}
            assert "video_url" not in payload["input"]
            assert "sound" not in payload["input"]


def test_fake_capabilities_do_not_claim_real_native_voice(tmp_path):
    provider = FakeVideoProvider(str(tmp_path / "fake.sqlite"), delay=0)
    caps = provider.continuity_capabilities()
    assert caps.provider == "fake"
    assert caps.native_audio is False
    assert caps.video_continuation is False
    assert caps.voice_identity_guaranteed is False


def test_transcribe_clip_uses_saved_planner_key(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "Mình đang giới thiệu cuốn sách này."})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.pipelines.video_api.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        "app.pipelines.video_api.CredentialVault.get",
        lambda self, provider: "test-openai-key" if provider == "openai_planner" else None,
    )
    registry = ProviderRegistry([FakeVideoProvider(str(tmp_path / "fake.sqlite"), delay=0)])
    with TestClient(create_app(registry, "test-secret")) as client:
        path = "/internal/v1/planner/transcribe-clip"
        assert client.post(path, content=b"clipbytes", headers={"Content-Type": "video/mp4"}).status_code == 401
        result = client.post(
            path, content=b"clipbytes",
            headers={"Content-Type": "video/mp4", "X-Internal-Key": "test-secret"},
        )
        assert result.status_code == 200
        assert result.json()["text"] == "Mình đang giới thiệu cuốn sách này."
    assert len(calls) == 1
    assert calls[0].url.path == "/v1/audio/transcriptions"
    assert calls[0].headers["authorization"] == "Bearer test-openai-key"
    assert b"clipbytes" in calls[0].content


def test_poyo_credential_is_encrypted_and_survives_restart(tmp_path, monkeypatch):
    credential_file = tmp_path / "provider-credentials.json.enc"
    monkeypatch.setenv("VIDEO_ENABLE_POYO", "true")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(credential_file))
    monkeypatch.delenv("POYO_API_KEY", raising=False)
    headers = {"X-Internal-Key": "vault-test-secret"}

    with TestClient(create_app(internal_key="vault-test-secret")) as client:
        path = "/internal/v1/video/poyo/credential"
        assert client.get(path, headers=headers).json() == {"configured": False}
        result = client.put(path, headers=headers, json={"api_key": "poyo-plain-secret"})
        assert result.json() == {"configured": True}
        assert "poyo-plain-secret" not in result.text

    assert "poyo-plain-secret" not in credential_file.read_text(encoding="utf-8")
    with TestClient(create_app(internal_key="vault-test-secret")) as client:
        assert client.get(path, headers=headers).json() == {"configured": True}
        assert client.delete(path, headers=headers).json() == {"configured": False}


def test_keys_saved_by_another_worker_process_are_picked_up(tmp_path, monkeypatch):
    # Two uvicorn workers share one key file; a key saved through one must work in the other.
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "provider-credentials.json.enc"))
    monkeypatch.delenv("POYO_API_KEY", raising=False)
    monkeypatch.delenv("SEEDVIS_API_KEY", raising=False)
    headers = {"X-Internal-Key": "vault-test-secret"}
    with TestClient(create_app(internal_key="vault-test-secret")) as first, \
            TestClient(create_app(internal_key="vault-test-secret")) as second:
        for provider in ("seedvis", "poyo"):
            path = f"/internal/v1/video/{provider}/credential"
            assert second.get(path, headers=headers).json() == {"configured": False}
            assert first.put(path, headers=headers, json={"api_key": f"{provider}-key-123456"}).json() == {
                "configured": True}
            assert second.get(path, headers=headers).json() == {"configured": True}
            assert first.delete(path, headers=headers).json() == {"configured": False}
            assert second.get(path, headers=headers).json() == {"configured": False}
