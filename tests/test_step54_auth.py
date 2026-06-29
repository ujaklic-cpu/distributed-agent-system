from __future__ import annotations

import pytest
from fastapi import HTTPException

from shared.auth import cluster_auth_headers, validate_bearer_token


def test_cluster_auth_allows_empty_token_for_development() -> None:
    validate_bearer_token(None, "")


def test_cluster_auth_requires_bearer_token_when_configured() -> None:
    with pytest.raises(HTTPException) as missing:
        validate_bearer_token(None, "secret")
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as invalid:
        validate_bearer_token("Bearer wrong", "secret")
    assert invalid.value.status_code == 403

    validate_bearer_token("Bearer secret", "secret")


def test_cluster_auth_headers_are_added_only_when_configured() -> None:
    assert cluster_auth_headers("") == {}
    assert cluster_auth_headers("secret") == {"Authorization": "Bearer secret"}


def test_validate_bearer_token_uses_realm_in_errors() -> None:
    with pytest.raises(HTTPException) as missing:
        validate_bearer_token(None, "secret", realm="operator")
    assert missing.value.status_code == 401
    assert missing.value.detail == "missing operator token"

    with pytest.raises(HTTPException) as invalid:
        validate_bearer_token("Bearer wrong", "secret", realm="admin")
    assert invalid.value.status_code == 403
    assert invalid.value.detail == "invalid admin token"


def test_select_role_token_prefers_role_token_and_falls_back_to_cluster_token() -> None:
    from shared.auth import select_role_token

    assert select_role_token(role_token="operator", cluster_token="cluster") == "operator"
    assert select_role_token(role_token="", cluster_token="cluster") == "cluster"
    assert select_role_token(role_token="", cluster_token="") == ""
