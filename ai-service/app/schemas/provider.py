from typing import Literal

from pydantic import Field, HttpUrl

from app.schemas.generation import Contract, Identifier


class VideoOutput(Contract):
    media_type: Literal["video"] = "video"
    source_url: HttpUrl
    mime_type: Literal["video/mp4"] = "video/mp4"


class VideoProviderCapabilities(Contract):
    provider: str
    model: str
    native_audio: bool = False
    audio_control: bool = False
    first_frame: bool = False
    reference_images: bool = False
    video_continuation: bool = False
    continuation_requires_video_url: bool = False
    voice_identity_guaranteed: bool = False
    generation_durations: list[int] = Field(default_factory=list)
    reference_image_durations: list[int] = Field(default_factory=list)
    continuation_durations: list[int] = Field(default_factory=list)
    continuation_output: Literal["UNSUPPORTED", "SEGMENT", "CUMULATIVE"] = "UNSUPPORTED"
    reason: str = "no_verified_continuation_contract"
    evidence_url: str | None = None


class ProviderResult(Contract):
    provider: str
    provider_task_id: Identifier
    status: Literal["queued", "generating", "completed", "failed", "cancelled"]
    outputs: list[VideoOutput] = Field(default_factory=list)
    usage: dict[str, int | float | None] = Field(default_factory=dict)
    error_code: str | None = None
    error_detail: str | None = Field(default=None, max_length=240)
    poll_after_seconds: float | None = Field(default=None, ge=1, le=120)
    poll_url: str | None = Field(default=None, max_length=2048)


class ProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, ambiguous: bool = False,
                 retry_after_seconds: float | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after_seconds = retry_after_seconds
