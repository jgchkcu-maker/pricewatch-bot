# PriceWatch Telegram Runtime Design

Date: 2026-09-05
Status: approved product design, implementation pending

## Goal

Turn the existing precision-first marketplace tracking core into a complete production vertical slice for buyers using Telegram.

A user should be able to send a product name such as `Xiaomi Pad 7 8/256`, confirm the interpreted identity, enable tracking, and later receive a Telegram alert only when a concrete marketplace offer has been reverified and creates a new rolling seven-day minimum.

The implementation must preserve the current core guarantees:

- one globally deduplicated tracked product regardless of subscriber count;
- fast scan target of about four minutes for active tracked products;
- primary query on every fast scan, with supplemental aliases chosen adaptively;
- LLM only for SearchPlan creation or infrequent refresh, never inside the four-minute hot loop;
- marketplace search results are discovery evidence, not trusted product identity or alert prices;
- hard identity contradictions cannot be overridden by a soft scorer;
- exact detail verification is required before a price can create an alert;
- online learning is trained only from verified evidence;
- main deal/history window is rolling seven days;
- no CAPTCHA solving, account farming, identity rotation, or deliberate anti-bot bypass.

## Chosen architecture

Use two independent application processes and one PostgreSQL database:

```text
Telegram users
     |
     v
+------------+          +-------------------+
| Telegram   | <------> |    PostgreSQL     |
| Bot        |          | products, subs,   |
+------------+          | listings, prices, |
                        | outbox, learning,  |
                        | leases             |
+------------+          +-------------------+
| Price      | <------>          ^
| Worker     |                   |
+------------+                   |
     |                            |
     +------ Ozon / Wildberries -+
```

The Telegram bot must never scrape marketplaces directly. The worker must never depend on Telegram being available in order to continue tracking prices.

Redis, Celery, Kafka, ClickHouse and TimescaleDB are deliberately excluded from the first production version. PostgreSQL is sufficient for persistence, deduplication, leases, the notification outbox and durable learning state.

## Runtime services

### Telegram bot

Responsibilities:

- accept user commands and free-text product names;
- call the configured SearchPlan provider only when creating or refreshing a product identity;
- show the normalized identity to the user before subscribing when important variant attributes exist;
- attach a user subscription to an existing globally deduplicated tracked product when identity is equivalent;
- show current verified best price and rolling seven-day statistics;
- pause and resume subscriptions;
- list the user's tracked products;
- send notifications from the durable outbox;
- never wait for a marketplace scan in the request/response path.

### Price worker

Responsibilities:

- acquire leases for due globally unique tracked products;
- scan each active tracked product at the target fast cadence;
- directly poll already known listings, because price changes must not depend on marketplace search ranking;
- run the primary search every fast cycle;
- run one supplemental adaptive alias on the existing cadence;
- discover new listings through search;
- apply taxonomy/blocking, hard vetoes, deterministic identity checks and the hybrid scorer;
- detail-verify candidates before recording alert-worthy prices;
- persist verified positive/negative learning updates atomically;
- append price events only when a verified price state changes;
- calculate rolling seven-day best/median/current state;
- enqueue notifications in the outbox instead of calling Telegram directly;
- back off or circuit-break marketplace access problems rather than retry aggressively.

### Notification dispatcher

The first implementation may run inside the Telegram bot process as a lightweight loop. It reads unsent outbox records, sends them through Telegram, marks successful deliveries and stores retry metadata for transient failures.

This keeps Telegram delivery separate from the marketplace worker while avoiding a third deployment unit.

## Global product identity and deduplication

A tracked product represents an exact buyer intent, not a search phrase.

Example canonical identity:

```text
product_type: tablet
brand: Xiaomi
model: Pad 7
ram: 8 GB
storage: 256 GB
```

Ten or ten thousand users tracking the same exact identity share one `tracked_product`, one set of marketplace listings, one price history and one learning state.

Each user owns only a `subscription` row pointing to that globally shared product.

Deduplication should be driven by a stable canonical identity fingerprint generated from normalized product type, brand/model and identity attributes, not from the raw user message.

If the system cannot safely determine that two intents are identical, it should keep them separate rather than merge them incorrectly.

## Scan pipeline

Each due product follows this flow:

```text
tracked_product
      |
      +--> direct poll of known listings
      |
      +--> primary marketplace search
      |
      +--> optional adaptive alias search
                  |
                  v
          search candidate dedup
                  |
                  v
         marketplace taxonomy gate
                  |
                  v
            identity blocking
                  |
                  v
              hard vetoes
                  |
                  v
        deterministic identity match
                  |
                  v
         probabilistic confidence
          /         |          \
       reject    ambiguous    accept
                    |            |
                    +-------> detail fetch
                                |
                                v
                    deterministic detail truth
                                |
                   +------------+------------+
                   |                         |
              verified reject           verified match
                   |                         |
                   +-------- learning -------+
                                             |
                                             v
                                      verified price
                                             |
                                             v
                                        price event
                                             |
                                             v
                                    rolling 7-day state
                                             |
                                             v
                                    notification outbox
```

Known listings are polled directly even when they no longer appear near the top of search results. Search exists primarily for discovery and freshness of the listing set.

## Learning integration

The already implemented hybrid learning engine remains the core matcher learning layer.

Durable state includes:

- online model weights;
- query and alias performance statistics;
- unresolved uncertainty queue;
- deduplicated hard negatives;
- verified provenance.

Rules:

- search-only evidence never updates model weights;
- exact detail identity determines the automatic training label;
- a scorer probability can request verification but cannot create its own label;
- ambiguous detail evidence is not converted into a negative label;
- state snapshot plus verified provenance are committed atomically;
- learning state is loaded once for a product scope and persisted after verified updates rather than written for every search candidate.

## PostgreSQL data model

The first production schema should contain the following logical tables.

### `telegram_user`

- internal id
- Telegram user id, unique
- chat id
- language/preferences as needed later
- created/updated timestamps

No unnecessary Telegram profile data should be persisted.

### `tracked_product`

- internal id
- canonical display name
- product type
- identity fingerprint, unique
- serialized/versioned SearchPlan
- active subscriber count or derived equivalent
- lifecycle state: active, paused/no subscribers, needs_refresh, invalid
- next scan time
- last successful scan time
- marketplace health metadata
- created/updated timestamps

### `subscription`

- user id
- tracked product id
- status: active/paused
- optional target price for later use
- created/updated timestamps

Unique constraint: one subscription per user/product pair.

### `marketplace_listing`

- tracked product id
- marketplace
- listing id
- variation id
- seller id when identity-relevant
- canonical URL
- latest verified title/attributes/taxonomy
- active/last-seen metadata
- first/last seen timestamps

Unique listing identity must use marketplace-native identifiers, not URL text.

### `listing_state`

Current verified offer state for a known listing:

- listing id
- verified public price
- optional conditional prices such as Ozon Card
- original price when trustworthy
- availability
- verified timestamp
- identity verification metadata

Search preview prices must never populate this table as verified prices.

### `price_event`

Append a row only when a verified price state changes.

- tracked product id
- listing id
- price
- conditional price payload when present
- availability
- observed/verified timestamp

Operational retention target for the buyer deal window is seven days. Older rows may be deleted by maintenance once no other feature depends on them.

### `notification_outbox`

- event id / dedup key
- user/subscription id
- tracked product id
- notification type
- payload
- status
- attempt count
- next attempt time
- sent time
- error metadata

A unique dedup key prevents repeated alerts for the same price event.

### `worker_lease`

Stores short-lived ownership of product scan work so multiple worker processes can safely scale later without scanning the same product concurrently.

### Learning tables

Reuse the implemented `pricewatch_learning_state` and `pricewatch_learning_evidence` contracts, with scope keys tied to stable tracked-product identity.

Taxonomy evidence/mappings should become durable as part of the storage/runtime phase rather than remain process-local.

## Scheduler and cadence

Active globally deduplicated products target approximately one fast cycle every four minutes.

The scheduler should use due timestamps and database leases rather than one Python timer per product.

Baseline cadence:

```text
00:00 primary
00:04 primary + adaptive alias
00:08 primary
00:12 primary + adaptive alias
...
```

The exact alias selected comes from learned verified query performance with periodic exploration.

Known-listing detail polls occur during the same product cycle. Discovery may later be slowed independently at scale, but direct known-listing polling and the primary fast radar remain the buyer-protection path.

Marketplace-wide rate budgets and circuit breakers take priority over an exact four-minute promise when access is degraded. The bot should describe the cadence as "примерно каждые 4 минуты", not guarantee an impossible exact interval.

## Rolling seven-day deal logic

For each tracked product maintain derived state from verified price events:

- current best verified public price;
- current best marketplace/listing;
- rolling seven-day minimum;
- rolling seven-day median;
- previous rolling minimum before the new event;
- absolute and percentage change.

Notification rule for a new low:

1. a new verified price event arrives;
2. its effective public price is lower than the previous rolling seven-day minimum;
3. the concrete listing has passed detail identity verification;
4. an outbox event with a deterministic dedup key does not already exist.

The first verified observation establishes the baseline and does not generate a "new minimum" alert.

A historical minimum merely expiring from the seven-day window must not itself generate a notification.

Conditional marketplace-card prices remain distinct from the normal public price. They may be displayed as an additional price but must not silently replace the public-price baseline.

## Telegram UX

The bot is intentionally simple. Internal terms such as taxonomy, SearchPlan, matcher, listing id, lease and confidence score never appear in buyer-facing copy.

### `/start`

```text
👋 PriceWatch

Отправь название товара, который хочешь купить.

Например:
Xiaomi Pad 7 8/256

Я буду проверять Ozon и Wildberries
и напишу, когда цена станет самой низкой
за последние 7 дней.
```

Buttons:

- `➕ Добавить товар`
- `📦 Мои товары`

### Product interpretation

After SearchPlan creation:

```text
🔎 Я понял товар так:

Xiaomi Pad 7
• RAM: 8 ГБ
• Память: 256 ГБ

Буду искать именно эту версию,
не смешивая её с Pad 7 Pro, другими объёмами
и аксессуарами.
```

Buttons:

- `✅ Всё верно`
- `✏️ Изменить`
- `❌ Отмена`

For products without meaningful variants, the bot may omit empty attributes instead of inventing details.

### Tracking card

```text
✅ Отслеживание включено

Xiaomi Pad 7 8/256

Сейчас лучшая цена:
29 490 ₽ • Ozon

Минимум за 7 дней:
29 490 ₽

Проверка: примерно каждые 4 минуты
```

Buttons when a verified listing exists:

- `🛒 Открыть товар`
- `📊 История`
- `⏸ Остановить`

Before the first verified scan, show a neutral state such as `Ищу актуальные предложения…` rather than fake a price or force the user to wait synchronously.

### `📦 Мои товары`

Show compact cards, preferably paginated when the list is long:

```text
📦 Отслеживается: 3

1. Xiaomi Pad 7 8/256
   24 990 ₽ · Ozon
   ↓ 15,3%

2. Samsung Galaxy S26 256GB
   69 990 ₽ · Wildberries
   без изменений
```

Selecting a product opens its full tracking card.

### New-low alert

```text
🔥 НОВАЯ МИНИМАЛЬНАЯ ЦЕНА

Xiaomi Pad 7 8/256

24 990 ₽ • Ozon

Было минимум: 29 490 ₽
Снижение: 4 500 ₽ · 15,3%

Цена проверена на карточке товара только что.
```

Buttons:

- `🛒 Купить за 24 990 ₽`
- `📊 История цены`

The buy button always points to the exact verified listing URL.

### Pause/resume

Pausing a subscription does not necessarily stop global tracking if other users remain subscribed. If the last active subscriber pauses/removes the product, the tracked product becomes inactive and no longer consumes fast scan budget until reactivated.

### User correction

`✏️ Изменить` should allow the user to send a corrected description. The bot creates a new SearchPlan candidate rather than mutating an already shared global product identity in place. This prevents one user's correction from changing what other subscribers are tracking.

## Telegram implementation approach

Use Telegram Bot API with long polling for the first VPS deployment. Webhooks add deployment and TLS complexity without improving the core product at this stage.

The bot API layer should be behind a small interface so webhook transport can be introduced later without changing domain logic.

Conversation state for product confirmation should be short-lived. Durable subscriptions/products live in PostgreSQL; temporary confirmation state can also be stored in PostgreSQL to survive bot restarts, avoiding an additional Redis dependency.

## SearchPlan provider

Keep the existing Gemini 3.5 Flash-Lite model identifier as the default configurable provider model.

The provider call is used for:

- initial product interpretation;
- user corrections;
- rare SearchPlan refresh when marketplace evidence demonstrates that the old plan is stale or incomplete.

It must not be called on every scan.

Provider failures should produce a friendly Telegram retry state and must not create a malformed tracked product.

## Error handling and health

Marketplace state must distinguish at least:

- healthy;
- rate limited / blocked / inconclusive access;
- parser drift/schema change.

On drift, do not record guessed prices.

On temporary access degradation, preserve the last verified price and communicate stale freshness when necessary rather than deleting state.

Telegram send failures remain in the outbox with bounded exponential retry. Permanent errors such as a blocked bot chat should disable further delivery to that user without affecting the shared product worker.

Database write failures must fail closed for alerts: no Telegram alert is sent unless the corresponding verified price event/outbox state has been durably committed.

## Deployment

First production deployment target:

```text
Docker Compose on one VPS

postgres
pricewatch-bot
pricewatch-worker
```

Environment configuration includes:

- PostgreSQL DSN;
- Telegram bot token;
- Gemini/provider credentials;
- marketplace runtime settings and conservative rate budgets.

GitHub Actions remains CI, not the continuous scraping runtime.

Processes should expose structured logs and basic health commands/endpoints suitable for Docker restart policies and later monitoring.

## Testing strategy

Keep fixture-driven parser tests and add the following layers.

### Database contract tests

Verify:

- product identity fingerprint uniqueness;
- subscription deduplication;
- lease ownership/expiry;
- listing identity uniqueness;
- price event insertion only on verified change;
- rolling seven-day calculations;
- first observation does not emit an alert;
- new lower verified event creates exactly one outbox item;
- expired old minimum alone creates no alert;
- atomic learning state + evidence persistence.

### Worker tests

Use fake marketplace adapters to verify:

- one global product is scanned once even with multiple subscribers;
- known listings are polled directly;
- primary search cadence remains active;
- adaptive alias cadence is respected;
- detail mismatch never records price;
- search preview price never creates an alert;
- access/drift errors do not corrupt price state;
- lease prevents concurrent duplicate scan.

### Telegram tests

Verify command/update handling and rendered messages without network calls:

- start screen;
- free-text product creation;
- SearchPlan confirmation;
- attaching to existing globally deduplicated product;
- tracked-product list;
- pause/resume;
- pre-first-scan state;
- new-low alert rendering with exact URL;
- outbox retry/dedup behaviour.

### End-to-end vertical-slice test

A deterministic fake flow should cover:

```text
Telegram message: Xiaomi Pad 7 8/256
→ SearchPlan
→ confirmation
→ subscription
→ due worker scan
→ marketplace search candidate
→ exact detail verification
→ first baseline price, no alert
→ next worker scan with lower verified detail price
→ price_event
→ rolling 7-day new minimum
→ outbox
→ Telegram alert with exact listing URL
```

## Explicit non-goals for this phase

- mobile/web application;
- user accounts outside Telegram identity;
- payments/subscriptions monetization;
- arbitrary regional price personalization;
- Redis/Celery/Kafka;
- long-term multi-year analytics;
- computer-vision matching in the hot path;
- marketplace anti-bot circumvention;
- guaranteed exact four-minute scans under marketplace blocking or outages.

## Completion criteria

This phase is complete when:

1. PostgreSQL schema/migrations exist for runtime entities and durable learning/taxonomy state.
2. Bot and worker start independently from configuration.
3. Telegram user can add, confirm, list, pause and resume a tracked product.
4. Equivalent users share one globally deduplicated tracked product.
5. Worker polls known listings and discovers new ones on the fast schedule.
6. Only exact-detail-verified prices enter trusted listing state and deal logic.
7. Rolling seven-day new-low logic emits one durable outbox event and never alerts on the first baseline observation.
8. Telegram dispatcher sends the alert with the exact verified listing URL.
9. Learning state survives worker restart and remains verified-only.
10. Full Ruff and pytest CI are green, including a deterministic end-to-end vertical-slice test.
