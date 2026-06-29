from __future__ import annotations

import socket
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    node_id: str = f"node-{socket.gethostname()}"
    node_name: str = socket.gethostname()
    smart_agent_url: str = "http://localhost:8090"
    worker_agent_url: str = "http://localhost:8091"
    code_worker_agent_url: str | None = None
    fast_worker_agent_url: str | None = None
    cpu_worker_agent_url: str | None = None
    research_agent_url: str = "http://localhost:8092"
    orchestrator_url: str = ""
    public_node_url: str | None = None
    heartbeat_interval_seconds: int = 5
    readiness_check_timeout_seconds: float = 2.0
    shutdown_grace_period_seconds: int = 30
    node_agent_version: str = "0.7.0"
    protocol_version: str = "2026-06-14"
    capabilities: str = "smart_agent,code_worker,fast_worker,tool_shell,tool_test,tool_internet,tool_filesystem,tool_build,tool_analysis"
    loaded_models: str = ""
    tool_image_shell: str = "local-agent/tool-shell:0.7.0"
    tool_image_test: str = "local-agent/tool-shell:0.7.0"
    tool_image_internet: str = "local-agent/tool-internet:0.7.0"
    tool_image_filesystem: str = "local-agent/tool-filesystem:0.7.0"
    tool_image_build: str = "local-agent/tool-build:0.7.0"
    tool_image_analysis: str = "local-agent/tool-analysis:0.7.0"
    node_workspace_root: str = "/workspaces"
    host_workspace_root: str = "/workspaces"
    allow_host_tool_fallback: bool = False
    cluster_token: str = ""
    node_tokens: str = ""
    deprecated_tokens: str = ""
    tool_container_auto_remove: bool = True
    tool_container_read_only_root: bool = True
    tool_container_tmpfs: bool = True
    tool_container_user: str = "1000:1000"
    log_level: str = "INFO"
    cors_allow_origins: str = ""
    max_request_body_bytes: int = 1048576
    max_response_body_bytes: int = 10485760
    log_raw_request_bodies: bool = False

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment == "production":
            weak = {"", "change_me", "change_me_cluster_token", "postgres", "password", "agents"}
            if self.cluster_token in weak or self.cluster_token.startswith("replace-with-"):
                raise RuntimeError("CLUSTER_TOKEN must be set to a strong secret in production")
            tokens = [item.strip() for item in self.node_tokens.split(",") if item.strip()]
            if not tokens:
                raise RuntimeError("NODE_TOKENS must be configured in production")
            if any(token in weak or token.startswith("replace-with-") for token in tokens):
                raise RuntimeError("NODE_TOKENS must not contain placeholder/default values in production")
            if self.cluster_token in tokens:
                raise RuntimeError("CLUSTER_TOKEN must not be reused as a node role token in production")
            if self.cors_allow_origins.strip() == "" or "*" in [
                item.strip() for item in self.cors_allow_origins.split(",")
            ]:
                raise RuntimeError("CORS_ALLOW_ORIGINS must be explicit and cannot contain '*' in production")
        return self

    def capabilities_list(self) -> list[str]:
        return [item.strip() for item in self.capabilities.split(",") if item.strip()]

    def loaded_models_list(self) -> list[str]:
        return [item.strip() for item in self.loaded_models.split(",") if item.strip()]

    def supported_tools_list(self) -> list[str]:
        mapping = {
            "tool_shell": "shell",
            "tool_test": "test",
            "tool_internet": "internet",
            "tool_filesystem": "filesystem",
            "tool_build": "build",
            "tool_analysis": "analysis",
        }
        tools = [mapping[item] for item in self.capabilities_list() if item in mapping]
        return sorted(set(tools))


settings = Settings()

if settings.environment != "development" and settings.allow_host_tool_fallback:
    raise RuntimeError("Host tool fallback is forbidden outside development")
