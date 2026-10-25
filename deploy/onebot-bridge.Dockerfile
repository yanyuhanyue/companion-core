FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY deploy/onebot-bridge.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && \
    useradd -m -u 10004 bridge && mkdir -m 0700 /bridge-data && \
    chown bridge:bridge /bridge-data
COPY companion_onebot_bridge ./companion_onebot_bridge
COPY companion_meme_protocol ./companion_meme_protocol
COPY companion_meme_sources ./companion_meme_sources
COPY companion_delivery_protocol ./companion_delivery_protocol
COPY scripts/probe_snowluma_direct.py ./scripts/probe_snowluma_direct.py
USER bridge
ENTRYPOINT ["python", "-m", "companion_onebot_bridge"]
