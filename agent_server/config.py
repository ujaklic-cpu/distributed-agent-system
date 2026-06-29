from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    agent_role: str = "smart_agent"
    model_name: str = "mock-agent"
    model_backend: str = "mock"
    mock_agent: bool = True
    ollama_url: str = "http://localhost:11434"
    log_level: str = "INFO"
    cors_allow_origins: str = ""
    max_request_body_bytes: int = 1048576
    max_response_body_bytes: int = 10485760
    log_raw_request_bodies: bool = False

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.environment == "production" and "*" in [item.strip() for item in self.cors_allow_origins.split(",")]:
            raise RuntimeError("CORS_ALLOW_ORIGINS cannot contain '*' in production")
        return self


settings = Settings()
