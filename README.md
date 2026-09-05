# PriceWatch Bot

Persistent buyer-oriented price radar for Ozon and Wildberries with Telegram UX, exact-offer verification, rolling 7-day deal logic and verified-only durable learning.

## What it does

A user sends a product name to the Telegram bot, for example `Xiaomi Pad 7 8/256`.
Gemini creates a strict `SearchPlan` once, the user confirms the interpreted identity, and the product becomes a globally deduplicated tracked entity.

Users watching the same exact product share one tracked product, one marketplace scan job, one listing set, one seven-day price history and one durable learning state.

The worker targets roughly one fast cycle every four minutes for active products with at least one active subscriber:

- poll already-known listings directly;
- run marketplace discovery search;
- apply taxonomy and identity matching;
- fetch the concrete listing detail before trusting a price;
- persist only exact-detail verified offer state;
- enqueue a Telegram alert when a newly verified public price is below the previous rolling 7-day minimum.

The first verified observation establishes a baseline and does **not** generate a fake “new minimum” alert. Search preview prices are discovery-only and never become trusted price state. Conditional prices such as Ozon Card remain separate from the normal public-price baseline.

## Runtime architecture

```text
Telegram users
     |
     v
pricewatch-bot  <------>  PostgreSQL  <------>  pricewatch-worker
     |                                          |             |
     v                                          v             v
Telegram API                                  Ozon      Wildberries
```

`pricewatch-bot` and `pricewatch-worker` are separate long-running processes:

- `pricewatch-bot`: Telegram long polling, product confirmation/subscriptions, product views and durable outbox dispatch;
- `pricewatch-worker`: claims due shared products, polls known listings, performs discovery, verifies detail identity/prices and updates durable learning;
- PostgreSQL: users, products, subscriptions, listings, trusted listing state, verified price events, pending confirmations, worker leases, notification outbox, taxonomy evidence and learning state.

GitHub Actions is used for CI only. It is **not** the production scraper scheduler.

## Implemented

- normalized universal `SearchPlan`;
- Gemini HTTP `GeminiSearchPlanProvider` using `httpx`, strict JSON parsing and configurable model/base URL;
- exact-identifier provenance guard for GTIN/EAN/UPC/MPN-style identifiers;
- deterministic `ACCEPT / REJECT / AMBIGUOUS` matcher with unit/model normalization;
- sibling-model and capacity/identifier hard vetoes;
- hybrid online scorer trained only by verified detail evidence;
- active-learning uncertainty queue and deduplicated hard-negative mining;
- adaptive alias exploration/exploitation based on verified query yield;
- marketplace taxonomy gate and verified taxonomy evidence;
- Wildberries search + exact card detail verification;
- Ozon search + exact PDP/SKU detail verification;
- PostgreSQL runtime schema and idempotent bootstrap;
- durable learning-state persistence;
- globally deduplicated tracked products and per-user subscriptions;
- worker claims with PostgreSQL row locking and `SKIP LOCKED` plus short leases;
- direct polling of saved marketplace listings before discovery;
- success/failure scan scheduling with rate-limit/backoff handling;
- rolling 7-day price-event logic and periodic pruning;
- trusted `listing_state` retained independently from historical event pruning;
- durable notification outbox with claim timeout, bounded exponential retry and blocked-chat delivery disable;
- Telegram `/start`, free-text add, confirmation/edit/cancel, product list pagination, product card, pause/resume/history and exact-link new-low alerts;
- long-polling Telegram transport with inline keyboards and no unbounded internal retry;
- executable `pricewatch-bot` and `pricewatch-worker` console entrypoints;
- Docker Compose deployment for Postgres + bot + worker;
- deterministic vertical-slice tests proving search-preview/wrong-model offers cannot create trusted price events or alerts.

## Telegram flow

```text
Xiaomi Pad 7 8/256
        ↓
🔎 confirmation of exact identity
        ↓
✅ tracking enabled immediately
        ↓
worker scans asynchronously
        ↓
first verified price = baseline, no alert
        ↓
later exact verified price falls
        ↓
🔥 new 7-day minimum + exact verified listing URL
```

The Telegram request does not wait for a marketplace scan. Internal implementation terms such as taxonomy, matcher confidence, listing IDs and leases are not exposed to buyers.

If one user pauses a shared product, other active subscribers keep it in the worker queue. If the last active subscriber pauses it, the shared product becomes `paused_no_subscribers` and stops consuming the fast polling budget.

## Local development

Requires Python 3.12+.

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

CI runs the same Ruff + pytest checks on GitHub Actions.

## Runtime configuration

Required application variables:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `GEMINI_API_KEY` | Gemini API key used only when creating a product plan |

Optional runtime variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | SearchPlan model |
| `WORKER_ID` | container/host name | Worker lease owner identifier |
| `WORKER_BATCH_SIZE` | `20` | Maximum products claimed per worker pass |
| `SCAN_INTERVAL_SECONDS` | `240` | Successful scan cadence |
| `LEASE_SECONDS` | `180` | Product worker lease duration |
| `TELEGRAM_POLL_TIMEOUT` | `30` | Telegram long-poll timeout |
| `MARKETPLACE_TIMEOUT_SECONDS` | `20` | Marketplace HTTP timeout |
| `OUTBOX_BATCH_SIZE` | `50` | Notifications claimed per dispatch pass |

Legacy `WORKER_LEASE_SECONDS` and `TELEGRAM_POLL_TIMEOUT_SECONDS` are accepted as compatibility fallbacks, but new deployments should use the canonical names above.

## Docker Compose

Create the environment file:

```bash
cp .env.example .env
```

Set at least:

```text
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
POSTGRES_PASSWORD=...
```

`docker-compose.yml` supplies `DATABASE_URL` for the internal Postgres service by default. For an external PostgreSQL/VPS deployment, set `DATABASE_URL` explicitly.

Start the stack:

```bash
docker compose up -d --build
```

Compose contains only:

- `postgres` — PostgreSQL with healthcheck and persistent volume;
- `pricewatch-bot` — Telegram long polling + outbox dispatcher;
- `pricewatch-worker` — Ozon/Wildberries polling + verification + learning + maintenance.

Both application services wait for PostgreSQL health and use restart policies.

## Console entrypoints

After installation:

```bash
pricewatch-bot
pricewatch-worker
```

Both entrypoints bootstrap the PostgreSQL schema before entering their runtime loop and handle process cancellation through `asyncio` shutdown semantics.

## Important correctness rules

- Search results are discovery only; a search preview price is never alert-worthy trusted state.
- Exact detail identity verification is required before a listing price enters trusted state.
- Hard identity contradictions cannot be overridden by the soft scorer.
- Search-only evidence never trains the online matcher.
- Ambiguous detail evidence is not converted into a negative training label.
- Marketplace parser drift/access failure does not write a guessed price.
- A historical minimum merely expiring from the seven-day window does not create an alert by itself.
- Price-event maintenance removes old history but does not delete current trusted `listing_state`.
- Marketplace worker code never calls Telegram directly; notifications go through the durable outbox.

## Marketplace safety

Marketplace response structures are unstable implementation details. Parsers are isolated and fixture-tested. Rate limits, access blocks and schema drift trigger backoff/fail-closed behavior rather than aggressive retry loops.

The project does not implement CAPTCHA solving, account farming, identity rotation or deliberate access-control bypass.

## Design documents

- `docs/superpowers/specs/2026-09-05-pricewatch-fast-radar-design.md`
- `docs/superpowers/specs/2026-09-05-marketplace-taxonomy-gate-design.md`
- `docs/superpowers/specs/2026-09-05-hybrid-match-learning-design.md`
- `docs/superpowers/specs/2026-09-05-telegram-runtime-design.md`
- `docs/superpowers/plans/2026-09-05-telegram-runtime.md`
