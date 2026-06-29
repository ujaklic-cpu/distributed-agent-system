FROM python:3.12.8-slim

WORKDIR /app
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY shared /app/shared
COPY agent_server /app/agent_server
COPY agent-server/requirements.txt /app/agent-server/requirements.txt
RUN pip install --no-cache-dir -r /app/agent-server/requirements.txt

ENV PYTHONPATH=/app
USER 10001
EXPOSE 8090
CMD ["uvicorn", "agent_server.app:app", "--host", "0.0.0.0", "--port", "8090"]
