from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.logging import log_event
from shared.security import (
    RequestSizeLimitMiddleware,
    ResponseSizeLimitMiddleware,
    install_security_middleware,
    redact_value,
    validate_cors_origins,
)


def test_redacts_common_secret_patterns_and_keys() -> None:
    payload = {
        "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "database_url": "postgresql://user:supersecret@db:5432/app",
        "nested": {"password": "clear-text", "safe": "hello"},
        "text": "api_key=sk-abcdefghijklmnopqrstuvwxyz and -----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    }

    redacted = redact_value(payload)

    assert redacted["Authorization"] == "[redacted]"
    assert redacted["database_url"] == "[redacted]"
    assert redacted["nested"]["password"] == "[redacted]"
    assert redacted["nested"]["safe"] == "hello"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted["text"]
    assert "PRIVATE KEY" not in redacted["text"]


def test_log_event_redacts_before_emitting(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("step614-redaction-test")
    caplog.set_level(logging.INFO, logger="step614-redaction-test")

    log_event(logger, "auth_test", token="secret-token-value-123456", message="Bearer abcdefghijklmnopqrstuvwxyz")

    rendered = caplog.text
    assert "secret-token-value" not in rendered
    assert "abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "[redacted]" in rendered


def test_production_cors_rejects_wildcard() -> None:
    with pytest.raises(RuntimeError):
        validate_cors_origins("production", ["*"])


def test_request_size_limit_rejects_large_body() -> None:
    app = FastAPI()
    app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=8)

    @app.post("/echo")
    async def echo(payload: dict) -> dict:
        return payload

    response = TestClient(app).post("/echo", json={"value": "0123456789"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_response_size_limit_rejects_large_response() -> None:
    app = FastAPI()
    app.add_middleware(ResponseSizeLimitMiddleware, max_body_bytes=8)

    @app.get("/large")
    async def large() -> dict:
        return {"value": "0123456789"}

    response = TestClient(app).get("/large")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RESPONSE_TOO_LARGE"


def test_security_middleware_adds_restricted_cors_not_wildcard() -> None:
    app = FastAPI()
    install_security_middleware(
        app,
        environment="production",
        cors_allow_origins="https://ops.example.org",
        max_request_body_bytes=1024,
        max_response_body_bytes=2048,
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    response = TestClient(app).options(
        "/health",
        headers={"Origin": "https://ops.example.org", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://ops.example.org"


def test_json_response_redaction_is_global() -> None:
    app = FastAPI()
    install_security_middleware(
        app,
        environment="development",
        cors_allow_origins="",
        max_request_body_bytes=1024,
        max_response_body_bytes=4096,
    )

    @app.get("/secret")
    async def secret() -> dict:
        return {"token": "clear-token-123456789", "message": "Bearer abcdefghijklmnopqrstuvwxyz"}

    response = TestClient(app).get("/secret")
    assert response.status_code == 200
    assert response.json()["token"] == "[redacted]"
    assert "abcdefghijklmnopqrstuvwxyz" not in response.text
