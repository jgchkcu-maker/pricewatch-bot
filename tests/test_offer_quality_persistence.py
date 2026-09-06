import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.offer_quality import (
    OfferQualityDecision,
    OfferQualityReason,
    OfferQualityStatus,
)
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.search_plan import SearchPlan
from pricewatch.verified_store import VerifiedOfferStore

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def product() -> TrackedProductRecord:
    plan = SearchPlan(
        canonical_name="Apple AirPods Pro 3",
        primary_query="apple airpods pro 3",
        product_type="wireless earbuds",
        identity_attributes={"brand": "apple", "model": "airpods pro 3"},
    )
    return TrackedProductRecord(
        id=42,
        canonical_name=plan.canonical_name,
        product_type=plan.product_type,
        identity_fingerprint="airpods-pro-3",
        search_plan=plan,
        lifecycle_state="active",
        subscriber_count=1,
        next_scan_at=NOW,
        last_successful_scan_at=NOW,
    )


def candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="123",
        variation_id="456",
        seller_id="789",
        seller_name="Seller",
        title="AirPods Pro 3",
        url="https://www.wildberries.ru/catalog/123/detail.aspx",
    )


def snapshot(price: str) -> OfferSnapshot:
    return OfferSnapshot(
        locator=OfferLocator(
            marketplace="wildberries",
            listing_id="123",
            variation_id="456",
            seller_id="789",
            url="https://www.wildberries.ru/catalog/123/detail.aspx",
        ),
        title="AirPods Pro 3",
        price=Decimal(price),
        available=True,
        price_source="card",
    )


def quarantine_decision() -> OfferQualityDecision:
    return OfferQualityDecision(
        status=OfferQualityStatus.QUARANTINED,
        reason_codes=(OfferQualityReason.PRICE_OUTLIER, OfferQualityReason.NEEDS_CONFIRMATION),
        reference_price=Decimal("19990"),
        price_ratio=Decimal("827") / Decimal("19990"),
        confirmation_count=1,
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
        if "insert into marketplace_listing" in normalized:
            return FakeCursor((100,))
        if "select public_price" in normalized and "listing_state" in normalized:
            return FakeCursor(None)
        if "from price_event" in normalized:
            return FakeCursor(rows=[])
        if "insert into price_event" in normalized:
            return FakeCursor((500,))
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_quarantined_offer_does_not_write_price_event_state_or_outbox() -> None:
    connection = FakeConnection()
    store = VerifiedOfferStore(FakeFactory(connection))

    result = asyncio.run(
        store.record_quarantined_offer(
            product(),
            candidate(),
            snapshot("827"),
            quarantine_decision(),
            verified_at=NOW,
        )
    )

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert result.status == "quarantined"
    assert "insert into offer_quality_observation" in sql
    assert "insert into price_event" not in sql
    assert "insert into listing_state" not in sql
    assert "update listing_state" not in sql
    assert "insert into notification_outbox" not in sql
    assert connection.commits == 1


def test_quality_rejection_is_diagnostic_only() -> None:
    connection = FakeConnection()
    store = VerifiedOfferStore(FakeFactory(connection))
    decision = OfferQualityDecision(
        status=OfferQualityStatus.REJECTED,
        reason_codes=(OfferQualityReason.EXPLICIT_COUNTERFEIT,),
    )

    result = asyncio.run(
        store.record_quality_rejection(
            product(), candidate(), snapshot("827"), decision, verified_at=NOW
        )
    )

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert result.status == "rejected"
    assert "insert into offer_quality_observation" in sql
    assert "price_event" not in sql
    assert "listing_state" not in sql
    assert "notification_outbox" not in sql


def test_verified_offer_defensively_stamps_trusted_quality() -> None:
    connection = FakeConnection()
    store = VerifiedOfferStore(FakeFactory(connection))

    asyncio.run(
        store.record_verified_offer(
            product(), candidate(), snapshot("19990"), verified_at=NOW, allow_alerts=False
        )
    )

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "quality_status" in sql
    assert "'trusted'" in sql
    assert "from price_event" in sql
    assert "quality_status = 'trusted'" in sql
