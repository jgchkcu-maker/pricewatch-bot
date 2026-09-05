import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pricewatch.runtime_models import identity_fingerprint
from pricewatch.runtime_repository import RuntimeRepository
from pricewatch.search_plan import SearchPlan


def plan(*, ram: str = "8 GB", storage: str = "256 GB") -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        aliases=("xiaomi pad7 8 256",),
        identity_attributes={
            "brand": "Xiaomi",
            "model": "Pad 7",
            "ram": ram,
            "storage": storage,
        },
    )


def test_identity_fingerprint_is_stable_for_equivalent_normalized_identity() -> None:
    left = plan()
    right = SearchPlan(
        canonical_name="  Xiaomi   Pad 7 8/256  ",
        primary_query="XIAOMI PAD7 8+256",
        product_type="TABLET",
        identity_attributes={
            "Brand": "xiaomi",
            "MODEL": "pad7",
            "RAM": "8GB",
            "storage": "256 gb",
        },
    )

    assert identity_fingerprint(left) == identity_fingerprint(right)


def test_identity_fingerprint_changes_for_identity_variant() -> None:
    assert identity_fingerprint(plan(ram="8 GB")) != identity_fingerprint(plan(ram="12 GB"))
    assert identity_fingerprint(plan(storage="256 GB")) != identity_fingerprint(
        plan(storage="128 GB")
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
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.commits = 0

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "insert into telegram_user" in normalized:
            return FakeCursor((11,))
        if "insert into tracked_product" in normalized:
            return FakeCursor(
                (
                    42,
                    "Xiaomi Pad 7 8/256",
                    "tablet",
                    params[2],
                    params[3],
                    "active",
                    0,
                    datetime(2026, 9, 5, tzinfo=UTC),
                    None,
                )
            )
        if "insert into subscription" in normalized:
            return FakeCursor((77, 11, 42, "active"))
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_repository_uses_conflict_safe_global_product_and_subscription_upserts() -> None:
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))

    user_id = asyncio.run(repository.ensure_user(telegram_user_id=1234, chat_id=5678))
    tracked = asyncio.run(repository.upsert_tracked_product(plan()))
    subscription = asyncio.run(repository.subscribe(user_id=user_id, product_id=tracked.id))

    assert user_id == 11
    assert tracked.id == 42
    assert subscription.id == 77
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "on conflict (telegram_user_id) do update" in sql
    assert "on conflict (identity_fingerprint) do update" in sql
    assert "on conflict (user_id, tracked_product_id) do update" in sql
    assert "subscriber_count = (" in sql
