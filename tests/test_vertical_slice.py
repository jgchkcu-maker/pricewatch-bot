import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pricewatch.bot import TelegramBotApp
from pricewatch.deals import evaluate_verified_price
from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.outbox import OutboxDispatcher, OutboxItem
from pricewatch.prices import PriceEvent
from pricewatch.runtime_models import (
    SubscriptionRecord,
    TrackedProductRecord,
    UserProductSummary,
    identity_fingerprint,
)
from pricewatch.search_plan import SearchPlan
from pricewatch.worker import PriceWorker

START = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
EXACT_URL = "https://www.wildberries.ru/catalog/123/detail.aspx"


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        required_tokens=("xiaomi",),
        identity_attributes={
            "brand": "Xiaomi",
            "model": "Pad 7",
            "ram": "8 GB",
            "storage": "256 GB",
        },
    )


class FakeProvider:
    async def create_plan(self, text: str) -> SearchPlan:
        return plan()


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": len(self.messages)}

    async def answer_callback_query(self, callback_query_id, *, text=None):
        return None


class MemoryRuntime:
    def __init__(self) -> None:
        self.users: dict[int, tuple[int, int]] = {}
        self.pending: dict[str, tuple[int, str, SearchPlan]] = {}
        self.products: dict[int, TrackedProductRecord] = {}
        self.by_fingerprint: dict[str, int] = {}
        self.subscriptions: dict[int, SubscriptionRecord] = {}
        self.known: dict[tuple[int, str], dict[str, SearchCandidate]] = {}
        self.events: dict[int, list[PriceEvent]] = {}
        self.outbox: list[OutboxItem] = []
        self.claim_count = 0
        self.next_user = 1
        self.next_product = 1
        self.next_subscription = 1
        self.next_outbox = 1

    async def ensure_user(self, *, telegram_user_id, chat_id):
        if telegram_user_id not in self.users:
            self.users[telegram_user_id] = (self.next_user, chat_id)
            self.next_user += 1
        return self.users[telegram_user_id][0]

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
        if consume and value is not None:
            self.pending.pop(confirmation_id, None)
        return value

    async def upsert_tracked_product(self, search_plan):
        fingerprint = identity_fingerprint(search_plan)
        existing_id = self.by_fingerprint.get(fingerprint)
        if existing_id is not None:
            return self.products[existing_id]
        record = TrackedProductRecord(
            id=self.next_product,
            canonical_name=search_plan.canonical_name,
            product_type=search_plan.product_type,
            identity_fingerprint=fingerprint,
            search_plan=search_plan,
            lifecycle_state="active",
            subscriber_count=0,
            next_scan_at=START,
            last_successful_scan_at=None,
        )
        self.products[record.id] = record
        self.by_fingerprint[fingerprint] = record.id
        self.next_product += 1
        return record

    async def subscribe(self, *, user_id, product_id):
        for sub in self.subscriptions.values():
            if sub.user_id == user_id and sub.tracked_product_id == product_id:
                return sub
        sub = SubscriptionRecord(
            id=self.next_subscription,
            user_id=user_id,
            tracked_product_id=product_id,
            status="active",
        )
        self.subscriptions[sub.id] = sub
        self.next_subscription += 1
        self._refresh_count(product_id)
        return sub

    def _refresh_count(self, product_id: int) -> None:
        old = self.products[product_id]
        count = sum(
            sub.status == "active" and sub.tracked_product_id == product_id
            for sub in self.subscriptions.values()
        )
        self.products[product_id] = TrackedProductRecord(
            id=old.id,
            canonical_name=old.canonical_name,
            product_type=old.product_type,
            identity_fingerprint=old.identity_fingerprint,
            search_plan=old.search_plan,
            lifecycle_state="active" if count else "paused_no_subscribers",
            subscriber_count=count,
            next_scan_at=old.next_scan_at,
            last_successful_scan_at=old.last_successful_scan_at,
        )

    async def list_user_products(self, user_id):
        return tuple(
            UserProductSummary(
                subscription=sub,
                product=self.products[sub.tracked_product_id],
            )
            for sub in self.subscriptions.values()
            if sub.user_id == user_id
        )

    async def pause_subscription(self, subscription_id):
        old = self.subscriptions[subscription_id]
        self.subscriptions[subscription_id] = SubscriptionRecord(
            id=old.id,
            user_id=old.user_id,
            tracked_product_id=old.tracked_product_id,
            status="paused",
        )
        self._refresh_count(old.tracked_product_id)

    async def resume_subscription(self, subscription_id):
        old = self.subscriptions[subscription_id]
        self.subscriptions[subscription_id] = SubscriptionRecord(
            id=old.id,
            user_id=old.user_id,
            tracked_product_id=old.tracked_product_id,
            status="active",
        )
        self._refresh_count(old.tracked_product_id)

    async def claim_due_products(self, *, worker_id, now, limit, lease_seconds):
        due = tuple(
            product
            for product in self.products.values()
            if product.lifecycle_state == "active"
            and product.subscriber_count > 0
            and product.next_scan_at <= now
        )[:limit]
        self.claim_count += len(due)
        return due

    async def known_listings(self, product_id, marketplace):
        return tuple(self.known.get((product_id, marketplace), {}).values())

    async def complete_scan(
        self,
        product_id,
        *,
        now,
        success,
        interval_seconds,
        retry_after_seconds=None,
    ):
        old = self.products[product_id]
        delay = interval_seconds if success else (retry_after_seconds or 900)
        self.products[product_id] = TrackedProductRecord(
            id=old.id,
            canonical_name=old.canonical_name,
            product_type=old.product_type,
            identity_fingerprint=old.identity_fingerprint,
            search_plan=old.search_plan,
            lifecycle_state=old.lifecycle_state,
            subscriber_count=old.subscriber_count,
            next_scan_at=now + timedelta(seconds=delay),
            last_successful_scan_at=now if success else old.last_successful_scan_at,
        )

    async def record_taxonomy_positive(self, product, candidate):
        return None

    async def record_verified_offer(
        self,
        product,
        candidate,
        snapshot,
        *,
        verified_at,
        allow_alerts=True,
    ):
        self.known.setdefault((product.id, candidate.marketplace), {})[
            candidate.listing_id
        ] = candidate
        history = self.events.setdefault(product.id, [])
        if history and history[-1].price == snapshot.price:
            return None
        decision = evaluate_verified_price(history, snapshot.price, observed_at=verified_at)
        history.append(PriceEvent(price=snapshot.price, observed_at=verified_at))
        if allow_alerts and decision.is_new_low:
            for sub in self.subscriptions.values():
                if sub.tracked_product_id != product.id or sub.status != "active":
                    continue
                chat_id = next(
                    chat
                    for _, (internal_id, chat) in self.users.items()
                    if internal_id == sub.user_id
                )
                self.outbox.append(
                    OutboxItem(
                        id=self.next_outbox,
                        user_id=sub.user_id,
                        subscription_id=sub.id,
                        tracked_product_id=product.id,
                        notification_type="new_low",
                        payload={
                            "chat_id": chat_id,
                            "product_name": product.canonical_name,
                            "marketplace": candidate.marketplace,
                            "public_price": str(snapshot.price),
                            "previous_min": str(decision.previous_min),
                            "delta": str(decision.delta),
                            "delta_percent": str(decision.delta_percent),
                            "url": snapshot.locator.url,
                            "verified_at": verified_at.isoformat(),
                            "conditional_prices": {},
                        },
                        attempt_count=0,
                    )
                )
                self.next_outbox += 1
        return decision

    async def claim_due(self, *, now, limit):
        return tuple(self.outbox[:limit])

    async def mark_sent(self, item_id, *, now):
        self.outbox = [item for item in self.outbox if item.id != item_id]

    async def mark_retry(self, item, *, now, error, retry_after_seconds=None):
        return None

    async def mark_permanent_failure(self, item, *, error):
        self.outbox = [value for value in self.outbox if value.id != item.id]


class FakeLearningStore:
    def __init__(self) -> None:
        self.engines: dict[str, HybridMatchEngine] = {}

    async def load_engine(self, scope_key):
        return self.engines.setdefault(scope_key, HybridMatchEngine())

    async def save_verified_update(self, scope_key, engine, evidence):
        self.engines[scope_key] = engine


class MutableAdapter:
    marketplace = "wildberries"

    def __init__(self) -> None:
        self.detail_price = Decimal("29490")
        self.search_preview_price = Decimal("799")
        self.detail_title = "Xiaomi Pad 7 8GB 256GB"
        self.search_calls = 0
        self.detail_calls = 0

    def candidate(self):
        return SearchCandidate(
            marketplace=self.marketplace,
            listing_id="123",
            variation_id="123",
            title="Xiaomi Pad 7 8GB 256GB",
            attributes={
                "brand": "Xiaomi",
                "model": "Pad 7",
                "ram": "8 GB",
                "storage": "256 GB",
            },
            url=EXACT_URL,
            price=self.search_preview_price,
            price_source="search",
        )

    async def search(self, query, *, limit=50, page=1, category_path=None):
        self.search_calls += 1
        return [self.candidate()]

    async def fetch_offer(self, locator: OfferLocator):
        self.detail_calls += 1
        return OfferSnapshot(
            locator=OfferLocator(
                marketplace=self.marketplace,
                listing_id="123",
                variation_id="123",
                url=EXACT_URL,
            ),
            title=self.detail_title,
            attributes={
                "brand": "Xiaomi",
                "model": "Pad 7",
                "ram": "8 GB",
                "storage": "256 GB",
            },
            price=self.detail_price,
            available=True,
            price_source="detail",
        )


def message(update_id: int, user: int, chat: int, text: str):
    return {
        "update_id": update_id,
        "message": {"from": {"id": user}, "chat": {"id": chat}, "text": text},
    }


def callback(update_id: int, user: int, chat: int, data: str):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": user},
            "message": {"chat": {"id": chat}},
            "data": data,
        },
    }


def confirmation_id(telegram: FakeTelegram, message_index: int) -> str:
    markup = telegram.messages[message_index][2]
    callback_data = markup["inline_keyboard"][0][0]["callback_data"]
    return callback_data.split(":", 1)[1]


def test_two_users_share_one_worker_product_and_receive_verified_new_low_alerts() -> None:
    runtime = MemoryRuntime()
    telegram = FakeTelegram()
    bot = TelegramBotApp(repository=runtime, plan_provider=FakeProvider(), telegram=telegram)

    asyncio.run(bot.handle_update(message(1, 101, 1001, "Xiaomi Pad 7 8/256")))
    first_confirmation = confirmation_id(telegram, 0)
    asyncio.run(bot.handle_update(callback(2, 101, 1001, f"confirm:{first_confirmation}")))

    asyncio.run(bot.handle_update(message(3, 202, 2002, "Xiaomi Pad 7 8/256")))
    second_confirmation = confirmation_id(telegram, 2)
    asyncio.run(bot.handle_update(callback(4, 202, 2002, f"confirm:{second_confirmation}")))

    assert len(runtime.products) == 1
    product = next(iter(runtime.products.values()))
    assert product.subscriber_count == 2

    adapter = MutableAdapter()
    worker = PriceWorker(
        repository=runtime,
        verified_store=runtime,
        learning_store=FakeLearningStore(),
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    assert asyncio.run(worker.run_once(START)) == 1
    assert runtime.events[product.id][-1].price == Decimal("29490")
    assert runtime.outbox == []
    assert adapter.search_preview_price == Decimal("799")

    adapter.detail_price = Decimal("24990")
    second_scan = START + timedelta(minutes=4)
    assert asyncio.run(worker.run_once(second_scan)) == 1
    assert [event.price for event in runtime.events[product.id]] == [
        Decimal("29490"),
        Decimal("24990"),
    ]
    assert len(runtime.outbox) == 2
    assert runtime.claim_count == 2

    dispatcher = OutboxDispatcher(store=runtime, telegram=telegram)
    assert asyncio.run(dispatcher.run_once(now=second_scan)) == 2
    alerts = [text for _, text, _ in telegram.messages if "НОВАЯ МИНИМАЛЬНАЯ" in text]
    assert len(alerts) == 2
    alert_markups = [
        markup
        for _, text, markup in telegram.messages
        if "НОВАЯ МИНИМАЛЬНАЯ" in text
    ]
    assert all(EXACT_URL in str(markup) for markup in alert_markups)


def test_mismatched_detail_never_becomes_price_event_even_with_fake_search_low() -> None:
    runtime = MemoryRuntime()
    product = asyncio.run(runtime.upsert_tracked_product(plan()))
    asyncio.run(runtime.subscribe(user_id=1, product_id=product.id))
    adapter = MutableAdapter()
    adapter.detail_title = "Xiaomi Pad 7 Pro 8GB 256GB"
    worker = PriceWorker(
        repository=runtime,
        verified_store=runtime,
        learning_store=FakeLearningStore(),
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(START))

    assert runtime.events.get(product.id, []) == []
    assert runtime.outbox == []
