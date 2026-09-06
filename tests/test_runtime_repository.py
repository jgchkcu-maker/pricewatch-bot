import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

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


class DeletionConnection(FakeConnection):
    def __init__(self, *, owned: bool = True, has_remaining_subscriptions: bool = False) -> None:
        super().__init__()
        self.owned = owned
        self.has_remaining_subscriptions = has_remaining_subscriptions

    async def execute(self, query: str, params=None):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "delete from subscription" in normalized and "returning tracked_product_id" in normalized:
            return FakeCursor((42,) if self.owned else None)
        if "select exists" in normalized and "from subscription" in normalized:
            return FakeCursor((self.has_remaining_subscriptions,))
        return FakeCursor()


def test_delete_subscription_is_scoped_to_owner() -> None:
    connection = DeletionConnection(owned=False)
    repository = RuntimeRepository(FakeFactory(connection))

    with pytest.raises(KeyError):
        asyncio.run(repository.delete_subscription(user_id=99, subscription_id=77))

    delete_query, params = next(
        (query, params)
        for query, params in connection.calls
        if "delete from subscription" in query.lower()
    )
    normalized = " ".join(delete_query.lower().split())
    assert "where id = %s and user_id = %s" in normalized
    assert params == (77, 99)


def test_delete_last_subscription_removes_product_price_history() -> None:
    connection = DeletionConnection(owned=True, has_remaining_subscriptions=False)
    repository = RuntimeRepository(FakeFactory(connection))

    asyncio.run(repository.delete_subscription(user_id=11, subscription_id=77))

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "delete from price_event where tracked_product_id = %s" in " ".join(sql.split())
    assert "subscriber_count = (" in sql
    assert connection.commits == 1


def test_delete_subscription_keeps_history_when_someone_else_still_tracks_product() -> None:
    connection = DeletionConnection(owned=True, has_remaining_subscriptions=True)
    repository = RuntimeRepository(FakeFactory(connection))

    asyncio.run(repository.delete_subscription(user_id=11, subscription_id=77))

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "delete from price_event where tracked_product_id = %s" not in " ".join(sql.split())


def test_price_history_retention_keeps_one_day_buffer_after_seven_day_window() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    connection = FakeConnection()
    repository = RuntimeRepository(FakeFactory(connection))

    asyncio.run(repository.prune_price_events(now=now))

    prune_query, params = next(
        (query, params)
        for query, params in connection.calls
        if "delete from price_event where verified_at" in " ".join(query.lower().split())
    )
    assert "verified_at < %s" in " ".join(prune_query.lower().split())
    assert params == (now - timedelta(days=8),)
