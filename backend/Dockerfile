# syntax=docker/dockerfile:1
# KAPITAL backend — FastAPI (uvicorn) + Celery worker/beat share this image.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs: libmagic (python-magic file sniffing) + curl (healthcheck).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching. The win32-only magic wheel is skipped
# automatically by the platform marker in requirements.txt.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Non-root runtime user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default command runs the API. The worker/beat services override `command` in
# docker-compose with the same image.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
