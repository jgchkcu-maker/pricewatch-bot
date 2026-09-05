import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pricewatch.outbox import OutboxDispatcher, OutboxItem, PostgresOutboxStore
from pricewatch.telegram_api import TelegramApiError, TelegramPermanentError, TelegramRateLimitError

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
PAYLOAD = {
    "chat_id": 2002,
    "product_name": "Xiaomi Pad 7 8/256",
    "marketplace": "ozon",
    "public_price": "24990",
    "previous_min": "29490",
    "delta": "4500",
    "delta_percent": "15.26",
    "url": "https://www.ozon.ru/product/123/",
    "verified_at": NOW.isoformat(),
    "conditional_prices": {},
}


class FakeCursor:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.commits = 0

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "from notification_outbox" in normalized and "skip locked" in normalized:
            return FakeCursor(
                rows=[(9, 11, 70, 42, "new_low", PAYLOAD, 0)]
            )
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_postgres_outbox_claim_uses_skip_locked_and_marks_claim() -> None:
    connection = FakeConnection()
    store = PostgresOutboxStore(FakeFactory(connection))

    items = asyncio.run(store.claim_due(now=NOW, limit=20))

    assert len(items) == 1
    assert items[0].id == 9
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "for update skip locked" in sql
    assert "claimed_until" in sql
    assert connection.commits == 1


class MemoryStore:
    def __init__(self) -> None:
        self.item = OutboxItem(
            id=9,
            user_id=11,
            subscription_id=70,
            tracked_product_id=42,
            notification_type="new_low",
            payload=PAYLOAD,
            attempt_count=0,
        )
        self.sent: list[int] = []
        self.retries: list[tuple[int, int | None]] = []
        self.permanent: list[tuple[int, int]] = []

    async def claim_due(self, *, now, limit):
        return (self.item,)

    async def mark_sent(self, item_id, *, now):
        self.sent.append(item_id)

    async def mark_retry(self, item, *, now, error, retry_after_seconds=None):
        self.retries.append((item.id, retry_after_seconds))

    async def mark_permanent_failure(self, item, *, error):
        self.permanent.append((item.id, item.user_id))


class FakeTelegram:
    def __init__(self, error=None) -> None:
        self.error = error
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": 1}


def test_dispatcher_sends_new_low_and_marks_sent_once() -> None:
    store = MemoryStore()
    telegram = FakeTelegram()
    dispatcher = OutboxDispatcher(store=store, telegram=telegram)

    sent = asyncio.run(dispatcher.run_once(now=NOW))

    assert sent == 1
    assert store.sent == [9]
    assert "НОВАЯ МИНИМАЛЬНАЯ" in telegram.sent[0][1]
    assert "https://www.ozon.ru/product/123/" in str(telegram.sent[0][2])


def test_dispatcher_retries_rate_limit_and_transient_failure() -> None:
    store = MemoryStore()
    dispatcher = OutboxDispatcher(
        store=store,
        telegram=FakeTelegram(TelegramRateLimitError("slow", retry_after_seconds=17)),
    )
    assert asyncio.run(dispatcher.run_once(now=NOW)) == 0
    assert store.retries == [(9, 17)]

    store = MemoryStore()
    dispatcher = OutboxDispatcher(store=store, telegram=FakeTelegram(TelegramApiError("network")))
    assert asyncio.run(dispatcher.run_once(now=NOW)) == 0
    assert store.retries == [(9, None)]


def test_dispatcher_marks_blocked_chat_permanent_without_touching_tracking() -> None:
    store = MemoryStore()
    dispatcher = OutboxDispatcher(
        store=store,
        telegram=FakeTelegram(TelegramPermanentError("bot was blocked")),
    )

    assert asyncio.run(dispatcher.run_once(now=NOW)) == 0
    assert store.permanent == [(9, 11)]
    assert store.sent == []
