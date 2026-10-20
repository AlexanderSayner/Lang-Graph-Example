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

import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

from cachetools import LRUCache
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings

logger = logging.getLogger(__name__)


class LLMConfigError(ValueError):
    """The provider/model selection itself is invalid (e.g. an unknown provider).

    Raised while a graph is compiled, so a typo in a graph fails fast and loud
    instead of silently falling back to another provider.
    """


class LLMRequestError(ValueError):
    """A request to the model provider failed at run time (timeout, connection refused,
    HTTP error). Raised from the LLM client and mapped to a user-facing message in the
    graph engine, so a chat stays readable instead of echoing a stack dump.
    """


class MissingApiKeyError(LLMConfigError):
    """The selected provider has no API key configured in the service settings.

    Raised when a provider client is actually built. Graph compilation goes through
    :class:`LazyLLMClient`, which defers construction to the first model call, so a
    missing key never blocks a graph from being built or saved - it surfaces as a
    node error at run time, exactly like it did before the multi-provider layer.
    """


class MissingUserApiKeyError(MissingApiKeyError):
    """The request required a personal key and the user has none for this provider.

    Only raised when the caller explicitly asked for it (``require_user_key`` in the
    run's ``configurable``, set by the backend under ``llm.credentials.policy=REQUIRE``).
    Under the default FALLBACK policy a missing personal key is not an error: the
    service key is used and the run is reported as ``billing=service`` instead.
    """


def missing_user_key_message(provider: str) -> str:
    """The one wording a user sees when policy=REQUIRE and they have no key yet.

    Shared by the graph-node path and the copilot so the instruction is identical
    wherever the run happens to stop.
    """
    return (f"No personal API key saved for provider '{provider}', and this request "
            f"requires one (llm.credentials.policy=REQUIRE). Add it with the "
            f"'🔑 Keys' button in the sidebar.")


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
class LLMCredentials:
    """Per-request credentials supplied by the caller (a user's own token).

    Empty fields mean "inherit from service settings" - this is what makes the
    migration additive: an all-empty LLMCredentials behaves exactly like None.

    Frozen and hashable so it can participate in ``LLMConfig`` equality, which is what
    lets :class:`LLMClientRegistry` cache one client per (config, credentials) pair.
    ``repr=False`` because a dataclass repr would otherwise print the API key into logs
    and tracebacks.
    """
    api_key: str = ""
    folder_id: str = ""      # YandexGPT only: YC_FOLDER_ID equivalent

    def is_empty(self) -> bool:
        return not (self.api_key.strip() or self.folder_id.strip())


# One entry: (normalised provider id, credentials for that provider).
_CredentialEntry = Tuple[str, "LLMCredentials"]


@dataclass(frozen=True)
class LLMCredentialSet:
    """Every personal key the caller supplied, keyed by provider id.

    A single request carries *all* of a user's keys rather than one, because a graph
    resolves its provider per NODE (node metadata > graph config > service default): the
    same run can spend tokens on OpenAI and Anthropic, and Spring cannot know which nodes
    will ask which provider. The engine picks the entry matching the provider it resolved
    (``for_provider``), so a missing entry simply means "no personal key for this provider"
    and the service key is used.

    Frozen with a tuple of pairs so it is hashable and immutable; it never leaves the
    engine unencrypted, and it is never logged.
    """
    by_provider: Tuple[_CredentialEntry, ...] = ()

    @classmethod
    def empty(cls) -> "LLMCredentialSet":
        return cls()

    @classmethod
    def from_json(cls, raw: Any) -> "LLMCredentialSet":
        """Parse the ``llm_credentials_json`` context value. Never raises.

        Tolerant by design: the caller may be an older backend that sends nothing, a
        malformed string, or an object with unknown providers. Anything unusable
        degrades to "no personal keys" rather than failing the run.
        """
        if not raw or not str(raw).strip():
            return cls()
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, TypeError):
            logger.warning("llm_credentials_json is not valid JSON; ignoring personal keys")
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Any) -> "LLMCredentialSet":
        """Build a set from ``{provider: {api_key, folder_id}}``; malformed entries are dropped."""
        if not isinstance(data, dict):
            return cls()
        entries = []
        for provider, value in data.items():
            provider_id = _normalize_provider(provider)
            if not provider_id:
                continue
            if not isinstance(value, dict):
                continue
            credentials = LLMCredentials(
                api_key=str(value.get("api_key") or "").strip(),
                folder_id=str(value.get("folder_id") or "").strip(),
            )
            if credentials.is_empty():
                continue
            entries.append((provider_id, credentials))
        return cls(by_provider=tuple(entries))

    def for_provider(self, provider: Any) -> Optional[LLMCredentials]:
        """Credentials for ``provider`` (alias-aware) or None when the user has none."""
        wanted = _normalize_provider(provider)
        if not wanted:
            return None
        for provider_id, credentials in self.by_provider:
            if provider_id == wanted:
                return credentials
        return None

    def is_empty(self) -> bool:
        return not self.by_provider

    def providers(self) -> Tuple[str, ...]:
        return tuple(provider_id for provider_id, _ in self.by_provider)


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    # Per-request credentials for THIS config's provider. None/empty = inherit the
    # service settings. Excluded from repr: it carries an API key, and a config is
    # routinely passed to loggers and wrapped in exceptions.
    credentials: Optional[LLMCredentials] = field(default=None, repr=False)

    @property
    def billing_owner(self) -> str:
        """Who pays for this config's calls: "user" (a personal key) or "service"."""
        if self.credentials is None or self.credentials.is_empty():
            return "service"
        return "user"


def resolve_llm_config(node_metadata: Optional[Dict[str, Any]] = None,
                       graph_config: Optional[Dict[str, Any]] = None,
                       *,
                       credentials: Optional[LLMCredentials] = None) -> LLMConfig:
    """Combine node metadata, graph config and service defaults into one config.

    Values arriving over gRPC/JSON are strings, so temperature/max_tokens are parsed
    leniently. An unknown provider raises immediately, so a typo in a graph surfaces
    when the graph is compiled instead of silently falling back to another provider.

    Model precedence is slightly subtler than the provider one: ``LLM_MODEL`` is the model
    of the *service default* provider, so it is only consulted when the resolved provider
    IS that default. Otherwise a graph/node that switches provider would inherit a model
    name belonging to another provider (e.g. ``LLM_MODEL=yandexgpt-lite`` sent to Groq).
    The per-provider setting (``YC_MODEL_NAME`` / ``<PROVIDER>_MODEL``) comes next.

    ``credentials`` is keyword-only and optional: every pre-existing call site keeps
    working and produces a credential-free config, which the registry serves with the
    service settings (the original behaviour).
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

    # An all-empty credential is the same as none, so the registry never builds a
    # second client for a caller that supplied nothing.
    if credentials is not None and credentials.is_empty():
        credentials = None

    return LLMConfig(provider=provider, model=model or preset.default_model,
                     temperature=temperature, max_tokens=max_tokens,
                     credentials=credentials)


class LLMClient:
    """Uniform async chat client; the concrete LangChain model stays an implementation detail."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider = config.provider
        self.model = config.model
        # Per-request credentials for this provider (None/empty = the shared service key).
        # Holding them on the client is what makes `billing_owner` loggable per call.
        self._credentials: LLMCredentials = config.credentials or LLMCredentials()
        self._yandex = None
        self._runnable = self._build_runnable()

    @property
    def billing_owner(self) -> str:
        """"user" when this client spends a personal key, "service" otherwise."""
        return "user" if not self._credentials.is_empty() else "service"

    def _api_key(self, preset: ProviderPreset) -> str:
        """Personal key first, then the shared service key from the settings."""
        personal = self._credentials.api_key.strip()
        if personal:
            return personal
        return str(getattr(settings, preset.api_key_setting, "") or "").strip()

    def _folder_id(self) -> str:
        """YandexGPT folder: the caller's folder, else the service-wide ``YC_FOLDER_ID``."""
        personal = self._credentials.folder_id.strip()
        if personal:
            return personal
        return str(getattr(settings, "YC_FOLDER_ID", "") or "").strip()

    def _base_url(self, preset: ProviderPreset) -> str:
        override = getattr(settings, f"{self.provider.upper()}_BASE_URL", "") or ""
        return str(override).strip() or preset.base_url

    def _build_runnable(self):
        preset = PROVIDER_PRESETS[self.provider]
        api_key = self._api_key(preset)

        if preset.kind == "yandex":
            if not api_key:
                raise MissingApiKeyError(f"No API key for LLM provider 'yandex'. Set {preset.api_key_env}.")
            folder_id = self._folder_id()
            # Imported lazily: the Yandex client imports this module's response type.
            from app.clients.yandex_client import YandexGPTClient
            logger.info("LLM client: yandex model=%s folder=%s owner=%s",
                        self.config.model, folder_id, self.billing_owner)
            self._yandex = YandexGPTClient(
                api_key=api_key,
                folder_id=folder_id,
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
            logger.info("LLM client: anthropic model=%s base_url=%s owner=%s",
                        self.config.model, base_url, self.billing_owner)
            return ChatAnthropic(
                model=self.config.model,
                anthropic_api_key=api_key,
                anthropic_api_url=base_url or None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

        from langchain_openai import ChatOpenAI
        logger.info("LLM client: %s (openai-compatible) model=%s base_url=%s owner=%s",
                    self.provider, self.config.model, base_url, self.billing_owner)
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
            response = await self._yandex.generate(user_message=user_message,
                                                   system_message=system_message)
            # YandexGPT answers through its own client, which knows nothing about billing.
            logger.info("LLM %s/%s answered: %d tokens billing=%s",
                        self.provider, self.model,
                        response.usage.get("total_tokens", 0), self.billing_owner)
            return response

        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        messages.append(HumanMessage(content=user_message))

        message = await self._runnable.ainvoke(messages)
        text = _flatten_content(getattr(message, "content", message))
        usage = _usage_from_metadata(getattr(message, "usage_metadata", None))
        logger.info("LLM %s/%s answered: %d tokens billing=%s",
                    self.provider, self.model, usage["total_tokens"], self.billing_owner)
        return LLMResponse(text, usage, self.provider, self.model)


class LazyLLMClient:
    """A client whose provider/model is resolved only on the first model call.

    Graph compilation must not depend on credentials: a graph can be built and saved
    without an API key, and the error surfaces when the node actually calls the model -
    the same timing the pre-multi-provider code had. A key added later is also picked up
    without restarting the service.

    Compiled graphs are CACHED, so a compile-time config can never carry a user's key -
    callers pass their credentials per call instead (``generate(credentials=...)``) and
    they are folded into the config only for that call, which is what makes the registry
    cache per-user clients.
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

    @property
    def billing_owner(self) -> str:
        """Who the *configured* (compile-time) credentials say pays: the service by default."""
        if self._config.credentials is None or self._config.credentials.is_empty():
            return "service"
        return "user"

    def _resolve(self, credentials: Optional[LLMCredentials] = None) -> LLMClient:
        """Return a client for ``credentials``; the credential-free config stays the cached default.

        Passing credentials does NOT disturb the shared compile-time client: it resolves a
        *different* config (one that includes the key) through the same bounded registry, so
        two users of the same graph never see each other's key.
        """
        if credentials is not None and not credentials.is_empty():
            return self._registry.get(replace(self._config, credentials=credentials))
        if self._client is None:
            self._client = self._registry.get(self._config)
        return self._client

    async def generate(self, user_message: str, system_message: str = "",
                       credentials: Optional[LLMCredentials] = None) -> LLMResponse:
        return await self._resolve(credentials).generate(user_message=user_message,
                                                         system_message=system_message)


class LLMClientRegistry:
    """Builds one client per distinct config and reuses it (keeps HTTP pools warm).

    Deliberately BOUNDED: every user key creates a config of its own, and an unbounded
    map would keep decrypted keys and their connection pools resident forever (including
    after the key was rotated or deleted). Eviction is safe - the next call rebuilds the
    client from the credentials the request still carries.
    """

    def __init__(self, maxsize: Optional[int] = None):
        size = maxsize or getattr(settings, "MAX_CACHED_LLM_CLIENTS", 256)
        self._clients: "LRUCache[LLMConfig, LLMClient]" = LRUCache(maxsize=max(int(size), 1))

    @property
    def cached_client_count(self) -> int:
        """Number of live clients - for tests and cache-bound assertions."""
        return len(self._clients)

    def lazy(self, config: LLMConfig) -> LazyLLMClient:
        """Deferred client: the underlying provider client is built on first use.

        Use this anywhere a graph definition is being compiled, so that a missing API key
        cannot fail a build.
        """
        return LazyLLMClient(self, config)

    def get(self, config: LLMConfig) -> LLMClient:
        client = self._clients.get(config)
        if client is None:
            client = LLMClient(config)
            self._clients[config] = client
            logger.info("Registered LLM client %s/%s (temp=%s, max_tokens=%s) owner=%s cached=%d/%d",
                        config.provider, config.model, config.temperature, config.max_tokens,
                        config.billing_owner, len(self._clients), self._clients.maxsize)
        return client
