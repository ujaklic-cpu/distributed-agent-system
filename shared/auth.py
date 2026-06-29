from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status


@dataclass(frozen=True)
class TokenValidationResult:
    role: str
    token_hash: str
    state: str = "active"


def parse_token_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_hash_prefix(value: str | None) -> str:
    if not value:
        return ""
    return token_hash(value)[:12]


def _extract_bearer(authorization: str | None, realm: str) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"missing {realm} token")
    supplied = authorization[len(prefix) :].strip()
    if not supplied:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"missing {realm} token")
    return supplied


def validate_bearer_token(authorization: str | None, expected_token: str, *, realm: str = "cluster") -> None:
    """Validate a bearer token when one is configured.

    This compatibility helper is kept for local development and non-role based
    call sites. Production services should use validate_role_token_list so role
    fallback cannot accidentally widen privileges.
    """
    if not expected_token:
        return
    supplied = _extract_bearer(authorization, realm)
    if not secrets.compare_digest(supplied, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"invalid {realm} token")


def validate_role_token_list(
    authorization: str | None,
    accepted_tokens: list[str],
    *,
    realm: str,
    deprecated_tokens: list[str] | None = None,
    allow_empty: bool = False,
) -> TokenValidationResult:
    """Validate a bearer token against a role-specific rotation list.

    The returned token_hash is safe to use in rate-limit keys or audit payloads.
    Raw token values are never returned.
    """
    if not accepted_tokens:
        if allow_empty:
            return TokenValidationResult(role=realm, token_hash="", state="disabled")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{realm} token is not configured")
    supplied = _extract_bearer(authorization, realm)
    deprecated = set(deprecated_tokens or [])
    for candidate in accepted_tokens:
        if secrets.compare_digest(supplied, candidate):
            return TokenValidationResult(
                role=realm,
                token_hash=token_hash(supplied),
                state="deprecated" if candidate in deprecated else "active",
            )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"invalid {realm} token")


def select_role_token(*, role_token: str = "", cluster_token: str = "") -> str:
    """Return the role-specific token, falling back to the cluster token.

    This is retained only for development/test compatibility. Production
    settings validation rejects role fallback and requires role-specific token
    lists.
    """
    return role_token or cluster_token


def cluster_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}
