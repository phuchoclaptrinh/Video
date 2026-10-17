import httpx

from app.providers.seedvis import SeedvisProvider
from app.schemas.generation import ExecutionRequest

JPEG = "data:image/jpeg;base64," + "A" * 200


def omni_request(duration=10, image=True):
    spec = {
        "request": {"request_id": "req-1", "user_id": "user-1", "tenant_id": "user-1"},
        "intent": {"operation": "REFERENCE_GENERATION" if image else "TEXT_GENERATION", "goal": "Scene."},
        "output": {"duration_seconds": duration, "aspect_ratio": "9:16", "generate_audio": True},
        "references": [{"asset_id": "frame-1", "role": "FIRST_FRAME"}] if image else [],
    }
    assets = [{"asset_id": "frame-1", "mime_type": "image/jpeg", "source_url": JPEG,
               "moderation_status": "APPROVED"}] if image else []
    return ExecutionRequest.model_validate({"model": "Omni-Flash", "spec": spec, "assets": assets,
                                            "preflight_decision": "ALLOW", "policy_decision_id": "p-1"})


def test_omni_flash_is_a_fixed_length_native_speech_story_model():
    adapter = SeedvisProvider(httpx.AsyncClient(), "Omni-Flash")
    caps = adapter.continuity_capabilities()
    assert caps.native_audio and caps.first_frame and caps.generation_durations == [10]
    body = adapter.compile(omni_request())["body"]
    # Omni has no duration field; the requested length is never sent.
    assert "duration" not in body and body["mode"] == "image-to-video" and body["image"] == JPEG
    assert "duration" not in adapter.compile(omni_request(duration=8, image=False))["body"]
