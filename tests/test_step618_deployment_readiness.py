from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from orchestrator.config import Settings
from shared.auth import parse_token_list, token_hash_prefix, validate_role_token_list
from shared.rate_limit import InMemoryRateLimiter


def test_role_token_list_accepts_deprecated_without_leaking_raw_token() -> None:
    result = validate_role_token_list(
        "Bearer old-token",
        ["new-token", "old-token"],
        realm="operator",
        deprecated_tokens=["old-token"],
    )
    assert result.role == "operator"
    assert result.state == "deprecated"
    assert result.token_hash != "old-token"
    assert token_hash_prefix("old-token") == result.token_hash[:12]


def test_wrong_role_token_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_role_token_list("Bearer node-token", ["admin-token"], realm="admin")
    assert exc.value.status_code == 403


def test_production_requires_distinct_role_token_lists() -> None:
    kwargs = dict(
        environment="production",
        cluster_token="cluster-secret",
        node_tokens="node-secret",
        operator_tokens="operator-secret",
        admin_tokens="admin-secret",
        readonly_tokens="readonly-secret",
        cors_allow_origins="https://ops.example.invalid",
        database_url="postgresql+psycopg://agents:db-secret@postgres:5432/agents",
        queue_url="redis://:redis-secret@redis:6379/0",
    )
    assert Settings(**kwargs).tokens_for_role("readonly") == ["readonly-secret"]
    with pytest.raises(Exception):
        Settings(**{**kwargs, "admin_tokens": "node-secret"})
    with pytest.raises(Exception):
        Settings(**{**kwargs, "queue_url": "redis://redis:6379/0"})
    with pytest.raises(Exception):
        Settings(**{**kwargs, "database_url": "postgresql+psycopg://agents:password@postgres:5432/agents"})


def test_rate_limit_exceeded_after_limit() -> None:
    limiter = InMemoryRateLimiter()
    assert limiter.check("task:a", 2, 60).allowed
    assert limiter.check("task:a", 2, 60).allowed
    decision = limiter.check("task:a", 2, 60)
    assert not decision.allowed
    assert decision.remaining == 0


def test_production_compose_uses_docker_socket_proxy() -> None:
    text = Path("deploy/docker-compose.prod.yml").read_text()
    assert "docker-socket-proxy:" in text
    assert "DOCKER_HOST: tcp://docker-socket-proxy:2375" in text
    node_section = text.split("  node-agent:", 1)[1].split("\n  ", 1)[0]
    assert "/var/run/docker.sock" not in node_section


def test_makefile_security_targets_use_version_variables() -> None:
    text = Path("Makefile").read_text()
    assert "APP_VERSION ?= 7.1" in text
    assert "TOOL_VERSION ?= 0.7.0" in text
    assert ":6.16" not in text
    assert ":0.6.11" not in text


def test_parse_token_list_trims_empty_values() -> None:
    assert parse_token_list(" a, ,b ,, ") == ["a", "b"]
