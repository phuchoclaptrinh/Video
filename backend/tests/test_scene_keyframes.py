# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import io

from PIL import Image
from sqlalchemy import select, update

from app.services.platform import drafts
from app.services.video_continuity import ContinuityReference, ContinuityRequest, build_scene_prompt
from tests.test_platform import (  # noqa: F401 - shared fixture
    client,
    register,
    use_legacy_test_provider,
)


def jpeg(color):
    data = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(data, format="JPEG")
    return data.getvalue()


class InternalClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs["json"]))
        outer = self

        class Response:
            content = jpeg("blue")

            def raise_for_status(self):
                if outer.fail:
                    import httpx
                    raise httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://ai"), response=httpx.Response(502))

        return Response()

    def close(self):
        return None


def owned_draft(client):
    platform = client.platform
    use_legacy_test_provider(platform, "poyo")
    platform.paid_enabled = True
    platform.public_base_url = ""
    register(client)
    asset = client.post("/api/v1/assets", files={"file": ("outfit.png", jpeg("white"), "image/jpeg")}).json()
    created = client.post("/api/v1/plans", json={"prompt": "Quảng cáo bộ đồ", "duration": 8, "asset_id": asset["id"]}).json()
    with platform.engine.begin() as db:
        row = db.execute(select(drafts).where(drafts.c.id == created["id"])).mappings().first()
        payload = dict(row["payload"])
        payload["story_plan"] = {"visual_bible": "A Vietnamese boy in the outfit", "clips": [
            {"index": 1, "video_prompt": "Boy in pajamas on the bed.", "start_state": "Boy sits on bed."},
            {"index": 2, "transition_intent": "hard_cut", "video_prompt": "Boy walks the hallway in the outfit."},
            {"index": 3, "transition_intent": "continue", "video_prompt": "Boy keeps walking."},
        ]}
        db.execute(update(drafts).where(drafts.c.id == created["id"]).values(payload=payload))
        row = db.execute(select(drafts).where(drafts.c.id == created["id"])).mappings().first()
    return platform, dict(row)


def test_product_photo_becomes_worn_anchor_and_cut_scenes_get_keyframes(client):
    platform, draft = owned_draft(client)
    platform.http = InternalClient()
    anchor = platform._keyframe_path(draft["id"], 1)
    assert platform._create_product_anchor(draft, draft["user_id"], anchor)
    platform._prepare_scene_keyframes(draft, draft["user_id"])
    paths = [path for path, _ in platform.http.calls]
    assert paths == ["/internal/v1/planner/scene-image"] * 2
    first, second = (body for _, body in platform.http.calls)
    assert len(first["reference_images"]) == 1 and "never as a flat-lay product photo" in first["prompt"]
    assert len(second["reference_images"]) == 2 and "same person, face" in second["prompt"]
    assert all(image.startswith("data:image/jpeg;base64,") for image in second["reference_images"])
    assert platform.scene_keyframe_path(draft["id"], 2) is not None
    # A continuous scene keeps following the previous clip's final frame.
    assert platform.scene_keyframe_path(draft["id"], 3) is None


def test_keyframe_failure_falls_back_without_blocking(client):
    platform, draft = owned_draft(client)
    platform.http = InternalClient(fail=True)
    anchor = platform._keyframe_path(draft["id"], 1)
    assert not platform._create_product_anchor(draft, draft["user_id"], anchor)
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(jpeg("white"))
    platform._prepare_scene_keyframes(draft, draft["user_id"])
    assert platform.scene_keyframe_path(draft["id"], 2) is None


def test_cut_scene_may_open_on_its_own_keyframe_but_not_previous_frame():
    plan = {"clips": [{"index": 1}, {"index": 2}]}
    clip = {"index": 2, "transition_intent": "hard_cut", "video_prompt": "A new hallway shot.",
            "voice_script": "Cảnh mới."}
    keyframe = ContinuityRequest("first_frame", (ContinuityReference("keyframe", "d-2", "FIRST_FRAME"),), "parent")
    assert "exact opening composition" in build_scene_prompt(plan, clip, keyframe, native_audio=False)
    previous = ContinuityRequest("first_frame", (ContinuityReference("frame", "parent", "FIRST_FRAME"),), "parent")
    try:
        build_scene_prompt(plan, clip, previous, native_audio=False)
    except ValueError as exc:
        assert str(exc) == "scene_cut_requires_new_opening_image_or_reference_mode"
    else:
        raise AssertionError("cut from previous frame must be rejected")


def test_keyframe_prompt_shows_only_the_opening_moment_with_two_hands():
    from app.services.platform_keyframes import PlatformKeyframesMixin

    clip = {"index": 2, "start_state": "Left hand holds the blue outfit by its hanger.",
            "video_prompt": "She spreads one outfit, indicates the bear print, then shows both colors."}
    later = PlatformKeyframesMixin._keyframe_prompt({}, clip, product=True, follows_anchor=True)
    opening = PlatformKeyframesMixin._keyframe_prompt({}, clip, product=True, follows_anchor=False)
    assert "exactly two arms and two hands" in later and "no extra, floating or disembodied hands" in later
    assert "its actions happen later and are not in this still: She spreads" in later
    assert "capture one instant of it, not every action at once: She spreads" in opening


def test_product_scene_prompt_pins_the_product_description():
    look = "Cream raglan tee with blue sleeves, green collar and a bear print; matching blue shorts."
    plan = {"visual_bible": look, "clips": [{"index": 1}, {"index": 2}]}
    clip = {"index": 2, "transition_intent": "hard_cut", "video_prompt": "A close-up of the bear print.",
            "voice_script": "Cảnh mới."}
    keyframe = ContinuityRequest("first_frame", (ContinuityReference("keyframe", "d-2", "FIRST_FRAME"),), "parent")
    prompt = build_scene_prompt(plan, clip, keyframe, native_audio=False, product=True)
    assert "Product lock:" in prompt and "Product description: " + look in prompt
    assert "Product lock:" not in build_scene_prompt(plan, clip, keyframe, native_audio=False)
    # A long description is shortened to fit, never the scene action.
    long_plan = plan | {"visual_bible": look * 60}
    long_prompt = build_scene_prompt(long_plan, clip, keyframe, native_audio=False, product=True)
    assert len(long_prompt) <= 4000 and "A close-up of the bear print." in long_prompt
