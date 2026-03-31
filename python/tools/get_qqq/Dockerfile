FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DRY_RUN=1

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY index_notify.py /app/index_notify.py

CMD ["python", "/app/index_notify.py"]
