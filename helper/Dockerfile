# Dependency-free, like crypto/ and stats/: stdlib Telegram client, no pip.
# This container holds the hub's logins, so nothing is pulled at build time.
FROM python:3.12-alpine

RUN adduser -D -u 1000 helper
WORKDIR /app
COPY app/ /app/app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HELPER_DB=/data/helper.db

USER helper

# The poll loop touches /tmp/heartbeat at least every ~50 s (long-poll length).
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c "import time,sys; sys.exit(0 if time.time()-int(open('/tmp/heartbeat').read())<180 else 1)"

CMD ["python3", "/app/app/bot.py"]
