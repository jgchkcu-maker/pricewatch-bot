import asyncio
import json

import httpx

from pricewatch.search_plan_llm import GeminiSearchPlanProvider, SearchPlanPayloadError


def response_payload(text: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                }
            }
        ]
    }


def valid_plan_json() -> str:
    return json.dumps(
        {
            "canonical_name": "Xiaomi Pad 7 8/256",
            "product_type": "tablet",
            "primary_query": "xiaomi pad 7 8 256",
            "condition": "new",
            "aliases": ["xiaomi pad7 8 256"],
            "required_tokens": ["xiaomi"],
            "excluded_terms": ["pad 7 pro", "чехол"],
            "identity_attributes": {
                "brand": "Xiaomi",
                "model": "Pad 7",
                "ram": "8 GB",
                "storage": "256 GB",
            },
        },
        ensure_ascii=False,
    )


def exact_identifier_plan_json() -> str:
    return json.dumps(
        {
            "canonical_name": "Example Product",
            "product_type": "device",
            "primary_query": "example product",
            "condition": "new",
            "aliases": [],
            "required_tokens": ["example"],
            "excluded_terms": [],
            "identity_attributes": {
                "brand": "Example",
                "model": "Product",
                "gtin": "1234567890123",
            },
        }
    )


def test_gemini_provider_uses_system_instruction_json_mode_and_parses_plan() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-goog-api-key")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=response_payload(valid_plan_json()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    plan = asyncio.run(provider.create_plan("Xiaomi Pad 7 8/256"))
    asyncio.run(client.aclose())

    assert plan.identity_attributes["storage"] == "256 gb"
    assert plan.condition == "new"
    assert "gemini-3.5-flash-lite:generateContent" in str(captured["url"])
    assert captured["key"] == "secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "systemInstruction" in payload
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_provider_honors_configurable_base_url_and_model() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json=response_payload(valid_plan_json()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(
        api_key="secret",
        model="gemini-custom",
        base_url="https://gemini.example.test/v1",
        client=client,
    )
    asyncio.run(provider.create_plan("Xiaomi Pad 7 8/256"))
    asyncio.run(client.aclose())

    assert captured == ["https://gemini.example.test/v1/models/gemini-custom:generateContent"]


def test_gemini_provider_rejects_http_failure_and_malformed_response() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except RuntimeError as exc:
        assert "gemini" in str(exc).lower()
    else:
        raise AssertionError("provider HTTP failure must be surfaced")
    asyncio.run(client.aclose())

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(malformed))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except RuntimeError as exc:
        assert "candidate" in str(exc).lower()
    else:
        raise AssertionError("missing candidate text must fail closed")
    asyncio.run(client.aclose())


def test_gemini_provider_rejects_malformed_plan_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload("{not valid json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except SearchPlanPayloadError as exc:
        assert "valid json" in str(exc).lower()
    else:
        raise AssertionError("malformed plan JSON must fail closed")
    asyncio.run(client.aclose())


def test_gemini_provider_rejects_missing_required_plan_fields() -> None:
    incomplete = json.dumps(
        {
            "canonical_name": "Xiaomi Pad 7",
            "product_type": "tablet",
            "primary_query": "xiaomi pad 7",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(incomplete))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except SearchPlanPayloadError as exc:
        assert "missing keys" in str(exc).lower()
    else:
        raise AssertionError("missing required plan fields must fail closed")
    asyncio.run(client.aclose())


def test_gemini_provider_keeps_strict_search_plan_payload_validation() -> None:
    invalid = json.dumps(
        {
            "canonical_name": "Xiaomi Pad 7",
            "product_type": "tablet",
            "primary_query": "xiaomi pad 7",
            "condition": "new",
            "aliases": [],
            "required_tokens": [],
            "excluded_terms": [],
            "identity_attributes": {},
            "invented_sku": "123",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(invalid))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except SearchPlanPayloadError as exc:
        assert "unexpected keys" in str(exc)
    else:
        raise AssertionError("strict SearchPlan validation must remain authoritative")
    asyncio.run(client.aclose())


def test_gemini_provider_rejects_invalid_condition() -> None:
    invalid = json.loads(valid_plan_json())
    invalid["condition"] = "open box"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(json.dumps(invalid)))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Xiaomi Pad 7"))
    except SearchPlanPayloadError as exc:
        assert "condition" in str(exc).lower()
    else:
        raise AssertionError("invalid condition must fail closed")
    asyncio.run(client.aclose())


def test_gemini_provider_rejects_exact_identifier_not_present_in_user_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(exact_identifier_plan_json()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    try:
        asyncio.run(provider.create_plan("Example Product"))
    except SearchPlanPayloadError as exc:
        assert "identifier" in str(exc).lower()
    else:
        raise AssertionError("invented exact identifier must be rejected")
    asyncio.run(client.aclose())


def test_gemini_provider_accepts_exact_identifier_when_user_supplies_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload(exact_identifier_plan_json()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiSearchPlanProvider(api_key="secret", client=client)
    plan = asyncio.run(provider.create_plan("Example Product GTIN 1234567890123"))
    asyncio.run(client.aclose())

    assert plan.identity_attributes["gtin"] == "1234567890123"
