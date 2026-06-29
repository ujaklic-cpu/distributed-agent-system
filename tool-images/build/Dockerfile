FROM python:3.12.8-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git ca-certificates build-essential make cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir build==1.2.2.post1 wheel==0.45.1 setuptools==75.6.0
WORKDIR /tool
USER 1000:1000
COPY tool-images/common_runner.py /tool/common_runner.py
COPY tool-images/build/runner.py /tool/runner.py
CMD ["python", "/tool/runner.py"]
