# Build from the repo root:  docker build -f api/Dockerfile -t scamshield .
# One container serves both the core API (/api/*) and the parent PWA (the Next.js static export at /).

# --- stage 1: the parent PWA ------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

# --- stage 2: the API, with the export mounted at / ---------------------------------------------------
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock api/.python-version ./
COPY api/scamshield ./scamshield
RUN uv sync --frozen --no-dev

COPY seed /app/seed
COPY --from=web /web/out /app/web
ENV PATH="/app/api/.venv/bin:$PATH" \
    SCAMSHIELD_SEED_DIR=/app/seed \
    SCAMSHIELD_WEB_DIR=/app/web \
    SCAMSHIELD_DB=/data/scamshield.db
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "scamshield.main:app", "--host", "0.0.0.0", "--port", "8000"]
