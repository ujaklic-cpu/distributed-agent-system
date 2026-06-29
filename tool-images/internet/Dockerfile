FROM python:3.12.8-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl wget git ca-certificates dnsutils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tool
USER 1000:1000
COPY tool-images/common_runner.py /tool/common_runner.py
COPY tool-images/internet/runner.py /tool/runner.py
CMD ["python", "/tool/runner.py"]
