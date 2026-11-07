# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# ---- dependencies are cached independently from application code ----
FROM base AS deps
COPY pyproject.toml README.md ./
RUN python -m venv /app/.venv \
 && /app/.venv/bin/pip install --upgrade pip setuptools wheel \
 && /app/.venv/bin/pip install .

# ---- runtime image: non-root, no build tooling ----
FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
COPY . .
RUN useradd --create-home --shell /bin/bash app \
 && mkdir -p staticfiles media \
 && chown -R app:app /app
USER app

ENV DJANGO_SETTINGS_MODULE=web.config.settings

EXPOSE 8000 8001 8002
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
