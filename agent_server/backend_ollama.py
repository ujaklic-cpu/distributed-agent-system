from __future__ import annotations

import httpx

from agent_server.config import settings
from shared.schemas import AgentChatRequest, AgentChatResponse


async def ollama_chat(request: AgentChatRequest) -> AgentChatResponse:
    payload = {
        "model": settings.model_name,
        "messages": [message.model_dump() for message in request.messages],
        "stream": False,
        "options": request.options or {},
    }
    async with httpx.AsyncClient(timeout=360) as client:
        response = await client.post(f"{settings.ollama_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
    content = data.get("message", {}).get("content", "")
    return AgentChatResponse(content=content, tool_requests=[])
