FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DIGVPS_STATE_FILE=/data/digvps_state.json \
    DIGVPS_OUTPUT_FILE=/data/digvps_updates.log \
    DIGVPS_TIMEZONE=Asia/Shanghai \
    DIGVPS_NOTIFY_ON_FIRST_RUN=0 \
    DIGVPS_MIN_CHECK_INTERVAL_SECONDS=600 \
    DIGVPS_REQUEST_TIMEOUT=20 \
    DIGVPS_FETCH_RETRIES=2 \
    DIGVPS_RETRY_BACKOFF_SECONDS=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY digvps_daily_update.py /app/digvps_daily_update.py

VOLUME ["/data"]

CMD ["python", "/app/digvps_daily_update.py"]

