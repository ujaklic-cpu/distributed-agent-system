from __future__ import annotations

from fastapi import FastAPI

from agent_server.backend_ollama import ollama_chat
from agent_server.config import settings
from agent_server.prompt_runtime import mock_chat
from agent_server.tool_request_parser import parse_tool_requests
from shared.schemas import AgentChatRequest, AgentChatResponse
from shared.security import install_security_middleware

app = FastAPI(title="Agent Server", version="0.1.0")
install_security_middleware(
    app,
    environment=settings.environment,
    cors_allow_origins=settings.cors_allow_origins,
    max_request_body_bytes=settings.max_request_body_bytes,
    max_response_body_bytes=settings.max_response_body_bytes,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "role": settings.agent_role, "model": settings.model_name}


@app.get("/models")
def models() -> dict:
    return {"backend": settings.model_backend, "model": settings.model_name, "mock": settings.mock_agent}


@app.post("/chat", response_model=AgentChatResponse)
async def chat(request: AgentChatRequest) -> AgentChatResponse:
    if settings.mock_agent or settings.model_backend == "mock":
        return mock_chat(request)
    response = await ollama_chat(request)
    parsed_requests = parse_tool_requests(response.content)
    response.tool_requests = parsed_requests
    return response


@app.post("/generate", response_model=AgentChatResponse)
async def generate(request: AgentChatRequest) -> AgentChatResponse:
    return await chat(request)
