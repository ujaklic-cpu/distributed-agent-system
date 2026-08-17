FROM python:3.14.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends bash curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY shared /app/shared
COPY node_agent /app/node_agent
COPY node-agent/requirements.txt /app/node-agent/requirements.txt
RUN pip install --no-cache-dir -r /app/node-agent/requirements.txt

ENV PYTHONPATH=/app
USER 10001
EXPOSE 8081
CMD ["uvicorn", "node_agent.app:app", "--host", "0.0.0.0", "--port", "8081"]
