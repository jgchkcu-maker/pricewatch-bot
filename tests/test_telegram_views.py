from datetime import UTC, datetime
from decimal import Decimal

from pricewatch.runtime_models import SubscriptionRecord, TrackedProductRecord, UserProductSummary
from pricewatch.search_plan import SearchPlan
from pricewatch.telegram_views import (
    render_confirmation,
    render_new_low,
    render_product_list,
    render_start,
    render_tracking_card,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        identity_attributes={
            "brand": "Xiaomi",
            "model": "Pad 7",
            "ram": "8 GB",
            "storage": "256 GB",
        },
    )


def summary(
    *,
    price: str | None = "29490",
    status: str = "active",
    subscription_id: int = 7,
) -> UserProductSummary:
    product = TrackedProductRecord(
        id=42 + subscription_id,
        canonical_name="Xiaomi Pad 7 8/256",
        product_type="tablet",
        identity_fingerprint=f"fp-{subscription_id}",
        search_plan=plan(),
        lifecycle_state="active",
        subscriber_count=1,
        next_scan_at=NOW,
        last_successful_scan_at=NOW if price else None,
    )
    subscription = SubscriptionRecord(
        id=subscription_id,
        user_id=1,
        tracked_product_id=product.id,
        status=status,
    )
    return UserProductSummary(
        subscription=subscription,
        product=product,
        public_price=price,
        marketplace="ozon" if price else None,
        listing_url="https://www.ozon.ru/product/123/" if price else None,
        verified_at=NOW if price else None,
        seven_day_min_price=price,
    )


def test_buyer_views_hide_internal_jargon_and_render_confirmation_attributes() -> None:
    start = render_start()
    confirmation = render_confirmation(plan(), confirmation_id="abc")
    tracking = render_tracking_card(summary())
    product_list = render_product_list((summary(),))
    combined = "\n".join(
        (start.text, confirmation.text, tracking.text, product_list.text)
    ).casefold()

    assert "pricewatch" in start.text.casefold()
    assert "ram: 8 гб" in confirmation.text.casefold()
    assert "память: 256 гб" in confirmation.text.casefold()
    for jargon in ("taxonomy", "searchplan", "listing_id", "lease", "confidence", "matcher"):
        assert jargon not in combined
    callback_values = [
        button["callback_data"]
        for row in confirmation.reply_markup["inline_keyboard"]
        for button in row
        if "callback_data" in button
    ]
    assert "confirm:abc" in callback_values


def test_tracking_card_has_pre_scan_state_and_rolling_minimum() -> None:
    pending = render_tracking_card(summary(price=None))
    active = render_tracking_card(summary())
    paused = render_tracking_card(summary(status="paused"))

    assert "ищу актуальные предложения" in pending.text.casefold()
    assert "29 490 ₽" in active.text
    assert "минимум за 7 дней" in active.text.casefold()
    assert "примерно каждые 4 минуты" in active.text.casefold()
    assert "pause:7" in str(active.reply_markup)
    assert "resume:7" in str(paused.reply_markup)
    assert active.reply_markup["inline_keyboard"][0][0]["url"] == (
        "https://www.ozon.ru/product/123/"
    )


def test_new_low_alert_uses_exact_verified_url_and_public_price() -> None:
    view = render_new_low(
        {
            "product_name": "Xiaomi Pad 7 8/256",
            "marketplace": "ozon",
            "public_price": "24990",
            "previous_min": "29490",
            "delta": "4500",
            "delta_percent": "15.26",
            "url": "https://www.ozon.ru/product/123/",
            "verified_at": NOW.isoformat(),
            "conditional_prices": {"ozon_card": "23990"},
        }
    )

    assert "🔥 НОВАЯ МИНИМАЛЬНАЯ ЦЕНА" in view.text
    assert "24 990 ₽" in view.text
    assert "29 490 ₽" in view.text
    assert "23 990 ₽" in view.text
    buy = view.reply_markup["inline_keyboard"][0][0]
    assert buy["url"] == "https://www.ozon.ru/product/123/"


def test_product_list_is_compact_numbers_items_and_paginates() -> None:
    products = tuple(summary(subscription_id=index) for index in range(1, 11))

    first = render_product_list(products)
    second = render_product_list(products, page=1)

    assert "📦 Отслеживается: 10" in first.text
    assert "1." in first.text and "8." in first.text
    assert "9." not in first.text
    assert "my_page:1" in str(first.reply_markup)
    assert "9." in second.text and "10." in second.text
    assert "my_page:0" in str(second.reply_markup)
    assert "29 490 ₽" in first.text
    assert Decimal("29490") == Decimal(summary().public_price)
