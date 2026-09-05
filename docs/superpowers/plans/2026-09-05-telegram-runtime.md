# PriceWatch Telegram Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production vertical slice where a Telegram user adds an exact product, a globally deduplicated worker tracks Ozon/Wildberries about every four minutes, verified price events are stored in PostgreSQL, and a durable outbox delivers one Telegram alert for each new rolling seven-day minimum.

**Architecture:** Keep Telegram and marketplace work in separate processes backed by one PostgreSQL database. The bot owns interaction/subscriptions/outbox delivery; the worker owns leases, discovery, direct known-listing polling, exact detail verification, verified price persistence, rolling seven-day deal logic, and durable matcher learning.

**Tech Stack:** Python 3.12, httpx, psycopg 3, PostgreSQL, Telegram Bot API long polling, existing PriceWatch matcher/adapters/scheduler, pytest, Ruff, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-05-telegram-runtime-design.md`

## Global Constraints

- One globally deduplicated tracked product regardless of subscriber count.
- Active product fast-scan target is approximately four minutes.
- Primary query every fast scan; supplemental adaptive alias follows existing cadence.
- Gemini/SearchPlan provider is used only on create/refresh, never in the four-minute hot loop.
- Search results and search prices are discovery evidence only.
- Hard identity contradictions cannot be overridden by probabilistic scoring.
- Exact detail verification is mandatory before trusted price state or alerts.
- Learning trains only from verified evidence.
- Buyer deal window is rolling seven days.
- First verified observation establishes baseline and emits no new-low alert.
- Conditional marketplace-card price stays separate from public-price baseline.
- No CAPTCHA solving, account farming, identity rotation, or anti-bot bypass.

---

### Task 1: Runtime schema and database connection

**Files:**
- Create: `sql/001_runtime.sql`
- Create: `src/pricewatch/db.py`
- Modify: `pyproject.toml`
- Test: `tests/test_runtime_schema.py`

**Interfaces:**
- Produces `PsycopgConnectionFactory(dsn: str)` async context manager.
- Produces schema tables: `telegram_user`, `tracked_product`, `subscription`, `marketplace_listing`, `listing_state`, `price_event`, `notification_outbox`, `worker_lease`, `pending_product_confirmation`, plus durable taxonomy evidence.

- [ ] Write a failing schema test that loads `sql/001_runtime.sql` and asserts required table names, unique identity constraints and outbox dedup constraint are present.
- [ ] Run `pytest tests/test_runtime_schema.py -q` and verify failure because the migration does not exist.
- [ ] Add `psycopg[binary]>=3.2,<4` to runtime dependencies and implement the SQL migration with UUID/bigserial keys, timestamptz timestamps, JSONB SearchPlan/payload fields, unique `(user_id, tracked_product_id)` subscription, unique product `identity_fingerprint`, marketplace-native listing uniqueness, and unique outbox `dedup_key`.
- [ ] Implement `PsycopgConnectionFactory` with `psycopg.AsyncConnection.connect()` and explicit commit/rollback semantics.
- [ ] Run schema tests and existing tests.

### Task 2: Canonical identity, product/subscription repository

**Files:**
- Create: `src/pricewatch/runtime_models.py`
- Create: `src/pricewatch/runtime_repository.py`
- Test: `tests/test_runtime_repository.py`

**Interfaces:**
- `identity_fingerprint(plan: SearchPlan) -> str`
- `RuntimeRepository.ensure_user(telegram_user_id: int, chat_id: int) -> int`
- `RuntimeRepository.upsert_tracked_product(plan: SearchPlan) -> TrackedProductRecord`
- `RuntimeRepository.subscribe(user_id: int, product_id: int) -> SubscriptionRecord`
- `RuntimeRepository.pause_subscription(...)`, `resume_subscription(...)`, `list_user_products(...)`
- [ ] Write tests using a fake async connection proving semantically equivalent normalized SearchPlans have the same fingerprint and different RAM/storage/model produce different fingerprints.
- [ ] Write repository SQL contract tests proving `ON CONFLICT(identity_fingerprint)` reuses one product and subscription uses `ON CONFLICT(user_id, tracked_product_id)`.
- [ ] Implement deterministic SHA-256 fingerprint from normalized product type + sorted normalized identity attributes + canonical model/brand tokens.
- [ ] Implement repository methods with no marketplace calls.
- [ ] Run focused tests and full suite.

### Task 3: Verified price state, rolling seven-day low and outbox

**Files:**
- Create: `src/pricewatch/deals.py`
- Extend: `src/pricewatch/runtime_repository.py`
- Test: `tests/test_deals.py`

**Interfaces:**
- `DealDecision(is_baseline, is_new_low, previous_min, current_price, delta, delta_percent)`
- `evaluate_verified_price(history: Sequence[PricePoint], new_price: Decimal) -> DealDecision`
- `RuntimeRepository.record_verified_offer(...)` atomically updates listing state, appends changed price event, and inserts outbox rows only for active subscribers when a new low occurs.
- [ ] Write RED tests for first observation baseline/no alert, unchanged price/no duplicate event, lower verified price/new low, higher price/no alert, expired old minimum/no spontaneous alert, conditional price excluded from public baseline.
- [ ] Implement pure deal evaluator using the existing seven-day price primitives where possible.
- [ ] Add repository atomic transaction contract and deterministic outbox key `new-low:{product_id}:{listing_id}:{price_event_id}`.
- [ ] Run tests.

### Task 4: Worker leases and product scan orchestration

**Files:**
- Create: `src/pricewatch/worker.py`
- Extend: `src/pricewatch/runtime_repository.py`
- Test: `tests/test_worker_runtime.py`

**Interfaces:**
- `PriceWorker.run_once(now: datetime) -> int`
- `RuntimeRepository.claim_due_products(worker_id, now, limit, lease_seconds)`
- `RuntimeRepository.complete_scan(product_id, next_scan_at, success)`
- [ ] Write RED tests proving multiple subscribers still produce one claimed product, an unexpired lease prevents duplicate concurrent work, and last paused subscriber removes product from due work.
- [ ] Write a fake-adapter test proving known listings are fetched directly before/alongside primary discovery search.
- [ ] Integrate existing `scan_once()` and `verify_candidate()`; search preview prices must never call `record_verified_offer`.
- [ ] Load one durable `HybridMatchEngine` per product scope through `PostgresLearningStateStore` and pass learning persistence into verification.
- [ ] On marketplace access/drift exceptions, fail closed and schedule backoff without mutating trusted price state.
- [ ] Run focused and full tests.

### Task 5: SearchPlan provider transport

**Files:**
- Extend: `src/pricewatch/search_plan_llm.py`
- Test: `tests/test_search_plan_provider.py`

**Interfaces:**
- `GeminiSearchPlanProvider(api_key, model, base_url).create_plan(user_text) -> SearchPlan`
- [ ] Write RED tests with `httpx.MockTransport` for valid structured response, malformed JSON, provider HTTP failure and prohibition on invented identifiers via existing validator.
- [ ] Implement Gemini REST call with configurable model/base URL and strict response extraction into the existing SearchPlan parser/validator.
- [ ] Keep provider completely outside worker scan path.
- [ ] Run tests.

### Task 6: Telegram API client and message rendering

**Files:**
- Create: `src/pricewatch/telegram_api.py`
- Create: `src/pricewatch/telegram_views.py`
- Test: `tests/test_telegram_views.py`
- Test: `tests/test_telegram_api.py`

**Interfaces:**
- `TelegramClient.get_updates(offset, timeout)`, `send_message(chat_id, text, reply_markup=None)`
- Renderers for start, confirmation, tracking card, product list and new-low alert.
- [ ] Write exact/semantic copy tests: buyer-facing messages contain no taxonomy/SearchPlan/listing_id/lease jargon.
- [ ] Write URL button test proving new-low buy button uses exact verified listing URL.
- [ ] Implement Telegram Bot API through existing httpx style, bounded bodies/timeouts, no retries inside transport.
- [ ] Run tests.

### Task 7: Telegram application flow and durable confirmation state

**Files:**
- Create: `src/pricewatch/bot.py`
- Extend: `src/pricewatch/runtime_repository.py`
- Test: `tests/test_bot_flow.py`

**Interfaces:**
- `TelegramBotApp.handle_update(update: Mapping[str, Any])`
- [ ] Write RED flow tests for `/start`, free text -> SearchPlan -> confirmation, confirm -> shared subscription, correction -> new plan rather than mutating shared product, list products, pause/resume, and pre-first-scan card.
- [ ] Persist pending confirmation in PostgreSQL with expiry so restarts do not lose the interaction.
- [ ] Implement callbacks with compact stable callback data (`confirm:<pending_id>`, `pause:<subscription_id>`, etc.).
- [ ] Never block user response waiting for marketplace scanning.
- [ ] Run tests.

### Task 8: Durable outbox dispatcher

**Files:**
- Create: `src/pricewatch/outbox.py`
- Extend: `src/pricewatch/runtime_repository.py`
- Test: `tests/test_outbox.py`

**Interfaces:**
- `OutboxDispatcher.run_once(limit=50) -> int`
- [ ] Write RED tests for one send per deduped event, transient failure increments attempts and schedules bounded exponential retry, permanent Telegram blocked-chat error disables delivery for that user without affecting tracking.
- [ ] Implement claim/send/mark-sent workflow using DB row locking/lease semantics.
- [ ] Render new-low payload using `telegram_views`.
- [ ] Run tests.

### Task 9: Executable runtime, Docker Compose and maintenance

**Files:**
- Create: `src/pricewatch/config.py`
- Create: `src/pricewatch/main_bot.py`
- Create: `src/pricewatch/main_worker.py`
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `.env.example`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/test_config.py`

**Interfaces:**
- Console scripts `pricewatch-bot` and `pricewatch-worker`.
- [ ] Write RED config tests for required PostgreSQL DSN, Telegram token and Gemini key/model; marketplace rate settings have conservative defaults.
- [ ] Implement environment config without third-party settings framework.
- [ ] Bot entrypoint runs migration bootstrap, long polling and outbox dispatcher.
- [ ] Worker entrypoint runs migration bootstrap and due-product loop with shutdown-safe sleep.
- [ ] Compose exactly three services: `postgres`, `pricewatch-bot`, `pricewatch-worker`.
- [ ] Add maintenance SQL/runtime method deleting price events older than seven days only after derived current state is committed.
- [ ] Update README with setup/run instructions and architecture.
- [ ] Run tests.

### Task 10: End-to-end deterministic vertical slice and completion

**Files:**
- Create: `tests/test_vertical_slice.py`
- Update: `docs/superpowers/specs/2026-09-05-telegram-runtime-design.md`
- Update: PR #1 body

**Interfaces:** complete product flow.
- [ ] Write E2E fake test: Telegram `Xiaomi Pad 7 8/256` -> plan -> confirmation -> subscription -> worker baseline verified 29,490 (no alert) -> next verified 24,990 -> one outbox -> Telegram alert exact URL.
- [ ] Add second subscriber and prove worker scans one shared product but emits one outbox item per active subscription.
- [ ] Prove mismatched detail and search-only low price never create trusted events or alerts.
- [ ] Run `ruff check .` and `pytest -q`.
- [ ] Verify GitHub Actions on final HEAD is green.
- [ ] Update PR description to reflect durable runtime rather than process-local learning.
