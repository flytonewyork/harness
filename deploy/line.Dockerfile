# The doctor's own line (spec 005), for Cloud Run in asia-east2.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir ".[line]"
RUN useradd --create-home app
USER app
CMD ["python", "-m", "backend.line", "serve"]
