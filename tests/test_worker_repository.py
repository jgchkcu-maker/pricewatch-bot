import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.taxonomy import MarketplaceTaxonomy
from pricewatch.worker_repository import PostgresWorkerRepository

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
        if "from tracked_product p" in normalized and "for update" in normalized:
            return FakeCursor(
                rows=[
                    (
                        42,
                        "Xiaomi Pad 7 8/256",
                        "tablet",
                        "fingerprint",
                        '{"canonical_name":"Xiaomi Pad 7 8/256","primary_query":"xiaomi pad 7 8 256","product_type":"tablet","aliases":[],"required_tokens":[],"excluded_terms":[],"identity_attributes":{"model":"pad 7","ram":"8 gb","storage":"256 gb"}}',
                        "active",
                        2,
                        NOW,
                        None,
                    )
                ]
            )
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
                        "Xiaomi Pad 7 8GB 256GB",
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


def test_claim_due_products_uses_skip_locked_and_upserts_short_lease() -> None:
    connection = FakeConnection()
    repository = PostgresWorkerRepository(FakeFactory(connection))

    products = asyncio.run(
        repository.claim_due_products(
            worker_id="worker-a",
            now=NOW,
            limit=10,
            lease_seconds=180,
        )
    )

    assert len(products) == 1
    assert isinstance(products[0], TrackedProductRecord)
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "for update of p skip locked" in sql
    assert "insert into worker_lease" in sql
    assert "on conflict (tracked_product_id) do update" in sql
    assert connection.commits == 1


def test_known_listings_restore_attributes_and_taxonomy() -> None:
    repository = PostgresWorkerRepository(FakeFactory(FakeConnection()))

    listings = asyncio.run(repository.known_listings(42, "wildberries"))

    assert len(listings) == 1
    listing = listings[0]
    assert listing.listing_id == "123"
    assert listing.variation_id == "456"
    assert listing.attributes["model"] == "Pad 7"
    assert listing.taxonomy == MarketplaceTaxonomy(subject_id="107", entity="Планшеты")


def test_complete_scan_releases_lease_and_uses_success_or_backoff_schedule() -> None:
    success_connection = FakeConnection()
    success_repo = PostgresWorkerRepository(FakeFactory(success_connection))
    asyncio.run(
        success_repo.complete_scan(
            42,
            now=NOW,
            success=True,
            interval_seconds=240,
        )
    )
    success_sql = "\n".join(query for query, _ in success_connection.calls).lower()
    assert "last_successful_scan_at = %s" in success_sql
    assert "delete from worker_lease" in success_sql

    failure_connection = FakeConnection()
    failure_repo = PostgresWorkerRepository(FakeFactory(failure_connection))
    asyncio.run(
        failure_repo.complete_scan(
            42,
            now=NOW,
            success=False,
            interval_seconds=240,
            retry_after_seconds=600,
        )
    )
    params = [params for query, params in failure_connection.calls if "update tracked_product" in query.lower()][0]
    assert params is not None
    assert params[0] == NOW


def test_verified_taxonomy_positive_is_append_only_evidence() -> None:
    connection = FakeConnection()
    repository = PostgresWorkerRepository(FakeFactory(connection))
    product = asyncio.run(
        repository.claim_due_products(
            worker_id="worker-a",
            now=NOW,
            limit=1,
            lease_seconds=180,
        )
    )[0]
    listing = asyncio.run(repository.known_listings(42, "wildberries"))[0]

    asyncio.run(repository.record_taxonomy_positive(product, listing))

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "insert into taxonomy_evidence" in sql
    assert "verified_label" in sql
