from __future__ import annotations

import pytest

from node_agent.config import Settings as NodeSettings
from orchestrator.config import Settings as OrchestratorSettings


@pytest.mark.parametrize("settings_cls", [OrchestratorSettings, NodeSettings])
@pytest.mark.parametrize("cluster_token", ["", "change_me_cluster_token"])
def test_production_rejects_missing_or_default_cluster_token(settings_cls, cluster_token: str) -> None:
    with pytest.raises(RuntimeError, match="CLUSTER_TOKEN must be set to a strong"):
        settings_cls(environment="production", cluster_token=cluster_token)


def _valid_production_kwargs(settings_cls) -> dict[str, str]:
    common = {
        "environment": "production",
        "cluster_token": "test-strong-cluster-token",
        "node_tokens": "test-node-role-token",
        "cors_allow_origins": "https://ops.example.invalid",
    }
    if settings_cls is OrchestratorSettings:
        common.update(
            operator_tokens="test-operator-role-token",
            admin_tokens="test-admin-role-token",
            readonly_tokens="test-readonly-role-token",
            database_url="postgresql+psycopg://agents:test-db-secret@postgres:5432/agents",
            queue_url="redis://:test-redis-secret@redis:6379/0",
        )
    return common


@pytest.mark.parametrize("settings_cls", [OrchestratorSettings, NodeSettings])
def test_production_accepts_non_default_cluster_token(settings_cls) -> None:
    settings_cls(**_valid_production_kwargs(settings_cls))
