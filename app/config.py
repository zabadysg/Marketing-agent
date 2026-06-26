from pydantic import AnyHttpUrl, Field, SecretStr
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

    # Anthropic
    anthropic_api_key: SecretStr = Field(default=SecretStr("placeholder"))
    strategy_model: str = "claude-sonnet-4-6"
    critic_model: str = "claude-haiku-4-5-20251001"

    # Postiz
    postiz_api_url: str = "http://postiz:5000"
    postiz_api_key: SecretStr = Field(default=SecretStr("placeholder"))


settings = Settings()
