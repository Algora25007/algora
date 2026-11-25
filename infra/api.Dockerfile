# API image. Build from the repository root:
#   docker build -f infra/api.Dockerfile -t algora-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 algora
WORKDIR /srv

# Engine first: it changes less often than the API, so this layer caches well.
COPY engine /srv/engine
RUN pip install /srv/engine
COPY api /srv/api
RUN pip install /srv/api

WORKDIR /srv/api
USER algora
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Apply migrations, then serve. Put a TLS-terminating proxy in front in every non-local environment.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.asgi:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
