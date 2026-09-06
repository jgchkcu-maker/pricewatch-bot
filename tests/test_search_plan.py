from decimal import Decimal

from pricewatch.marketplaces import OfferCondition, OfferLocator, OfferSnapshot
from pricewatch.runtime_models import search_plan_from_payload, search_plan_to_payload
from pricewatch.search_plan import SearchPlan, normalize_query, queries_for_cycle


def test_normalize_query_removes_marketplace_unfriendly_separators() -> None:
    assert normalize_query("Xiaomi Pad 7 8/256") == "xiaomi pad 7 8 256"
    assert normalize_query("Xiaomi Pad 7 8+256") == "xiaomi pad 7 8 256"
    assert normalize_query("Xiaomi   Pad-7   8/256") == "xiaomi pad 7 8 256"


def test_search_plan_deduplicates_normalized_aliases() -> None:
    plan = SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="Xiaomi Pad 7 8/256",
        aliases=(
            "Xiaomi Pad 7 8+256",
            "Xiaomi Pad7 8 256",
            "Сяоми Пад 7 8 256",
            "Xiaomi Pad7 8 256",
        ),
        required_tokens=("xiaomi", "pad", "7"),
        excluded_terms=("pad 7 pro", "чехол"),
        identity_attributes={"ram": "8 gb", "storage": "256 gb"},
    )

    assert plan.primary_query == "xiaomi pad 7 8 256"
    assert plan.aliases == ("xiaomi pad7 8 256", "сяоми пад 7 8 256")


def test_primary_query_runs_every_cycle_and_aliases_are_supplemental() -> None:
    plan = SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        aliases=("xiaomi pad7 8 256", "сяоми пад 7 8 256"),
    )

    assert [queries_for_cycle(plan, i) for i in range(8)] == [
        ("xiaomi pad 7 8 256",),
        ("xiaomi pad 7 8 256", "xiaomi pad7 8 256"),
        ("xiaomi pad 7 8 256",),
        ("xiaomi pad 7 8 256", "сяоми пад 7 8 256"),
        ("xiaomi pad 7 8 256",),
        ("xiaomi pad 7 8 256", "xiaomi pad7 8 256"),
        ("xiaomi pad 7 8 256",),
        ("xiaomi pad 7 8 256", "сяоми пад 7 8 256"),
    ]


def test_alias_frequency_is_configurable() -> None:
    plan = SearchPlan(
        canonical_name="x",
        primary_query="main",
        aliases=("alt",),
    )

    assert queries_for_cycle(plan, 2, alias_every_cycles=4) == ("main",)
    assert queries_for_cycle(plan, 3, alias_every_cycles=4) == ("main", "alt")


def test_search_plan_defaults_to_new_condition() -> None:
    plan = SearchPlan(canonical_name="AirPods Pro 3", primary_query="airpods pro 3")
    assert plan.condition == "new"


def test_search_plan_normalizes_and_validates_condition() -> None:
    plan = SearchPlan(
        canonical_name="AirPods Pro 3",
        primary_query="airpods pro 3",
        condition=" Refurbished ",
    )
    assert plan.condition == "refurbished"

    try:
        SearchPlan(
            canonical_name="AirPods Pro 3",
            primary_query="airpods pro 3",
            condition="open box",
        )
    except ValueError as exc:
        assert str(exc) == "condition must be one of: new, used, refurbished, any"
    else:
        raise AssertionError("unsupported conditions must fail closed")


def test_search_plan_json_without_condition_defaults_to_new() -> None:
    plan = search_plan_from_payload(
        {
            "canonical_name": "AirPods Pro 3",
            "primary_query": "airpods pro 3",
            "product_type": "earbuds",
            "aliases": [],
            "required_tokens": [],
            "excluded_terms": [],
            "identity_attributes": {"model": "airpods pro 3"},
        }
    )
    assert plan.condition == "new"


def test_search_plan_json_round_trip_preserves_condition() -> None:
    plan = SearchPlan(
        canonical_name="AirPods Pro 3",
        primary_query="airpods pro 3",
        condition="used",
    )
    restored = search_plan_from_payload(search_plan_to_payload(plan))
    assert restored.condition == "used"


def test_offer_snapshot_quality_signals_are_optional() -> None:
    snapshot = OfferSnapshot(
        locator=OfferLocator(marketplace="ozon", listing_id="1"),
        title="AirPods Pro 3",
        price=Decimal("19990"),
        available=True,
    )
    assert snapshot.quality_signals.condition is OfferCondition.UNKNOWN
