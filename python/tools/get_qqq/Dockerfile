FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY index_notify.py /app/index_notify.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/index_notify.py"]

