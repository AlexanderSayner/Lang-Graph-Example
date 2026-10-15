import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent

# -------------------------------------------------
# Module‑level cached loader
# -------------------------------------------------
@functools.lru_cache(maxsize=1)
def _read_prompt_file() -> str:
    """Read the prompt file once per process."""
    prompt_path = Path(__file__).with_name("ya_prompt.md")
    return prompt_path.read_text(encoding="utf-8")
# -------------------------------------------------

class Settings(BaseSettings):
    # Server Configuration
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 4 * 1024 * 1024  # 4 MB

    # Logging
    LOG_LEVEL: str = "INFO"

    # Yandex Cloud
    YC_API_KEY: str = ""
    YC_FOLDER_ID: str = ""
    YC_MODEL_NAME: str = "yandexgpt"  # or "yandexgpt-lite"

    # Yandex Cloud LLM completion mode:
    #   "sync"  – synchronous /completion endpoint (default, predictable latency for chat)
    #   "async" – deferred /completionAsync endpoint (~50% cheaper, but queued/batch: the
    #             operation can stay pending for an arbitrary amount of time)
    YC_COMPLETION_MODE: str = "sync"
    YC_TIMEOUT_SECONDS: float = 60.0

    # Bounded polling for the deferred (async) mode so a stuck operation never hangs the graph
    YC_ASYNC_POLL_INTERVAL_SECONDS: float = 1.0
    YC_ASYNC_MAX_WAIT_SECONDS: float = 90.0
    YC_ASYNC_FALLBACK_TO_SYNC: bool = True

    # How many attempts a graph node makes when the LLM answer is unusable (refusal,
    # empty text or unparsable JSON). YandexGPT occasionally refuses a benign prompt,
    # so one quiet retry with a stricter instruction usually saves the run.
    LLM_MAX_ATTEMPTS: int = 2

    # -------------------------------------------------
    # Multi-provider LLM selection (see app/clients/llm.py)
    # -------------------------------------------------
    # Service-wide default provider / model. A graph may override these per graph
    # (graph config) or per AI node (node metadata); this is the last fallback.
    #   yandex | openai | groq | deepseek | openrouter | anthropic
    LLM_PROVIDER: str = "yandex"
    LLM_MODEL: str = ""                 # empty -> the provider's default model
    # 0.6 keeps the pre-multi-provider YandexGPT default, so existing graphs behave exactly
    # as before; every provider inherits it unless a node/graph overrides it explicitly.
    LLM_TEMPERATURE: float = 0.6
    LLM_MAX_OUTPUT_TOKENS: int = 2000

    # Copilot (the in-UI assistant) may run on its own provider/model; empty falls
    # back to LLM_PROVIDER / LLM_MODEL above.
    COPILOT_PROVIDER: str = ""
    COPILOT_MODEL: str = ""

    # Model catalog (UI model suggestions via ListLlmModels / the llmModels GraphQL query).
    # Live provider lists are cached so the dropdown does not hammer the providers; failures
    # are never cached, so a transient blip recovers on the next attempt.
    LLM_MODELS_CACHE_TTL_SECONDS: int = 600
    LLM_MODELS_HTTP_TIMEOUT_SECONDS: float = 5.0

    # Per-provider credentials (only the selected provider's key is required).
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-latest"
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"

    # Environment
    model_config = SettingsConfigDict(
        # Explicitly point to the .env file location
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Redis
    REDIS_URL: str = "redis://localhost:6380"
    # Postgres (Checkpointer / Execution State)
    DATABASE_URL: str = "postgresql://langgraph:langgraph_password@localhost:7432/langgraph_db"

    # Java back end service
    HTTP_REQUEST_SERVICE_HOST: str = "localhost"
    HTTP_REQUEST_SERVICE_PORT: int = 9090  # The port your Java gRPC server is running on

    # Prompt handling
    @property
    def system_prompt(self) -> str:
        """Legacy accessor for app/ya_prompt.md.

        No longer used as a system-message fallback: the multi-provider client sends
        exactly the system_message its caller provides, and a blank one means "no system
        message" (an empty system text would be rejected by the Yandex REST API).
        Kept because ya_prompt.md is still a documented operational artifact.
        """
        return _read_prompt_file()


settings = Settings()
