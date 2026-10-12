UV ?= uv

.PHONY: setup test eval eval-md models api
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
api:
	$(UV) run --project api uvicorn scamshield.main:app --reload --port 8000
