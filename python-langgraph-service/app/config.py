from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SERVER_PORT: int = 50051
    MAX_WORKERS: int = 10
    MAX_MESSAGE_LENGTH: int = 50 * 1024 * 1024  # 50MB

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()