from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    # Server Configuration
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 4 * 1024 * 1024  # 4 MB

    # Logging
    LOG_LEVEL: str = "INFO"

    YC_API_KEY: str = ""
    YC_FOLDER_ID: str = ""
    YC_MODEL_NAME: str = "yandexgpt"  # or "yandexgpt-lite"

    model_config = SettingsConfigDict(
        # Explicitly point to the .env file location
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
