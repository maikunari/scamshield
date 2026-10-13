# ScamShield

A private scam guard for elderly parents. Built for the Nebius x NVIDIA Global AI Hackathon (Personal AI track).

A parent pastes or forwards a suspicious text, email or voicemail transcript and gets a verdict: 🟢 Safe, 🟡 Careful or 🔴 Scam. The verdict comes with red flags, evidence links, a safe next step and a calm script the family can use when phoning her. ScamShield never explains paperwork: it outputs only the verdict, the red flags, the evidence and the next safe action.

**Status:** the core pipeline runs end-to-end on the command line and behind a small FastAPI service, with a spend guard and a per-IP rate limit, and the parent `/check` PWA (paste, share, three examples, full-screen verdict with read-aloud) runs against it. Email intake, family alerts, the dashboard and deployment are not built yet.

## How it works

```
normalize -> verdict cache -> redact PII -> triage (Nano) -> memory check -> evidence (Super plans, Tavily searches) -> verdict (Ultra) -> persist
```

1. **Normalize** the raw message into `{channel, sender, body, urls[], phones[], ts}` (phones to E.164).
2. **Verdict cache**: a repeat of the same message (same sender, text, parent profile and models) is answered from the cache with no model or search call, so the seed messages are free and instant after `make warm`. Degraded or failed runs are never cached.
3. **Redact** card numbers (Luhn-checked), My Number / SSN-like ids and passwords. Only redacted text reaches a model.
4. **Triage** (`MODEL_FAST`, Nemotron Nano): scam signals (urgency, money ask, impersonation, secrecy, links) and a risk level.
5. **Memory**: SQLite per-parent profile of trusted contacts and known services. A message from a trusted contact, or from a known service linking only to its own domain, takes the fast path (🟢, no search, no Ultra call). An unknown sender claiming to be family and asking for money or secrecy is floored at 75; any unknown sender asking for money is floored at 50. A sender or link that imitates a known service's domain (`sakurabank-online.test` vs `sakurabank.example.com`) is floored at 75.
6. **Evidence** (`MODEL_MID`, Nemotron Super, plans which lookups): up to 3 Tavily searches (plus at most one page extract) per message through the skills `check_phone`, `check_url` and `check_phrase`. Results are cached for 24h. The suspicious URL is never fetched, only searched about. A search hit counts as evidence only if it actually mentions the number or domain.
7. **Verdict** (`MODEL_REASON`, Nemotron Ultra): score 0–100, label, `red_flags[{text, evidence_url}]`, `safe_action`, `family_script`. A red flag can only cite a URL Tavily returned for this message; anything else is stripped. If nothing was found the verdict says "No public reports found." and invents nothing.
8. **Persist** the result with its trace. 🟢 bodies are dropped (hash and label kept) unless the family opts in.

Every step writes a trace entry (model id, prompt/completion tokens, latency, and cost computed from the listed Token Factory prices) stored with the result; the dashboard will show it as "How ScamShield decided".

### Models and structured output

Defaults are the Nemotron ids from the Token Factory catalog (see [docs/models.md](docs/models.md)): Nano `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` ($0.06 / $0.24 per 1M tokens in / out), Super `nvidia/nemotron-3-super-120b-a12b` ($0.30 / $0.90), Ultra `nvidia/Nemotron-3-Ultra-550b-a55b` ($1.00 / $3.00), and `MODEL_LONG` `nvidia/Nemotron-3_5-Lightning` for a future digest. Model ids are compared case-insensitively because the catalog's casing is inconsistent.

Each model step asks for `response_format: json_schema` (the schema is also repeated in the prompt). A model that rejects it falls back to `json_object`, and a reply that still does not parse gets one repair call. Reasoning/thinking blocks are stripped before parsing. The trace notes when a step did not use plain `json_schema`.

Tavily is called with `search_depth: basic` (1 credit), `topic: news` for phrase lookups, at most 3 searches and at most one `extract` (a page a search returned, never the message's own link) per message.

### Budget guard

`api/scamshield/budget.py` keeps a spend ledger in the same SQLite database and decides a mode before every paid call:

| Mode | When | Effect |
|---|---|---|
| `normal` | within all caps | as routed |
| `degraded` | daily Ultra calls or dollars reached, or 80% of the total cap spent | every model step runs on Nano; searches still run |
| `exhausted` | total spend cap reached | no paid call; rule-based "could not fully check, ask your family" verdict |
| `killed` | `BUDGET_KILL_SWITCH=1`, or the file `BUDGET_KILL_FILE` exists | same as exhausted |

Cached verdicts and cached searches are free, so they are served in every mode. `GET /budget` shows the current state; results carry `budget_mode`.

### Analyze endpoint

`POST /parents/{id}/analyze` and `POST /api/check` are limited per client IP (`RATE_LIMIT_PER_MINUTE`, default 10, `429` with `Retry-After`). `X-Forwarded-For` is ignored unless `TRUST_FORWARDED_FOR=1` (set it only behind a proxy you control).

### Skills as tools

`api/scamshield/tools.py` exposes `check_message`, `check_phone`, `check_url`, `check_phrase` and `compare_to_contacts` as plain functions: `Toolbox(pipeline).call(name, arguments)` takes and returns JSON-serialisable dicts, `Toolbox(pipeline).specs()` is the MCP `tools/list` shape, and nothing is global. A stdio MCP server (for Hermes or NemoClaw) is a thin wrapper over those two methods.

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

### Parent PWA (`web/`)

Requires Node 20.9+.

```bash
make web-setup            # npm install
make dev                  # API on :8000 + Next.js dev server on :3000 (proxies /api/*); open http://localhost:3000/check/
make serve                # build the static export, then serve it *and* the API from FastAPI on :8000
make docker               # the same, in one container (docker compose up --build)
```

`/check` is the parent's whole product: a paste box, one big **Check this message** button, three "Try an example"
buttons (grandchild-in-trouble, parcel redelivery fee, and the real grandson from a trusted number), and a
full-screen 🔴 / 🟡 / 🟢 verdict with the top red flags, evidence links, the safe next step and a **Read this to me**
button (browser speech API). It is an installable PWA; on Android the installed app is a **share target**, so
"Share → ScamShield" from the messaging app opens `/check/?text=…` and checks the message straight away.

Design notes: the type is Atkinson Hyperlegible Next (Braille Institute, OFL) at 22px base with 76px+ tap targets;
colour is never the only signal (icon + word on every verdict); `prefers-reduced-motion` turns the animations off.
There are no third-party assets or trademarks; the icon is an original shield mark.

`POST /api/check` takes `{body, sender?, channel?, subject?}` and returns only what the parent screen shows:
`{label, score, red_flags[{text, evidence_url}], safe_action, evidence_note, contact, family{name}, offline, budget_mode}`.
The family call script and the trace stay on the family side (`GET /results/{id}`). The endpoint analyses for the
demo parent (`SCAMSHIELD_DEMO_PARENT`, default `michiko`) and loads her seed profile into the database on first use.

### Environment variables

| Variable | Purpose |
|---|---|
| `NEBIUS_API_KEY` | Token Factory key. Missing: the LLM client is the offline fake. |
| `NEBIUS_BASE_URL` | OpenAI-compatible base URL. Default `https://api.tokenfactory.nebius.com/v1/`. |
| `MODEL_FAST` / `MODEL_MID` / `MODEL_REASON` / `MODEL_LONG` | Model ids for triage (Nano), query planning (Super), verdict (Ultra), digest (Lightning, unused so far). Default to the catalog ids in `docs/models.md`. |
| `TAVILY_API_KEY` | Tavily key. Missing: the search client replays `seed/search_fixtures.json`. |
| `SCAMSHIELD_OFFLINE=1` | Force both fakes even if keys are set. |
| `SCAMSHIELD_DB` | SQLite path for the API (default `scamshield.db`). |
| `SCAMSHIELD_SEED_DIR` | Seed directory (default `seed/`). |
| `SCAMSHIELD_DEMO_PARENT` | Parent id that `POST /api/check` analyses for (default `michiko`, seeded from `seed/profile.json`). |
| `SCAMSHIELD_WEB_DIR` | Next.js static export to serve at `/` (default `web/out`; skipped when absent). |
| `SCAMSHIELD_API_ORIGIN` | Dev only: where `next dev` proxies `/api/*` (default `http://127.0.0.1:8000`). |
| `PRICE_<FAST\|MID\|REASON\|LONG>_IN` / `_OUT` | Override the USD per 1M tokens for a tier. Default: the listed price of the model id. |
| `TAVILY_COST_PER_CALL` | USD per Tavily credit for the cost estimate (default 0.008; an extract is 1/5 of that). |
| `BUDGET_DAILY_ULTRA_CALLS` / `BUDGET_DAILY_ULTRA_USD` | Daily Ultra caps; past either, Nano-only (defaults 300 / 10.0). |
| `BUDGET_TOTAL_USD` | Total spend cap (default 40.0); Nano-only from 80%, no paid calls at 100%. |
| `BUDGET_KILL_SWITCH` / `BUDGET_KILL_FILE` | `1`, or a path whose existence turns the kill switch on. |
| `RATE_LIMIT_PER_MINUTE` / `TRUST_FORWARDED_FOR` | Per-IP limit on analyze (0 disables); trust `X-Forwarded-For` behind a proxy. |

### Discover models and smoke-test the real services

Once the keys exist:

```bash
NEBIUS_API_KEY=... make models      # GET {NEBIUS_BASE_URL}/models -> docs/models.md; warns if a default id is not listed
NEBIUS_API_KEY=... TAVILY_API_KEY=... make smoke
make warm                           # analyze the seed set once and pin the verdicts in SCAMSHIELD_DB
```

`make smoke` (`scripts/smoke.py`) checks that the default ids are listed (case-insensitive), that Nano, Super and Ultra each honour `response_format: json_schema` with no fallback, and runs seed message `m02` end to end (triage, plan, a live Tavily search, verdict) on a throwaway database, then prints PASS/FAIL per check and the trace. It exits 2 without both keys and costs a few cents. Findings for the hackathon's feedback section go in [docs/feedback.md](docs/feedback.md).

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
| 🔴 scam | 1.00 | 1.00 | 1.00 | 15 |
| 🟡 careful | 1.00 | 1.00 | 1.00 | 3 |
| 🟢 safe | 1.00 | 1.00 | 1.00 | 8 |
| **accuracy** | | | **1.00** | 26 |

`m15` (a phishing "statement is ready" email from the look-alike domain `sakurabank-online.test`) used to score 🟡. That was a gap in the rules, not just in the fake: the memory step now flags a sender or link whose host contains a known service's brand label (`sakurabank`) without being its domain, and a guardrail floors such a message at 75 whatever the model says. These numbers measure the keyword fake and the rules around it, not Nemotron. `python scripts/eval.py --markdown` prints just the table; `--fail-on-red-as-green` exits non-zero if any scam is called safe.

## Privacy

- Message text is PII-redacted before any model call; Tavily queries contain only phone numbers, domains and a short redacted phrase from the message.
- Model calls go only to the configured Token Factory base URL and searches only to `api.tavily.com`. Nothing else is contacted, and suspicious links are never opened.
- Data stays in your own SQLite database. The verdict cache stores the same thing as the analyses table: a 🟢 body is dropped unless the family opted in.

## Layout

```
api/        Python 3.12 FastAPI core API (uv, pytest, Dockerfile)
web/        Next.js 16 static export: the parent /check PWA (app/, components/, lib/, public/manifest + sw.js)
scripts/    discover_models.py, eval.py, smoke.py, dev.sh
seed/       messages.jsonl, expected.json, profile.json, search_fixtures.json
docs/       models.md, feedback.md
```

The Dockerfile builds from the repo root and produces one image that serves the PWA and the API:
`docker build -f api/Dockerfile -t scamshield .` (or `docker compose up --build`).

## License

MIT
