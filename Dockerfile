FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

RUN pip install --upgrade pip && pip install .

RUN pip install playwright && playwright install --with-deps chromium

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN groupadd --system --gid 10001 jobpilot \
    && useradd --system --uid 10001 --gid jobpilot --home-dir /app jobpilot \
    && chown -R jobpilot:jobpilot /app /ms-playwright /entrypoint.sh

USER jobpilot

ENTRYPOINT ["/entrypoint.sh"]
