import asyncio
import json

import httpx
import pytest

from pricewatch.search_plan_llm import (
    DEFAULT_SEARCH_PLAN_MODEL,
    SEARCH_PLAN_SYSTEM_PROMPT,
    GeminiSearchPlanProvider,
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
    assert "pro" in prompt
    assert "max" in prompt
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


def test_provider_retries_when_russian_pro_modifier_is_dropped() -> None:
    requests: list[dict[str, object]] = []
    responses = [
        {
            "canonical_name": "Apple AirPods 3",
            "product_type": "wireless headphones",
            "primary_query": "Apple AirPods 3",
            "aliases": ["AirPods 3"],
            "required_tokens": ["AirPods", "3"],
            "excluded_terms": ["case", "чехол"],
            "identity_attributes": {
                "brand": "Apple",
                "model": "AirPods 3",
                "generation": "3",
            },
        },
        {
            "canonical_name": "Apple AirPods Pro 3",
            "product_type": "wireless headphones",
            "primary_query": "Apple AirPods Pro 3",
            "aliases": ["AirPods Pro 3", "Аирподс Про 3"],
            "required_tokens": ["AirPods", "Pro", "3"],
            "excluded_terms": ["AirPods 3", "AirPods Max", "case", "чехол"],
            "identity_attributes": {
                "brand": "Apple",
                "model": "AirPods Pro",
                "generation": "3",
                "edition": "Pro",
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        plan = responses[len(requests) - 1]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(plan, ensure_ascii=False)}]}}
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="key", client=http)
    plan = asyncio.run(provider.create_plan("аирподс 3 про"))
    asyncio.run(http.aclose())

    assert len(requests) == 2
    assert plan.canonical_name == "Apple AirPods Pro 3"
    assert plan.primary_query == "apple airpods pro 3"
    assert plan.identity_attributes["edition"] == "pro"
    retry_text = requests[1]["contents"][0]["parts"][0]["text"]
    assert "pro" in str(retry_text).casefold()


def test_provider_does_not_retry_when_critical_modifier_is_preserved() -> None:
    requests = 0
    payload = {
        "canonical_name": "Samsung Galaxy S25 Ultra",
        "product_type": "smartphone",
        "primary_query": "Samsung Galaxy S25 Ultra",
        "aliases": [],
        "required_tokens": ["Samsung", "S25", "Ultra"],
        "excluded_terms": ["S25", "S25 Plus", "case"],
        "identity_attributes": {
            "brand": "Samsung",
            "model": "Galaxy S25 Ultra",
            "edition": "Ultra",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(payload)}]}}
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="key", client=http)
    plan = asyncio.run(provider.create_plan("Samsung S25 Ultra"))
    asyncio.run(http.aclose())

    assert requests == 1
    assert plan.canonical_name == "Samsung Galaxy S25 Ultra"
