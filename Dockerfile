FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && git config --global --add safe.directory /app \
    && git config --global user.name "Reprofy Bot" \
    && git config --global user.email "reprofy@localhost" \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[webdemo]"
COPY examples/shop ./examples/shop

EXPOSE 8000 8001
