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
