# Fast Radar Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first tested core of the four-minute universal price radar: SearchPlan/query rotation, deterministic matching, marketplace adapter contracts, scheduler primitives, and seven-day event-based price intelligence.

**Architecture:** Keep the first slice as a dependency-light Python package with immutable domain records and pure functions. Marketplace-specific networking is behind a protocol and is deliberately not implemented until we have stable response fixtures from Ozon/WB. CI is GitHub Actions; GitHub Actions is not used as the production scheduler.

**Tech Stack:** Python 3.12, dataclasses/typing/decimal from stdlib, pytest, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-05-pricewatch-fast-radar-design.md`

## Global Constraints
- Normal active-product scan target: 4 minutes.
- Scheduler unit is a globally deduplicated tracked product, never a user subscription.
- Main price decision window: rolling 7 days.
- LLM must not be in the four-minute hot loop.
- Product identity is universal: arbitrary identity attributes, no mandatory region/revision taxonomy.
- Search result membership is not proof of identity; matcher decides.
- No live marketplace scraping in this first slice.

---

### Task 1: Project scaffold and failing CI tests

**Files:**
- Create: `pyproject.toml`
- Create: `.github/workflows/ci.yml`
- Create: `src/pricewatch/__init__.py`
- Create: `tests/test_search_plan.py`
- Create: `tests/test_matching.py`
- Create: `tests/test_scheduler.py`
- Create: `tests/test_prices.py`

**Interfaces:**
- Tests define the public APIs implemented by Tasks 2-5.
- CI runs `ruff check .` and `pytest -q` on Python 3.12.

- [ ] Write tests importing non-existent `pricewatch.search_plan`, `pricewatch.matching`, `pricewatch.scheduler`, and `pricewatch.prices` APIs.
- [ ] Push tests and CI config.
- [ ] Verify CI fails because production modules/functions do not exist.

### Task 2: SearchPlan and query rotation

**Files:**
- Create: `src/pricewatch/search_plan.py`
- Test: `tests/test_search_plan.py`

**Interfaces:**
- Produces `SearchPlan`, `normalize_query(text: str) -> str`, and `query_for_cycle(plan: SearchPlan, cycle: int) -> str`.
- `SearchPlan` fields: `canonical_name`, `primary_query`, `aliases`, `required_tokens`, `excluded_terms`, `identity_attributes`.

- [ ] Implement separator/whitespace normalization so `8/256`, `8+256`, `8-256` become searchable `8 256` forms while preserving alphanumeric model tokens.
- [ ] Implement deterministic alias de-duplication.
- [ ] Implement rotation: even cycles use primary; odd cycles round-robin aliases; no aliases means primary always.
- [ ] Run CI and confirm search-plan tests pass while later task tests remain failing.

### Task 3: Neutral marketplace records and deterministic matcher

**Files:**
- Create: `src/pricewatch/marketplaces.py`
- Create: `src/pricewatch/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Produces `SearchCandidate`, `OfferLocator`, `OfferSnapshot`, `MarketplaceAdapter` protocol.
- Produces `MatchStatus` (`ACCEPT`, `REJECT`, `AMBIGUOUS`), `MatchDecision`, and `match_candidate(plan, candidate)`.

- [ ] Normalize candidate title/attributes into lowercase token evidence.
- [ ] Reject any configured excluded term.
- [ ] Reject explicit contradictions for identity attributes when candidate provides the same attribute key with a different normalized value.
- [ ] Require all required tokens that are actually representable in text/attributes.
- [ ] If critical identity evidence is absent rather than contradictory, return `AMBIGUOUS`.
- [ ] Accept when required evidence is present with no hard conflict.
- [ ] Run CI and confirm matching tests pass.

### Task 4: Four-minute scheduler primitive

**Files:**
- Create: `src/pricewatch/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces `TrackedProductSchedule` and `next_scan_at(last_scan_at, interval_minutes=4)`.
- Produces `due_product_ids(schedules, now)` returning unique product IDs only once.

- [ ] Implement timezone-aware next scan calculation.
- [ ] Reject naive datetimes.
- [ ] Implement due-product de-duplication by product ID.
- [ ] Run CI and confirm scheduler tests pass.

### Task 5: Event-based seven-day price intelligence

**Files:**
- Create: `src/pricewatch/prices.py`
- Test: `tests/test_prices.py`

**Interfaces:**
- Produces `PriceEvent`, `CurrentPriceState`, `apply_observation(state, price, observed_at)` and `rolling_stats(events, now, window_days=7)`.

- [ ] Use `Decimal` for currency.
- [ ] `apply_observation` returns unchanged state and no event when normalized price is unchanged.
- [ ] Emit exactly one event when price changes.
- [ ] `rolling_stats` ignores events older than 7 days and returns minimum and median for in-window events.
- [ ] Reject naive datetimes and non-positive prices.
- [ ] Run full CI: ruff + all tests.

### Task 6: Documentation and PR

**Files:**
- Create: `README.md`
- Modify only if necessary: design/plan docs.

**Interfaces:**
- README explains what is implemented, what is intentionally missing, and why live Ozon/WB adapters are the next slice.

- [ ] Document local setup and `pytest`/`ruff` commands.
- [ ] Document four-minute/global-dedup/7-day invariants.
- [ ] Verify full CI on the final branch head.
- [ ] Open PR from `feat/fast-radar-core` to `main` with test evidence and next-step notes.
