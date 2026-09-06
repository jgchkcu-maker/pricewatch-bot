import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.search_plan import SearchPlan
from pricewatch.transport import MarketplaceRateLimitedError
from pricewatch.worker import PriceWorker

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def product(*, first_scan: bool = True) -> TrackedProductRecord:
    plan = SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        required_tokens=("xiaomi",),
        identity_attributes={"model": "pad 7", "ram": "8 gb", "storage": "256 gb"},
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


def candidate(listing_id: str) -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id=listing_id,
        variation_id=listing_id,
        title="Xiaomi Pad 7 8GB 256GB",
        attributes={"model": "Pad 7", "ram": "8 GB", "storage": "256 GB"},
        url=f"https://www.wildberries.ru/catalog/{listing_id}/detail.aspx",
    )


class FakeAdapter:
    marketplace = "wildberries"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.fetch_counts: dict[str, int] = {}

    async def search(self, query, *, limit=50, page=1, category_path=None):
        self.events.append(f"search:{query}")
        return [candidate("known"), candidate("new")]

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        self.events.append(f"fetch:{locator.listing_id}")
        self.fetch_counts[locator.listing_id] = self.fetch_counts.get(locator.listing_id, 0) + 1
        return OfferSnapshot(
            locator=locator,
            title="Xiaomi Pad 7 8GB 256GB",
            attributes={"model": "Pad 7", "ram": "8 GB", "storage": "256 GB"},
            price=Decimal("24990"),
            available=True,
            price_source="detail",
        )


class ExactPriceAdapter(FakeAdapter):
    def __init__(self, price: str) -> None:
        super().__init__()
        self.price = Decimal(price)

    async def search(self, query, *, limit=50, page=1, category_path=None):
        self.events.append(f"search:{query}")
        return [candidate("new")]

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        self.events.append(f"fetch:{locator.listing_id}")
        self.fetch_counts[locator.listing_id] = self.fetch_counts.get(locator.listing_id, 0) + 1
        return OfferSnapshot(
            locator=locator,
            title="Xiaomi Pad 7 8GB 256GB",
            attributes={"model": "Pad 7", "ram": "8 GB", "storage": "256 GB"},
            price=self.price,
            available=True,
            price_source="detail",
        )


class RateLimitedAdapter(FakeAdapter):
    async def search(self, query, *, limit=50, page=1, category_path=None):
        raise MarketplaceRateLimitedError("limited", retry_after_seconds=600)


class FakeWorkerRepository:
    def __init__(
        self,
        tracked: TrackedProductRecord,
        *,
        trusted_prices: tuple[Decimal, ...] = (Decimal("24990"),),
        known: tuple[SearchCandidate, ...] | None = None,
    ) -> None:
        self.tracked = tracked
        self.trusted_prices = trusted_prices
        self.known = (candidate("known"),) if known is None else known
        self.complete_calls: list[tuple[int, bool, int | None]] = []
        self.taxonomy_calls: list[str] = []

    async def claim_due_products(self, *, worker_id, now, limit, lease_seconds):
        return (self.tracked,)

    async def list_known_candidates(self, product_id, marketplace):
        return self.known

    async def list_trusted_price_reference(self, product_id, marketplace, *, since):
        return self.trusted_prices

    async def complete_scan(
        self,
        product_id,
        *,
        now,
        success,
        interval_seconds,
        retry_after_seconds=None,
    ):
        self.complete_calls.append((product_id, success, retry_after_seconds))

    async def record_taxonomy_positive(self, product, candidate):
        self.taxonomy_calls.append(candidate.listing_id)


class FakeVerifiedStore:
    def __init__(self) -> None:
        self.trusted_calls: list[tuple[str, bool]] = []
        self.quarantine_calls: list[str] = []
        self.rejection_calls: list[str] = []

    @property
    def calls(self) -> list[tuple[str, bool]]:
        return self.trusted_calls

    async def record_verified_offer(
        self,
        product,
        candidate,
        snapshot,
        *,
        verified_at,
        allow_alerts=True,
    ):
        self.trusted_calls.append((candidate.listing_id, allow_alerts))
        return None

    async def record_quarantined_offer(
        self,
        product,
        candidate,
        snapshot,
        decision,
        *,
        verified_at,
    ):
        self.quarantine_calls.append(candidate.listing_id)
        return None

    async def record_quality_rejection(
        self,
        product,
        candidate,
        snapshot,
        decision,
        *,
        verified_at,
    ):
        self.rejection_calls.append(candidate.listing_id)
        return None


class FakeLearningStore:
    def __init__(self) -> None:
        self.engine = HybridMatchEngine()
        self.saved = 0

    async def load_engine(self, scope_key):
        return self.engine

    async def save_verified_update(self, scope_key, engine, evidence):
        self.saved += 1


def completed_scan_record(caplog):
    records = [record for record in caplog.records if record.message == "marketplace scan completed"]
    assert len(records) == 1
    return records[0].marketplace_scan_stats


def test_worker_polls_known_listing_before_discovery_and_deduplicates_detail_fetch() -> None:
    adapter = FakeAdapter()
    repository = FakeWorkerRepository(product(first_scan=True))
    verified = FakeVerifiedStore()
    learning = FakeLearningStore()
    worker = PriceWorker(
        repository=repository,
        verified_store=verified,
        learning_store=learning,
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    processed = asyncio.run(worker.run_once(NOW))

    assert processed == 1
    assert adapter.events[0] == "fetch:known"
    assert adapter.fetch_counts == {"known": 1, "new": 1}
    assert [listing_id for listing_id, _ in verified.calls] == ["known", "new"]
    assert all(allow_alerts is False for _, allow_alerts in verified.calls)
    assert repository.complete_calls == [(42, True, None)]


def test_worker_enables_alerts_after_product_has_successful_baseline_scan() -> None:
    adapter = FakeAdapter()
    repository = FakeWorkerRepository(product(first_scan=False))
    verified = FakeVerifiedStore()
    worker = PriceWorker(
        repository=repository,
        verified_store=verified,
        learning_store=FakeLearningStore(),
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(NOW))

    assert verified.calls
    assert all(allow_alerts is True for _, allow_alerts in verified.calls)


def test_worker_quarantines_anomalous_exact_price_without_verified_write(caplog) -> None:
    caplog.set_level(logging.INFO, logger="pricewatch.worker")
    repository = FakeWorkerRepository(
        product(first_scan=False),
        trusted_prices=(Decimal("19990"),),
        known=(),
    )
    adapter = ExactPriceAdapter("827")
    verified = FakeVerifiedStore()
    worker = PriceWorker(
        repository=repository,
        verified_store=verified,
        learning_store=FakeLearningStore(),
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(NOW))

    assert verified.trusted_calls == []
    assert verified.quarantine_calls == ["new"]
    stats = completed_scan_record(caplog)
    assert stats.quarantined_count == 1
    assert stats.trusted_count == 0
    assert stats.verified_count == 1
    assert stats.reason_code_counts["price_outlier"] == 1


def test_worker_sends_normal_exact_offer_to_existing_trusted_store() -> None:
    repository = FakeWorkerRepository(
        product(first_scan=False),
        trusted_prices=(Decimal("19990"),),
        known=(),
    )
    adapter = ExactPriceAdapter("19990")
    verified = FakeVerifiedStore()
    worker = PriceWorker(
        repository=repository,
        verified_store=verified,
        learning_store=FakeLearningStore(),
        adapters={"wildberries": adapter},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(NOW))

    assert [listing_id for listing_id, _ in verified.trusted_calls] == ["new"]
    assert verified.quarantine_calls == []
    assert verified.rejection_calls == []


def test_worker_uses_rate_limit_backoff_when_no_marketplace_scan_succeeds() -> None:
    repository = FakeWorkerRepository(product(first_scan=False))
    worker = PriceWorker(
        repository=repository,
        verified_store=FakeVerifiedStore(),
        learning_store=FakeLearningStore(),
        adapters={"wildberries": RateLimitedAdapter()},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(NOW))

    assert repository.complete_calls == [(42, False, 600)]


def test_worker_caps_failed_first_scan_backoff_to_five_minutes() -> None:
    repository = FakeWorkerRepository(product(first_scan=True))
    worker = PriceWorker(
        repository=repository,
        verified_store=FakeVerifiedStore(),
        learning_store=FakeLearningStore(),
        adapters={"wildberries": RateLimitedAdapter()},
        worker_id="worker-1",
    )

    asyncio.run(worker.run_once(NOW))

    assert repository.complete_calls == [(42, False, 300)]
