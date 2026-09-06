import asyncio
from datetime import UTC, datetime

from pricewatch.bot import TelegramBotApp
from pricewatch.runtime_models import (
    SubscriptionRecord,
    TrackedProductRecord,
    UserProductSummary,
)
from pricewatch.search_plan import SearchPlan

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


class FakePlanProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_plan(self, text: str) -> SearchPlan:
        self.calls.append(text)
        return plan()


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, object]] = []
        self.callbacks: list[str] = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": len(self.messages)}

    async def answer_callback_query(self, callback_query_id, *, text=None):
        self.callbacks.append(callback_query_id)


class FakeRepository:
    def __init__(self) -> None:
        self.user_id = 11
        self.pending: dict[str, tuple[int, str, SearchPlan]] = {}
        self.products: dict[int, TrackedProductRecord] = {}
        self.subscriptions: dict[int, SubscriptionRecord] = {}
        self.next_product = 42
        self.next_subscription = 70

    async def ensure_user(self, *, telegram_user_id, chat_id):
        return self.user_id

    async def save_pending_confirmation(
        self,
        *,
        confirmation_id,
        user_id,
        raw_input,
        plan,
        ttl_minutes=15,
    ):
        self.pending[confirmation_id] = (user_id, raw_input, plan)

    async def get_pending_confirmation(self, confirmation_id, *, consume=False):
        value = self.pending.get(confirmation_id)
        if value is not None and consume:
            self.pending.pop(confirmation_id, None)
        return value

    async def upsert_tracked_product(self, search_plan):
        for product in self.products.values():
            if product.search_plan.identity_attributes == search_plan.identity_attributes:
                return product
        product = TrackedProductRecord(
            id=self.next_product,
            canonical_name=search_plan.canonical_name,
            product_type=search_plan.product_type,
            identity_fingerprint="fp",
            search_plan=search_plan,
            lifecycle_state="active",
            subscriber_count=0,
            next_scan_at=NOW,
            last_successful_scan_at=None,
        )
        self.products[product.id] = product
        self.next_product += 1
        return product

    async def subscribe(self, *, user_id, product_id):
        for subscription in self.subscriptions.values():
            if subscription.user_id == user_id and subscription.tracked_product_id == product_id:
                subscription = SubscriptionRecord(
                    id=subscription.id,
                    user_id=user_id,
                    tracked_product_id=product_id,
                    status="active",
                )
                self.subscriptions[subscription.id] = subscription
                return subscription
        subscription = SubscriptionRecord(
            id=self.next_subscription,
            user_id=user_id,
            tracked_product_id=product_id,
            status="active",
        )
        self.subscriptions[subscription.id] = subscription
        self.next_subscription += 1
        return subscription

    async def list_user_products(self, user_id):
        result = []
        for subscription in self.subscriptions.values():
            if subscription.user_id != user_id:
                continue
            result.append(
                UserProductSummary(
                    subscription=subscription,
                    product=self.products[subscription.tracked_product_id],
                )
            )
        return tuple(result)

    async def pause_subscription(self, subscription_id):
        subscription = self.subscriptions[subscription_id]
        self.subscriptions[subscription_id] = SubscriptionRecord(
            id=subscription.id,
            user_id=subscription.user_id,
            tracked_product_id=subscription.tracked_product_id,
            status="paused",
        )

    async def resume_subscription(self, subscription_id):
        subscription = self.subscriptions[subscription_id]
        self.subscriptions[subscription_id] = SubscriptionRecord(
            id=subscription.id,
            user_id=subscription.user_id,
            tracked_product_id=subscription.tracked_product_id,
            status="active",
        )


def message(text: str) -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 1001},
            "chat": {"id": 2002},
            "text": text,
        },
    }


def callback(data: str) -> dict[str, object]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 1001},
            "data": data,
            "message": {"chat": {"id": 2002}},
        },
    }


def create_confirmed_app() -> tuple[FakeRepository, FakeTelegram, TelegramBotApp, int]:
    repository = FakeRepository()
    telegram = FakeTelegram()
    app = TelegramBotApp(repository=repository, plan_provider=FakePlanProvider(), telegram=telegram)
    asyncio.run(app.handle_update(message("Xiaomi Pad 7 8/256")))
    confirmation_id = next(iter(repository.pending))
    asyncio.run(app.handle_update(callback(f"confirm:{confirmation_id}")))
    return repository, telegram, app, next(iter(repository.subscriptions))


def test_start_then_free_text_creates_durable_confirmation_without_marketplace_wait() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    provider = FakePlanProvider()
    app = TelegramBotApp(repository=repository, plan_provider=provider, telegram=telegram)

    asyncio.run(app.handle_update(message("/start")))
    asyncio.run(app.handle_update(message("Xiaomi Pad 7 8/256")))

    assert "PriceWatch" in telegram.messages[0][1]
    assert provider.calls == ["Xiaomi Pad 7 8/256"]
    assert len(repository.pending) == 1
    confirmation_text = telegram.messages[-1][1]
    assert "Я понял товар" in confirmation_text
    assert repository.products == {}


def test_confirm_subscribes_and_second_user_identity_would_reuse_global_product() -> None:
    repository, telegram, _, _ = create_confirmed_app()

    assert len(repository.products) == 1
    assert len(repository.subscriptions) == 1
    assert repository.pending == {}
    assert "Ищу актуальные предложения" in telegram.messages[-1][1]


def test_correction_creates_no_shared_product_and_next_text_is_new_pending_plan() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    provider = FakePlanProvider()
    app = TelegramBotApp(repository=repository, plan_provider=provider, telegram=telegram)
    asyncio.run(app.handle_update(message("Xiaomi Pad 7 8/256")))
    first_confirmation = next(iter(repository.pending))

    asyncio.run(app.handle_update(callback(f"correct:{first_confirmation}")))
    asyncio.run(app.handle_update(message("Xiaomi Pad 7 12/256")))

    assert repository.products == {}
    assert first_confirmation not in repository.pending
    assert len(repository.pending) == 1
    assert provider.calls == ["Xiaomi Pad 7 8/256", "Xiaomi Pad 7 12/256"]
    assert "исправленное" in telegram.messages[-2][1].casefold()


def test_cancel_consumes_pending_without_creating_product_or_subscription() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    app = TelegramBotApp(repository=repository, plan_provider=FakePlanProvider(), telegram=telegram)
    asyncio.run(app.handle_update(message("Xiaomi Pad 7 8/256")))
    confirmation_id = next(iter(repository.pending))

    asyncio.run(app.handle_update(callback(f"cancel:{confirmation_id}")))

    assert repository.pending == {}
    assert repository.products == {}
    assert repository.subscriptions == {}
    assert "отменено" in telegram.messages[-1][1].casefold()


def test_my_products_product_card_and_history_callbacks() -> None:
    _, telegram, app, subscription_id = create_confirmed_app()

    asyncio.run(app.handle_update(callback("my")))
    assert "📦 Отслеживается: 1" in telegram.messages[-1][1]

    asyncio.run(app.handle_update(callback(f"product:{subscription_id}")))
    assert "Отслеживание включено" in telegram.messages[-1][1]

    asyncio.run(app.handle_update(callback(f"history:{subscription_id}")))
    assert "последние 7 дней" in telegram.messages[-1][1]


def test_pause_resume_only_operates_on_current_users_subscription() -> None:
    repository, _, app, subscription_id = create_confirmed_app()

    asyncio.run(app.handle_update(callback(f"pause:{subscription_id}")))
    assert repository.subscriptions[subscription_id].status == "paused"
    asyncio.run(app.handle_update(callback(f"resume:{subscription_id}")))
    assert repository.subscriptions[subscription_id].status == "active"

    repository.subscriptions[999] = SubscriptionRecord(
        id=999,
        user_id=9999,
        tracked_product_id=next(iter(repository.products)),
        status="active",
    )
    asyncio.run(app.handle_update(callback("pause:999")))
    assert repository.subscriptions[999].status == "active"


def test_my_products_pagination_callback_shows_second_page() -> None:
    repository = FakeRepository()
    telegram = FakeTelegram()
    app = TelegramBotApp(repository=repository, plan_provider=FakePlanProvider(), telegram=telegram)
    for index in range(10):
        product_id = 100 + index
        subscription_id = 200 + index
        repository.products[product_id] = TrackedProductRecord(
            id=product_id,
            canonical_name=f"Product {index + 1}",
            product_type="test",
            identity_fingerprint=f"fp-{index}",
            search_plan=SearchPlan(
                canonical_name=f"Product {index + 1}",
                primary_query=f"product {index + 1}",
            ),
            lifecycle_state="active",
            subscriber_count=1,
            next_scan_at=NOW,
            last_successful_scan_at=None,
        )
        repository.subscriptions[subscription_id] = SubscriptionRecord(
            id=subscription_id,
            user_id=repository.user_id,
            tracked_product_id=product_id,
            status="active",
        )

    asyncio.run(app.handle_update(callback("my")))
    assert "my_page:1" in str(telegram.messages[-1][2])

    asyncio.run(app.handle_update(callback("my_page:1")))
    assert "9. Product 9" in telegram.messages[-1][1]
    assert "10. Product 10" in telegram.messages[-1][1]
