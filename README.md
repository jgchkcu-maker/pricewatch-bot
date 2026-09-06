# PriceWatch Bot

Buyer-oriented price radar for Ozon and Wildberries with Telegram UX, exact-detail price verification, rolling 7-day minimum logic and durable PostgreSQL state.

## Production architecture

The production runtime is split between Vercel and GitHub Actions. A VPS is not required.

```text
Telegram
   |
   | webhook
   v
Vercel /api/telegram
   |
   +------> Gemini (only when user adds/edits a product)
   |
   v
PostgreSQL
   ^
   |
GitHub Actions every 5 minutes
   |
   v
pricewatch-scheduled
   |
   +------> Ozon / Wildberries
   |
   +------> durable outbox ------> Telegram
```

### Vercel

`api/telegram.py` is a short-lived Python Function. It:

- accepts Telegram webhook updates;
- validates `X-Telegram-Bot-Api-Secret-Token` before doing database or Gemini work;
- uses the existing `TelegramBotApp` buyer flow;
- stores users, confirmations and subscriptions in PostgreSQL;
- calls Gemini only when a user sends/edits a product description;
- does **not** run marketplace polling loops or cron jobs.

`vercel.json` exposes only the Telegram function and intentionally contains no Vercel Cron configuration.

### GitHub Actions

`.github/workflows/pricewatch-scheduled.yml` runs `pricewatch-scheduled` on:

- `schedule: */5 * * * *`;
- manual `workflow_dispatch`.

A scheduled pass:

1. bootstraps all PostgreSQL runtime migrations idempotently;
2. claims due shared products with the existing PostgreSQL lease/`SKIP LOCKED` logic;
3. polls known listings and performs marketplace discovery;
4. verifies exact detail identity, then evaluates offer quality before trusting a price;
5. persists trusted price events / new-low outbox rows while quarantined and rejected offers remain diagnostic-only;
6. dispatches pending Telegram notifications;
7. prunes old price events while retaining current trusted `listing_state`.

The GitHub worker does not need a Gemini key. It only needs PostgreSQL and Telegram access.

> GitHub scheduled workflows run from the repository default branch. The 5-minute schedule becomes active after this workflow is merged into `main` (or whatever branch is configured as default).

The internal successful-scan interval remains `240` seconds, but GitHub Actions wakes the worker every 5 minutes, so the practical production polling cadence is approximately 5 minutes plus any GitHub scheduling delay.

## Product flow

A user sends, for example, `Xiaomi Pad 7 8/256`.

Gemini creates a strict `SearchPlan` once, the user confirms the interpreted identity, and the product becomes a globally deduplicated tracked entity. Users watching the same exact product share one marketplace scan job, one listing set, one seven-day price history and one durable learning state.

The marketplace worker:

- polls already-known exact listings directly;
- runs marketplace discovery when needed;
- applies taxonomy and identity matching;
- fetches the concrete listing detail before trusting a price;
- applies a deterministic quality gate to exact verified offers;
- persists price/history state only for offers classified as trusted;
- creates a Telegram alert only when a newly trusted public price is below the previous rolling 7-day minimum.

When no trusted price baseline exists, the first exact observation is quarantined for confirmation instead of becoming price history immediately. A stable repeated exact observation can establish the baseline without producing a fake “new minimum” alert. Search preview prices are discovery-only. Conditional prices such as Ozon Card remain separate from the normal public-price baseline.

A very low price alone is never labelled counterfeit. For example, an otherwise exact `827 ₽` observation against a normal trusted baseline is a price outlier and is quarantined. Explicit wording such as “копия”, “реплика” or “fake” is separate hard evidence and is rejected with its own reason code.

## Required configuration

The PostgreSQL database must be reachable from both Vercel and GitHub-hosted runners. A managed PostgreSQL service is sufficient; a VPS is not required.

### Vercel environment variables

```text
DATABASE_URL=...
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
TELEGRAM_WEBHOOK_SECRET=...
```

Optional:

```text
GEMINI_MODEL=gemini-3.5-flash-lite
```

Use a long random value for `TELEGRAM_WEBHOOK_SECRET` and use the exact same value when registering the Telegram webhook.

### GitHub Actions repository secrets

```text
DATABASE_URL=...
TELEGRAM_BOT_TOKEN=...
```

No `GEMINI_API_KEY` is required by the scheduled worker workflow.

Worker tuning remains available through the workflow/env configuration:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `SCAN_INTERVAL_SECONDS` | `240` | When a successful product becomes due again |
| `WORKER_BATCH_SIZE` | `20` | Products claimed per scheduled pass |
| `LEASE_SECONDS` | `180` | Worker lease duration |
| `MARKETPLACE_TIMEOUT_SECONDS` | `20` | Marketplace HTTP timeout |
| `OUTBOX_BATCH_SIZE` | `50` | Notifications dispatched per pass |
| `WORKER_ID` | generated from Actions run | Lease owner identifier |

## Telegram webhook activation

After Vercel has a production URL, register:

```text
https://<your-vercel-project>.vercel.app/api/telegram
```

as the Telegram Bot API webhook URL and send `TELEGRAM_WEBHOOK_SECRET` as Telegram's `secret_token`. Telegram will then send that value in `X-Telegram-Bot-Api-Secret-Token`, which the Vercel function verifies before processing the update.

The function also supports `GET /api/telegram` as a lightweight health response.

## Console entrypoints

After installation:

```bash
pricewatch-bot        # legacy long-polling runtime
pricewatch-worker     # legacy long-running worker runtime
pricewatch-scheduled  # one bounded worker/outbox/maintenance pass for GitHub Actions
```

The long-running entrypoints remain available for Docker/VPS deployments, but the recommended no-VPS production topology is Vercel + GitHub Actions + managed PostgreSQL.

## No-write marketplace quality canary

For diagnostic verification against current marketplace responses, run:

```bash
python scripts/quality_canary.py --marketplace both --query "AirPods Pro 3" --limit 10
```

The canary uses the normal Ozon/Wildberries search adapters, exact detail verification and the same offer-quality policy as the worker, but it accepts no repository or store object. It therefore cannot create `listing_state`, `price_event` or notification-outbox rows. Output is JSON counters and reason codes only; raw marketplace payloads and credentials are never emitted.

A healthy empty marketplace result is reported as zero results. Access blocks, rate limits, transport failures or parser drift produce a non-zero exit. In particular, Ozon may reject generic HTTP clients or cloud egress even when the parser itself is healthy; that means marketplace access is unknown for that run, not that there are no offers. The canary does not attempt CAPTCHA solving, browser-profile spoofing or any other access-control bypass.

With no persisted trusted reference supplied to the CLI, a first exact live observation can remain quarantined for baseline confirmation. Offline fixture coverage supplies known peer prices and separately verifies the stronger `PRICE_OUTLIER` path for an AirPods-like `827 ₽` observation.

## Local development

Requires Python 3.12+.

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

CI runs the same Ruff + pytest checks on GitHub Actions.

## Existing correctness guarantees

- normalized universal `SearchPlan`;
- Gemini HTTP transport with strict JSON parsing and identifier provenance guard;
- deterministic hard identity vetoes plus verified-only learning;
- Ozon and Wildberries exact-detail verification;
- search preview prices never become trusted price state;
- parser/access failures do not write guessed prices;
- global tracked-product deduplication with per-user subscriptions;
- PostgreSQL worker leases using row locking / `SKIP LOCKED`;
- direct known-listing polling before discovery;
- quarantined/rejected/unavailable offers cannot enter trusted price history or buyer alerts;
- buyer-facing current price, recent prices and rolling 7-day minimum use trusted rows only;
- rolling 7-day price logic and trusted `listing_state` retention;
- durable notification outbox with claim timeout and bounded retry;
- blocked Telegram delivery does not pause product tracking;
- buyer-facing Telegram flow with add/confirm/edit/cancel/list/pagination/pause/resume/history;
- deterministic vertical-slice coverage for baseline confirmation/no-alert, real new-low alerts, two subscribers and wrong-model/accessory rejection.

## Optional Docker deployment

The previous Docker Compose topology remains supported for users who prefer always-on processes:

- `postgres`;
- `pricewatch-bot`;
- `pricewatch-worker`.

For the Vercel + GitHub split, do not run those long-lived bot/worker containers in parallel with the production split unless you intentionally want additional workers. PostgreSQL leases prevent duplicate product claims, but running both Telegram long polling and webhook delivery for the same bot is not recommended.

## Marketplace safety

Marketplace response structures are unstable implementation details. Parsers are isolated and fixture-tested. Rate limits, access blocks and schema drift trigger backoff/fail-closed behavior rather than aggressive retry loops.

The project does not implement CAPTCHA solving, account farming, identity rotation or deliberate access-control bypass.
