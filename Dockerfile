FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --shell /usr/sbin/nologin worker

USER worker

CMD ["uvicorn", "github_oauth_worker.app:app", "--host", "0.0.0.0", "--port", "8080"]
