"""Execution contract v1; policy and asset resolution belong to upstream services."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequestContext(Contract):
    request_id: Identifier
    user_id: Identifier
    tenant_id: Identifier


class Intent(Contract):
    operation: Literal["TEXT_GENERATION", "REFERENCE_GENERATION"]
    goal: Text


class Output(Contract):
    media_type: Literal["video"] = "video"
    duration_seconds: int = Field(default=5, ge=1, le=120, strict=True)
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] = "16:9"
    resolution_preference: Literal["480p", "720p", "1080p"] = "720p"
    generate_audio: bool = Field(default=False, strict=True)


class Reference(Contract):
    asset_id: Identifier
    role: Literal["FIRST_FRAME", "LAST_FRAME", "CHARACTER_REFERENCE", "STYLE_REFERENCE", "PRODUCT_REFERENCE"]
    strength: Literal["low", "medium", "high"] = "high"


class Shot(Contract):
    shot_id: Identifier
    subject: dict[str, Text] = Field(default_factory=dict)
    environment: dict[str, Text] = Field(default_factory=dict)
    camera: dict[str, Text] = Field(default_factory=dict)
    style: dict[str, Text] = Field(default_factory=dict)


class Story(Contract):
    shots: list[Shot] = Field(default_factory=list, max_length=1)


class Constraints(Contract):
    preserve_identity: bool = False
    avoid_text_overlay: bool = True
    negative_prompt: str = Field(default="", max_length=2000)


class Preferences(Contract):
    priority: Literal["quality", "speed", "cost"] = "quality"
    max_attempts: int = Field(default=3, ge=1, le=5)


class GenerationSpec(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request: RequestContext
    intent: Intent
    output: Output = Field(default_factory=Output)
    references: list[Reference] = Field(default_factory=list, max_length=3)
    story: Story = Field(default_factory=Story)
    constraints: Constraints = Field(default_factory=Constraints)
    execution_preferences: Preferences = Field(default_factory=Preferences)

    @model_validator(mode="after")
    def compatible_references(self):
        if bool(self.references) != (self.intent.operation == "REFERENCE_GENERATION"):
            raise ValueError("References must match the requested operation")
        if len({r.asset_id for r in self.references}) != len(self.references):
            raise ValueError("Duplicate reference assets")
        if self.constraints.preserve_identity and not self.references:
            raise ValueError("Identity preservation requires a reference")
        return self


class ResolvedAsset(Contract):
    asset_id: Identifier
    media_type: Literal["image"] = "image"
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    source_url: str = Field(min_length=10, max_length=12_000_000)
    moderation_status: Literal["APPROVED"]

    @field_validator("source_url")
    @classmethod
    def supported_source(cls, value: str) -> str:
        if value.startswith("https://"):
            return value
        if value.startswith("data:image/jpeg;base64,"):
            return value
        raise ValueError("Asset source must be HTTPS or a JPEG data URI")


class ExecutionRequest(Contract):
    model: Identifier | None = None
    spec: GenerationSpec
    assets: list[ResolvedAsset] = Field(default_factory=list, max_length=3)
    # Only trusted backend code may provide this assertion after policy preflight.
    preflight_decision: Literal["ALLOW"]
    policy_decision_id: Identifier

    @model_validator(mode="after")
    def resolved_assets(self):
        ids = [a.asset_id for a in self.assets]
        if len(ids) != len(set(ids)) or set(ids) != {r.asset_id for r in self.spec.references}:
            raise ValueError("Every reference must resolve to exactly one approved asset")
        return self
