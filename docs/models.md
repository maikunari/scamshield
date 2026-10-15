# Token Factory models

Defaults used by ScamShield, from the Token Factory model catalog (<https://tokenfactory.nebius.com/model-catalog.md>,
read 2026-10-13). Ids are copied exactly as the catalog spells them; the casing is inconsistent, so the code compares
ids case-insensitively. Prices are USD per 1M tokens.

| Env var | Role | Default id | In | Out | Context |
|---|---|---|---|---|---|
| `MODEL_FAST` | triage (Nemotron Nano) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 0.06 | 0.24 | 262k |
| `MODEL_MID` | Nemotron Super; probed by `make smoke`, no longer on the check path (lookups follow a fixed plan) | `nvidia/nemotron-3-super-120b-a12b` | 0.30 | 0.90 | 256k |
| `MODEL_REASON` | verdict (Nemotron Ultra) | `nvidia/Nemotron-3-Ultra-550b-a55b` | 1.00 | 3.00 | 1024k |
| `MODEL_LONG` | digest, long context (Nemotron 3.5 Lightning); not used by the pipeline yet | `nvidia/Nemotron-3_5-Lightning` | 0.06 | 0.24 | 1024k |

There is no NVIDIA safety/guard model and no speech model on Token Factory, so `MODEL_SAFETY` no longer exists.

The same prices drive the `cost_usd` column of every trace step (`MODEL_PRICES` in `api/scamshield/config.py`). If you
point a tier at another model, its price is looked up by id and falls back to the tier default; set
`PRICE_<TIER>_IN` / `PRICE_<TIER>_OUT` to override.

## Verifying against the live account

This file is replaced by the real listing once a key exists:

```bash
NEBIUS_API_KEY=... make models      # GET {NEBIUS_BASE_URL}/models -> docs/models.md
```

It lists every model the account can call, whether each default above is present (matched case-insensitively) and warns
if one is not. `make smoke` then checks that Nano, Super and Ultra each honour `response_format: json_schema`.
