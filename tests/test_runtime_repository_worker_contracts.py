import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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
