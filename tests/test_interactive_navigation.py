import asyncio
from datetime import UTC, datetime, timedelta

from pricewatch.bot import TelegramBotApp
from pricewatch.runtime_models import (
    SubscriptionRecord,
    TrackedProductRecord,
    UserProductSummary,
)
from pricewatch.search_plan import SearchPlan
from pricewatch.telegram_api import TelegramClient
from pricewatch.telegram_views import (
    render_add_prompt,
    render_product_list,
    render_tracking_card,
)

NOW = datetime(2026, 9, 6, 7, 30, tzinfo=UTC)


def _summary() -> UserProductSummary:
    plan = SearchPlan(
        canonical_name="Apple AirPods Pro 3",
        primary_query="apple airpods pro 3",
        product_type="headphones",
        identity_attributes={
            "brand": "Apple",
            "model": "AirPods Pro",
            "generation": "3",
        },
    )
    product = TrackedProductRecord(
        id=42,
        canonical_name=plan.canonical_name,
        product_type=plan.product_type,
        identity_fingerprint="fp",
        search_plan=plan,
        lifecycle_state="active",
        subscriber_count=1,
        next_scan_at=NOW,
        last_successful_scan_at=NOW,
    )
    subscription = SubscriptionRecord(
        id=7,
        user_id=11,
        tracked_product_id=42,
        status="active",
    )
    return UserProductSummary(
        subscription=subscription,
        product=product,
        public_price="18490",
        marketplace="ozon",
        listing_url="https://www.ozon.ru/product/42/",
        verified_at=NOW,
        seven_day_min_price="17990",
    )


class FakeProvider:
    async def create_plan(self, text):
        return _summary().product.search_plan


class FakeRepository:
    def __init__(self) -> None:
        self.summary = _summary()

    async def ensure_user(self, *, telegram_user_id, chat_id):
        return 11

    async def list_user_products(self, user_id):
        return (self.summary,)

    async def recent_public_prices(self, product_id, *, since):
        assert product_id == 42
        assert since <= datetime.now(UTC)
        return [
            (20990, NOW - timedelta(days=2)),
            (17990, NOW - timedelta(hours=2)),
            (18490, NOW - timedelta(hours=1)),
        ]

    async def pause_subscription(self, subscription_id):
        return None

    async def resume_subscription(self, subscription_id):
        return None


class FakeTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.edited = []
        self.callbacks = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    async def edit_message_text(self, chat_id, message_id, text, *, reply_markup=None):
        self.edited.append((chat_id, message_id, text, reply_markup))
        return {"message_id": message_id}

    async def answer_callback_query(self, callback_query_id, *, text=None):
        self.callbacks.append(callback_query_id)


def _callback(data: str, *, message_id: int = 55):
    return {
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 1001},
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": 2002}},
        }
    }


def _callback_values(view):
    markup = view.reply_markup or {}
    return [
        button["callback_data"]
        for row in markup.get("inline_keyboard", [])
        for button in row
        if "callback_data" in button
    ]


def _app(telegram: FakeTelegram) -> TelegramBotApp:
    return TelegramBotApp(
        repository=FakeRepository(),
        plan_provider=FakeProvider(),
        telegram=telegram,
    )


def test_callback_navigation_edits_existing_message_instead_of_sending_new_one() -> None:
    telegram = FakeTelegram()
    app = _app(telegram)

    asyncio.run(app.handle_update(_callback("my", message_id=55)))

    assert telegram.sent == []
    assert telegram.edited[-1][0:2] == (2002, 55)
    assert "Отслеживается" in telegram.edited[-1][2]


def test_history_shows_timestamped_prices_sorted_from_min_to_max() -> None:
    telegram = FakeTelegram()
    app = _app(telegram)

    asyncio.run(app.handle_update(_callback("history:7")))

    text = telegram.edited[-1][2]
    assert "17 990 ₽" in text
    assert "18 490 ₽" in text
    assert "20 990 ₽" in text
    history_rows = text.split("Проверенные цены:\n", 1)[1]
    assert (
        history_rows.index("17 990 ₽")
        < history_rows.index("18 490 ₽")
        < history_rows.index("20 990 ₽")
    )
    assert "06.09.2026" in text
    assert ":" in text
    assert "product:7" in str(telegram.edited[-1][3])


def test_nested_views_have_back_navigation() -> None:
    assert "home" in _callback_values(render_add_prompt())
    assert "home" in _callback_values(render_product_list((_summary(),)))
    assert "my" in _callback_values(render_tracking_card(_summary()))


def test_telegram_client_supports_edit_message_text() -> None:
    assert callable(getattr(TelegramClient, "edit_message_text", None))
