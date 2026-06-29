from __future__ import annotations

import time

import httpx

from node_agent.config import settings
from shared.schemas import AgentChatRequest, AgentJobRequest, AgentJobResult, AgentRole


class AgentJobRunner:
    def url_for_role(self, role: AgentRole) -> str:
        if role == AgentRole.code_worker:
            return (settings.code_worker_agent_url or settings.worker_agent_url).rstrip("/")
        if role == AgentRole.fast_worker:
            return (settings.fast_worker_agent_url or settings.worker_agent_url).rstrip("/")
        if role == AgentRole.cpu_worker:
            return (
                settings.cpu_worker_agent_url or settings.fast_worker_agent_url or settings.worker_agent_url
            ).rstrip("/")
        if role == AgentRole.research_worker:
            return settings.research_agent_url.rstrip("/")
        return settings.smart_agent_url.rstrip("/")

    def run(self, job: AgentJobRequest) -> AgentJobResult:
        start = time.monotonic()
        url = self.url_for_role(job.role)
        request = AgentChatRequest(
            role=job.role, messages=job.messages, workspace_id=job.workspace_id, options=job.options
        )
        try:
            with httpx.Client(timeout=360) as client:
                response = client.post(f"{url}/chat", json=request.model_dump(mode="json"))
                response.raise_for_status()
                return AgentJobResult(
                    job_id=job.job_id,
                    attempt_id=job.attempt_id,
                    generation=job.generation,
                    node_id=settings.node_id,
                    status="ok",
                    response=response.json(),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        except Exception as exc:
            return AgentJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=settings.node_id,
                status="error",
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
