import json

import pytest

from pricewatch.search_plan_llm import (
    DEFAULT_SEARCH_PLAN_MODEL,
    SEARCH_PLAN_SYSTEM_PROMPT,
    SearchPlanPayloadError,
    parse_search_plan_response,
)


def test_prompt_is_strict_about_universal_identity_and_query_spam() -> None:
    prompt = SEARCH_PLAN_SYSTEM_PROMPT.casefold()

    assert "do not invent" in prompt
    assert "word-order permutations" in prompt
    assert "region" in prompt
    assert "identity_attributes" in prompt
    assert "brand" in prompt
    assert "gtin" in prompt
    assert "ean" in prompt
    assert "upc" in prompt
    assert "mpn" in prompt
    assert "preserve" in prompt
    assert "7 aliases" in prompt
    assert DEFAULT_SEARCH_PLAN_MODEL == "gemini-3.5-flash-lite"


def test_parse_search_plan_response_builds_normalized_plan() -> None:
    raw = json.dumps(
        {
            "canonical_name": "Xiaomi Pad 7 8/256",
            "product_type": "tablet",
            "primary_query": "Xiaomi Pad 7 8/256",
            "aliases": [
                "Xiaomi Pad7 8 256",
                "Сяоми Пад 7 8 256",
            ],
            "required_tokens": ["Xiaomi"],
            "excluded_terms": ["Pad 7 Pro", "чехол", "case"],
            "identity_attributes": {
                "model": "Pad 7",
                "ram": "8 GB",
                "storage": "256 GB",
            },
        },
        ensure_ascii=False,
    )

    plan = parse_search_plan_response(raw)

    assert plan.product_type == "tablet"
    assert plan.primary_query == "xiaomi pad 7 8 256"
    assert plan.aliases == ("xiaomi pad7 8 256", "сяоми пад 7 8 256")
    assert plan.identity_attributes["ram"] == "8 gb"


def test_parse_search_plan_response_rejects_invented_shape_and_alias_spam() -> None:
    with pytest.raises(SearchPlanPayloadError, match="JSON object"):
        parse_search_plan_response("[]")

    payload = {
        "canonical_name": "x",
        "product_type": "other",
        "primary_query": "x",
        "aliases": [f"x alias {index}" for index in range(8)],
        "required_tokens": [],
        "excluded_terms": [],
        "identity_attributes": {},
    }
    with pytest.raises(SearchPlanPayloadError, match="7 aliases"):
        parse_search_plan_response(json.dumps(payload))


def test_parse_search_plan_response_requires_primary_and_canonical_name() -> None:
    with pytest.raises(SearchPlanPayloadError, match="canonical_name"):
        parse_search_plan_response(
            json.dumps(
                {
                    "canonical_name": "",
                    "product_type": "tablet",
                    "primary_query": "x",
                    "aliases": [],
                    "required_tokens": [],
                    "excluded_terms": [],
                    "identity_attributes": {},
                }
            )
        )
