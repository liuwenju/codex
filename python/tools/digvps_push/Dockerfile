FROM python:3.11-slim

WORKDIR /app

# 安装依赖
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY digvps_update_push.py /app/digvps_update_push.py

RUN pip install --no-cache-dir requests beautifulsoup4 lxml

RUN chmod +x /app/digvps_update_push.py

# 运行一次脚本（建议外部用 cron / k8s 定时调度）
ENTRYPOINT ["python", "/app/digvps_update_push.py"]

