from dataclasses import dataclass

from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import ProviderError


@dataclass(frozen=True)
class SeedanceCapabilities:
    min_duration: int = 4
    max_duration: int = 12
    resolutions: tuple[str, ...] = ("480p", "720p", "1080p")
    ratios: tuple[str, ...] = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
    reference_roles: tuple[str, ...] = ("FIRST_FRAME", "LAST_FRAME")

    def supports(self, spec: GenerationSpec) -> bool:
        roles = [ref.role for ref in spec.references]
        return (
            self.min_duration <= spec.output.duration_seconds <= self.max_duration
            and spec.output.resolution_preference in self.resolutions
            and spec.output.aspect_ratio in self.ratios
            and all(role in self.reference_roles for role in roles)
            and len(roles) == len(set(roles))
            and (not roles or "FIRST_FRAME" in roles)
        )


class SeedanceCompiler:
    version = "seedance_compiler_v1"

    def __init__(self, model: str, capabilities: SeedanceCapabilities | None = None):
        self.model = model
        self.capabilities = capabilities or SeedanceCapabilities()

    def compile(self, request: ExecutionRequest) -> dict:
        spec = request.spec
        if not self.capabilities.supports(spec):
            raise ProviderError("unsupported_capability")
        if spec.output.generate_audio and not self.model.startswith("seedance-1-5-pro"):
            raise ProviderError("native_audio_unsupported_model")
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
        content = [{"type": "text", "text": "\n".join(sentences)}]
        assets = {asset.asset_id: asset for asset in request.assets}
        for ref in spec.references:
            content.append(
                {
                    "type": "image_url",
                    "role": ref.role.lower(),
                    "image_url": {"url": str(assets[ref.asset_id].source_url)},
                }
            )
        return {
            "model": self.model,
            "content": content,
            "duration": spec.output.duration_seconds,
            "ratio": spec.output.aspect_ratio,
            "resolution": spec.output.resolution_preference,
            "generate_audio": spec.output.generate_audio,
        }
