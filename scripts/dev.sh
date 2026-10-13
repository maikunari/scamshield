#!/usr/bin/env bash
# Run the API (:8000) and the Next.js dev server (:3000) together. The dev server proxies /api/* to
# the API, so open http://localhost:3000/check/. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-uv}
API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-3000}

[ -d web/node_modules ] || (cd web && npm install --no-audit --no-fund)

"$UV" run --project api uvicorn scamshield.main:app --reload --port "$API_PORT" &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM

SCAMSHIELD_API_ORIGIN="http://127.0.0.1:$API_PORT" npm --prefix web run dev -- --port "$WEB_PORT"
