FROM eclipse-temurin:25-jre-noble AS java
FROM python:3.12-slim-bookworm
COPY --from=java /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk PATH="/opt/java/openjdk/bin:${PATH}" PYTHONUNBUFFERED=1 HYPANEL_FRONTEND=/app/frontend
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip libstdc++6 procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# The public downloader contains no server files or account credentials.
RUN curl --fail --location --retry 3 https://downloader.hytale.com/hytale-downloader.zip -o /tmp/downloader.zip \
    && unzip -j /tmp/downloader.zip hytale-downloader-linux-amd64 -d /opt \
    && mv /opt/hytale-downloader-linux-amd64 /opt/hytale-downloader \
    && chmod 755 /opt/hytale-downloader && rm /tmp/downloader.zip
COPY pyproject.toml /app/
COPY backend /app/backend
RUN pip install --no-cache-dir .
COPY frontend /app/frontend
EXPOSE 8080/tcp
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3) if os.getenv('HYPANEL_MODE','panel')=='panel' else None"
ENTRYPOINT ["python3", "-m", "hypanel"]
