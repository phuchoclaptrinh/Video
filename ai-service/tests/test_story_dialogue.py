import asyncio
import json
import unicodedata

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.credential_vault import CredentialVault
from app.pipelines.story_planner import BookStoryPlan, create_book_story_plan
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.schemas.provider import VideoProviderCapabilities

FIRST = "Hôm nay mình chia sẻ một cuốn sách hay cùng bạn."
SECOND = "Cuốn sách giúp mình hiểu bản thân rõ hơn qua từng trang."
JOINED = FIRST + " " + SECOND


def story():
    return {"core_value": "A continuous meaningful video story.",
            "full_voice_script": FIRST + " " + SECOND,
            "visual_continuity": "One seated presenter with stable hands, wardrobe and camera throughout.",
            "reference_image_prompt": "A realistic seated presenter holds the same book in a locked shot.",
            "voice_bible": "Vietnamese, northern accent, warm, steady delivery; pace 1.0-2.0 syllables/s.",
            "clips": [{"index": index, "beat": "Continuation", "duration_seconds": duration,
                       "video_prompt": "The same presenter speaks calmly in the same locked shot.",
                       "voice_script": line, "start_state": "Hands still with the same book.",
                       "end_state": "Hands still with the same book.", "transition_intent": "continue"}
                      for index, (duration, line) in enumerate(((6, FIRST), (8, SECOND)), 1)]}


@pytest.mark.parametrize("full", [
    FIRST + " " + SECOND,
    (FIRST + " " + SECOND).upper(),
    (FIRST + " " + SECOND).replace(".", " ! "),
    f'"{FIRST}"\n\n{SECOND}',
    unicodedata.normalize("NFD", FIRST + " " + SECOND),
])
def test_formatting_only_difference_is_canonicalized_to_scene_dialogue(full):
    output = story()
    output["full_voice_script"] = full
    parsed = BookStoryPlan.model_validate(output, context={"generation_durations": [4, 6, 8]})
    assert parsed.full_voice_script == FIRST + " " + SECOND
    assert [clip.voice_script for clip in parsed.clips] == [FIRST, SECOND]
    assert [clip.duration_seconds for clip in parsed.clips] == [6, 8]


@pytest.mark.parametrize("replacement", [
    "Hôm nay mình chia sẻ hai cuốn sách hay.",
    "Hom nay mình chia sẻ một cuốn sách hay.",
    "Hôm nay mình chia sẻ một cuốn sách.",
    FIRST + " sách hay",
    "Hôm nay mình chia sẻ 30 cuốn sách hay.",
])
def test_real_word_accent_number_or_boundary_changes_still_fail_validation(replacement):
    output = story()
    output["clips"][0]["voice_script"] = replacement
    with pytest.raises(ValidationError) as error:
        BookStoryPlan.model_validate(output)
    assert error.value.errors()[0]["type"] == "voice_partition_mismatch"


@pytest.mark.parametrize("source,changed", [("-30", "30"), ("3.5", "3 5"), ("30%", "30"), ("$30", "30")])
def test_spoken_number_sign_decimal_and_units_are_not_discarded(source, changed):
    output = story()
    first = f"Giá sản phẩm là {source} cho mỗi hộp."
    output["full_voice_script"] = first + " " + SECOND
    output["clips"][0]["voice_script"] = first.replace(source, changed)
    with pytest.raises(ValidationError) as error:
        BookStoryPlan.model_validate(output)
    assert error.value.errors()[0]["type"] == "voice_partition_mismatch"


def mock_planner(monkeypatch, output, repair):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) > 2:
            raise AssertionError("Planner repair must not loop")
        content = output if len(calls) == 1 else repair
        if isinstance(content, httpx.Response):
            return content
        return httpx.Response(200, json={"id": f"test-response-{len(calls)}", "choices": [
            {"message": {"content": json.dumps(content, ensure_ascii=False)}}
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls


class Vault:
    def get(self, _name):
        return "unit-test-not-real-key"


async def plan_story():
    return await create_book_story_plan("Một video giới thiệu sách, tốc độ 1.0-2.0 âm tiết/giây", Vault(), "gpt-4o-mini", video_capabilities=(
        VideoProviderCapabilities(provider="poyo", model="veo3.1-fast-official", generation_durations=[4, 6, 8])
    ))


@pytest.mark.asyncio
async def test_formatting_mismatch_uses_one_request_without_paid_repair(monkeypatch):
    output = story()
    output["full_voice_script"] = (FIRST + " " + SECOND).lower().replace(".", "!")
    calls = mock_planner(monkeypatch, output, {})
    assert (await plan_story())["full_voice_script"] == JOINED
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_partition_mismatch_is_rejected_without_rewrite(monkeypatch):
    output = story()
    output["clips"][1]["voice_script"] = "cuốn sách hay " + SECOND
    calls = mock_planner(monkeypatch, {"story_plan": output}, {})
    with pytest.raises(RuntimeError, match=r"^planner_dialogue_mismatch$"):
        await plan_story()
    assert len(calls) == 1 and calls[0]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_non_dialogue_validation_errors_do_not_trigger_repair(monkeypatch):
    output = story()
    output["clips"][0]["duration_seconds"] = 5
    output["clips"][1]["voice_script"] = "sách hay " + SECOND
    calls = mock_planner(monkeypatch, output, {})
    with pytest.raises(RuntimeError, match=r"^planner_schema_mismatch$"):
        await plan_story()
    assert len(calls) == 1


def test_internal_planner_returns_specific_error_without_repair(tmp_path, monkeypatch):
    output = story()
    output["clips"][0]["duration_seconds"] = 8
    output["clips"][1]["voice_script"] = "sách hay " + SECOND
    calls = mock_planner(monkeypatch, output, {"voice_scripts": [FIRST]})
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda _self, _name: "unit-test-not-real-key")
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        response = client.post("/internal/v1/planner/story", json={"prompt": "Một video giới thiệu sách, tốc độ 1.0-2.0 âm tiết/giây"},
                               headers={"X-Internal-Key": "test-secret"})
    assert response.status_code == 502 and response.json()["detail"] == "planner_dialogue_mismatch"
    assert len(calls) == 1


def test_internal_planner_returns_valid_initial_canonical_plan(tmp_path, monkeypatch):
    output = story()
    output["clips"][0]["duration_seconds"] = 8
    output["clips"][0]["voice_script"] = SECOND
    output["full_voice_script"] = SECOND + " " + SECOND
    calls = mock_planner(monkeypatch, output, {})
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda _self, _name: "unit-test-not-real-key")
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        response = client.post("/internal/v1/planner/story", json={"prompt": "Một video giới thiệu sách, tốc độ 1.0-2.0 âm tiết/giây"},
                               headers={"X-Internal-Key": "test-secret"})
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["full_voice_script"] == " ".join(clip["voice_script"] for clip in plan["clips"])
    assert plan["duration_target_seconds"] == 16 and len(calls) == 1


def mock_planner_transport(monkeypatch, *outcomes):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) > len(outcomes):
            raise AssertionError("Planner exceeded its network retry budget")
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(200, json={"id": "transport-test", "choices": [
            {"message": {"content": json.dumps(outcome, ensure_ascii=False)}}
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("app.pipelines.planner_transport.NETWORK_RETRY_DELAY_SECONDS", 0)
    return calls


@pytest.mark.asyncio
async def test_story_read_error_recovers_without_changing_request_context(monkeypatch):
    calls = mock_planner_transport(monkeypatch, httpx.ReadError("reset"), story())
    plan = await plan_story()
    assert len(calls) == 2 and calls[0] == calls[1]
    assert plan["full_voice_script"] == JOINED
    assert plan["duration_target_seconds"] == 14


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,reason", [
    (httpx.ReadError, "planner_connection_failed"),
    (httpx.RemoteProtocolError, "planner_connection_failed"),
    (httpx.ReadTimeout, "planner_timeout"),
])
async def test_persistent_transport_error_has_specific_reason_after_one_retry(monkeypatch, error_type, reason):
    calls = mock_planner_transport(monkeypatch, error_type("reset"), error_type("reset"))
    with pytest.raises(RuntimeError, match="^" + reason + "$"):
        await plan_story()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_successful_network_retry_does_not_trigger_semantic_repair(monkeypatch):
    output = story()
    output["clips"][1]["voice_script"] = "sach hay " + SECOND
    calls = mock_planner_transport(monkeypatch, httpx.ReadError("reset"), output)
    with pytest.raises(RuntimeError, match=r"^planner_dialogue_mismatch$"):
        await plan_story()
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [
    (401, "planner_invalid_key"), (403, "planner_invalid_key"),
    (404, "planner_model_unavailable"), (429, "planner_quota_or_rate_limit"),
    (500, "planner_upstream_error"),
])
async def test_http_errors_keep_classification_without_retry(monkeypatch, status, reason):
    calls = mock_planner_transport(monkeypatch, httpx.Response(status, json={"error": "upstream"}))
    with pytest.raises(RuntimeError, match="^" + reason + "$"):
        await plan_story()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_total_planner_deadline_cancels_hanging_request(monkeypatch):
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.Event().wait()

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr("app.pipelines.story_planner.PLANNER_DEADLINE_SECONDS", 0.02)
    with pytest.raises(RuntimeError, match=r"^planner_timeout$"):
        await plan_story()
    assert len(calls) == 1


@pytest.mark.parametrize("error_type,reason", [(httpx.ReadError, "planner_connection_failed"), (httpx.ReadTimeout, "planner_timeout")])
def test_internal_planner_preserves_transport_error_reason(tmp_path, monkeypatch, error_type, reason):
    calls = mock_planner_transport(monkeypatch, error_type("reset"), error_type("reset"))
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda _self, _name: "unit-test-not-real-key")
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        response = client.post("/internal/v1/planner/story", json={"prompt": "A calm presenter video"},
                               headers={"X-Internal-Key": "test-secret"})
    assert response.status_code == 502 and response.json()["detail"] == reason
    assert len(calls) == 2
