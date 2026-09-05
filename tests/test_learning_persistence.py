import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

from pricewatch.learning_persistence import (
    LEARNING_STATE_SCHEMA_VERSION,
    PostgresLearningStateStore,
    decode_engine_state,
    encode_engine_state,
)
from pricewatch.marketplaces import SearchCandidate
from pricewatch.match_learning import HybridMatchEngine, LearningEvidenceSource
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import MarketplaceTaxonomy, TaxonomyGateStatus


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        product_type="tablet",
        aliases=("xiaomi pad7 8 256", "сяоми пад 7 8 256"),
        required_tokens=("xiaomi",),
        excluded_terms=("case", "чехол"),
        identity_attributes={"model": "pad 7", "ram": "8 gb", "storage": "256 gb"},
    )


def ambiguous_candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="10",
        variation_id="20",
        title="Xiaomi Pad 7 256GB",
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
        price=Decimal("29990"),
        price_source="search",
    )


def accessory_candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="30",
        title="Чехол case Xiaomi Pad 7",
        taxonomy=MarketplaceTaxonomy(subject_id="203", entity="Чехлы"),
        price=Decimal("990"),
    )


def test_engine_state_round_trip_preserves_learned_runtime_state() -> None:
    engine = HybridMatchEngine()
    candidate = ambiguous_candidate()
    engine.classify(
        plan(),
        candidate,
        taxonomy_status=TaxonomyGateStatus.PASS,
        source_queries=("xiaomi pad 7 8 256",),
    )
    engine.query_performance.record_discovery(
        "xiaomi pad7 8 256",
        candidate_ids={"10", "11"},
        accepted_ids={"11"},
    )
    engine.query_performance.record_verified("xiaomi pad7 8 256", "11", matched=True)
    engine.classify(plan(), accessory_candidate())
    before_weights = dict(engine.model.weights)
    before_alias = engine.query_performance.select_alias(plan().aliases, slot=2)

    payload = encode_engine_state(engine)
    restored = decode_engine_state(payload)

    assert payload["schema_version"] == LEARNING_STATE_SCHEMA_VERSION
    assert restored.model.weights == before_weights
    assert restored.query_performance.score("xiaomi pad7 8 256") == engine.query_performance.score(
        "xiaomi pad7 8 256"
    )
    assert restored.query_performance.select_alias(plan().aliases, slot=2) == before_alias
    assert len(restored.uncertain_queue.items()) == 1
    assert restored.uncertain_queue.items()[0].candidate.listing_id == "10"
    assert len(restored.hard_negatives) == 1
    assert restored.hard_negatives[0].candidate.listing_id == "30"


def test_verified_learning_survives_state_round_trip() -> None:
    engine = HybridMatchEngine()
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="40",
        title="Xiaomi Pad 7 8GB 256GB",
        attributes={"model": "Pad 7", "ram": "8 GB", "storage": "256 GB"},
    )
    engine.query_performance.record_discovery(
        "xiaomi pad7 8 256",
        candidate_ids={"40"},
        accepted_ids={"40"},
    )
    decision = engine.classify(plan(), candidate)
    engine.learn_verified(
        plan(),
        candidate,
        decision,
        matched=True,
        source=LearningEvidenceSource.DETAIL,
        source_queries=("xiaomi pad7 8 256",),
    )

    restored = decode_engine_state(encode_engine_state(engine))

    assert restored.model.weights == engine.model.weights
    assert restored.query_performance.score("xiaomi pad7 8 256") > 0


def test_unknown_learning_state_schema_is_rejected() -> None:
    payload = encode_engine_state(HybridMatchEngine())
    payload["schema_version"] = 999

    try:
        decode_engine_state(payload)
    except ValueError as exc:
        assert "schema" in str(exc).lower()
    else:
        raise AssertionError("unknown learning-state schema must fail loudly")


class FakeCursor:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.saved_payload: str | None = None
        self.commits = 0

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> FakeCursor:
        self.calls.append((query, params))
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into pricewatch_learning_state") and params is not None:
            self.saved_payload = str(params[2])
        if normalized.startswith("select") and self.saved_payload is not None:
            return FakeCursor((LEARNING_STATE_SCHEMA_VERSION, self.saved_payload))
        return FakeCursor()

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def __call__(self):
        yield self.connection


def test_postgres_store_initializes_saves_and_loads_versioned_state() -> None:
    connection = FakeConnection()
    store = PostgresLearningStateStore(FakeFactory(connection))
    payload = encode_engine_state(HybridMatchEngine())

    asyncio.run(store.initialize())
    asyncio.run(store.save("product:42", payload))
    loaded = asyncio.run(store.load("product:42"))

    assert loaded == payload
    assert connection.commits == 2
    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "create table if not exists pricewatch_learning_state" in sql
    assert "create table if not exists pricewatch_learning_evidence" in sql
    assert "on conflict (scope_key) do update" in sql


def test_postgres_store_appends_only_verified_provenance() -> None:
    connection = FakeConnection()
    store = PostgresLearningStateStore(FakeFactory(connection))
    engine = HybridMatchEngine()
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="50",
        title="Xiaomi Pad 7 8GB 256GB",
        attributes={"model": "Pad 7", "ram": "8 GB", "storage": "256 GB"},
    )
    decision = engine.classify(plan(), candidate)
    engine.learn_verified(
        plan(),
        candidate,
        decision,
        matched=True,
        source=LearningEvidenceSource.DETAIL,
        source_queries=("xiaomi pad7 8 256",),
    )
    verified = engine.evidence[-1]

    asyncio.run(store.append_verified_evidence("product:42", verified))

    sql = "\n".join(query for query, _ in connection.calls).lower()
    assert "insert into pricewatch_learning_evidence" in sql
    assert connection.commits == 1

    search_engine = HybridMatchEngine()
    search_decision = search_engine.classify(plan(), candidate)
    search_engine.record_search_evidence(plan(), candidate, search_decision)
    try:
        asyncio.run(
            store.append_verified_evidence("product:42", search_engine.evidence[-1])
        )
    except ValueError as exc:
        assert "verified" in str(exc).lower()
    else:
        raise AssertionError("search-only evidence must not be persisted as verified provenance")
