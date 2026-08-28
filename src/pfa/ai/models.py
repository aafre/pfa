from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from pfa.config import Settings


def local_model(settings: Settings) -> OllamaModel:
    return OllamaModel(
        settings.model,
        provider=OllamaProvider(base_url=f"{settings.ollama_base_url.rstrip('/')}/v1"),
    )
