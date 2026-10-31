FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sentinel_payflow ./sentinel_payflow
COPY tests ./tests
COPY .env.example ./.env.example

RUN useradd -m sentinel && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8080

CMD ["uvicorn", "sentinel_payflow.api:app", "--host", "0.0.0.0", "--port", "8080"]
