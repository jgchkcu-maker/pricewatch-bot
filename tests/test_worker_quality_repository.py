import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pricewatch.worker_repository import PostgresWorkerRepository

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "from price_event" in normalized:
            return FakeCursor(rows=[(Decimal("18990"),), (Decimal("19990"),)])
        if "from marketplace_listing" in normalized:
            return FakeCursor(
                rows=[
                    (
                        "wildberries",
                        "123",
                        "456",
                        "seller",
                        "Seller",
                        "https://www.wildberries.ru/catalog/123/detail.aspx",
                        "AirPods Pro 3",
                        "{}",
                        None,
                        "quarantined",
                        1,
                    )
                ]
            )
        return FakeCursor()


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_trusted_price_reference_uses_latest_event_per_exact_listing() -> None:
    connection = FakeConnection()
    repository = PostgresWorkerRepository(FakeFactory(connection))
    since = NOW - timedelta(days=7)

    prices = asyncio.run(
        repository.list_trusted_price_reference(42, "wildberries", since=since)
    )

    assert prices == (Decimal("18990"), Decimal("19990"))
    query, params = next(
        (query, params)
        for query, params in connection.calls
        if "from price_event" in query.lower()
    )
    normalized = " ".join(query.lower().split())
    assert "join marketplace_listing" in normalized
    assert "quality_status = 'trusted'" in normalized
    assert "distinct on" in normalized or "row_number()" in normalized
    assert "marketplace_listing_id" in normalized
    assert "verified_at >= %s" in normalized
    assert params == (42, "wildberries", since)


def test_known_candidates_restore_quality_metadata() -> None:
    repository = PostgresWorkerRepository(FakeFactory(FakeConnection()))

    listings = asyncio.run(repository.list_known_candidates(42, "wildberries"))

    assert len(listings) == 1
    listing = listings[0]
    assert listing.quality_status == "quarantined"
    assert listing.quality_observation_count == 1
