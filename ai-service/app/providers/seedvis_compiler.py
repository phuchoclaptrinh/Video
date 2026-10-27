import uuid

from app.providers.seedvis_models import VIDEO_MODELS
from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import ProviderError


class SeedvisCompiler:
    version = "seedvis_compiler_v1"

    def __init__(self, model: str):
        if model not in VIDEO_MODELS:
            raise ValueError("Unsupported Seedvis video model")
        self.model = model
        self.capabilities = VIDEO_MODELS[model]

    def supports(self, spec: GenerationSpec) -> bool:
        roles = [ref.role for ref in spec.references]
        valid_roles = roles in ([], ["FIRST_FRAME"], ["CHARACTER_REFERENCE", "STYLE_REFERENCE", "FIRST_FRAME"])
        valid_roles |= self.model == "Veo-3.1" and roles == ["FIRST_FRAME", "LAST_FRAME"]
        # Product mode: the planned scene image plus up to two photos of the real product, sent together as
        # reference images (Omni `images`, Veo `referenceImages`, Seedance `reference_images`), never a start frame.
        valid_roles |= (roles[:1] == ["STYLE_REFERENCE"] and 2 <= len(roles) <= 3
                        and set(roles[1:]) == {"PRODUCT_REFERENCE"})
        return (valid_roles and spec.output.aspect_ratio in self.capabilities.ratios
                # A model without a duration field ignores the requested length instead of rejecting it.
                and (not self.capabilities.durations or not self.capabilities.duration_parameter
                     or spec.output.duration_seconds in self.capabilities.durations)
                and spec.output.resolution_preference in ({"720p", "1080p"} if self.model == "Veo-3.1" else {"720p"}))

    def compile(self, request: ExecutionRequest) -> dict:
        spec = request.spec
        if not self.supports(spec):
            raise ProviderError("unsupported_capability")
        if "generate_audio" in spec.output.model_fields_set:
            if not spec.output.generate_audio:
                raise ProviderError("native_audio_disable_unsupported_model")
            if not self.capabilities.native_audio:
                raise ProviderError("native_audio_unverified_model")
        parts = [spec.intent.goal]
        for shot in spec.story.shots:
            for section in ("subject", "environment", "camera", "style"):
                details = getattr(shot, section)
                if details:
                    parts.append(section + ": " + ", ".join(f"{k}={v}" for k, v in sorted(details.items())))
        if spec.constraints.preserve_identity:
            parts.append("Preserve the subject's identity and appearance.")
        if spec.constraints.avoid_text_overlay:
            parts.append("Do not add text overlays.")
        if spec.constraints.negative_prompt:
            parts.append("Avoid: " + spec.constraints.negative_prompt)
        body = {"model": self.model, "prompt": "\n".join(parts), "aspect_ratio": spec.output.aspect_ratio, "count": 1}
        if self.model == "Veo-3.1":
            # v2.0 has no resolution or upscale field for Veo; a 1080p preference renders at the default size.
            body["duration"] = f"{spec.output.duration_seconds}s"
        elif self.capabilities.durations and self.capabilities.duration_parameter:
            body["duration"] = spec.output.duration_seconds
        assets = {asset.asset_id: asset.source_url for asset in request.assets}
        images = [assets[ref.asset_id] for ref in spec.references]
        roles = [ref.role for ref in spec.references]
        body["mode"] = "text-to-video"
        if images:
            if self.model.startswith("seedance_"):
                body.update(mode="image-to-video", reference_images=images)
            elif roles == ["FIRST_FRAME", "LAST_FRAME"]:
                body.update(mode="batch-frame", image=images[0], lastFrame=images[1])
            elif len(images) == 1:
                body.update(mode="image-to-video", image=images[0])
            else:
                body["mode"] = "multi-image-to-video"
                body["referenceImages" if self.model == "Veo-3.1" else "images"] = images
        # Same logical execution retains its key across retries and process restarts.
        context = spec.request
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"vo3:seedvis:{context.tenant_id}:{context.user_id}:{context.request_id}"))
        return {"body": body, "idempotency_key": key, "reference_count": len(images)}
