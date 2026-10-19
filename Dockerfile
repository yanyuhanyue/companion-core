FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy HF_HUB_DISABLE_XET=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock ./
COPY companion ./companion
COPY migrations ./migrations
COPY db ./db
RUN uv sync --frozen --no-dev && useradd -m -u 10001 companion && \
    mkdir -p /data /privacy && chown companion:companion /data /privacy
USER companion
ENTRYPOINT ["/app/.venv/bin/python", "-m", "companion"]
CMD ["serve", "--host", "0.0.0.0"]
