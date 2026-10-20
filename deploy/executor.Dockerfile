FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.146.0 uvicorn==0.54.0 httpx==0.28.1 \
    deepseek-harness-sdk==0.2.1rc2 && \
    useradd -u 10001 -M companion-proxy && useradd -u 10002 -M companion-dsh && \
    mkdir /state /scratch
# The image contains no Core database layer, QQ bridge, configuration or credentials.
COPY companion/__init__.py companion/executive_protocol.py companion/executor_gateway.py \
    companion/executor_budget_proxy.py companion/executor_files.py companion/dsh_job.py /app/companion/
CMD ["python", "-m", "uvicorn", "companion.executor_gateway:app_from_env", "--factory", \
     "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
