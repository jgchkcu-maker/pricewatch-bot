import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pricewatch.runtime_repository import RuntimeRepository

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
PLAN_JSON = (
    '{"canonical_name":"Xiaomi Pad 7 8/256",'
    '"primary_query":"xiaomi pad 7 8 256","product_type":"tablet",'
    '"aliases":[],"required_tokens":[],"excluded_terms":[],'
    '"identity_attributes":{"model":"pad 7","ram":"8 gb","storage":"256 gb"}}'
)


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
        if "FROM subscription s" in query:
            return FakeCursor(
                rows=[
                    (
                        70,
                        11,
                        42,
                        "active",
                        42,
                        "Xiaomi Pad 7 8/256",
                        "tablet",
                        "fingerprint",
                        PLAN_JSON,
                        "active",
                        2,
                        NOW,
                        NOW,
                        "31990",
                        "ozon",
                        "https://www.ozon.ru/product/123/",
                        NOW,
                        "24990",
                    )
                ]
            )
        return FakeCursor()

    async def commit(self) -> None:
        return None


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_user_summary_separates_current_best_from_rolling_seven_day_minimum() -> None:
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))

    products = asyncio.run(repository.list_user_products(11))

    assert len(products) == 1
    assert products[0].public_price == "31990"
    assert products[0].seven_day_min_price == "24990"
    assert products[0].listing_url == "https://www.ozon.ru/product/123/"
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "order by current_state.public_price asc" in sql
    assert "min(pe.public_price)" in sql
