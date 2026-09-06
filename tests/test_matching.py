from pricewatch.marketplaces import SearchCandidate
from pricewatch.matching import MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan


def make_plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="xiaomi pad 7 8 256",
        required_tokens=("xiaomi", "pad", "7"),
        excluded_terms=("pad 7 pro", "чехол", "case", "клавиатура"),
        identity_attributes={"ram": "8 gb", "storage": "256 gb"},
    )


def test_accepts_exact_product_with_required_attributes() -> None:
    candidate = SearchCandidate(
        marketplace="wb",
        listing_id="123",
        title="Планшет Xiaomi Pad 7 8GB 256GB WiFi",
        attributes={"ram": "8GB", "storage": "256 GB"},
    )
    assert match_candidate(make_plan(), candidate).status is MatchStatus.ACCEPT


def test_rejects_explicitly_excluded_model() -> None:
    candidate = SearchCandidate(
        marketplace="ozon",
        listing_id="456",
        title="Xiaomi Pad 7 Pro 8GB 256GB",
        attributes={"ram": "8 gb", "storage": "256 gb"},
    )
    decision = match_candidate(make_plan(), candidate)
    assert decision.status is MatchStatus.REJECT
    assert "excluded term" in decision.reason


def test_rejects_accessory_even_when_product_name_is_present() -> None:
    candidate = SearchCandidate(
        marketplace="wb",
        listing_id="789",
        title="Чехол для Xiaomi Pad 7 8/256",
        attributes={},
    )
    assert match_candidate(make_plan(), candidate).status is MatchStatus.REJECT


def test_rejects_explicit_identity_attribute_contradiction() -> None:
    candidate = SearchCandidate(
        marketplace="ozon",
        listing_id="999",
        title="Xiaomi Pad 7 12GB 256GB",
        attributes={"ram": "12 GB", "storage": "256 GB"},
    )
    decision = match_candidate(make_plan(), candidate)
    assert decision.status is MatchStatus.REJECT
    assert "ram" in decision.reason


def test_marks_missing_critical_attribute_as_ambiguous() -> None:
    candidate = SearchCandidate(
        marketplace="wb",
        listing_id="1000",
        title="Xiaomi Pad 7 256GB",
        attributes={"storage": "256GB"},
    )
    decision = match_candidate(make_plan(), candidate)
    assert decision.status is MatchStatus.AMBIGUOUS
    assert "ram" in decision.reason
