from dataclasses import dataclass
from typing import ClassVar

from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import ProviderError


@dataclass(frozen=True)
class PoyoVeoCapabilities:
    generation_durations: tuple[int, ...] = (8,)
    audio_control: bool = False
    first_frame: bool = True
    reference_images: bool = False
    resolutions: tuple[str, ...] = ("720p", "1080p")
    ratios: tuple[str, ...] = ("16:9", "9:16")

    @classmethod
    def for_model(cls, model: str):
        official = model.endswith("-official")
        return cls(
            generation_durations=(4, 6, 8) if official else (8,),
            audio_control=official, first_frame=model != "veo3.1-lite",
            reference_images=model in {"veo3.1-fast", "veo3.1-fast-official", "veo3.1-quality-official"},
        )

    def supports(self, spec: GenerationSpec, *, images_enabled: bool, model: str = "veo3.1-fast") -> bool:
        roles = [ref.role for ref in spec.references]
        valid_roles = roles in ([], ["FIRST_FRAME"], ["FIRST_FRAME", "LAST_FRAME"])
        valid_roles |= self.reference_images and roles == [
            "CHARACTER_REFERENCE", "STYLE_REFERENCE", "FIRST_FRAME"
        ]
        return (
            spec.output.duration_seconds in self.generation_durations
            and spec.output.resolution_preference in self.resolutions
            and spec.output.aspect_ratio in self.ratios
            and (images_enabled or not roles)
            and (self.first_frame or not roles)
            and valid_roles
            and (len(roles) != 3 or spec.output.duration_seconds == 8)
            and not (model == "veo3.1-lite-official" and spec.output.duration_seconds != 8
                     and (len(roles) == 2 or spec.output.resolution_preference == "1080p"))
        )


class PoyoVeoCompiler:
    version = "poyo_veo_compiler_v3"
    models: ClassVar[set[str]] = {
        "veo3.1-fast", "veo3.1-lite", "veo3.1-quality",
        "veo3.1-fast-official", "veo3.1-lite-official", "veo3.1-quality-official",
    }

    def __init__(self, model: str, capabilities: PoyoVeoCapabilities | None = None):
        if model not in self.models:
            raise ValueError("Unsupported PoYo VEO model")
        self.model = model
        self.capabilities = capabilities or PoyoVeoCapabilities.for_model(model)

    def compile(self, request: ExecutionRequest) -> dict:
        spec = request.spec
        if spec.references and self.model == "veo3.1-lite":
            raise ProviderError("model_does_not_support_image_input")
        if not self.capabilities.supports(spec, images_enabled=True, model=self.model):
            raise ProviderError("unsupported_capability")
        audio_explicit = "generate_audio" in spec.output.model_fields_set
        if audio_explicit and not spec.output.generate_audio and not self.capabilities.audio_control:
            raise ProviderError("native_audio_disable_unsupported_model")

        sentences = [spec.intent.goal]
        for shot in spec.story.shots:
            for section in ("subject", "environment", "camera", "style"):
                details = getattr(shot, section)
                if details:
                    sentences.append(
                        section
                        + ": "
                        + ", ".join(f"{key}={value}" for key, value in sorted(details.items()))
                    )
        if spec.constraints.preserve_identity:
            sentences.append("Preserve the subject's identity and appearance.")
        if spec.constraints.avoid_text_overlay:
            sentences.append("Do not add text overlays.")
        if spec.constraints.negative_prompt:
            sentences.append("Avoid: " + spec.constraints.negative_prompt)

        input_payload = {
            "prompt": "\n".join(sentences),
            "duration": spec.output.duration_seconds,
            "aspect_ratio": spec.output.aspect_ratio,
            "resolution": spec.output.resolution_preference,
        }
        if self.capabilities.audio_control:
            # Omitted legacy audio preference keeps the provider's documented default.
            input_payload["sound"] = bool(spec.output.generate_audio) if audio_explicit else True
        if spec.references:
            assets = {asset.asset_id: asset for asset in request.assets}
            urls = [str(assets[ref.asset_id].source_url) for ref in spec.references]
            # Inline JPEGs are uploaded to PoYo storage by the adapter before submission.
            if not all(url.startswith(("https://", "data:image/jpeg;base64,")) for url in urls):
                raise ProviderError("poyo_requires_public_image_url")
            input_payload["image_urls"] = urls
            if len(urls) == 2:
                input_payload["generation_type"] = "frame"
            elif len(urls) == 3:
                input_payload["generation_type"] = "reference"
        return {"model": self.model, "input": input_payload}
