from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

PROJECT_ROOT = Path(__file__).parent.parent


class LLMProviderConfig(BaseSettings):
    """Configuration for a single LLM provider."""
    provider_type: str
    api_key: Optional[str] = None
    model: str = ""
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    
    # Yandex-specific
    folder_id: Optional[str] = None
    
    model_config = SettingsConfigDict(extra="ignore")


class Settings(BaseSettings):
    # Server Configuration
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 4 * 1024 * 1024  # 4 MB

    # Logging
    LOG_LEVEL: str = "INFO"

    # Yandex GPT (legacy support)
    YC_API_KEY: str = ""
    YC_FOLDER_ID: str = ""
    YC_MODEL_NAME: str = "yandexgpt"

    # Multi-LLM Configuration
    # Default LLM provider to use
    DEFAULT_LLM_PROVIDER: str = "yandex"
    
    # LLM Provider configurations (JSON string or individual env vars)
    # Example: OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-opus-20240229"
    
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama2"
    
    MISTRAL_API_KEY: str = ""
    MISTRAL_MODEL: str = "mistral-large-latest"
    
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "mixtral-8x7b-32768"

    # LangSmith Tracing
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "langgraph-service"
    LANGCHAIN_TRACING_V2: bool = False

    # Java gRPC Service
    JAVA_GRPC_HOST: str = "localhost"
    JAVA_GRPC_PORT: int = 50052

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    def get_llm_config(self, provider_name: str) -> Dict[str, Any]:
        """Get configuration for a specific LLM provider."""
        provider_name = provider_name.lower()
        
        configs = {
            'openai': {
                'provider_type': 'openai',
                'api_key': self.OPENAI_API_KEY,
                'model': self.OPENAI_MODEL,
            },
            'anthropic': {
                'provider_type': 'anthropic',
                'api_key': self.ANTHROPIC_API_KEY,
                'model': self.ANTHROPIC_MODEL,
            },
            'ollama': {
                'provider_type': 'ollama',
                'base_url': self.OLLAMA_BASE_URL,
                'model': self.OLLAMA_MODEL,
            },
            'mistral': {
                'provider_type': 'mistral',
                'api_key': self.MISTRAL_API_KEY,
                'model': self.MISTRAL_MODEL,
            },
            'groq': {
                'provider_type': 'groq',
                'api_key': self.GROQ_API_KEY,
                'model': self.GROQ_MODEL,
            },
            'yandex': {
                'provider_type': 'yandex',
                'api_key': self.YC_API_KEY,
                'folder_id': self.YC_FOLDER_ID,
                'model': self.YC_MODEL_NAME,
            },
        }
        
        return configs.get(provider_name, {})


settings = Settings()
