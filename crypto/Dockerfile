# Dependency-free by design: the websocket client, HTTP server and Telegram
# sender are all stdlib, matching stats/server.py. Nothing to audit, nothing
# to pull at build time, and no supply chain for an alerting path that
# handles money.
FROM python:3.12-alpine

RUN adduser -D -u 1000 crypto
WORKDIR /app
COPY app/ /app/app/
COPY web/ /app/web/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CRYPTO_DB=/data/crypto.db \
    CRYPTO_WEB=/app/web \
    CRYPTO_PORT=9102

EXPOSE 9102
USER crypto

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9102/api/health',timeout=4).status==200 else 1)"

CMD ["python3", "/app/app/main.py"]
