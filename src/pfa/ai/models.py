import httpx
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from pfa.config import Settings


def available_models(settings: Settings) -> set[str] | None:
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
        return {str(item.get("name")) for item in response.json().get("models", [])}
    except Exception:
        return None


def local_model(settings: Settings) -> OllamaModel:
    return OllamaModel(
        settings.model,
        provider=OllamaProvider(base_url=f"{settings.ollama_base_url.rstrip('/')}/v1"),
    )
