FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Tesseract reads scanned claim pages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --no-dev --no-install-project

COPY . .

RUN useradd --create-home appuser \
    && mkdir -p /home/appuser/fastembed \
    && chown -R appuser /srv /home/appuser
USER appuser
ENV FASTEMBED_CACHE_PATH=/home/appuser/fastembed

EXPOSE 8000
# Readiness, not just liveness: the container is unhealthy if it cannot reach the database.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/ready', timeout=2)"
# The request middleware writes the access log (as JSON), so uvicorn's own is off.
CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
