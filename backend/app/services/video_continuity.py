"""Continuity requests supported by the installed provider contracts.

The current PoYo/Seedance adapters accept images, not a previous video or an
audio reference. A parent job ID below is local provenance, not a provider
continuation ID. Image guidance and voice instructions cannot lock a voice.
"""

import json
import re
import textwrap
from dataclasses import dataclass

from app.core.voice_presets import get_voice_preset
from app.services.spoken_text import STORY_PLACEHOLDER_RE, clean_spoken_text
from app.services.video_provider_capabilities import VideoProviderCapabilities

VOICE_FIELDS = (
    "language", "accent", "gender", "age", "pitch", "timbre", "resonance", "texture",
    "breathiness", "nasality", "speaking_style", "pace", "emotion", "intonation",
    "pronunciation", "volume", "recording",
)

# Numeric speaking rates (also in plans saved before rates were removed) never reach the model.
SPEECH_RATE_RE = re.compile(
    r";?\s*(?:pace|speed|tốc độ)\s*[:=]?\s*\d+(?:[.,]\d+)?(?:\s*[-\u2013]\s*\d+(?:[.,]\d+)?)?"
    r"\s*(?:syllables?|âm tiết)\s*/\s*(?:s|sec|giây)\b",
    re.IGNORECASE,
)
# A hand that leaves a held product to point or gesture otherwise leaves the product floating in mid-air.
HELD_PROP_RE = re.compile(
    r"\b(?:hold(?:s|ing)?|held|hangers?|carr(?:y|ies|ying)|lift(?:s|ing)?|rais(?:e|es|ing)|grip(?:s|ping)?"
    r"|cầm|giữ|giơ|móc|treo)\b",
    re.IGNORECASE,
)
HELD_PROP_RULE = (
    " Held items stay gripped or rest on a visible support, never floating; "
    "a holding hand never lets go to gesture."
)
# Video models redraw products on close-ups and camera moves unless the prompt pins them down.
PRODUCT_LOCK_RULE = (
    "Product lock: the products in the supplied first frame are the real items being sold. Keep every product "
    "identical for the whole shot, including close-ups and camera moves: same cut, body, sleeve and collar colors, "
    "prints, graphics, trims and matching pieces. Never redesign, recolor, swap prints, merge variants, or add "
    "or remove items; whoever wears or holds a variant keeps that exact variant."
)
# Product mode sends the product photos themselves, so the lock points at them instead of a first frame.
PRODUCT_PHOTO_LOCK_RULE = (
    "Product lock: the product photos among the reference images show the real items being sold. Keep every "
    "product identical to those photos for the whole shot, including close-ups and camera moves: same cut, colors, "
    "print pattern and its placement, graphics, trims, collar and matching pieces. Never redesign, recolor, "
    "simplify the print, merge variants, or add or remove items."
)
SCENE_TRANSITIONS = {"continue", "hard_cut", "match_cut", "intentional_camera_transition", "fade_to_black"}
CUT_TRANSITIONS = {"hard_cut", "match_cut", "fade_to_black"}


def scene_transition(clip: dict) -> str:
    value = clip.get("transition_intent")
    return value if isinstance(value, str) and value in SCENE_TRANSITIONS else "continue"


def is_scene_quality_error(error: str | None) -> bool:
    return (error or "").split(":", 1)[0] in {"native_speech_quality_failed", "visual_reference_drift", "native_audio_missing"}


def voice_instruction(value: str | dict | None) -> str:
    if isinstance(value, dict):
        value = "; ".join(f"{key}: {value[key]}" for key in VOICE_FIELDS if value.get(key))
    value = SPEECH_RATE_RE.sub("", value or "").strip()
    return value or get_voice_preset().default_voice_profile


@dataclass(frozen=True)
class ContinuityReference:
    kind: str
    identifier: str
    role: str


@dataclass(frozen=True)
class ContinuityRequest:
    # Parent URLs identify real outputs. Image fallback never sends them as video input.
    mode: str
    references: tuple[ContinuityReference, ...] = ()
    parent_generation_id: str | None = None
    native_continuation: bool = False
    voice_reference_supported: bool = False
    parent_video_url: str | None = None
    parent_asset_path: str | None = None


@dataclass(frozen=True)
class VideoContinuityStrategy:
    provider: str
    model: str = ""
    reference_mode: str = "adaptive"
    provider_capabilities: VideoProviderCapabilities | None = None

    @property
    def capabilities(self) -> VideoProviderCapabilities:
        return self.provider_capabilities or VideoProviderCapabilities.documented_fallback(self.provider, self.model)

    @property
    def requires_parent(self) -> bool:
        return self.provider in {"poyo", "seedance", "seedvis"}

    @property
    def native_audio_supported(self) -> bool:
        return self.capabilities.native_audio

    def build_initial_request(self, draft_id: str, has_anchor: bool, duration_seconds: int = 8) -> ContinuityRequest:
        if not has_anchor or self.provider == "fake":
            return ContinuityRequest("none")
        opening = ContinuityReference("scene", f"anchor-{draft_id}", "FIRST_FRAME")
        return self._image_request(draft_id, opening, initial=True, duration_seconds=duration_seconds)

    def build_continuation_request(
        self, draft_id: str, parent_id: str, *, has_anchor: bool = True,
        parent_video_url: str | None = None, parent_asset_path: str | None = None,
        duration_seconds: int = 8,
    ) -> ContinuityRequest:
        if not parent_id:
            raise ValueError("continuity_parent_required")
        if self.capabilities.video_continuation:
            # A flag alone cannot implement an extension contract or its output semantics.
            raise ValueError("native_continuation_adapter_not_implemented")
        opening = ContinuityReference("frame", parent_id, "FIRST_FRAME")
        request = self._image_request(draft_id, opening, initial=False, duration_seconds=duration_seconds) if has_anchor else ContinuityRequest("first_frame", (opening,))
        return ContinuityRequest(
            request.mode, request.references, parent_id,
            parent_video_url=parent_video_url, parent_asset_path=parent_asset_path,
        )

    def _image_request(self, draft_id: str, opening: ContinuityReference, *, initial: bool, duration_seconds: int = 8):
        if not self.capabilities.first_frame and not self.capabilities.reference_images:
            raise ValueError("continuity_images_unsupported")
        ingredients = (
            self.capabilities.reference_images
            and (not self.capabilities.reference_image_durations or duration_seconds in self.capabilities.reference_image_durations)
            and (not self.capabilities.first_frame or self.reference_mode == "reference" or (self.reference_mode == "adaptive" and not initial))
        )
        if ingredients:
            return ContinuityRequest("image_references", (
                ContinuityReference("character", draft_id, "CHARACTER_REFERENCE"),
                ContinuityReference("style", draft_id, "STYLE_REFERENCE"), opening,
            ))
        return ContinuityRequest("first_frame", (opening,))

    def validate_continuation(self, parent: dict, *, user_id: str, expected_parent_id: str | None) -> None:
        context = parent.get("payload", {}).get("spec", {}).get("request", {})
        if context.get("user_id") != user_id or parent.get("generation_id") != expected_parent_id:
            raise ValueError("continuity_parent_mismatch")
        if parent.get("status") != "COMPLETED":
            raise ValueError("continuity_parent_not_completed")

    def extract_continuation_reference(self, platform, parent_id: str):
        return platform._extract_continuity_frame(parent_id)


def build_scene_prompt(
    plan: dict, clip: dict, request: ContinuityRequest, *, native_audio: bool, product: bool = False,
) -> str:
    voice = voice_instruction(plan.get("voice_bible")) if native_audio else ""
    spoken = clean_spoken_text(clip["voice_script"]) if native_audio else ""
    if native_audio and not spoken:
        raise ValueError("native_audio_requires_scene_dialogue")
    if native_audio and (clip.get("unresolved_voice_placeholder") or STORY_PLACEHOLDER_RE.search(clip["voice_script"])):
        raise ValueError("native_audio_unresolved_scene_dialogue")
    previous = next((item for item in plan.get("clips", []) if item.get("index") == clip["index"] - 1), {})
    transition = scene_transition(clip)
    new_shot = transition in CUT_TRANSITIONS
    if (new_shot and request.parent_generation_id and request.mode == "first_frame"
            and request.references and request.references[0].kind == "frame"):
        raise ValueError("scene_cut_requires_new_opening_image_or_reference_mode")
    duration = clip.get("duration_seconds", 8)
    parts = [f"Scene {clip['index']}: one {duration}-second shot."]
    bounded_context = []

    if request.references:
        if request.mode == "product_references":
            visual = (
                "Reference image 1 is the planned scene: open on its subject, setting, wardrobe, props and framing. "
                "The other reference images are photos of the real product; use them only for the product's exact look."
            )
        elif request.mode == "image_references":
            visual = (
                "Use the character reference for identity and the style reference for visual treatment. "
                "The final image is previous-shot context, not the new opening composition. "
                "Use the planned starting state and Scene action for this new shot."
            ) if new_shot and request.parent_generation_id else (
                "Use the character reference for identity and the style reference for the setting. "
                "The final reference guides the opening pose, props and framing; image references guide "
                "appearance, not the voice or a previous recording."
            )
        else:
            visual = (
                "Use the supplied first frame as the exact opening composition. Preserve its visible "
                "subject, wardrobe, props, setting and lighting."
            ) if transition == "continue" else (
                "Use the supplied first frame as the exact opening composition. Then follow only "
                "the explicitly planned visual changes while preserving the subject identity."
            )
    else:
        visual = plan.get("visual_bible") or plan.get("visual_continuity") or "Keep the same subject and setting."
    bounded_context.append((len(parts), "Visual context: ", visual, 160))
    parts.append("Visual context: " + visual)
    look = plan.get("visual_bible") or plan.get("visual_continuity")
    if product and request.references:
        parts.append(PRODUCT_PHOTO_LOCK_RULE if request.mode == "product_references" else PRODUCT_LOCK_RULE)
        if look:
            bounded_context.append((len(parts), "Product description: ", look, 240))
            parts.append("Product description: " + look)

    parts.append("Scene action: " + clip["video_prompt"].strip())
    if new_shot:
        camera = "Begin directly in the planned new composition; the cut between clips is handled during editing."
        if transition == "fade_to_black":
            camera += " The editor adds the fade; render a fully visible shot without an in-camera fade."
    elif transition == "continue":
        camera = "Hold the existing camera framing and angle for this continuous shot."
    else:
        camera = "Follow only the planned shot or camera transition in Scene action; do not add another move."
    if clip["index"] == 1:
        camera += " Hook opening: action is already under way in the first frame with an immediate camera move."
    parts.append("Camera and transition: " + camera + f" Transition intent: {transition}.")

    start = clip.get("start_state") or (None if new_shot else previous.get("end_state")) or "Use the planned opening composition."
    if request.parent_generation_id and request.references and not new_shot:
        start = "Follow the visible pose, prop positions and framing in the supplied previous-frame image."
    end = clip.get("end_state") or "Keep a stable physical state suitable for the next opening."
    for prefix, state in (("Starting state: ", start), ("Ending state: ", end)):
        bounded_context.append((len(parts), prefix, state, 100))
        parts.append(prefix + state)

    continuity = (
        "Continuity: maintain subject appearance, wardrobe, props and lighting. "
        if transition == "continue" else
        "Continuity: preserve the subject identity and unchanged props; allow only the explicitly planned "
        "changes to framing, setting or lighting. "
    )
    parts.append(
        continuity +
        "Use natural expression and only the planned restrained action; keep the physical state stable "
        "at the boundary, without unrequested swaying, prop changes or abrupt pose resets."
        + (HELD_PROP_RULE if HELD_PROP_RE.search(" ".join((clip["video_prompt"], start, end))) else "")
    )
    if native_audio:
        voice_over = plan.get("narration_mode") == "voice_over"
        parts.extend([
            f"VOICE DESCRIPTION (not dialogue): {voice}",
            # Phrased like a person speaking, which a manual Omni Flash test showed keeps a natural pace.
            ("The unseen narrator" if voice_over else "The on-screen speaker")
            + f" says this Vietnamese line exactly once, word for word: {json.dumps(spoken, ensure_ascii=False)}.",
            "Audio: Generate picture and native audio together. "
            "Speak at a natural, fast conversational pace from the first half second and stop when the line is done; "
            "never slow down to fill the shot; expressive not flat: stress key words, vary pitch. "
            "Do not stretch vowels, cut off words or add dialogue. "
            + ("The voice is an unseen off-screen narrator: nobody on screen speaks or moves their lips; "
               "on-screen people only act and react silently."
               if voice_over else
               "For the on-screen speaker, synchronize lips with native speech and keep the face visible; "
               "no reading down or page-turning during speech."),
        ])
    else:
        parts.append("Audio: Do not generate audio; the finished video receives one continuous narration track.")

    prompt = "\n".join(parts)
    # Shorten only shared context and physical states, never scene action, voice or dialogue.
    for index, prefix, context, minimum in bounded_context:
        if len(prompt) <= 4000:
            break
        width = max(minimum, len(parts[index]) - len(prefix) - (len(prompt) - 4000))
        parts[index] = prefix + textwrap.shorten(context, width)
        prompt = "\n".join(parts)
    if len(prompt) > 4000:
        raise ValueError("continuity_prompt_too_long")
    return prompt
