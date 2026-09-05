import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.runtime_repository import RuntimeRepository


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, row=None, rows=None) -> None:
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.commits = 0

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "from tracked_product p" in normalized and "for update skip locked" in normalized:
            return FakeCursor(rows=[])
        if "select ml.marketplace" in normalized and "from marketplace_listing ml" in normalized:
            return FakeCursor(
                rows=[
                    (
                        "wildberries",
                        "123",
                        "456",
                        "789",
                        "seller",
                        "https://www.wildberries.ru/catalog/123/detail.aspx",
                        "Xiaomi Pad 7 8ГБ 256ГБ",
                        '{"model":"Pad 7","ram":"8 GB","storage":"256 GB"}',
                        '{"subject_id":"107","entity":"Планшеты"}',
                    )
                ]
            )
        if "select public_price, verified_at from price_event" in normalized:
            return FakeCursor(rows=[])
        if "insert into marketplace_listing" in normalized:
            return FakeCursor((501,))
        if "select public_price" in normalized and "from listing_state" in normalized:
            return FakeCursor(None)
        if "insert into price_event" in normalized:
            return FakeCursor((9001,))
        if "select u.id, u.chat_id, s.id" in normalized:
            return FakeCursor(rows=[(11, 777, 33)])
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_claim_due_products_uses_skip_locked_and_worker_lease() -> None:
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))

    claimed = asyncio.run(
        repository.claim_due_products(
            worker_id="worker-a",
            now=NOW,
            limit=10,
            lease_seconds=90,
        )
    )

    assert claimed == ()
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "for update skip locked" in sql
    assert "insert into worker_lease" in sql
    assert "lease_until" in sql
    assert connection.commits == 1


def test_known_listing_rows_become_detail_poll_candidates() -> None:
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))

    candidates = asyncio.run(repository.list_known_candidates(42, "wildberries"))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.listing_id == "123"
    assert candidate.variation_id == "456"
    assert candidate.seller_id == "789"
    assert candidate.price is None
    assert candidate.taxonomy is not None
    assert candidate.taxonomy.subject_id == "107"


def test_verified_offer_persists_state_event_and_baseline_without_outbox() -> None:
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="123",
        variation_id="456",
        seller_id="789",
        title="Xiaomi Pad 7 8ГБ 256ГБ",
        url="https://www.wildberries.ru/catalog/123/detail.aspx",
    )
    snapshot = OfferSnapshot(
        locator=OfferLocator(
            marketplace="wildberries",
            listing_id="123",
            variation_id="456",
            seller_id="789",
            url=candidate.url,
        ),
        title=candidate.title,
        price=Decimal("29490"),
        available=True,
        conditional_prices={"wb_wallet": Decimal("28990")},
        price_source="detail",
    )

    result = asyncio.run(
        repository.record_verified_offer(
            product_id=42,
            candidate=candidate,
            snapshot=snapshot,
            verified_at=NOW,
        )
    )

    assert result.price_event_id == 9001
    assert result.deal.is_baseline is True
    assert result.outbox_count == 0
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "insert into marketplace_listing" in sql
    assert "insert into listing_state" in sql
    assert "insert into price_event" in sql
    assert "insert into notification_outbox" not in sql
    assert connection.commits == 1
