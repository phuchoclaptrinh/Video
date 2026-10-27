# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
from types import SimpleNamespace

from sqlalchemy import select

from app.services.platform_shared import drafts
from app.services.video_continuity import (
    PRODUCT_PHOTO_LOCK_RULE,
    ContinuityReference,
    ContinuityRequest,
    build_scene_prompt,
)
from tests.test_platform import client, register  # noqa: F401 - shared fixture

OPENING = ContinuityRequest("first_frame", (ContinuityReference("keyframe", "draft-2", "FIRST_FRAME"),), "parent")
STRATEGY = SimpleNamespace(capabilities=SimpleNamespace(reference_images=True))


def draft(product_reference=True, photos=("p1", "p2", "p3")):
    return {"payload": {"product_reference": product_reference, "input_asset_ids": list(photos),
                        "spec": {"references": []}}}


def test_product_mode_sends_the_scene_image_and_two_product_photos(client):
    platform = client.platform
    platform.provider = "seedvis"
    request = platform._with_product_photos(draft(), STRATEGY, OPENING)
    assert request.mode == "product_references" and request.parent_generation_id == "parent"
    assert [(ref.kind, ref.identifier, ref.role) for ref in request.references] == [
        ("keyframe", "draft-2", "STYLE_REFERENCE"), ("asset", "p1", "PRODUCT_REFERENCE"),
        ("asset", "p2", "PRODUCT_REFERENCE")]
    # Off, without photos, without an opening image or on another provider nothing changes.
    assert platform._with_product_photos(draft(False), STRATEGY, OPENING) is OPENING
    assert platform._with_product_photos(draft(photos=()), STRATEGY, OPENING) is OPENING
    assert platform._with_product_photos(draft(), STRATEGY, ContinuityRequest("none")).mode == "none"
    platform.provider = "poyo"
    assert platform._with_product_photos(draft(), STRATEGY, OPENING) is OPENING


def test_product_mode_prompt_points_at_the_product_photos():
    request = ContinuityRequest("product_references", (
        ContinuityReference("scene", "anchor-d", "STYLE_REFERENCE"), ContinuityReference("asset", "p1", "PRODUCT_REFERENCE")))
    clip = {"index": 1, "duration_seconds": 10, "video_prompt": "A mother shows the outfit.", "voice_script": "Xin chào."}
    prompt = build_scene_prompt({"clips": [clip], "visual_bible": "Bộ đồ vàng"}, clip, request,
                                native_audio=False, product=True)
    assert "Reference image 1 is the planned scene" in prompt and PRODUCT_PHOTO_LOCK_RULE in prompt
    assert "exact opening composition" not in prompt


def test_the_render_choice_is_stored_on_the_draft(client):
    register(client)
    plan = client.post("/api/v1/plans", json={"prompt": "A short product demo video"}).json()
    response = client.post(f"/api/v1/plans/{plan['id']}/generate", json={"confirm_paid": True, "product_reference": True})
    assert response.status_code == 202
    with client.platform.engine.connect() as db:
        assert db.execute(select(drafts.c.payload).where(drafts.c.id == plan["id"])).scalar_one()["product_reference"] is True
    assert client.post(f"/api/v1/plans/{plan['id']}/generate",
                       json={"confirm_paid": True, "product_reference": "yes"}).status_code == 422
