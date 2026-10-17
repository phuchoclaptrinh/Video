from app.providers.base import VideoProviderAdapter
from app.providers.poyo import PoyoVeoProvider
from app.providers.seedvis import SeedvisProvider
from app.schemas.provider import ProviderError


class ProviderRegistry:
    def __init__(self, providers: list[VideoProviderAdapter]):
        self.providers = {provider.name: provider for provider in providers}
        if len(self.providers) != len(providers):
            raise ValueError("Duplicate provider registration")

    def get(self, name: str, model: str | None = None) -> VideoProviderAdapter:
        try:
            adapter = self.providers[name]
        except KeyError:
            raise ProviderError("unknown_provider") from None
        if model is None or model == adapter.continuity_capabilities().model:
            return adapter
        if isinstance(adapter, (PoyoVeoProvider, SeedvisProvider)):
            # Share the credential-bearing HTTP client, never mutate the default compiler.
            try:
                return type(adapter)(adapter.client, model)
            except ValueError:
                raise ProviderError("unsupported_model") from None
        raise ProviderError("unsupported_model")
