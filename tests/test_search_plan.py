from pricewatch.search_plan import SearchPlan, normalize_query, query_for_cycle


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


def test_query_rotation_uses_primary_every_second_cycle_and_rotates_aliases() -> None:
    plan = SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        aliases=("xiaomi pad7 8 256", "сяоми пад 7 8 256"),
    )

    assert [query_for_cycle(plan, i) for i in range(6)] == [
        "xiaomi pad 7 8 256",
        "xiaomi pad7 8 256",
        "xiaomi pad 7 8 256",
        "сяоми пад 7 8 256",
        "xiaomi pad 7 8 256",
        "xiaomi pad7 8 256",
    ]
