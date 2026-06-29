from __future__ import annotations

import json
import re
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REDACTION = "[redacted]"
_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "private_key",
    "connection_string",
    "database_url",
    "queue_url",
)
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|pwd)\b\s*[:=]\s*(['\"]?)[^\s'\",;]{6,}\2"
    ),
    re.compile(r"(?i)\b[A-Z0-9._%+-]+://[^\s:@/]+:[^\s@/]+@[^\s]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk|rk|AKIA|ASIA|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_\-]{16,}\b"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:

        def replace(match: re.Match[str]) -> str:
            if match.re.pattern.lower().startswith("(?i)(bearer"):
                return f"{match.group(1)}{REDACTION}"
            if "PRIVATE KEY" in match.group(0):
                return REDACTION
            if "://" in match.group(0):
                return re.sub(r"://([^\s:@/]+):([^\s@/]+)@", f"://{REDACTION}:{REDACTION}@", match.group(0), count=1)
            if match.lastindex and match.lastindex >= 1:
                key = match.group(1)
                return f"{key}={REDACTION}"
            return REDACTION

        redacted = pattern.sub(replace, redacted)
    return redacted


def is_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: REDACTION if is_secret_key(key) else redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_json_string(raw: str | None, default: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except json.JSONDecodeError:
        return default
    return redact_value(value)


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max(0, int(max_body_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length.decode("ascii")) > self.max_body_bytes:
                    await _send_json_error(send, 413, "REQUEST_TOO_LARGE", "Request body exceeds configured size limit")
                    return
            except ValueError:
                await _send_json_error(send, 400, "BAD_CONTENT_LENGTH", "Invalid Content-Length header")
                return

        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_body_bytes:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)


def _response_content_type(start: Message | None) -> str:
    if start is None:
        return ""
    for key, value in start.get("headers", []):
        if key.lower() == b"content-type":
            return value.decode("latin1").lower()
    return ""


def _replace_content_length(start: Message, length: int) -> Message:
    headers = [(key, value) for key, value in start.get("headers", []) if key.lower() != b"content-length"]
    headers.append((b"content-length", str(length).encode("ascii")))
    updated = dict(start)
    updated["headers"] = headers
    return updated


def _redact_response_body(start: Message | None, body: bytes) -> bytes:
    if "application/json" not in _response_content_type(start):
        return body
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    return json.dumps(redact_value(decoded), sort_keys=True, default=str).encode("utf-8")


class ResponseSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max(0, int(max_body_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return

        response_start: Message | None = None
        body_parts: list[bytes] = []
        total = 0
        failed = False

        async def limited_send(message: Message) -> None:
            nonlocal response_start, total, failed
            if failed:
                return
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > self.max_body_bytes:
                    failed = True
                    await _send_json_error(
                        send, 500, "RESPONSE_TOO_LARGE", "Response body exceeds configured size limit"
                    )
                    return
                body_parts.append(chunk)
                if not message.get("more_body", False):
                    body = _redact_response_body(response_start, b"".join(body_parts))
                    if len(body) > self.max_body_bytes:
                        failed = True
                        await _send_json_error(
                            send, 500, "RESPONSE_TOO_LARGE", "Response body exceeds configured size limit"
                        )
                        return
                    if response_start is not None:
                        await send(_replace_content_length(response_start, len(body)))
                    await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, limited_send)


async def _send_json_error(send: Send, status_code: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "details": {}}}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def parse_origins(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


def validate_cors_origins(environment: str, origins: list[str]) -> list[str]:
    if environment == "production" and any(origin == "*" for origin in origins):
        raise RuntimeError("CORS_ALLOW_ORIGINS cannot contain '*' in production")
    return origins


def install_security_middleware(
    app: FastAPI,
    *,
    environment: str,
    cors_allow_origins: str | list[str] | tuple[str, ...] | None,
    max_request_body_bytes: int,
    max_response_body_bytes: int,
) -> None:
    origins = validate_cors_origins(environment, parse_origins(cors_allow_origins))
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            max_age=600,
        )
    app.add_middleware(ResponseSizeLimitMiddleware, max_body_bytes=max_response_body_bytes)
    app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=max_request_body_bytes)
