# The upstream shell release is newer than its published Docker image.
# Keep the official runtime/entrypoint and replace only the verified release archive.
FROM mlikiowa/napcat-docker@sha256:f611790ecffa23c3c182017d5cf68179dc24612403608cd835ad13d79642f1bd
COPY NapCat.Shell.zip /app/NapCat.Shell.zip
RUN echo '735f2e633247df3ffd6e8ee8b84d7039c315b2655f5807e5f0029f93d2c30016  /app/NapCat.Shell.zip' | sha256sum -c - \
    && test ! -f /app/napcat/napcat.mjs \
    && unzip -tq /app/NapCat.Shell.zip
LABEL org.opencontainers.image.version="4.18.38" \
      org.opencontainers.image.source="https://github.com/NapNeko/NapCatQQ" \
      org.opencontainers.image.description="Official NapCat 4.18.38 shell on official 4.18.37 Docker runtime"
