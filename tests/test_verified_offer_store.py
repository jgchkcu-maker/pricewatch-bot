import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pricewatch.verified_store import VerifiedOfferStore

from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.search_plan import SearchPlan

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def product(*, first_scan: bool = False) -> TrackedProductRecord:
    plan = SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        identity_attributes={
            "brand": "xiaomi",
            "model": "pad 7",
            "ram": "8 gb",
            "storage": "256 gb",
        },
    )
    return TrackedProductRecord(
        id=42,
        canonical_name=plan.canonical_name,
        product_type=plan.product_type,
        identity_fingerprint="fingerprint",
        search_plan=plan,
        lifecycle_state="active",
        subscriber_count=2,
        next_scan_at=NOW,
        last_successful_scan_at=None if first_scan else NOW,
    )


def candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="ozon",
        listing_id="123",
        variation_id="123",
        title="Xiaomi Pad 7 8GB 256GB",
        url="https://www.ozon.ru/product/123/",
    )


def snapshot(price: str) -> OfferSnapshot:
    return OfferSnapshot(
        locator=OfferLocator(
            marketplace="ozon",
            listing_id="123",
            variation_id="123",
            url="https://www.ozon.ru/product/123/",
        ),
        title="Xiaomi Pad 7 8GB 256GB",
        price=Decimal(price),
        available=True,
        conditional_prices={"ozon_card": Decimal("23990")},
        price_source="detail",
    )


class FakeCursor:
    def __init__(self, row=None, rows=None) -> None:
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        history=None,
        listing_state=None,
        subscribers=None,
    ) -> None:
        self.history = history or []
        self.listing_state = listing_state
        self.subscribers = subscribers or []
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.commits = 0

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "insert into marketplace_listing" in normalized:
            return FakeCursor((100,))
        if normalized.startswith("select public_price") and "listing_state" in normalized:
            return FakeCursor(self.listing_state)
        if "select public_price, verified_at" in normalized and "from price_event" in normalized:
            return FakeCursor(rows=self.history)
        if "insert into price_event" in normalized:
            return FakeCursor((500,))
        if "select u.id, u.chat_id, s.id" in normalized:
            return FakeCursor(rows=self.subscribers)
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_first_scan_records_baseline_but_suppresses_new_low_outbox() -> None:
    connection = FakeConnection(history=[(Decimal("29490"), NOW)])
    store = VerifiedOfferStore(FakeFactory(connection))

    result = asyncio.run(
        store.record_verified_offer(
            product(first_scan=True),
            candidate(),
            snapshot("24990"),
            verified_at=NOW,
            allow_alerts=False,
        )
    )

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert result.event_id == 500
    assert result.decision.is_new_low is True
    assert "insert into notification_outbox" not in sql
    assert connection.commits == 1


def test_new_verified_low_creates_one_deduped_outbox_row_per_active_subscriber() -> None:
    connection = FakeConnection(
        history=[(Decimal("29490"), NOW)],
        subscribers=[(1, 111, 10), (2, 222, 20)],
    )
    store = VerifiedOfferStore(FakeFactory(connection))

    result = asyncio.run(
        store.record_verified_offer(
            product(),
            candidate(),
            snapshot("24990"),
            verified_at=NOW,
            allow_alerts=True,
        )
    )

    outbox_calls = [
        (query, params)
        for query, params in connection.calls
        if "insert into notification_outbox" in query.lower()
    ]
    assert result.decision.previous_min == Decimal("29490")
    assert result.decision.delta == Decimal("4500")
    assert result.outbox_count == 2
    assert len(outbox_calls) == 2
    assert all("on conflict (dedup_key) do nothing" in query.lower() for query, _ in outbox_calls)


def test_unchanged_verified_state_updates_freshness_without_duplicate_price_event() -> None:
    connection = FakeConnection(
        listing_state=(
            Decimal("24990"),
            '{"ozon_card":"23990"}',
            None,
            True,
        )
    )
    store = VerifiedOfferStore(FakeFactory(connection))

    result = asyncio.run(
        store.record_verified_offer(
            product(),
            candidate(),
            snapshot("24990"),
            verified_at=NOW,
        )
    )

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert result.event_id is None
    assert result.outbox_count == 0
    assert "insert into price_event" not in sql
    assert "update listing_state" in sql
