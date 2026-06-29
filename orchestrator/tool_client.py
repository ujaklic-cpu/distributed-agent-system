from __future__ import annotations

import time

import httpx

from orchestrator.config import settings
from shared.auth import cluster_auth_headers, parse_token_list
from shared.policy import DEFAULT_TOOL_POLICY, PolicyViolation
from shared.schemas import ToolJobRequest, ToolJobResult


class NodeAgentToolClient:
    def __init__(self, base_url: str = settings.node_agent_url) -> None:
        self.base_url = base_url.rstrip("/")

    def run_tool(self, request: ToolJobRequest, base_url: str | None = None) -> ToolJobResult:
        start = time.monotonic()
        try:
            DEFAULT_TOOL_POLICY.validate(request)
        except PolicyViolation as exc:
            return ToolJobResult(
                job_id=request.job_id,
                attempt_id=request.attempt_id,
                generation=request.generation,
                node_id="policy",
                status="blocked",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            with httpx.Client(timeout=request.limits.timeout_seconds + 30) as client:
                target = (base_url or self.base_url).rstrip("/")
                response = client.post(
                    f"{target}/jobs/tool",
                    json=request.model_dump(mode="json"),
                    headers=cluster_auth_headers(
                        (parse_token_list(settings.node_tokens) or [settings.cluster_token])[0]
                    ),
                )
                response.raise_for_status()
                return ToolJobResult.model_validate(response.json())
        except Exception as exc:
            return ToolJobResult(
                job_id=request.job_id,
                attempt_id=request.attempt_id,
                generation=request.generation,
                node_id="unknown",
                status="error",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
