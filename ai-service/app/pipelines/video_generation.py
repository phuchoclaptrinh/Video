from app.providers.registry import ProviderRegistry
from app.providers.seedvis import SeedvisProvider
from app.providers.seedvis_keys import SeedvisKeyPool
from app.schemas.generation import ExecutionRequest
from app.schemas.provider import ProviderError, ProviderResult


class VideoGenerationPipeline:
    def __init__(self, registry: ProviderRegistry, seedvis_keys: SeedvisKeyPool | None = None):
        self.registry = registry
        self.seedvis_keys = seedvis_keys

    def adapter(self, provider: str, model: str | None = None, credential: str | None = None):
        adapter = self.registry.get(provider, model)
        if credential is None:
            return adapter
        if not isinstance(adapter, SeedvisProvider) or self.seedvis_keys is None:
            raise ProviderError("credential_unsupported_provider")
        api_key = self.seedvis_keys.key(credential)
        if not api_key:
            # The key was removed while its task was still running; nothing else may poll it.
            raise ProviderError("seedvis_credential_missing")
        return SeedvisProvider(adapter.client, adapter.compiler.model, api_key=api_key)

    def validate(self, provider: str, request: ExecutionRequest) -> None:
        self.registry.get(provider, request.model).compile(request)

    async def submit(self, provider: str, request: ExecutionRequest, credential: str | None = None) -> ProviderResult:
        adapter = self.adapter(provider, request.model, credential)
        return await adapter.submit(adapter.compile(request))

    async def status(
        self, provider: str, task_id: str, poll_url: str | None = None, credential: str | None = None,
    ) -> ProviderResult:
        adapter = self.adapter(provider, credential=credential)
        if poll_url:
            if not isinstance(adapter, SeedvisProvider):
                raise ProviderError("poll_url_unsupported_provider")
            return await adapter.get_status(task_id, poll_url=poll_url)
        return await adapter.get_status(task_id)

    async def cancel(self, provider: str, task_id: str, credential: str | None = None) -> ProviderResult:
        return await self.adapter(provider, credential=credential).cancel(task_id)
