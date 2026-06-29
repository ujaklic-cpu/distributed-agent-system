from __future__ import annotations

import time

import httpx

from orchestrator.config import settings
from shared.auth import cluster_auth_headers, parse_token_list
from shared.schemas import AgentJobRequest, AgentJobResult


class NodeAgentModelClient:
    def __init__(self, base_url: str = settings.node_agent_url) -> None:
        self.base_url = base_url.rstrip("/")

    def run_agent(self, request: AgentJobRequest, base_url: str | None = None) -> AgentJobResult:
        start = time.monotonic()
        try:
            with httpx.Client(timeout=settings.request_timeout_seconds) as client:
                target = (base_url or self.base_url).rstrip("/")
                response = client.post(
                    f"{target}/jobs/agent",
                    json=request.model_dump(mode="json"),
                    headers=cluster_auth_headers(
                        (parse_token_list(settings.node_tokens) or [settings.cluster_token])[0]
                    ),
                )
                response.raise_for_status()
                return AgentJobResult.model_validate(response.json())
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            return AgentJobResult(
                job_id=request.job_id,
                attempt_id=request.attempt_id,
                generation=request.generation,
                node_id="unknown",
                status="error",
                error=str(exc),
                duration_ms=duration,
            )
