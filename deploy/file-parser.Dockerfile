FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TMPDIR=/scratch HOME=/scratch
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer && \
    rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv==0.12.10
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --only-group file-parser --no-emit-project --format requirements-txt \
      --output-file /tmp/file-parser.txt && \
    uv pip install --system --require-hashes -r /tmp/file-parser.txt && \
    useradd -m -u 10003 companion-parser && mkdir /scratch && chown 10003:10003 /scratch
# The worker image contains no Core, transport, storage or provider credential code.
COPY companion/file_parser.py /app/file_parser.py
RUN chmod a+r /app/file_parser.py && soffice --version > /app/libreoffice-version.txt
USER 10003:10003
ENTRYPOINT ["python", "/app/file_parser.py"]
