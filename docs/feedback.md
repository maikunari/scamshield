# Feedback on Nebius Token Factory and AI Cloud

Raw material for the hackathon's feedback section. Everything under "Collected" was found while building ScamShield
and checking the public docs on 2026-10-13 (sources are linked per item). Each item has a repro and a suggested fix.
"To collect" lists what only a live key can answer; the smoke script (`make smoke`) and `make models` produce most of it.

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

## To collect once the keys exist

These need `NEBIUS_API_KEY` (and `TAVILY_API_KEY` for the second). Run `make models` then `make smoke` and record the answers here.

- [ ] Does `GET /v1/models` return the same ids and casing as `model-catalog.md`? Does it include prices, context length or capability flags?
- [ ] Does each of Nano, Super and Ultra honour `response_format: json_schema` (strict)? The smoke script prints PASS/FAIL per model with the error message.
- [ ] What does a failed `json_schema` request return (status, body), and is the error code stable enough to branch on?
- [ ] Do the Nemotron models return reasoning in `message.reasoning_content`, inline in `content` (`<think>` blocks, or only a closing tag), or not at all? `strip_reasoning` handles all three; note which one occurs and how many extra completion tokens it costs on Ultra.
- [ ] Is `usage.prompt_tokens` / `completion_tokens` always present, and does it include reasoning tokens?
- [ ] Observed latency (p50 and p95) per tier from the trace column, against the prices in `docs/models.md`.
- [ ] What happens at the 60 RPM baseline: status code, `Retry-After` or rate-limit headers?
