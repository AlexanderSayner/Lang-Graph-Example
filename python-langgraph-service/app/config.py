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
    TOOL_SERVICE_HOST: str = "localhost"
    TOOL_SERVICE_PORT: int = 9090  # The port your Java gRPC server is running on

    # Prompt handling
    @property
    def system_prompt(self) -> str:
        """Expose the cached prompt."""
        return _read_prompt_file()


settings = Settings()
