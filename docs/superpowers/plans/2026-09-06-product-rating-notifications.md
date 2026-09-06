# Product Rating Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the exact marketplace product card's rating and review count to new-low Telegram alerts, with a second button that opens reviews for that same listing.

**Architecture:** Extend the verified `OfferSnapshot` contract with optional product-level rating metadata. Extract that metadata from the same WB/Ozon detail payload already used for price verification, carry it directly into the new-low outbox payload (no extra hot-loop request and no schema migration), derive a host-allowlisted reviews URL from the verified locator, and render it only when both rating and review count are valid.

**Tech Stack:** Python 3.12, dataclasses, Decimal, PostgreSQL JSONB outbox payloads, httpx-based marketplace adapters, Telegram Bot API, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-06-product-rating-notifications-design.md`

## Global Constraints

- Rating must be product-level data for the exact verified card, never seller-level data.
- Wildberries uses `reviewRating` + `feedbacks`; `supplierRating` must never populate product rating.
- Ozon uses `webReviewProductScore.totalScore` + `reviewsCount`.
- Missing/malformed rating metadata must never invalidate an otherwise verified price snapshot or block a price alert.
- No extra marketplace request in the 4-minute hot loop solely for rating.
- Reviews URLs must be derived from the verified locator and restricted to the marketplace host.
- Existing price-only alert behavior remains valid when rating metadata is absent.

---

### Task 1: Extend verified offer domain model and extract product rating

**Files:**
- Modify: `src/pricewatch/marketplaces.py`
- Modify: `src/pricewatch/adapters/wildberries.py`
- Modify: `src/pricewatch/adapters/ozon.py`
- Modify: `tests/fixtures/wb_search_minimal.json`
- Test: `tests/test_marketplace_search_parsers.py`
- Test: `tests/test_ozon_offer_verification.py`

**Interfaces:**
- Produces: `OfferSnapshot.rating: Decimal | None` and `OfferSnapshot.review_count: int | None`.
- WB `parse_offer_payload(...)` returns these from the matching product object's `reviewRating` and `feedbacks` only.
- Ozon `parse_offer_payload(...)` returns these from optional `webReviewProductScore-*` metadata.

- [ ] **Step 1: Write failing WB regression test**

Add product-level and seller-level values to `wb_search_minimal.json`:

```json
"reviewRating": 4.8,
"feedbacks": 12436,
"supplierRating": 3.1
```

Add a test that calls WB `parse_offer_payload(...)` with listing `123456789` / variation `987654` and asserts:

```python
assert snapshot.rating == Decimal("4.8")
assert snapshot.review_count == 12436
assert snapshot.rating != Decimal("3.1")
```

- [ ] **Step 2: Write failing Ozon regression tests**

Inject an optional widget into `ozon_detail_minimal.json` in-memory:

```python
payload["widgetStates"]["webReviewProductScore-999-default-1"] = json.dumps(
    {"totalScore": 4.9, "reviewsCount": 731}
)
```

Assert:

```python
assert snapshot.rating == Decimal("4.9")
assert snapshot.review_count == 731
```

Also add a malformed/missing rating-widget test proving the existing price snapshot still succeeds with both fields `None`.

- [ ] **Step 3: Run focused tests and confirm RED**

Run:

```bash
pytest -q tests/test_marketplace_search_parsers.py tests/test_ozon_offer_verification.py
```

Expected: FAIL because `OfferSnapshot` has no rating fields / adapters do not populate them.

- [ ] **Step 4: Implement minimal domain fields and tolerant parsers**

In `OfferSnapshot`, add defaults:

```python
rating: Decimal | None = None
review_count: int | None = None
```

WB: parse only `reviewRating` (optionally schema-compatible product `rating` fallback) and `feedbacks`; never inspect `supplierRating`. Accept numeric/string rating only when `0 < rating <= 5`; accept non-negative integer-like review counts. Resolve metadata for the exact `listing_id` chosen by offer verification.

Ozon: read optional `webReviewProductScore` widgets best-effort. Catch rating-widget decode/schema errors locally and return `(None, None)` rather than raising `ParserDriftError`; hard price/identity widgets remain fail-closed.

- [ ] **Step 5: Run focused tests and commit GREEN**

```bash
ruff check src/pricewatch/marketplaces.py src/pricewatch/adapters/wildberries.py src/pricewatch/adapters/ozon.py tests/test_marketplace_search_parsers.py tests/test_ozon_offer_verification.py
pytest -q tests/test_marketplace_search_parsers.py tests/test_ozon_offer_verification.py
```

Commit message: `feat: extract product ratings from verified offers`

---

### Task 2: Carry exact rating metadata into the new-low outbox payload

**Files:**
- Modify: `src/pricewatch/verified_store.py`
- Test: `tests/test_verified_store.py` (or the existing verified-store test module in the branch)

**Interfaces:**
- Consumes: `OfferSnapshot.rating`, `OfferSnapshot.review_count`, verified `OfferLocator`.
- Produces optional payload keys: `rating`, `review_count`, `reviews_url`.

- [ ] **Step 1: Write failing payload regression test**

Create a verified snapshot with:

```python
rating=Decimal("4.8"),
review_count=12436,
```

Trigger a new-low write and assert the serialized outbox payload contains:

```python
"rating": "4.8",
"review_count": 12436,
"reviews_url": "https://www.ozon.ru/product/123456789/reviews/",
```

Add a WB case asserting exact-card destination:

```python
"https://www.wildberries.ru/catalog/123456789/feedbacks"
```

- [ ] **Step 2: Add missing-metadata regression**

For `rating=None` / `review_count=None`, assert all three optional rating keys are omitted and new-low outbox creation still succeeds.

- [ ] **Step 3: Run test and confirm RED**

```bash
pytest -q tests/test_verified_store.py
```

Expected: FAIL because payload does not carry rating metadata.

- [ ] **Step 4: Implement allowlisted review URL derivation and payload propagation**

Add a small pure helper in `verified_store.py` that derives only from `candidate.marketplace` + verified numeric `snapshot.locator.listing_id`:

```python
if marketplace == "ozon":
    return f"https://www.ozon.ru/product/{listing_id}/reviews/"
if marketplace == "wildberries":
    return f"https://www.wildberries.ru/catalog/{listing_id}/feedbacks"
return None
```

Reject non-numeric IDs for these routes by returning `None`. Populate rating keys only when `rating is not None`, `review_count is not None`, and `review_count > 0`. Do not persist rating into `listing_state`; the exact verified snapshot already exists in the transaction that creates the alert, so carrying it directly prevents stale metadata and avoids a migration.

- [ ] **Step 5: Run focused tests and commit GREEN**

```bash
ruff check src/pricewatch/verified_store.py tests/test_verified_store.py
pytest -q tests/test_verified_store.py
```

Commit message: `feat: include product rating in new-low payloads`

---

### Task 3: Render product rating and review button in Telegram alerts

**Files:**
- Modify: `src/pricewatch/telegram_views.py`
- Test: `tests/test_telegram_views.py` or the existing notification-view test module.

**Interfaces:**
- Consumes optional `rating`, `review_count`, `reviews_url` from outbox payload.
- Produces unchanged price alert plus optional rating line and second URL button.

- [ ] **Step 1: Write failing rendering test with rating**

Given:

```python
payload.update({
    "rating": "4.8",
    "review_count": 12436,
    "reviews_url": "https://www.ozon.ru/product/123456789/reviews/",
})
```

assert:

```python
assert "⭐ 4.8 · 12 436 отзывов" in view.text
assert view.reply_markup["inline_keyboard"][0][0]["text"] == "🛒 Открыть товар"
assert view.reply_markup["inline_keyboard"][1][0] == {
    "text": "⭐ 4.8 · 12 436 отзывов",
    "url": "https://www.ozon.ru/product/123456789/reviews/",
}
```

- [ ] **Step 2: Write failing fallback test**

With rating keys absent, assert there is no `⭐` line/button and the original one-button price alert remains valid.

- [ ] **Step 3: Run tests and confirm RED**

```bash
pytest -q tests/test_telegram_views.py
```

- [ ] **Step 4: Implement locale-friendly formatter and optional button**

Format review count with spaces (`12 436`). Normalize rating through `Decimal`, remove redundant trailing zeroes, and render only when rating is in `(0, 5]`, count is positive integer, and `reviews_url` is a non-empty string. Do not let malformed optional metadata raise from `render_new_low`; simply omit the rating UI.

- [ ] **Step 5: Run focused tests and commit GREEN**

```bash
ruff check src/pricewatch/telegram_views.py tests/test_telegram_views.py
pytest -q tests/test_telegram_views.py
```

Commit message: `feat: show product ratings in price alerts`

---

### Task 4: End-to-end regression, CI, and Railway deployment

**Files:**
- Test: existing adapter/store/outbox/view tests
- No production file unless a regression reveals an integration mismatch.

**Interfaces:**
- Verifies the complete path `detail adapter -> OfferSnapshot -> VerifiedOfferStore -> notification_outbox -> render_new_low`.

- [ ] **Step 1: Add/extend an integration-level test**

Use the existing fake DB/outbox harness to prove a new-low generated from a snapshot with `rating=Decimal("4.8")` and `review_count=12436` reaches `render_new_low` with the exact same listing's reviews URL. Keep a separate test proving no-rating snapshots still dispatch.

- [ ] **Step 2: Run all tests and Ruff**

```bash
ruff check .
pytest -q
```

Expected: all checks pass.

- [ ] **Step 3: Push final commit and verify GitHub Actions**

Wait for the workflow run attached to the final `feat/fast-radar-core` SHA. Require both `ruff check .` and `pytest -q` to succeed before deployment.

- [ ] **Step 4: Deploy the exact final SHA to the existing Railway PriceWatch service**

Use Railway's exact-commit deploy operation for service `576c7c4e-8cd4-4c76-9304-a973a47c0174` in production environment `a3a7b307-a2a3-4db8-8094-addc8c195cd1`. Preserve existing variables/start command and do not create a public domain.

- [ ] **Step 5: Verify runtime health**

Confirm the new Railway deployment reaches `SUCCESS`, the previous instance is removed, startup has no persistent exception loop, and any transient Telegram `409 getUpdates` occurs only during old/new container overlap. Never expose the bot token in reported logs.
