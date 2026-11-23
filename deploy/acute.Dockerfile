# Multi-stage production container for Clinic AI Harness (Flu-Season Acute Care MVP)
# Builds the 4-surface React/TypeScript/Vite PWA and serves it alongside the FastAPI backend.

FROM node:24-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund --no-package-lock
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HARNESS_REGION=asia-east2 \
    HARNESS_ENV=staging

WORKDIR /app

# Install Noto CJK fonts for native Traditional Chinese MCHK sick-leave PDFs & create non-root user
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

COPY pyproject.toml ./
COPY backend/ ./backend/
COPY protocols/ ./protocols/
COPY demo/ec-care-line/ ./demo/ec-care-line/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN pip install --no-cache-dir -e "." \
    "sqlalchemy>=2.0" "psycopg[binary]>=3.2" "alembic>=1.14" \
    "cryptography>=43.0" "pyjwt>=2.9" "fpdf2>=2.8" \
    "python-multipart" "google-auth>=2.35" "requests>=2.32" "google-cloud-storage>=2.18" \
    "httpx>=0.28" "pyyaml>=6.0" \
    && mkdir -p /tmp/tmrw-vault && chown -R app:app /app /tmp/tmrw-vault

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8080)}/api/v1/health')" || exit 1

CMD ["sh", "-c", "uvicorn backend.acute.app:app_from_env --factory --host 0.0.0.0 --port ${PORT:-8080}"]
