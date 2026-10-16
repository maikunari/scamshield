# ScamShield

A private scam guard for elderly parents. Built for the Nebius x NVIDIA Global AI Hackathon (Personal AI track).

A parent pastes or forwards a suspicious text, email or voicemail transcript and gets a verdict: 🟢 Safe, 🟡 Careful or 🔴 Scam. The verdict comes with red flags, evidence links, a safe next step and a few short notes the family reads before phoning her (what the message is, the red flag that gives it away, what to tell her). ScamShield never explains paperwork: it outputs only the verdict, the red flags, the evidence and the next safe action.

**Status:** built and live at <https://scamshield.sonicpixel.io>. The core pipeline runs end-to-end behind a FastAPI service with a spend guard and a per-IP rate limit; the parent `/check` PWA (paste, share, three examples, full-screen verdict with read-aloud), family alerts (Telegram and Resend email on 🔴, with the "I called her" acknowledgement), the passphrase-gated family dashboard, privacy controls and the one-click demo family are all working. **Not built:** forward-to-email intake, the nightly job and the weekly digest.
n**Demo video:** https://youtu.be/CbP4Uv6cMYg

**Where it runs.** The app (the FastAPI API and the static PWA from one app) is hosted on a DigitalOcean droplet. It is not hosted on Nebius; only inference is: every Nemotron call goes to the Nebius Token Factory API. The reason is cost, see [docs/feedback.md](docs/feedback.md) item 4.

## Judge in 60 seconds

1. Open <https://scamshield.sonicpixel.io/>. The landing page explains both sides of the app. Tap **Load demo family** (under "Judging this?"). It resets the demo family: Margaret (74), her son David (41) and grandson Tom as trusted contacts, and five past checks including a scam that alerted David.
2. Open `/check/` (the landing page's **For the parent** button) and tap an example, for instance **“Grandma, I'm in trouble”**. It turns red. The screen says "David has been told" only when the alert really went out (Telegram or email delivered); if it was rate-limited, de-duplicated or could not be sent, it says "Tell someone in your family." instead. A live check takes about 6-10 s and shows its progress as it goes (reading the message, checking public scam reports, how many reports it found, the verdict); a well-known scam script or a trusted contact gets an early answer at once while the check finishes. A repeat of the same message is answered from the cache at once.
3. Open `/family/`, tap **Open as the demo family** (the passphrase is `scamshield-demo`). The newest check is yours: see the alert that went out and **How ScamShield decided**, the stored trace with each step's Nemotron model, tokens, latency and cost.

Past checks marked *Sample* ship with the demo family; their model ids are real but their token counts and timings are illustrative. Your own check carries a measured trace.

## How it works

```
normalize -> verdict cache -> redact PII -> memory check -> quick check -> triage (Nano) + Tavily searches, at the same time -> verdict (Ultra) -> persist
```

1. **Normalize** the raw message into `{channel, sender, body, urls[], phones[], ts}` (phones to E.164).
2. **Verdict cache**: a repeat of the same message (same sender, text, parent profile and models) is answered from the cache with no model or search call, so the seed messages are free and instant after `make warm`. Degraded or failed runs are never cached.
3. **Redact** card numbers (Luhn-checked), My Number / SSN-like ids and passwords. Only redacted text reaches a model.
4. **Memory**: SQLite per-parent profile of trusted contacts and known services. A message from a trusted contact, or from a known service linking only to its own domain, takes the fast path (🟢, no search, no Ultra call). An unknown sender claiming to be family and asking for money or secrecy is floored at 75; any unknown sender asking for money is floored at 50. A sender or link that imitates a known service's domain (`lakeviewbank-online.test` vs `lakeviewbank.example.com`) is floored at 75.
5. **Quick check** (`api/scamshield/quick.py`, no model): a well-known scam script (gift card codes, bail money, a "suspended" Social Security number, an unpaid toll, a redelivery fee...) or a look-alike address from a sender who is not a trusted contact gets an early 🔴 preview; a trusted contact whose message asks for no money, codes or secrecy gets an early 🟢 preview. It is only a preview: it never says safe for anyone else, the full check always completes, and its verdict is the answer.
6. **Triage and evidence, at the same time.** Triage (`MODEL_FAST`, Nemotron Nano) marks the scam signals (urgency, money ask, impersonation, secrecy, links), a risk level and the phrase worth searching for. For a sender who is not a known contact, the lookups that need no model (the message's link and phone number) start at once, alongside triage; the phrase lookup starts as soon as triage has picked the phrase. A fixed plan picks the lookups (first link, first number, the phrase, then the rest): up to 3 Tavily searches (plus at most one page extract) per message through the skills `check_phone`, `check_url` and `check_phrase`. Results are cached for 24h. A known scam script's own wording is searched at once, without waiting for triage. The one page extract starts on the first report page a lookup returns and gets 1.5 s more once the lookups are done; a page still loading then is left out of the verdict (pages behind a login, like Facebook posts, are never extracted). The suspicious URL is never fetched, only searched about. A search hit counts as evidence only if it actually mentions the number or domain.
7. **Verdict** (`MODEL_REASON`, Nemotron Ultra): score 0–100, label, `red_flags[{text, evidence_url}]`, `safe_action`, `family_points` (3-4 short notes for the family, not a script; ScamShield writes them itself if the model gives none). A red flag can only cite a URL Tavily returned for this message; anything else is stripped. If nothing was found the verdict says "No public reports found." and invents nothing. The memory floors and caps above are applied after the model.
8. **Persist** the result with its trace. 🟢 bodies are dropped (hash and label kept) unless the family opts in.

`POST /api/check/stream` runs the same check as `POST /api/check` and sends each step as a server-sent event (`reading`, the `quick` preview, `searching`, `found` with the number of reports, `deciding`, then the parent-screen `result`); the `/check` page shows them as they arrive.

Every step writes a trace entry (model id, prompt/completion tokens, latency, and cost computed from the listed Token Factory prices) stored with the result; the dashboard shows it as "How ScamShield decided".

### Models and structured output

Defaults are the Nemotron ids from the Token Factory catalog (see [docs/models.md](docs/models.md)): Nano `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` ($0.06 / $0.24 per 1M tokens in / out), Super `nvidia/nemotron-3-super-120b-a12b` ($0.30 / $0.90), Ultra `nvidia/Nemotron-3-Ultra-550b-a55b` ($1.00 / $3.00), and `MODEL_LONG` `nvidia/Nemotron-3_5-Lightning` for a future digest. Model ids are compared case-insensitively because the catalog's casing is inconsistent.

Both model steps run with Nemotron's reasoning switched off (`chat_template_kwargs: {"enable_thinking": false}`; `MODEL_THINKING=triage,verdict` turns it back on per step). Measured live on the sample messages, that took triage from 5-9 s to about 1.5-2.5 s and the Ultra verdict from 6-10 s to about 2.5-3 s with the same labels, and it cut the model spend per scam check by about 60%. Super is no longer on the check path: it used to plan the lookups, which cost a call and ~2 s and picked the same lookups as the fixed plan.

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
make web-test             # node --test over web/lib (verdict screen copy)
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
The family notes and the trace stay on the family side (`GET /results/{id}`). The endpoint analyses for the
demo parent (`SCAMSHIELD_DEMO_PARENT`, default `margaret`) and loads her seed profile into the database on first use.

### Family alerts

A 🔴 result from `/api/check` (or `/parents/{id}/analyze`) alerts the parent's family contacts: the trusted contacts whose relationship is `son`, `daughter` or `child`. 🟡 is not alerted (the spec batches it into a digest, a later slice). Each alert carries the verdict, the top three red flags with their evidence links, the short notes for the call and a link back to the check, on every channel that contact has:

- **Telegram**: `sendMessage` through the Bot API with an inline **I called her** button; the callback reaches `POST /api/telegram/webhook` and marks the alert acknowledged. The webhook is registered with `setWebhook` at startup only when `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and an `https` `PUBLIC_BASE_URL` are all set.
- **Email**: Resend (`POST https://api.resend.com/emails`) with HTML and plain text, and an **I called her** link (`/api/alerts/{id}/ack?t=...`). The link opens a confirm button rather than acknowledging on load, so a mail scanner that prefetches links cannot mark an unmade call as made.

A channel without credentials does not fail: the alert is stored as it would have been sent (`status: recorded`, with the payload), and `GET /parents/{id}/alerts` shows every alert, its deliveries and whether it was acknowledged. `family.notified` on the `/api/check` response is true only when something really went out. One alert is made per message per parent, and a family contact is capped at `ALERT_MAX_PER_CONTACT_PER_HOUR`.

**Linking a family member's Telegram** (demo flow): `POST /parents/margaret/telegram-link` with `{"contact": "David Reed"}` returns `{"url": "https://t.me/<bot>?start=<code>"}`. The family member opens it, taps Start, and the webhook stores their chat id. Codes are single use and valid for 7 days. Until a contact has linked, Telegram is skipped for them and email still goes out. These routes are behind the family passphrase gate (below).

### Family dashboard (`/family/`)

The adult child's page, in the same calm style as the parent app (18px base type, 44px+ targets, works at 390px). Four things:

1. **Recent checks** with verdict colour, the alerts sent per channel and the **I called her** state (also settable from the dashboard).
2. **How ScamShield decided**: the stored trace of each check as a step list with the Nemotron tier, model id, tokens, latency bar and cost per step, plus totals, the signals Nano found, the memory match and the Tavily reports. Replayed (cached) verdicts show the original run.
3. **Memory editing**: trusted contacts and known services, validated server-side (`PUT /parents/{id}/profile`); the next check uses them.
4. **Alerts**: who is alerted and on which channel, and **Link Telegram** (the `t.me` link plus a QR code; the page notices when the chat is linked). Alert emails and Telegram messages link to `/family/?check=<id>`.

**The gate.** One passphrase per family, no accounts. `POST /api/family/login {"passphrase": ...}` sets an HttpOnly session cookie and returns a bearer token (`Authorization: Bearer <token>`). Every `/parents/{id}/...` route and `/results/{id}` needs a session for that family; `/api/check`, the parent `/check` page, the email "I called her" link and the Telegram webhook (its own secret) stay public. Sessions are signed (`SESSION_SECRET`, else a secret kept in the database), last 7 days, and die when the passphrase changes. Login and demo-load attempts are limited per IP (`LOGIN_ATTEMPTS_PER_MINUTE`). The demo parent's passphrase is `FAMILY_PASSPHRASE` and defaults to the public demo one, `scamshield-demo`: **set it for a deployment that holds a real family**, and set `DEMO_LOAD_ENABLED=0` since `POST /api/demo/load` resets the demo parent's profile and history. Another family's passphrase: `scamshield set-passphrase <parent-id> <passphrase>`.

### Environment variables

| Variable | Purpose |
|---|---|
| `NEBIUS_API_KEY` | Token Factory key. Missing: the LLM client is the offline fake. |
| `NEBIUS_BASE_URL` | OpenAI-compatible base URL. Default `https://api.tokenfactory.nebius.com/v1/`. |
| `MODEL_FAST` / `MODEL_MID` / `MODEL_REASON` / `MODEL_LONG` | Model ids for triage (Nano), Super (probed by `make smoke`, not used by the check), verdict (Ultra), digest (Lightning, unused so far). Default to the catalog ids in `docs/models.md`. |
| `MODEL_THINKING` | Model steps that keep Nemotron's reasoning: `triage`, `verdict`, both comma-separated, or `none` (default: none, for speed). |
| `TAVILY_API_KEY` | Tavily key. Missing: the search client replays `seed/search_fixtures.json`. |
| `SCAMSHIELD_OFFLINE=1` | Force both fakes even if keys are set. |
| `SCAMSHIELD_DB` | SQLite path for the API (default `scamshield.db`). |
| `SCAMSHIELD_SEED_DIR` | Seed directory (default `seed/`). |
| `SCAMSHIELD_DEMO_PARENT` | Parent id that `POST /api/check` analyses for (default `margaret`, seeded from `seed/profile.json`). |
| `SCAMSHIELD_WEB_DIR` | Next.js static export to serve at `/` (default `web/out`; skipped when absent). |
| `SCAMSHIELD_API_ORIGIN` | Dev only: where `next dev` proxies `/api/*` (default `http://127.0.0.1:8000`). |
| `PRICE_<FAST\|MID\|REASON\|LONG>_IN` / `_OUT` | Override the USD per 1M tokens for a tier. Default: the listed price of the model id. |
| `TAVILY_COST_PER_CALL` | USD per Tavily credit for the cost estimate (default 0.008; an extract is 1/5 of that). |
| `BUDGET_DAILY_ULTRA_CALLS` / `BUDGET_DAILY_ULTRA_USD` | Daily Ultra caps; past either, Nano-only (defaults 300 / 10.0). |
| `BUDGET_TOTAL_USD` | Total spend cap (default 40.0); Nano-only from 80%, no paid calls at 100%. |
| `BUDGET_KILL_SWITCH` / `BUDGET_KILL_FILE` | `1`, or a path whose existence turns the kill switch on. |
| `RATE_LIMIT_PER_MINUTE` / `TRUST_FORWARDED_FOR` | Per-IP limit on analyze (0 disables); trust `X-Forwarded-For` behind a proxy. |
| `TELEGRAM_BOT_TOKEN` | Family alerts on Telegram (Bot API). Missing: Telegram alerts are recorded, not sent. |
| `TELEGRAM_WEBHOOK_SECRET` | Secret the webhook must present (`X-Telegram-Bot-Api-Secret-Token`). Required for the webhook to accept anything. |
| `RESEND_API_KEY` / `ALERT_EMAIL_FROM` | Family alerts by email through Resend; `ALERT_EMAIL_FROM` is a sender Resend has verified (`Name <alerts@your-domain>`). Either missing: email alerts are recorded, not sent. |
| `PUBLIC_BASE_URL` | Public origin of this app (`https://...`, no trailing slash). Used for the "see the check" and email "I called her" links and for `setWebhook`; without it those links are left out. |
| `FAMILY_PASSPHRASE` | Passphrase for the family dashboard (default `scamshield-demo`, public: change it for real data). |
| `SESSION_SECRET` | Signs dashboard sessions. Unset: generated once and kept in the database. |
| `DEMO_LOAD_ENABLED` | `0` switches off `POST /api/demo/load` ("Load demo family"). Default on. |
| `LOGIN_ATTEMPTS_PER_MINUTE` | Per-IP limit on login and demo-load attempts (default 10). |
| `TELEGRAM_BOT_USERNAME` | Optional: bot name for the `t.me` link, skipping the `getMe` call. |
| `ALERT_MAX_PER_CONTACT_PER_HOUR` | Alerts one family contact can receive per hour (default 5, 0 disables); the rest are recorded as `rate_limited`. |
| `RETENTION_DAYS` | Auto-delete checks, alerts and cached verdicts older than this many days (default 0 = keep until deleted). |

### Discover models and smoke-test the real services

Once the keys exist:

```bash
NEBIUS_API_KEY=... make models      # GET {NEBIUS_BASE_URL}/models -> docs/models.md; warns if a default id is not listed
NEBIUS_API_KEY=... TAVILY_API_KEY=... make smoke
make warm                           # analyze the seed set once and pin the verdicts in SCAMSHIELD_DB
```

`make smoke` (`scripts/smoke.py`) checks that the default ids are listed (case-insensitive), that Nano, Super and Ultra each honour `response_format: json_schema` with no fallback, and runs seed message `m02` end to end (triage, a live Tavily search, verdict) on a throwaway database, then prints PASS/FAIL per check and the trace. It exits 2 without both keys and costs a few cents. Findings for the hackathon's feedback section go in [docs/feedback.md](docs/feedback.md).

## Offline mode

Without keys (or with `SCAMSHIELD_OFFLINE=1`) the whole pipeline, the tests and the eval run on deterministic fakes:

- `FakeLLMClient` answers triage, planning and verdicts with English/Japanese keyword heuristics. It is a stand-in for wiring and tests, **not a measure of model quality**.
- `FakeSearchClient` replays `seed/search_fixtures.json`, synthetic recorded results on example/`.test` hosts. Identifiers with no fixture return nothing, as Tavily does for numbers nobody has reported.

Results are marked `offline: true` and the CLI prints `[offline fakes]`.

## Eval

`scripts/eval.py` runs the 26 seed messages (`seed/messages.jsonl`) through the pipeline with the Margaret profile (`seed/profile.json`) and compares the label to the gold labels in `seed/expected.json`. The seed mixes ten scam patterns, three Japanese variants (オレオレ詐欺, delivery SMS, ATM refund), an unknown-number "grandson" message next to the real grandson's, and safe look-alikes (bank statement, pharmacy, clinic, shop order). All numbers use reserved or invalid ranges (`212-555-01xx`, `090-0000-xxxx`) and all links are example.com / `.test` hosts.

Two runs of the same 26 messages and gold labels. **Offline fakes** (keyword heuristics, not a measure of model quality):

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| 🔴 scam | 1.00 | 1.00 | 1.00 | 15 |
| 🟡 careful | 1.00 | 1.00 | 1.00 | 3 |
| 🟢 safe | 1.00 | 1.00 | 1.00 | 8 |
| **accuracy** | | | **1.00** | 26 |

**Live models** (run once on 2026-10-14: Nemotron Nano, Super and Ultra on Token Factory, real Tavily searches, the Margaret profile, a fresh in-memory database so nothing was cached and no family alert was sent): **26/26 labels correct (accuracy 1.00)**, confusion matrix diagonal 15 / 3 / 8, no 🔴 called 🟢. Per class precision, recall and F1 are all 1.00, the same table as above. Cost: 62 model calls, 107k tokens, about **$0.12 of model spend** (Nano $0.006, Super $0.008, Ultra $0.106) plus 49 Tavily searches at an assumed $0.008 each (~$0.30, the estimate in `TAVILY_COST_PER_CALL`), about $0.42 for the whole run.

How to read it: it is a 26-message set we wrote and tuned the rules against, so 26/26 shows the pipeline is wired and the rules plus models agree with the gold labels, not a general accuracy claim. The eight 🟢 messages never reached Ultra: the memory step passed them (trusted contact, known service on its own domain, or no risk signals) after Nano triage. The 18 🔴/🟡 messages ran Nano, Super and Ultra. The `scam_type` agreement on gold 🔴 was only 2/15 because Nano returns free-text type names (`gift_card_scams`, `electric_shutoff`) that do not match the gold vocabulary; that field is informational and does not drive the label. Latency is in [docs/feedback.md](docs/feedback.md): a full live check took a median of about 24 s of summed step time in this run (Nano is the slow step). That run predates the faster check described above (no Super planning step, reasoning off, triage and searches at the same time).

`m15` (a phishing "statement is ready" email from the look-alike domain `lakeviewbank-online.test`) used to score 🟡. That was a gap in the rules, not just in the fake: the memory step now flags a sender or link whose host contains a known service's brand label (`lakeviewbank`) without being its domain, and a guardrail floors such a message at 75 whatever the model says. The offline numbers measure the keyword fake and the rules around it, not Nemotron. `python scripts/eval.py --markdown` prints just the table (live when `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set, offline otherwise; `run_eval` uses an in-memory store and never dispatches family alerts); `--fail-on-red-as-green` exits non-zero if any scam is called safe.

## Privacy

What is stored, per parent, in your own SQLite database: the messages checked and their sender, the verdict and its trace, family alerts and their deliveries, the profile (trusted contacts, known services) and linked Telegram chats. A 🟢 message body is dropped unless the family opted in (`store_green_bodies`); the verdict cache follows the same rule. The spend ledger holds no personal data.

Who sees what:

- **Nebius Token Factory** (the Nemotron models) sees the message text after PII redaction. Card numbers (Luhn-checked), SSNs, Japanese My Numbers and anything that looks like a password or PIN are replaced before any model call; the stored message keeps the original.
- **Tavily** sees only a phone number, a domain, or a short redacted phrase from the message, never the whole message. Suspicious links are never opened.
- **Family contacts** get the alert (sender, a few notes for the call, why it looks like a scam) over Telegram or Resend, if configured.
- Nothing else is contacted.

Controls:

- `GET /parents/{id}/export` returns everything stored for the parent as JSON (profile, checks with traces, alerts without ack tokens, Telegram link status).
- `DELETE /parents/{id}/data?confirm=yes` deletes it all: checks, traces, verdict-cache copies, alerts, deliveries, Telegram links, the profile (memory) and the search cache. The spend ledger is kept so the budget caps still hold. `DELETE /parents/{id}/checks/{check_id}` deletes one check with its alert and cached copy.
- The parent app exposes the same for the demo parent: `GET /api/my-data/export`, `DELETE /api/my-data?confirm=yes`, `DELETE /api/check/{id}`. The PWA has a plain "Delete my data" page (`/delete/`, with a confirm step and a download-a-copy link) and a privacy explainer (`/privacy/`).
- `RETENTION_DAYS=N` (default `0`, off) deletes checks, alerts and cached verdicts older than N days, at startup and then at most hourly. Profiles and the spend ledger are kept.
- These endpoints are unauthenticated, like the other `/parents/{id}` routes: the family dashboard's passphrase gate is the intended protection for a public deployment. A restart reloads the fictional seed profile for the demo parent.

## Layout

```
api/        Python 3.12 FastAPI core API (uv, pytest, Dockerfile)
web/        Next.js 16 static export: the landing page, the parent /check PWA and the /family dashboard (app/, components/, lib/, public/manifest + sw.js)
scripts/    discover_models.py, eval.py, smoke.py, dev.sh
seed/       messages.jsonl, expected.json, profile.json, search_fixtures.json
docs/       models.md, feedback.md
```

The Dockerfile builds from the repo root and produces one image that serves the PWA and the API:
`docker build -f api/Dockerfile -t scamshield .` (or `docker compose up --build`).

## License

MIT
