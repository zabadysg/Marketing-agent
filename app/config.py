import os

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "marketing-agent"
    app_version: str = "0.1.0"
    environment: str = Field(default="development", alias="APP_ENV")
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://postgres:changeme@db:5432/marketing"

    # Gemini
    google_api_key: SecretStr = Field(default=SecretStr("placeholder"))
    reasoning_model: str = "gemini-2.5-pro"
    cheap_model: str = "gemini-2.5-flash"
    max_tokens: int = 8192

    # LangSmith
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "marketing-agent"

    # Postiz
    postiz_api_url: str = "http://postiz:5000"
    postiz_api_key: SecretStr = Field(default=SecretStr("placeholder"))


settings = Settings()

if settings.langsmith_tracing:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
