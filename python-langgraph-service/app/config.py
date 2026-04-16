from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    # Server Configuration
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 4 * 1024 * 1024  # 4 MB

    # Logging
    LOG_LEVEL: str = "INFO"

    # Observability (LangSmith)
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "langgraph-service"

    # Java Backend gRPC (for hybrid architecture)
    JAVA_BACKEND_HOST: str = "localhost"
    JAVA_BACKEND_PORT: int = 9090

    # LLM Provider Configurations
    
    # Yandex GPT
    YC_API_KEY: str = ""
    YC_FOLDER_ID: str = ""
    YC_MODEL_NAME: str = "yandexgpt"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    OPENAI_BASE_URL: Optional[str] = None

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-sonnet-20240229"

    # Google
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-pro"

    # Ollama (Local)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama2"

    # Mistral AI
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = "mistral-medium"

    # Groq
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "mixtral-8x7b-32768"

    # HuggingFace
    HUGGINGFACE_API_KEY: str = ""
    HUGGINGFACE_MODEL: str = "mistralai/Mistral-7B-Instruct-v0.2"

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_llm_config(self, provider: str) -> Dict[str, Any]:
        """Get configuration for a specific LLM provider."""
        configs = {
            "yandex": {
                "api_key": self.YC_API_KEY,
                "folder_id": self.YC_FOLDER_ID,
                "model_name": self.YC_MODEL_NAME,
            },
            "openai": {
                "api_key": self.OPENAI_API_KEY,
                "model_name": self.OPENAI_MODEL,
                "base_url": self.OPENAI_BASE_URL,
            },
            "anthropic": {
                "api_key": self.ANTHROPIC_API_KEY,
                "model_name": self.ANTHROPIC_MODEL,
            },
            "google": {
                "api_key": self.GOOGLE_API_KEY,
                "model_name": self.GOOGLE_MODEL,
            },
            "ollama": {
                "model_name": self.OLLAMA_MODEL,
                "base_url": self.OLLAMA_BASE_URL,
            },
            "mistral": {
                "api_key": self.MISTRAL_API_KEY,
                "model_name": self.MISTRAL_MODEL,
            },
            "groq": {
                "api_key": self.GROQ_API_KEY,
                "model_name": self.GROQ_MODEL,
            },
            "huggingface": {
                "api_key": self.HUGGINGFACE_API_KEY,
                "model_name": self.HUGGINGFACE_MODEL,
            },
        }
        return configs.get(provider, {})


settings = Settings()
