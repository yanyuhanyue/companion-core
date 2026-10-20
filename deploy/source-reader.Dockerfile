FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --only-group reader --no-emit-project --format requirements-txt \
      --output-file /tmp/reader.txt && \
    uv pip install --system --require-hashes -r /tmp/reader.txt && \
    python -m playwright install --with-deps chromium --only-shell && \
    useradd -m -u 10003 companion-reader && mkdir /scratch && chown 10003:10003 /scratch
COPY source_reader ./source_reader
USER 10003:10003
ENV TMPDIR=/scratch
ENTRYPOINT ["python", "-m", "source_reader"]
