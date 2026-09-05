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


def summary(*, price: str | None = "29490", status: str = "active") -> UserProductSummary:
    product = TrackedProductRecord(
        id=42,
        canonical_name="Xiaomi Pad 7 8/256",
        product_type="tablet",
        identity_fingerprint="fp",
        search_plan=plan(),
        lifecycle_state="active",
        subscriber_count=1,
        next_scan_at=NOW,
        last_successful_scan_at=NOW if price else None,
    )
    subscription = SubscriptionRecord(
        id=7,
        user_id=1,
        tracked_product_id=42,
        status=status,
    )
    return UserProductSummary(
        subscription=subscription,
        product=product,
        public_price=price,
        marketplace="ozon" if price else None,
        listing_url="https://www.ozon.ru/product/123/" if price else None,
        verified_at=NOW if price else None,
    )


def test_buyer_views_hide_internal_jargon_and_render_confirmation_attributes() -> None:
    start = render_start()
    confirmation = render_confirmation(plan(), confirmation_id="abc")
    combined = f"{start.text}\n{confirmation.text}".casefold()

    assert "pricewatch" in start.text.casefold()
    assert "ram: 8 gb" in confirmation.text.casefold()
    assert "storage: 256 gb" in confirmation.text.casefold()
    for jargon in ("taxonomy", "searchplan", "listing_id", "lease", "confidence"):
        assert jargon not in combined
    callback_values = [
        button["callback_data"]
        for row in confirmation.reply_markup["inline_keyboard"]
        for button in row
        if "callback_data" in button
    ]
    assert "confirm:abc" in callback_values


def test_tracking_card_has_neutral_pre_scan_state_and_pause_resume_button() -> None:
    pending = render_tracking_card(summary(price=None))
    active = render_tracking_card(summary())
    paused = render_tracking_card(summary(status="paused"))

    assert "ищу актуальные предложения" in pending.text.casefold()
    assert "29 490 ₽" in active.text
    assert "примерно каждые 4 минуты" in active.text.casefold()
    assert "pause:7" in str(active.reply_markup)
    assert "resume:7" in str(paused.reply_markup)


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


def test_product_list_is_compact_and_numbers_items() -> None:
    view = render_product_list((summary(), summary(status="paused")))

    assert "📦" in view.text
    assert "1." in view.text and "2." in view.text
    assert "29 490 ₽" in view.text
    assert Decimal("29490") == Decimal(summary().public_price)
