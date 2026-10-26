# Synthetic Linux check runtime; supply already verified immutable image IDs.
ARG CORE_IMAGE
ARG READER_IMAGE
FROM ${CORE_IMAGE} AS core
FROM ${READER_IMAGE}
USER root
WORKDIR /app
COPY --from=core /app/.venv /app/.venv
COPY --from=core /usr/local/bin/uv /usr/local/bin/uv
COPY . .
RUN uv sync --frozen --dev --group reader && \
    apt-get update && apt-get install -y --no-install-recommends nginx && \
    rm -rf /var/lib/apt/lists/*
ENV PATH=/app/.venv/bin:$PATH COMPANION_TEST_BROWSER=1 COMPANION_TEST_CONSOLE_PROXY=1
USER 10003:10003
ENTRYPOINT ["/app/.venv/bin/python"]
