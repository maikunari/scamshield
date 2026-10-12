# ScamShield

A private scam guard for elderly parents. Built for the Nebius x NVIDIA Global AI Hackathon (Personal AI track).

A parent pastes or forwards a suspicious text, email or voicemail transcript and gets a verdict: 🟢 Safe, 🟡 Careful or 🔴 Scam. The verdict comes with red flags, evidence links, a safe next step and a calm script the family can use when phoning her. ScamShield never explains paperwork: it outputs only the verdict, the red flags, the evidence and the next safe action.

**Status:** the core pipeline (this repo's first slice) runs end-to-end on the command line. The parent PWA, email intake, family alerts and dashboard are not built yet.

## How it works

```
normalize -> redact PII -> triage (Nano) -> memory check -> evidence (Super plans, Tavily searches) -> verdict (Ultra) -> persist
```

1. **Normalize** the raw message into `{channel, sender, body, urls[], phones[], ts}` (phones to E.164).
2. **Redact** card numbers (Luhn-checked), My Number / SSN-like ids and passwords. Only redacted text reaches a model.
3. **Triage** (`MODEL_FAST`, Nemotron Nano): scam signals (urgency, money ask, impersonation, secrecy, links) and a risk level.
4. **Memory**: SQLite per-parent profile of trusted contacts and known services. A message from a trusted contact, or from a known service linking only to its own domain, takes the fast path (🟢, no search, no Ultra call). An unknown sender claiming to be family and asking for money or secrecy is floored at 75; any unknown sender asking for money is floored at 50.
5. **Evidence** (`MODEL_MID`, Nemotron Super, plans which lookups): up to 3 Tavily searches per message through the skills `check_phone`, `check_url` and `check_phrase`. Results are cached for 24h. The suspicious URL is never fetched, only searched about. A search hit counts as evidence only if it actually mentions the number or domain.
6. **Verdict** (`MODEL_REASON`, Nemotron Ultra): score 0–100, label, `red_flags[{text, evidence_url}]`, `safe_action`, `family_script`. A red flag can only cite a URL Tavily returned for this message; anything else is stripped. If nothing was found the verdict says "No public reports found." and invents nothing.
7. **Persist** the result with its trace. 🟢 bodies are dropped (hash and label kept) unless the family opts in.

Every step writes a trace entry (model, tokens, latency, estimated cost) stored with the result; the dashboard will show it as "How ScamShield decided".

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
make setup                # uv sync --project api
make test                 # pytest
make eval                 # eval over the seed set
uv run --project api scamshield analyze --seed-id m10      # one seed message, with trace
uv run --project api scamshield analyze --sender 212-555-0142 --body "Grandma it's me, I need bail money today"
make api                  # FastAPI on :8000
```

### Environment variables

| Variable | Purpose |
|---|---|
| `NEBIUS_API_KEY` | Token Factory key. Missing: the LLM client is the offline fake. |
| `NEBIUS_BASE_URL` | OpenAI-compatible base URL. Default `https://api.tokenfactory.nebius.com/v1/` (**unverified**). |
| `MODEL_FAST` / `MODEL_MID` / `MODEL_REASON` | Model ids for triage (Nano), query planning (Super), verdict (Ultra). Required when a key is set; pick them from `docs/models.md`. |
| `TAVILY_API_KEY` | Tavily key. Missing: the search client replays `seed/search_fixtures.json`. |
| `SCAMSHIELD_OFFLINE=1` | Force both fakes even if keys are set. |
| `SCAMSHIELD_DB` | SQLite path for the API (default `scamshield.db`). |
| `SCAMSHIELD_SEED_DIR` | Seed directory (default `seed/`). |
| `PRICE_<FAST\|MID\|REASON>_IN` / `_OUT` | USD per 1M tokens used for the cost estimate. Defaults are placeholders. |
| `TAVILY_COST_PER_CALL` | USD per live Tavily search for the cost estimate (default 0.008, placeholder). |

### Discover models

Once the key exists:

```bash
NEBIUS_API_KEY=... make models      # GET {NEBIUS_BASE_URL}/models -> docs/models.md
export MODEL_FAST=... MODEL_MID=... MODEL_REASON=...
```

`docs/models.md` lists every model and the Nemotron Nano / Super / Ultra candidates by name. It is a placeholder until that is run.

## Offline mode

Without keys (or with `SCAMSHIELD_OFFLINE=1`) the whole pipeline, the tests and the eval run on deterministic fakes:

- `FakeLLMClient` answers triage, planning and verdicts with English/Japanese keyword heuristics. It is a stand-in for wiring and tests, **not a measure of model quality**.
- `FakeSearchClient` replays `seed/search_fixtures.json`, synthetic recorded results on example/`.test` hosts. Identifiers with no fixture return nothing, as Tavily does for numbers nobody has reported.

Results are marked `offline: true` and the CLI prints `[offline fakes]`.

## Eval

`scripts/eval.py` runs the 26 seed messages (`seed/messages.jsonl`) through the pipeline with the Michiko profile (`seed/profile.json`) and compares the label to the gold labels in `seed/expected.json`. The seed mixes ten scam patterns, three Japanese variants (オレオレ詐欺, delivery SMS, ATM refund), an unknown-number "grandson" message next to the real grandson's, and safe look-alikes (bank statement, pharmacy, clinic, shop order). All numbers use reserved or invalid ranges (`212-555-01xx`, `090-0000-xxxx`) and all links are example.com / `.test` hosts.

Offline fakes (the model-backed numbers will replace these once keys are available):

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| 🔴 scam | 1.00 | 0.93 | 0.97 | 15 |
| 🟡 careful | 0.75 | 1.00 | 0.86 | 3 |
| 🟢 safe | 1.00 | 1.00 | 1.00 | 8 |
| **accuracy** | | | **0.96** | 26 |

The one miss is `m15`, a phishing "statement is ready" email from a look-alike bank domain that the keyword fake scores 🟡. `python scripts/eval.py --markdown` prints just the table; `--fail-on-red-as-green` exits non-zero if any scam is called safe.

## Privacy

- Message text is PII-redacted before any model call; Tavily queries contain only phone numbers, domains and a short redacted phrase from the message.
- Model calls go only to the configured Token Factory base URL and searches only to `api.tavily.com`. Nothing else is contacted, and suspicious links are never opened.
- Data stays in your own SQLite database.

## Layout

```
api/        Python 3.12 FastAPI core API (uv, pytest, Dockerfile)
scripts/    discover_models.py, eval.py
seed/       messages.jsonl, expected.json, profile.json, search_fixtures.json
docs/       models.md
web/        (later) Next.js frontend
```

The Dockerfile builds from the repo root: `docker build -f api/Dockerfile -t scamshield-api .` (not yet built in CI; untested).

## License

MIT
