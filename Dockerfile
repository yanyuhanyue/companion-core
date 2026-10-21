FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy HF_HUB_DISABLE_XET=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock ./
COPY companion ./companion
COPY source_reader ./source_reader
COPY companion_onebot_bridge ./companion_onebot_bridge
COPY migrations ./migrations
COPY db ./db
# Public source archives may have owner-only directories after private extraction.
RUN chmod -R a+rX companion source_reader companion_onebot_bridge migrations db && \
    uv sync --frozen --no-dev && useradd -m -u 10001 companion && \
    mkdir -p /data /privacy && chown companion:companion /data /privacy
USER companion
ENTRYPOINT ["/app/.venv/bin/python", "-m", "companion"]
CMD ["serve", "--host", "0.0.0.0"]
