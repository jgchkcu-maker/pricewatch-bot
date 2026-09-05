# PriceWatch Bot

Persistent buyer-oriented price radar for Ozon and Wildberries with Telegram UX, exact-offer verification, rolling 7-day deal logic and verified-only online learning.

## What it does

A user sends a product name to the Telegram bot, for example `Xiaomi Pad 7 8/256`.
Gemini creates a strict `SearchPlan` once, the user confirms the interpreted identity, and the product becomes a globally deduplicated tracked entity.

Ten or ten thousand users watching the same exact product share one marketplace scan job, one listing set, one seven-day price history and one learning state.

The worker targets roughly one fast cycle every four minutes for active products:

- poll already-known exact listings directly;
- run the primary marketplace search;
- periodically run one adaptively selected semantic alias;
- apply marketplace taxonomy, hard identity vetoes and deterministic/probabilistic matching;
- fetch the concrete listing detail before trusting a price;
- persist only verified offer state into deal logic;
- enqueue a Telegram alert when a newly verified public price is below the previous rolling 7-day minimum.

The first verified observation establishes a baseline and does **not** generate a fake “new minimum” alert. Conditional prices such as Ozon Card remain separate from the normal public-price baseline.

## Runtime architecture

```text
Telegram users
     |
     v
pricewatch-bot  <------>  PostgreSQL  <------>  pricewatch-worker
                                                |             |
                                                v             v
                                               Ozon      Wildberries
```

- `pricewatch-bot`: Telegram long polling, product confirmation/subscriptions and durable outbox delivery.
- `pricewatch-worker`: leases due global products, polls known listings, discovers new listings, verifies identity/prices and updates learning state.
- PostgreSQL: products, subscriptions, listings, verified price events, outbox, leases, pending confirmations, taxonomy evidence and durable learning state.

Redis, Celery, Kafka, ClickHouse and TimescaleDB are intentionally not required for the first production deployment.

## Implemented

- universal normalized `SearchPlan` with configurable Gemini provider (`gemini-3.5-flash-lite` by default);
- exact-identifier provenance guard: GTIN/EAN/UPC/MPN-style identifiers cannot be silently invented by the model;
- deterministic `ACCEPT / REJECT / AMBIGUOUS` matcher with unit/model normalization;
- sibling-model and capacity/identifier hard vetoes;
- hybrid online probabilistic scorer trained only by verified detail evidence;
- active-learning uncertainty queue and deduplicated hard-negative mining;
- adaptive alias exploration/exploitation based on verified query yield;
- marketplace taxonomy gate and verified taxonomy evidence;
- Wildberries v9 search + card-v4 detail verification;
- Ozon composer search + category scope + exact PDP/SKU detail verification;
- public and conditional marketplace prices kept separately;
- bounded HTTP transport with typed block/rate/drift failures and no aggressive internal retry;
- PostgreSQL runtime schema and idempotent bootstrap;
- globally deduplicated tracked products and subscriptions;
- worker leases via `FOR UPDATE ... SKIP LOCKED`;
- direct polling of known listings before discovery;
- rolling 7-day deal logic and price-event retention;
- durable notification outbox with retry/rate-limit/permanent-failure handling;
- Telegram `/start`, free-text add flow, confirmation, product list, pause/resume and exact-link new-low alerts;
- Docker Compose deployment for Postgres + bot + worker;
- deterministic vertical-slice tests proving search-preview prices cannot create alerts.

## Telegram flow

```text
Xiaomi Pad 7 8/256
        ↓
🔎 confirmation of exact identity
        ↓
✅ tracking enabled
        ↓
worker verifies marketplace listings
        ↓
first price = baseline, no alert
        ↓
later exact verified price falls
        ↓
🔥 new 7-day minimum + exact listing link
```

Internal terms such as taxonomy, matcher confidence, listing IDs and leases are not shown to buyers.

## Local development

Requires Python 3.12+.

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest -q
```

CI runs the same Ruff + pytest checks on GitHub Actions.

## Docker deployment

Create the environment file:

```bash
cp .env.example .env
```

At minimum set real values for:

```text
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
POSTGRES_PASSWORD=...
```

Then start the stack:

```bash
docker compose up -d --build
```

Compose runs:

- `postgres`
- `pricewatch-bot`
- `pricewatch-worker`

The default worker cadence is `240` seconds and is configurable through `SCAN_INTERVAL_SECONDS`.

## Important correctness rules

- Search results are discovery only; a search preview price is never an alert-worthy trusted price.
- Exact detail identity verification is required before a listing price enters trusted state.
- Hard contradictions cannot be overridden by the soft scorer.
- Search-only evidence never trains the online matcher.
- Ambiguous detail evidence is not converted into a negative training label.
- A historical minimum merely expiring from the seven-day window does not create an alert by itself.
- If the last active subscriber pauses a product, it stops consuming the fast scan budget.

## Marketplace safety

Marketplace response structures are unstable implementation details. Parsers are isolated and fixture-tested. Rate limits, access blocks and schema drift trigger backoff/fail-closed behavior rather than aggressive retry loops.

The project does not implement CAPTCHA solving, account farming, identity rotation or deliberate access-control bypass.

## Design documents

- `docs/superpowers/specs/2026-09-05-pricewatch-fast-radar-design.md`
- `docs/superpowers/specs/2026-09-05-marketplace-taxonomy-gate-design.md`
- `docs/superpowers/specs/2026-09-05-hybrid-match-learning-design.md`
- `docs/superpowers/specs/2026-09-05-telegram-runtime-design.md`
- `docs/superpowers/plans/2026-09-05-telegram-runtime.md`
