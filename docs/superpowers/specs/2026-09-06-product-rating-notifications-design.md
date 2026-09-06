# Product rating in price alerts — design

## Goal

Enrich verified low-price Telegram alerts with the rating and review count of the exact marketplace product card that produced the verified price. The rating must be product-level data, never seller-level data.

## User-facing behavior

When a verified offer becomes a new rolling 7-day minimum, the alert keeps the existing concrete product URL and, when product rating data is available, displays it and adds a second URL button pointing to the product reviews page.

Example:

```text
🔥 НОВАЯ МИНИМАЛЬНАЯ ЦЕНА

Apple AirPods Pro 3

13 990 ₽ • Ozon

Было минимум: 15 490 ₽
Снижение: 1 500 ₽ · 9,68%

⭐ 4.8 · 12 436 отзывов

Цена проверена на карточке товара только что.
```

Buttons:

- `🛒 Открыть товар` → the exact listing URL that triggered the alert.
- `⭐ 4.8 · 12 436 отзывов` → the reviews section/page for that same exact listing.

If rating or review-count data is unavailable, the price alert must still be delivered and the rating line/button is omitted.

## Marketplace extraction

### Wildberries

Use product-card fields from the same card/detail payload already used for verification. Prefer product-level `reviewRating` and `feedbacks` (falling back to equivalent product-level rating fields only when schema-compatible). Do not use `supplierRating` or any seller rating.

### Ozon

Read the product score from the PDP composer `webReviewProductScore-*` widget:

- `totalScore` → product rating
- `reviewsCount` → product review count

This is separate from seller data and therefore must not be sourced from seller widgets.

## Domain model

Extend `OfferSnapshot` with optional product-level fields:

- `rating: Decimal | None`
- `review_count: int | None`

The fields are optional so rating parser drift or unrated products never invalidate an otherwise verified price observation.

## Persistence and alert payload

Persist the latest verified product rating/review count alongside the trusted listing state or otherwise carry it through the existing verified-offer/event path so the exact snapshot that triggers a new-low event can populate the outbox payload.

The notification payload should contain optional `rating`, `review_count`, and a derived reviews URL for the exact listing. Seller rating must never be copied into these fields.

## Reviews URLs

- Wildberries: exact product card review/feedback destination derived from the concrete `listing_id`/listing URL using the marketplace-supported product page route; if a dedicated review route is not stable, fall back to the exact product page rather than inventing an invalid URL.
- Ozon: exact product reviews route for the concrete SKU (`/product/<sku>/reviews/`) when supported by the canonical listing URL.

All URLs must remain marketplace-host allowlisted and derived from already verified locators, not from untrusted arbitrary payload URLs.

## Telegram rendering

`render_new_low` formats product rating only when both values are valid. The rating button is a URL button, not a callback, so opening reviews does not disturb the bot's single-message navigation state.

Use locale-friendly formatting for the review count (`12 436`) and compact rating display (`4.8`).

## Failure behavior

Rating/review parsing is best-effort metadata. Any of the following must not block price verification or a new-low notification:

- missing rating widget/fields;
- zero reviews;
- rating schema drift;
- reviews URL derivation failure.

Price identity, exact-card verification, availability, and price remain the hard gates.

## Tests

Add regression tests for:

1. WB detail extraction uses `reviewRating`/`feedbacks` and explicitly ignores `supplierRating`.
2. Ozon detail extraction reads `totalScore`/`reviewsCount` from `webReviewProductScore`.
3. Missing rating metadata still produces a valid `OfferSnapshot` and new-low alert.
4. New-low outbox payload carries product rating/review count from the exact verified snapshot.
5. Telegram alert renders the product rating and review-count line and URL button when available.
6. Telegram alert omits the rating section when unavailable.
7. Reviews URL points to the same concrete listing/SKU as the alert's product URL.
8. Existing price-only alert behavior remains unchanged.

## Non-goals

- Seller ratings.
- Downloading or summarizing review bodies.
- Blocking alerts based on rating thresholds.
- Extra marketplace requests in the 4-minute hot loop solely for rating when the verified detail response already contains the data.
