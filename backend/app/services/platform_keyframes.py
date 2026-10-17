"""Per-scene opening keyframes so every cut starts from a real, recognizable shot."""

import io
import logging
from concurrent.futures import ThreadPoolExecutor

from app.services.platform_shared import (
    HTTPException,
    Image,
    ImageOps,
    Path,
    UnidentifiedImageError,
    assets,
    base64,
    httpx,
    secrets,
)
from app.services.video_continuity import CUT_TRANSITIONS, scene_transition

logger = logging.getLogger(__name__)


class PlatformKeyframesMixin:
    def _keyframe_path(self, draft_id: str, index: int) -> Path:
        # Scene one opens on the shared anchor, which also seeds character/style references.
        name = f"anchor-{draft_id}.jpg" if index == 1 else f"keyframe-{draft_id}-{index}.jpg"
        return self.storage / "continuity" / name

    @staticmethod
    def _jpeg_data_url(path: Path) -> str:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2048, 2048))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

    @staticmethod
    def _keyframe_prompt(story_plan: dict, clip: dict, *, product: bool, follows_anchor: bool) -> str:
        lines = [
            "Photorealistic opening frame for one scene of a short social video ad, captured like a real "
            "camera with natural light and believable depth, not a collage or studio product layout.",
            "Show the garments and accessories from the supplied product photo exactly as this scene describes, "
            "worn, held up to camera or used, matching their exact colors, patterns, textures, trims and shapes; "
            "never as a flat-lay product photo, and never resize children's clothing onto an adult."
            if product else "",
            "Keep exactly the same person, face, hairstyle and outfit as the supplied scene reference image."
            if follows_anchor else
            # The anchor seeds the character reference crop, so the face and whole outfit must be in frame.
            "Capture this scene's hook moment mid-action with an expressive face and a dynamic camera angle, "
            "the person near the center with face and complete outfit clearly visible.",
            "One single continuous photograph of one person: no duplicated subject, no oversized foreground "
            "cut-out, inset, collage or detail panel.",
            # A still that merges every action of the scene draws extra hands (holding and pointing at once).
            "Correct anatomy: the person has exactly two arms and two hands, both naturally attached; a hand "
            "holding a hanger or garment does not also point or gesture; no extra, floating or disembodied hands.",
            "Shared look: " + str(story_plan.get("visual_bible") or story_plan.get("visual_continuity") or ""),
            "This scene opens on: " + str(clip.get("start_state") or ""),
            (
                "Framing and setting cue only; its actions happen later and are not in this still: "
                if follows_anchor else
                "Scene direction; capture one instant of it, not every action at once: "
            ) + str(clip.get("video_prompt") or ""),
            "No text, captions, logos, watermarks or split screens.",
        ]
        return "\n".join(line for line in lines if line.strip())[:4000]

    def _request_keyframe(self, draft: dict, prompt: str, references: list[Path], label: str) -> bytes:
        self.require_image_credits(draft)
        response = self.http.post(
            "/internal/v1/planner/scene-image",
            json={
                "prompt": prompt,
                "aspect_ratio": draft["payload"]["spec"]["output"]["aspect_ratio"],
                "request_id": f"{draft['id']}-{label}",
                **self._image_target(draft),
                "reference_images": [self._jpeg_data_url(path) for path in references],
            },
            timeout=300,
        )
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as image:
            image.verify()
        self.charge_image(draft, f"{draft['id']}-{label}")
        return response.content

    def _write_keyframe(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.stem}-{secrets.token_hex(4)}.jpg")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _product_photo(self, draft: dict, uid: str) -> Path | None:
        references = draft["payload"].get("spec", {}).get("references", [])
        asset_id = references[0]["asset_id"] if references else None
        if not asset_id:
            return None
        return self.storage / self.owned(assets, asset_id, uid)["filename"]

    def _create_product_anchor(self, draft: dict, uid: str, anchor: Path) -> bool:
        """Turn an uploaded product photo into a worn, filmable opening frame; False keeps the photo."""
        product = self._product_photo(draft, uid)
        if product is None:
            return False
        clips = (draft["payload"].get("story_plan") or {}).get("clips") or []
        if not clips:
            return False
        prompt = self._keyframe_prompt(draft["payload"]["story_plan"], clips[0], product=True, follows_anchor=False)
        try:
            self._write_keyframe(anchor, self._request_keyframe(draft, prompt, [product], "scene-1"))
            return True
        except (httpx.HTTPError, OSError, UnidentifiedImageError, HTTPException) as exc:
            logger.warning("[VIDEO] product anchor generation failed draft=%s error=%s", draft["id"], type(exc).__name__)
            return False

    def _prepare_scene_keyframes(self, draft: dict, uid: str) -> None:
        """Generate one opening keyframe per cut scene; failures fall back to existing continuity."""
        anchor = self._keyframe_path(draft["id"], 1)
        story_plan = draft["payload"].get("story_plan") or {}
        if not anchor.is_file():
            return
        product = self._product_photo(draft, uid)
        pending = [
            clip for clip in story_plan.get("clips") or []
            if clip.get("index", 1) > 1 and scene_transition(clip) in CUT_TRANSITIONS
            and not self._keyframe_path(draft["id"], clip["index"]).is_file()
        ]

        def generate(clip: dict) -> None:
            prompt = self._keyframe_prompt(story_plan, clip, product=product is not None, follows_anchor=True)
            references = [anchor] + ([product] if product is not None else [])
            try:
                data = self._request_keyframe(draft, prompt, references, f"scene-{clip['index']}")
                self._write_keyframe(self._keyframe_path(draft["id"], clip["index"]), data)
            except (httpx.HTTPError, OSError, UnidentifiedImageError, HTTPException) as exc:
                logger.warning("[VIDEO] scene keyframe failed draft=%s scene=%s error=%s",
                               draft["id"], clip["index"], type(exc).__name__)

        if pending:
            with ThreadPoolExecutor(max_workers=min(3, len(pending))) as pool:
                list(pool.map(generate, pending))

    def _clear_scene_keyframes(self, draft_id: str) -> None:
        for path in (self.storage / "continuity").glob(f"keyframe-{draft_id}-*.jpg"):
            path.unlink(missing_ok=True)

    def scene_keyframe_path(self, draft_id: str, index: int) -> Path | None:
        path = self._keyframe_path(draft_id, index)
        return path if path.is_file() else None
