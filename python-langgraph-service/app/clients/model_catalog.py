"""Model catalog: which models a given LLM provider offers.

Used by the UI's model selectors so users can pick from real model ids instead of guessing.
Providers that expose an OpenAI-style ``/models`` endpoint (or Anthropic's ``/v1/models``) are
queried live and the answer is cached for a short TTL. YandexGPT has no equivalent list
endpoint, so a curated list is returned for it - and curated is also the fallback whenever a
live list cannot be obtained (missing key, network error, unsupported endpoint), so the
selector always has something to show and a suggestion list never fails loudly.

Everything here is server-side: the provider API keys stay in the service and never reach the
browser.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

import httpx
from cachetools import TTLCache

from app.clients.llm import PROVIDER_PRESETS, _normalize_provider
from app.config import settings

logger = logging.getLogger(__name__)

# Curated fallback lists. YandexGPT has no list endpoint, so this is authoritative there; for
# the other providers it is only a safety net when a live list is unavailable (or when no key
# is configured yet).
CURATED_MODELS: Dict[str, List[Tuple[str, str]]] = {
    "yandex": [
        ("yandexgpt", "YandexGPT"),
        ("yandexgpt-lite", "YandexGPT Lite"),
        ("yandexgpt-5", "YandexGPT 5"),
        ("yandexgpt-5-lite", "YandexGPT 5 Lite"),
    ],
    "openai": [
        ("gpt-4o-mini", "GPT-4o mini"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1-mini", "GPT-4.1 mini"),
    ],
    "groq": [("llama-3.3-70b-versatile", "LLaMA 3.3 70B Versatile")],
    "deepseek": [
        ("deepseek-chat", "DeepSeek Chat"),
        ("deepseek-reasoner", "DeepSeek Reasoner"),
    ],
    "openrouter": [("openai/gpt-4o-mini", "GPT-4o mini (OpenRouter)")],
    "anthropic": [
        ("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet"),
        ("claude-3-5-haiku-latest", "Claude 3.5 Haiku"),
    ],
}

# Anthropic requires this header on every request, including /models.
_ANTHROPIC_VERSION = "2023-06-01"

# Cap so a provider with hundreds of models (OpenRouter) does not overwhelm the <datalist>.
_MAX_MODELS = 200


@dataclass(frozen=True)
class ModelCatalogResult:
    """``models`` is a list of ``(id, label)`` pairs; ``source`` is "live" or "curated"."""

    models: List[Tuple[str, str]]
    source: str
    success: bool
    error_message: str = ""


# provider -> ModelCatalogResult, only ever populated with live results.
_cache: TTLCache = TTLCache(maxsize=64, ttl=max(1, int(settings.LLM_MODELS_CACHE_TTL_SECONDS)))


def _curated_models(provider: str) -> List[Tuple[str, str]]:
    return CURATED_MODELS.get(provider, [])


async def _fetch_live(provider: str, preset) -> List[Tuple[str, str]]:
    """Ask the provider for its model list. Raises on any failure (caller falls back)."""
    api_key = getattr(settings, preset.api_key_setting, "") or ""
    if not api_key:
        # Raised before any network call, so a keyless deployment costs nothing to poll.
        raise RuntimeError(f"no API key configured for provider '{provider}' (set {preset.api_key_env})")

    headers = {"Accept": "application/json"}
    if preset.kind == "anthropic":
        url = f"{preset.base_url.rstrip('/')}/v1/models"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = _ANTHROPIC_VERSION
    else:  # kind == "openai" - OpenAI, Groq, DeepSeek, OpenRouter
        url = f"{preset.base_url.rstrip('/')}/models"
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(settings.LLM_MODELS_HTTP_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()

    entries = payload.get("data") or []
    models: List[Tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not model_id:
            continue
        # OpenRouter uses "name", Anthropic "display_name"; OpenAI/Groq/DeepSeek give only ids.
        label = entry.get("name") or entry.get("display_name") or model_id
        models.append((str(model_id), str(label)))

    if not models:
        raise RuntimeError(f"provider '{provider}' returned an empty model list")
    return sorted(models)[:_MAX_MODELS]


async def list_models(provider: str) -> ModelCatalogResult:
    """Return the models ``provider`` offers - live when possible, curated otherwise.

    Never raises: an unknown provider, a missing key, a timeout or an unexpected payload all
    resolve to a (possibly empty) curated list plus a message, so a suggestion dropdown can
    always render something and a user can still type an arbitrary model id by hand.
    """
    provider = _normalize_provider(provider) or ""
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        return ModelCatalogResult(
            models=[],
            source="curated",
            success=False,
            error_message=f"Unknown LLM provider '{provider}'. Supported: "
                          + ", ".join(sorted(PROVIDER_PRESETS)),
        )

    if preset.kind == "yandex":
        # YandexGPT models are addressed by name in the modelUri; there is no list endpoint.
        return ModelCatalogResult(models=_curated_models(provider), source="curated", success=True)

    cached = _cache.get(provider)
    if cached is not None:
        return cached

    try:
        result = ModelCatalogResult(models=await _fetch_live(provider, preset), source="live", success=True)
    except Exception as exc:  # noqa: BLE001 - a suggestion list must never fail loudly
        logger.warning("Live model list for '%s' failed (%s); using the curated fallback", provider, exc)
        # Only live successes are cached, so a transient blip recovers on the next attempt
        # without hammering a provider that is genuinely down.
        return ModelCatalogResult(
            models=_curated_models(provider),
            source="curated",
            success=True,
            error_message=str(exc),
        )

    _cache[provider] = result
    return result
