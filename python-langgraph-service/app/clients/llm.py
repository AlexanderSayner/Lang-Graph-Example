"""
Provider-agnostic LLM layer.

Every AI node of a graph, and the UI copilot, talk to one tiny interface::

    response = await client.generate(user_message=..., system_message=...)

``response`` behaves like a plain string holding the model text (so the existing
node handlers keep working) and additionally exposes::

    response.content    -> the assistant text
    response.usage      -> {"input_tokens": .., "completion_tokens": .., "total_tokens": ..}
    response.provider   -> provider id that answered
    response.model      -> model id that answered

Providers are backed by LangChain chat models:

* OpenAI-compatible endpoints - OpenAI, Groq, DeepSeek, OpenRouter (``ChatOpenAI``)
* Anthropic / Claude (``ChatAnthropic``)
* YandexGPT (the pre-existing custom HTTP client)

A graph picks a provider in three steps, from most to least specific:

1. node metadata   - ``llm_provider`` / ``llm_model`` / ``llm_temperature`` / ``llm_max_tokens``
2. graph config    - ``llm_provider`` / ``llm_model`` / ...
3. service default - ``LLM_PROVIDER`` / ``LLM_MODEL`` / ... settings
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings

logger = logging.getLogger(__name__)


class LLMConfigError(ValueError):
    """The provider/model selection itself is invalid (e.g. an unknown provider).

    Raised while a graph is compiled, so a typo in a graph fails fast and loud
    instead of silently falling back to another provider.
    """


class MissingApiKeyError(LLMConfigError):
    """The selected provider has no API key configured in the service settings.

    Raised when a provider client is actually built. Graph compilation goes through
    :class:`LazyLLMClient`, which defers construction to the first model call, so a
    missing key never blocks a graph from being built or saved - it surfaces as a
    node error at run time, exactly like it did before the multi-provider layer.
    """


class LLMResponse(str):
    """Model answer: a plain string that also carries token usage and provenance."""

    def __new__(cls, text: str, usage: Optional[Dict[str, int]] = None,
                provider: str = "", model: str = ""):
        instance = super().__new__(cls, text)
        instance.content = text
        instance.usage = usage or {}
        instance.provider = provider
        instance.model = model
        return instance


# Historical name kept as an alias (the Yandex client used to define this).
GenerationResult = LLMResponse


def _flatten_content(content: Any) -> str:
    """LangChain returns a plain string for most providers, but Anthropic (and some
    OpenAI-compatible gateways) may answer with a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _usage_from_metadata(usage_metadata: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Normalise LangChain's ``usage_metadata`` to the Yandex-compatible shape."""
    usage_metadata = usage_metadata or {}
    input_tokens = int(usage_metadata.get("input_tokens") or 0)
    output_tokens = int(usage_metadata.get("output_tokens") or 0)
    total = usage_metadata.get("total_tokens")
    total_tokens = int(total) if total is not None else input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


@dataclass(frozen=True)
class ProviderPreset:
    kind: str            # "openai" | "anthropic" | "yandex"
    base_url: str
    default_model: str
    api_key_setting: str
    api_key_env: str


# OpenAI, Groq, DeepSeek and OpenRouter all speak the OpenAI chat-completions
# protocol, so they share one client implementation and differ only by base URL.
PROVIDER_PRESETS: Dict[str, ProviderPreset] = {
    "yandex": ProviderPreset("yandex", "", "yandexgpt", "YC_API_KEY", "YC_API_KEY"),
    "openai": ProviderPreset("openai", "https://api.openai.com/v1", "gpt-4o-mini",
                             "OPENAI_API_KEY", "OPENAI_API_KEY"),
    "groq": ProviderPreset("openai", "https://api.groq.com/openai/v1",
                           "llama-3.3-70b-versatile", "GROQ_API_KEY", "GROQ_API_KEY"),
    "deepseek": ProviderPreset("openai", "https://api.deepseek.com/v1", "deepseek-chat",
                               "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "openrouter": ProviderPreset("openai", "https://openrouter.ai/api/v1",
                                 "openai/gpt-4o-mini", "OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    "anthropic": ProviderPreset("anthropic", "https://api.anthropic.com",
                                "claude-3-5-sonnet-latest", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
}

# Friendly aliases accepted in node metadata / graph config.
PROVIDER_ALIASES = {
    "claude": "anthropic",
    "open-router": "openrouter",
    "open_router": "openrouter",
    "ds": "deepseek",
}

# Env/setting name that carries the *per-provider* model override. Yandex keeps its
# historical YC_MODEL_NAME; every other provider derives f"{PROVIDER.upper()}_MODEL"
# (OPENAI_MODEL / GROQ_MODEL / ...), so no entry is needed for them here.
PROVIDER_MODEL_SETTINGS: Dict[str, str] = {
    "yandex": "YC_MODEL_NAME",
}


def _pick(*values: Any) -> Optional[str]:
    """First non-empty value as a stripped string (metadata/config values arrive as strings)."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_provider(value: Any) -> str:
    """Alias-aware, case-insensitive normalisation of a provider id ("" when unset)."""
    text = _pick(value) or ""
    return PROVIDER_ALIASES.get(text.lower(), text.lower())


def _provider_model_setting(provider: str) -> str:
    """Setting name holding the per-provider model override (see PROVIDER_MODEL_SETTINGS)."""
    return PROVIDER_MODEL_SETTINGS.get(provider, f"{provider.upper()}_MODEL")


def _to_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        logger.warning("Invalid llm temperature %r, falling back to %s", value, default)
        return default


def _to_int(value: Optional[str], default: int) -> int:
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        logger.warning("Invalid llm max tokens %r, falling back to %s", value, default)
        return default


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int


def resolve_llm_config(node_metadata: Optional[Dict[str, Any]] = None,
                       graph_config: Optional[Dict[str, Any]] = None) -> LLMConfig:
    """Combine node metadata, graph config and service defaults into one config.

    Values arriving over gRPC/JSON are strings, so temperature/max_tokens are parsed
    leniently. An unknown provider raises immediately, so a typo in a graph surfaces
    when the graph is compiled instead of silently falling back to another provider.

    Model precedence is slightly subtler than the provider one: ``LLM_MODEL`` is the model
    of the *service default* provider, so it is only consulted when the resolved provider
    IS that default. Otherwise a graph/node that switches provider would inherit a model
    name belonging to another provider (e.g. ``LLM_MODEL=yandexgpt-lite`` sent to Groq).
    The per-provider setting (``YC_MODEL_NAME`` / ``<PROVIDER>_MODEL``) comes next.
    """
    node_metadata = node_metadata or {}
    graph_config = graph_config or {}

    provider = _normalize_provider(_pick(
        node_metadata.get("llm_provider"),
        graph_config.get("llm_provider"),
        settings.LLM_PROVIDER,
    ) or "yandex")

    if provider not in PROVIDER_PRESETS:
        raise LLMConfigError(
            f"Unknown LLM provider '{provider}'. Supported: {', '.join(sorted(PROVIDER_PRESETS))}"
        )

    preset = PROVIDER_PRESETS[provider]
    # `LLM_MODEL` describes the service default provider; a node/graph that selects a
    # different provider must not inherit a model name from an unrelated provider.
    default_provider = _normalize_provider(settings.LLM_PROVIDER) or "yandex"
    service_model = settings.LLM_MODEL if provider == default_provider else ""

    model = _pick(
        node_metadata.get("llm_model"),
        graph_config.get("llm_model"),
        service_model,
        getattr(settings, _provider_model_setting(provider), ""),
        preset.default_model,
    )

    temperature = _to_float(_pick(
        node_metadata.get("llm_temperature"),
        graph_config.get("llm_temperature"),
        settings.LLM_TEMPERATURE,
    ), settings.LLM_TEMPERATURE)

    max_tokens = _to_int(_pick(
        node_metadata.get("llm_max_tokens"),
        graph_config.get("llm_max_tokens"),
        settings.LLM_MAX_OUTPUT_TOKENS,
    ), settings.LLM_MAX_OUTPUT_TOKENS)

    return LLMConfig(provider=provider, model=model or preset.default_model,
                     temperature=temperature, max_tokens=max_tokens)


class LLMClient:
    """Uniform async chat client; the concrete LangChain model stays an implementation detail."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider = config.provider
        self.model = config.model
        self._yandex = None
        self._runnable = self._build_runnable()

    def _api_key(self, preset: ProviderPreset) -> str:
        return str(getattr(settings, preset.api_key_setting, "") or "").strip()

    def _base_url(self, preset: ProviderPreset) -> str:
        override = getattr(settings, f"{self.provider.upper()}_BASE_URL", "") or ""
        return str(override).strip() or preset.base_url

    def _build_runnable(self):
        preset = PROVIDER_PRESETS[self.provider]
        api_key = self._api_key(preset)

        if preset.kind == "yandex":
            if not api_key:
                raise MissingApiKeyError(f"No API key for LLM provider 'yandex'. Set {preset.api_key_env}.")
            # Imported lazily: the Yandex client imports this module's response type.
            from app.clients.yandex_client import YandexGPTClient
            logger.info("LLM client: yandex model=%s folder=%s", self.config.model, settings.YC_FOLDER_ID)
            self._yandex = YandexGPTClient(
                api_key=api_key,
                folder_id=settings.YC_FOLDER_ID,
                model_name=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            return None

        if not api_key:
            raise MissingApiKeyError(
                f"No API key for LLM provider '{self.provider}'. Set {preset.api_key_env}."
            )

        base_url = self._base_url(preset)

        if preset.kind == "anthropic":
            from langchain_anthropic import ChatAnthropic
            logger.info("LLM client: anthropic model=%s base_url=%s", self.config.model, base_url)
            return ChatAnthropic(
                model=self.config.model,
                anthropic_api_key=api_key,
                anthropic_api_url=base_url or None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

        from langchain_openai import ChatOpenAI
        logger.info("LLM client: %s (openai-compatible) model=%s base_url=%s",
                    self.provider, self.config.model, base_url)
        return ChatOpenAI(
            model=self.config.model,
            api_key=api_key,
            base_url=base_url or None,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    async def generate(self, user_message: str, system_message: str = "") -> LLMResponse:
        """Send one chat turn and return the answer text plus normalised token usage."""
        if self._yandex is not None:
            return await self._yandex.generate(user_message=user_message, system_message=system_message)

        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=user_message))

        message = await self._runnable.ainvoke(messages)
        text = _flatten_content(getattr(message, "content", message))
        usage = _usage_from_metadata(getattr(message, "usage_metadata", None))
        logger.info("LLM %s/%s answered: %d tokens", self.provider, self.model, usage["total_tokens"])
        return LLMResponse(text, usage, self.provider, self.model)


class LazyLLMClient:
    """A client whose provider/model is resolved only on the first model call.

    Graph compilation must not depend on credentials: a graph can be built and saved
    without an API key, and the error surfaces when the node actually calls the model -
    the same timing the pre-multi-provider code had. A key added later is also picked up
    without restarting the service.
    """

    def __init__(self, registry: "LLMClientRegistry", config: LLMConfig):
        self._registry = registry
        self._config = config
        self._client: Optional[LLMClient] = None

    @property
    def config(self) -> LLMConfig:
        return self._config

    @property
    def provider(self) -> str:
        return self._config.provider

    @property
    def model(self) -> str:
        return self._config.model

    def _resolve(self) -> LLMClient:
        if self._client is None:
            self._client = self._registry.get(self._config)
        return self._client

    async def generate(self, user_message: str, system_message: str = "") -> LLMResponse:
        return await self._resolve().generate(user_message=user_message,
                                              system_message=system_message)


class LLMClientRegistry:
    """Builds one client per distinct config and reuses it (keeps HTTP pools warm)."""

    def __init__(self):
        self._clients: Dict[LLMConfig, LLMClient] = {}

    def lazy(self, config: LLMConfig) -> LazyLLMClient:
        """Deferred client: the underlying provider client is built on first use.

        Use this anywhere a graph definition is being compiled, so that a missing API
        key cannot fail a build.
        """
        return LazyLLMClient(self, config)

    def get(self, config: LLMConfig) -> LLMClient:
        client = self._clients.get(config)
        if client is None:
            client = LLMClient(config)
            self._clients[config] = client
            logger.info("Registered LLM client %s/%s (temp=%s, max_tokens=%s)",
                        config.provider, config.model, config.temperature, config.max_tokens)
        return client
