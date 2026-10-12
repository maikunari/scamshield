# Build from the repo root:  docker build -f api/Dockerfile -t scamshield-api .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock api/.python-version ./
COPY api/scamshield ./scamshield
RUN uv sync --frozen --no-dev

COPY seed /app/seed
ENV PATH="/app/api/.venv/bin:$PATH" \
    SCAMSHIELD_SEED_DIR=/app/seed \
    SCAMSHIELD_DB=/data/scamshield.db
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "scamshield.main:app", "--host", "0.0.0.0", "--port", "8000"]
