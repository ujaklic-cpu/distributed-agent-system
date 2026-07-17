FROM python:3.12-slim

WORKDIR /app

COPY shared/ ./shared/
COPY orchestrator/ ./orchestrator/
COPY node_agent/ ./node_agent/
COPY agent_server/ ./agent_server/
COPY pyproject.toml ./

RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir uvicorn httpx pydantic pydantic-settings psycopg2-binary redis celery minio

EXPOSE 8080

COPY orchestrator/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["sh", "/app/entrypoint.sh"]
