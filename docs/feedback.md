# Feedback on Nebius Token Factory and AI Cloud

Raw material for the hackathon's feedback section. Everything under "Collected" was found while building ScamShield
and checking the public docs on 2026-10-13 (sources are linked per item). Each item has a repro and a suggested fix.
"Measured on the live keys" is what a small run on 2026-10-14 answered (`make models`, `make smoke`, one raw-HTTP probe of each tier, and the 26-message live eval).

## Collected

### 1. Model ids in the catalog use inconsistent casing and separators

- **Repro.** Open <https://tokenfactory.nebius.com/model-catalog.md> and compare the four NVIDIA ids:
  `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/Nemotron-3-Ultra-550b-a55b`,
  `nvidia/Nemotron-3_5-Lightning`. Upper case in the first, all lower case in the second, mixed in the third
  (`550b-a55b` vs `30B-A3B`), and an underscore in `3_5` where the family otherwise uses hyphens.
- **Impact.** An id typed from memory, or from the model's Hugging Face card, can 404 even though the model exists. We
  had to compare ids case-insensitively everywhere (`resolve_model_id` in `api/scamshield/config.py`), including when
  looking up the price for the cost column.
- **Suggested fix.** Accept ids case-insensitively on `/chat/completions`, and return the canonical spelling in the
  response's `model` field. Longer term, normalise new ids to one convention and keep the old ones as aliases.

### 2. The catalog does not say which models support structured output

- **Repro.** The docs (<https://docs.tokenfactory.nebius.com/ai-models-inference/json.md>) say to "use the `JSON mode` tag
  on a model card to find a model with structured output supported". The Markdown catalog
  (<https://tokenfactory.nebius.com/model-catalog.md>) carries no such tag, so a program cannot tell whether
  `response_format: {"type": "json_schema"}` will work on Nano, Super, Ultra or Lightning without trying it.
- **Impact.** ScamShield needs strict JSON from three different models. We send `json_schema`, fall back to
  `json_object` on a 400/422, and add one repair call as a last resort (`api/scamshield/llm/tokenfactory.py`); the
  fallback exists only because support cannot be discovered up front. `scripts/smoke.py` now probes each model with
  `json_schema` and no fallback.
- **Suggested fix.** Put capability flags (`json_schema`, `json_object`, `tools`, `reasoning`) and context length in the
  `/models` response and in `model-catalog.md`, one row per model. Have an unsupported `response_format` return a stable
  error code, not only a message.

### 3. No NVIDIA safety/guard model and no speech model on Token Factory

- **Repro.** Search <https://tokenfactory.nebius.com/model-catalog.md> for `guard`, `safety`, `Nemoguard`, `parakeet`,
  `canary`: no matches. The hackathon asks for NVIDIA open models, and the NVIDIA safety and speech families are the
  obvious complements for a personal-assistant project.
- **Impact.** We dropped two planned features: a Nemotron guard model to screen the family-facing text we generate
  (`MODEL_SAFETY` is removed from the config) and voicemail transcription with an NVIDIA ASR model. Both would have
  strengthened the "NVIDIA open models" story.
- **Suggested fix.** Serve at least one NVIDIA content-safety classifier and one NVIDIA ASR model. Until then, say on the
  hackathon resources page which NVIDIA model families are not available.

### 4. Hackathon credits cover Token Factory only; hosting on AI Cloud is billed to the card with no scale-to-zero

- **Repro.** The Devpost resources page and FAQ state that the $25 code (`NEBIUS-DEVPOST-GLOBAL26`) and the Builders
  Program grant are Token Factory credits. <https://docs.nebius.com/serverless/pricing-quotas> says a running endpoint
  is billed at ordinary VM and disk prices, and the endpoint docs describe no replica or scale-to-zero setting. The
  smallest CPU preset (`cpu-d3`, `2vcpu-8gb`) is about $0.066 per hour (<https://docs.nebius.com/compute/resources/pricing>).
- **Impact.** Keeping a demo endpoint up from Oct 20 to the end of judging on Dec 15 costs about $90 on the builder's own
  card, while inference for the same period costs a few dollars of credits. A hackathon that requires the demo to stay
  free to judges for six weeks effectively asks for that money.
- **Suggested fix.** Scale-to-zero (or a "pause on idle, wake on request" mode) for Serverless Endpoints, or a small AI
  Cloud credit grant for submitted projects. At minimum, state the hosting cost on the resources page next to the
  Token Factory credit.

### 5. Serverless Jobs have no scheduler

- **Repro.** <https://docs.nebius.com/serverless/jobs/manage.md> documents create, stop and timeout (1 h to 168 h) but no
  cron or trigger.
- **Impact.** ScamShield's nightly job (re-score recent messages, refresh the eval) needs an external trigger: a GitHub
  Actions cron that runs `nebius ai job create` with a service-account secret.
- **Suggested fix.** A `--schedule "<cron>"` option on `nebius ai job create`, or a documented recipe for the external
  trigger with the minimum IAM role it needs.

### 6. Endpoint token auth does not fit browser front ends

- **Repro.** The endpoint quickstart enables `--auth token` and expects 401/403 without a bearer header; the manage page says
  auth can be left off "for prototyping". A browser cannot add an `Authorization` header to a page navigation.
- **Impact.** A web app on an endpoint must run with auth disabled (so it is open to anyone), or be split into a static
  site elsewhere calling an authenticated API with the token embedded in the page, which is not a secret. We expect to
  verify this at deploy time and will update this item with what we actually hit.
- **Suggested fix.** A cookie- or signed-URL-based auth mode for browser clients, and built-in per-IP rate limiting for
  public endpoints, since the alternative is an open VM.

## Measured on the live keys (2026-10-14)

Sources: `make models`, `make smoke`, one raw `chat/completions` call per tier, a 130-request burst, and the 26-message live
eval (62 model calls, traces stored per step). Small samples, one account, one day: treat as observations, not benchmarks.

### 7. `GET /v1/models` is a bare id list, and a mistyped id is a hard 404

- **Observed.** 25 models; the NVIDIA ids and their casing match `model-catalog.md` exactly. Each entry has only `id`,
  `object`, `created`, `owned_by` (always `system`): no price, context length or capability flags. A lower-case
  `nvidia/nemotron-3-ultra-550b-a55b` returns `404 {"detail":"The model ... does not exist."}`, so item 1's casing problem
  is real, not theoretical. The `model` field of a successful reply echoes the canonical id.
- **Suggested fix.** As in item 2: capability and price fields in `/models`, and case-insensitive id matching.

### 8. `response_format: json_schema` works on Nano, Super and Ultra, and the errors are not uniform

- **Observed.** All three honoured `json_schema` (strict) with no fallback needed: `make smoke` passes for each, and in
  the live eval none of the 62 model steps noted a `json_object` fallback or a repair call.
- **Shape is guaranteed, content is not.** In one temperature-0 probe on a plainly scammy message with the schema
  `{label, why}`, Nano returned `"label": "green"` while its `why` text said it exhibits classic scam characteristics,
  and Super returned `"why": ""`. Strict mode fixed the structure, not the judgement, so ScamShield validates and
  guardrails the result in code and does not trust a single field. (One sample each; not a claim about the models in general.)
- **Failure shapes differ by layer.** An invalid schema returns `400 {"error":{"message":"Grammar error: Invalid type:
  nonsense","type":"BadRequestError","code":400},"detail":"..."}`. An unsupported `response_format.type` returns `422` with a
  FastAPI-style list, `{"detail":[{"type":"literal_error","loc":[...],"msg":"Input should be 'text', 'json_object' or
  'json_schema'"}]}`. An unknown model returns `404 {"detail":"..."}`. Three bodies, two JSON shapes, so a client can only
  branch on the status code. We fall back on 400/422.
- **Suggested fix.** One error envelope with a stable machine-readable `code` (for example `unsupported_response_format`).

### 9. Reasoning arrives in a separate field, differently named per model, and is billed as completion tokens

- **Observed.** No `<think>` tags appeared in `content` on any tier; it was clean JSON. The reasoning is in
  `message.reasoning` on Nano, in both `message.reasoning` and `message.reasoning_content` on Super, and in
  `message.reasoning_content` only on Ultra. `strip_reasoning` is still there as a guard but was not needed.
- **Usage.** `usage.prompt_tokens` and `completion_tokens` were present on every call. Reasoning is included in
  `completion_tokens`: in the probe, Super used 54 reasoning tokens of 69 and Ultra 114 of 238
  (`completion_tokens_details.reasoning_tokens`); Nano reports `completion_tokens_details: null`, so its reasoning cannot be
  separated. Nano wrote a median of 831 completion tokens for a triage reply that is a few lines of JSON, which is most of why Nano
  is the slowest step below.
- **Suggested fix.** One reasoning field name across the NVIDIA models, `reasoning_tokens` on Nano too, and a documented
  request flag to cap or disable reasoning when the caller only wants the structured answer.

### 10. Latency (26-message eval, steps run one after another)

| step | calls | p50 | p95 | max | tokens in / out | cost |
|---|---|---|---|---|---|---|
| Nano triage | 26 | 11.7 s | 47.1 s | 140.0 s | 15.8k / 22.1k | $0.006 |
| Super plan | 18 | 2.3 s | 3.2 s | 3.5 s | 8.3k / 6.0k | $0.008 |
| Ultra verdict | 18 | 4.9 s | 5.8 s | 6.0 s | 29.1k / 25.8k | $0.106 |
| Tavily search or extract | 49 | 1.6 s | 3.1 s | | | ~$0.30 |

- Per message, summed step time: p50 24 s, p95 58 s. The eight 🟢 messages stop after Nano; the other 18 run all three tiers.
- **The surprise is Nano, not Ultra.** The smallest model was the slowest and the most variable (the smoke run saw 3.6 s;
  the eval saw p50 11.7 s). Its five slowest calls (21 s to 140 s) were all in the first ten messages, so we read it as
  a slow window on the shared service rather than the model, but we did not measure it again to confirm. The 140 s step is
  longer than our 60 s client timeout, so it probably includes a retry. Super and Ultra were steady.
- **Suggested fix.** Published latency expectations per model, and a way to see queueing time separately from generation
  time in the response.

### 11. Rate limits are returned as headers, and a 130-request burst did not hit them

- **Observed.** Every reply carries `x-ratelimit-limit-requests` / `-tokens`, `-remaining-*`, `-reset-*` and a set of
  `x-ratelimit-dynamic-*` headers. This account showed `100` requests and `800000` tokens for Nano, `300` requests and
  `200000` tokens for Super and for Ultra, with `reset` reported as `1s`. That is not the 60 RPM baseline we had assumed from the docs.
- **Burst.** 130 concurrent one-token Nano requests finished in 5.9 s, all `200`, no `429`. We did not push further, so
  we have not seen the 429 body or a `Retry-After` header; `TokenFactoryClient` retries 429/5xx with exponential backoff
  (it does not read `Retry-After`).
- **Suggested fix.** Document what the `dynamic-*` headers mean (window, scaling) and that the limit differs per model.
