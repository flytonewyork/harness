# Lightweight container for the 24/7 Autonomous Cloud Infra On-Call SRE Agent (GCP asia-east2)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    GCP_REGION=asia-east2

WORKDIR /app

RUN groupadd --system sre && useradd --system --gid sre --home /app sre

COPY pyproject.toml ./
COPY backend/ ./backend/

RUN pip install --no-cache-dir -e "." \
    "httpx>=0.28" "google-auth>=2.35" "requests>=2.32" "pyyaml>=6.0" \
    "sqlalchemy>=2.0" "cryptography>=43.0" "pyjwt>=2.9" \
    && mkdir -p /tmp/tmrw-sre && chown -R sre:sre /app /tmp/tmrw-sre

USER sre
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8080)}/health')" || exit 1

CMD ["sh", "-c", "uvicorn backend.sre.app:app_from_env --factory --host 0.0.0.0 --port ${PORT:-8080}"]
