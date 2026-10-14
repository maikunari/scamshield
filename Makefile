UV ?= uv

.PHONY: setup test eval eval-md models smoke warm api web-setup web-build web-test web-dev dev serve docker
setup:
	$(UV) sync --project api
test:
	$(UV) run --project api pytest
eval:
	$(UV) run --project api python scripts/eval.py
eval-md:
	$(UV) run --project api python scripts/eval.py --markdown
models:
	$(UV) run --project api python scripts/discover_models.py
smoke:
	$(UV) run --project api python scripts/smoke.py
warm:
	$(UV) run --project api scamshield warm
api:
	$(UV) run --project api uvicorn scamshield.main:app --reload --port 8000
web-setup:
	cd web && npm install --no-audit --no-fund
web-test:
	cd web && npm test
web-build:
	cd web && npm run build
web-dev:
	cd web && npm run dev
dev:
	UV=$(UV) scripts/dev.sh
serve: web-build
	$(UV) run --project api uvicorn scamshield.main:app --port 8000
docker:
	docker compose up --build
