from __future__ import annotations

import json
from typing import Any

import httpx

from pricewatch.search_plan import SearchPlan

DEFAULT_SEARCH_PLAN_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

SEARCH_PLAN_SYSTEM_PROMPT = r"""
You are the product-identity and marketplace-search planner for a universal price radar.
The user may name ANY physical consumer product in any language.

Your job is NOT to find prices and NOT to browse. Build a compact deterministic SearchPlan
that ordinary code can reuse for repeated marketplace searches.

Rules:
1. Preserve the exact product the user requested. Do not invent specifications, identifiers,
   model numbers, RAM, storage, generation, color, region, revision, bundle contents, or other
   attributes that are not explicit or highly certain from the product name itself.
2. Do not add region as an identity attribute unless the user explicitly requested a region or
   the region word is literally part of the requested product identity.
3. Put only truly identity-critical differentiators into identity_attributes. Examples vary by
   product: brand, model, generation, capacity, RAM, storage, size, socket, interface, edition,
   etc. When a brand is explicit or highly certain from the requested product name, preserve it
   under the semantic key "brand". The schema is universal; keys are free-form lowercase names.
4. If the user explicitly provides a GTIN, EAN, UPC, MPN, manufacturer part number, or equivalent
   exact product identifier, preserve it verbatim in identity_attributes under the corresponding
   lowercase semantic key. Exact identifiers are stronger identity evidence than title wording.
5. required_tokens are only stable lexical anchors that should normally be visible in a matching
   result. Do not put formatting-sensitive expressions there when identity_attributes can express
   them better.
6. excluded_terms contain obvious conflicting sibling models and accessory-only concepts that
   would otherwise produce dangerous false positives. Be conservative: an exclusion is a hard
   rejection later.
7. primary_query must be the best natural high-precision marketplace query, concise and free of
   unnecessary punctuation. Prefer spaces over separators such as '/', '+', '-', or commas.
8. Generate at most 7 aliases. Aliases must be semantically distinct ways real listings/searches
   may express the same product: compact model spelling, common transliteration,
   common abbreviation, or a known model code ONLY when that code is already present in the user
   query or certain from the product name. Do not generate word-order permutations just to make
   the list longer.
9. Never invent GTIN, EAN, UPC, SKU, MPN, article numbers, or manufacturer codes. If an exact
   identifier was not supplied or cannot be known with very high certainty from the product name,
   omit it instead of guessing.
10. Do not put used/refurbished/accessory variants into aliases for a new main product.
11. Output JSON only. No markdown, explanation, comments, or extra keys.

Required JSON object:
{
  "canonical_name": "string",
  "product_type": "short generic type or null",
  "primary_query": "string",
  "aliases": ["string", "... at most 7 aliases"],
  "required_tokens": ["string"],
  "excluded_terms": ["string"],
  "identity_attributes": {"free_form_key": "string value"}
}
""".strip()

_ALLOWED_KEYS = {
    "canonical_name",
    "product_type",
    "primary_query",
    "aliases",
    "required_tokens",
    "excluded_terms",
    "identity_attributes",
}


class SearchPlanPayloadError(ValueError):
    """LLM output did not satisfy the deterministic SearchPlan contract."""


def _require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SearchPlanPayloadError(f"{field} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise SearchPlanPayloadError(f"{field} must not be empty")
    return value


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SearchPlanPayloadError(f"{field} must be a JSON array")
    result: list[str] = []
    for item in value:
        result.append(_require_string(item, f"{field} item"))
    return tuple(result)


def parse_search_plan_response(raw_text: str) -> SearchPlan:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SearchPlanPayloadError("response must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise SearchPlanPayloadError("response must be a JSON object")

    unknown = set(payload) - _ALLOWED_KEYS
    if unknown:
        raise SearchPlanPayloadError(f"unexpected keys: {', '.join(sorted(unknown))}")

    missing = _ALLOWED_KEYS - set(payload)
    if missing:
        raise SearchPlanPayloadError(f"missing keys: {', '.join(sorted(missing))}")

    canonical_name = _require_string(payload["canonical_name"], "canonical_name")
    primary_query = _require_string(payload["primary_query"], "primary_query")

    product_type_raw = payload["product_type"]
    if product_type_raw is None:
        product_type = None
    else:
        product_type = _require_string(product_type_raw, "product_type")

    aliases = _string_list(payload["aliases"], "aliases")
    if len(aliases) > 7:
        raise SearchPlanPayloadError("response may contain at most 7 aliases")

    required_tokens = _string_list(payload["required_tokens"], "required_tokens")
    excluded_terms = _string_list(payload["excluded_terms"], "excluded_terms")

    raw_attributes = payload["identity_attributes"]
    if not isinstance(raw_attributes, dict):
        raise SearchPlanPayloadError("identity_attributes must be a JSON object")
    identity_attributes: dict[str, str] = {}
    for key, value in raw_attributes.items():
        normalized_key = _require_string(key, "identity_attributes key")
        normalized_value = _require_string(value, f"identity_attributes.{normalized_key}")
        identity_attributes[normalized_key] = normalized_value

    try:
        return SearchPlan(
            canonical_name=canonical_name,
            primary_query=primary_query,
            product_type=product_type,
            aliases=aliases,
            required_tokens=required_tokens,
            excluded_terms=excluded_terms,
            identity_attributes=identity_attributes,
        )
    except ValueError as exc:
        raise SearchPlanPayloadError(str(exc)) from exc


class GeminiSearchPlanProvider:
    """Create a SearchPlan once from Gemini, outside the marketplace polling hot path."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_SEARCH_PLAN_MODEL,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key.strip()
        model_name = model.strip()
        base = base_url.rstrip("/").strip()
        if not key:
            raise ValueError("api_key must not be empty")
        if not model_name:
            raise ValueError("model must not be empty")
        if not base:
            raise ValueError("base_url must not be empty")
        self._api_key = key
        self._model = model_name
        self._base_url = base
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_plan(self, user_text: str) -> SearchPlan:
        query = user_text.strip()
        if not query:
            raise ValueError("user_text must not be empty")

        url = f"{self._base_url}/models/{self._model}:generateContent"
        payload = {
            "systemInstruction": {
                "parts": [{"text": SEARCH_PLAN_SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": query}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }
        try:
            response = await self._client.post(
                url,
                headers={"x-goog-api-key": self._api_key},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("Gemini SearchPlan request timed out") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("Gemini SearchPlan network request failed") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"Gemini SearchPlan request failed: HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("Gemini SearchPlan response was not valid JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("Gemini SearchPlan response root must be an object")

        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("Gemini SearchPlan response contained no candidate")
        first = candidates[0]
        if not isinstance(first, dict):
            raise RuntimeError("Gemini SearchPlan candidate must be an object")
        content = first.get("content")
        if not isinstance(content, dict):
            raise RuntimeError("Gemini SearchPlan candidate contained no content")
        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise RuntimeError("Gemini SearchPlan candidate contained no text part")
        text_parts = [
            part.get("text")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        if not text_parts:
            raise RuntimeError("Gemini SearchPlan candidate contained no text")
        raw_text = "".join(text_parts)
        return parse_search_plan_response(raw_text)
