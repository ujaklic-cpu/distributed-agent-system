from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.auth import parse_token_list


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    database_url: str = "postgresql+psycopg://agents:change_me@localhost:5432/agents"
    queue_url: str = "redis://localhost:6379/0"
    node_agent_url: str = "http://localhost:8081"
    orchestrator_protocol_version: str = "2026-06-14"
    min_node_protocol_version: str = "2026-06-14"
    node_unreachable_after_seconds: int = 60
    node_dead_after_seconds: int = 300
    min_node_agent_version: str = "0.7.0"
    max_node_agent_major_version: int = 0
    max_task_attempts: int = 3
    max_agent_steps: int = 8
    max_tool_calls_per_agent_step: int = 4
    max_same_tool_call_repeats: int = 1
    request_timeout_seconds: int = 360
    log_level: str = "INFO"
    cors_allow_origins: str = ""
    max_request_body_bytes: int = 1048576
    max_response_body_bytes: int = 10485760
    log_raw_request_bodies: bool = False
    worker_poll_timeout_seconds: int = 5
    readiness_check_timeout_seconds: float = 2.0
    shutdown_grace_period_seconds: int = 30
    max_reservation_retries: int = 5
    job_lease_seconds: int = 900
    workspace_lock_seconds: int = 900
    max_optimization_rounds: int = 2
    require_final_verification: bool = True
    orchestrator_workspace_root: str = "workspaces"
    artifact_storage_root: str = "artifacts"
    artifact_uri_prefix: str = "artifact://"
    cluster_token: str = ""
    operator_token: str = ""
    admin_token: str = ""
    node_tokens: str = ""
    operator_tokens: str = ""
    admin_tokens: str = ""
    readonly_tokens: str = ""
    deprecated_tokens: str = ""
    allow_shared_role_tokens: bool = False
    allow_in_memory_rate_limit_fallback: bool = False
    max_workspace_bytes: int = 1073741824
    max_workspace_files: int = 100000
    max_artifact_bytes: int = 104857600

    audit_log_retention_days: int = 90
    tool_run_retention_days: int = 30
    task_retention_days: int = 180
    memory_retention_days: int = 365
    temporary_memory_retention_hours: int = 24

    def tokens_for_role(self, role: str) -> list[str]:
        if role == "node":
            tokens = parse_token_list(self.node_tokens)
            return tokens or ([] if self.environment == "production" else parse_token_list(self.cluster_token))
        if role == "operator":
            tokens = parse_token_list(self.operator_tokens)
            return tokens or (
                [] if self.environment == "production" else parse_token_list(self.operator_token or self.cluster_token)
            )
        if role == "admin":
            tokens = parse_token_list(self.admin_tokens)
            return tokens or (
                [] if self.environment == "production" else parse_token_list(self.admin_token or self.cluster_token)
            )
        if role == "readonly":
            tokens = parse_token_list(self.readonly_tokens)
            return tokens or (
                [] if self.environment == "production" else parse_token_list(self.operator_token or self.cluster_token)
            )
        return []

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment != "production":
            return self

        weak_values = {
            "",
            "change_me",
            "change_me_cluster_token",
            "change_me_operator_token",
            "change_me_admin_token",
            "postgres",
            "password",
            "agents",
        }

        def reject_secret(name: str, value: str) -> None:
            if value in weak_values or value.startswith("replace-with-"):
                raise RuntimeError(f"{name} must be set to a strong non-placeholder value in production")

        reject_secret("CLUSTER_TOKEN", self.cluster_token)
        role_tokens = {
            "NODE_TOKENS": parse_token_list(self.node_tokens),
            "OPERATOR_TOKENS": parse_token_list(self.operator_tokens),
            "ADMIN_TOKENS": parse_token_list(self.admin_tokens),
            "READONLY_TOKENS": parse_token_list(self.readonly_tokens),
        }
        for name, tokens in role_tokens.items():
            if not tokens:
                raise RuntimeError(f"{name} must be configured in production")
            for token in tokens:
                reject_secret(name, token)
        all_role_tokens = [token for tokens in role_tokens.values() for token in tokens]
        if not self.allow_shared_role_tokens and len(set(all_role_tokens)) != len(all_role_tokens):
            raise RuntimeError("role token values must be distinct in production")
        if self.cluster_token in all_role_tokens and not self.allow_shared_role_tokens:
            raise RuntimeError("CLUSTER_TOKEN must not be reused as a role token in production")

        db = urlparse(self.database_url)
        if (db.password or "") in weak_values or (db.password or "").startswith("replace-with-"):
            raise RuntimeError("DATABASE_URL must not contain placeholder/default credentials in production")
        queue = urlparse(self.queue_url)
        if queue.scheme.startswith("redis") and not queue.password:
            raise RuntimeError("QUEUE_URL must include a Redis password in production")
        if (queue.password or "") in weak_values or (queue.password or "").startswith("replace-with-"):
            raise RuntimeError("QUEUE_URL must not contain placeholder/default credentials in production")
        if self.cors_allow_origins.strip() == "" or "*" in [
            item.strip() for item in self.cors_allow_origins.split(",")
        ]:
            raise RuntimeError("CORS_ALLOW_ORIGINS must be explicit and cannot contain '*' in production")
        if self.database_url.startswith("sqlite"):
            raise RuntimeError("DATABASE_URL must not use sqlite in production")
        return self


settings = Settings()
