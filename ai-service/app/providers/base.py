from abc import ABC, abstractmethod

from app.schemas.generation import ExecutionRequest, GenerationSpec
from app.schemas.provider import ProviderResult, VideoProviderCapabilities


class VideoProviderAdapter(ABC):
    name: str

    def continuity_capabilities(self) -> VideoProviderCapabilities:
        return VideoProviderCapabilities(provider=self.name, model="unknown")

    @abstractmethod
    def supports(self, spec: GenerationSpec) -> bool: ...

    @abstractmethod
    def compile(self, request: ExecutionRequest) -> dict: ...

    @abstractmethod
    async def submit(self, payload: dict) -> ProviderResult: ...

    @abstractmethod
    async def get_status(self, task_id: str) -> ProviderResult: ...

    @abstractmethod
    async def cancel(self, task_id: str) -> ProviderResult: ...

    @abstractmethod
    def normalize_result(self, response: dict) -> ProviderResult: ...
