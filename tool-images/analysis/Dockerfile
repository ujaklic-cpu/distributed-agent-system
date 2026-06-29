FROM python:3.12.8-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ruff==0.8.4 mypy==1.14.0 bandit==1.8.0
WORKDIR /tool
USER 1000:1000
COPY tool-images/common_runner.py /tool/common_runner.py
COPY tool-images/analysis/runner.py /tool/runner.py
CMD ["python", "/tool/runner.py"]
