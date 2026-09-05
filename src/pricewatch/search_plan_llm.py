from __future__ import annotations

import json
from typing import Any

from pricewatch.search_plan import SearchPlan

DEFAULT_SEARCH_PLAN_MODEL = "gemini-3.5-flash-lite"

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
   product: model, generation, capacity, RAM, storage, size, socket, interface, edition, etc.
   The schema is universal; keys are free-form lowercase semantic names.
4. required_tokens are only stable lexical anchors that should normally be visible in a matching
   result. Do not put formatting-sensitive expressions there when identity_attributes can express
   them better.
5. excluded_terms contain obvious conflicting sibling models and accessory-only concepts that
   would otherwise produce dangerous false positives. Be conservative: an exclusion is a hard
   rejection later.
6. primary_query must be the best natural high-precision marketplace query, concise and free of
   unnecessary punctuation. Prefer spaces over separators such as '/', '+', '-', or commas.
7. Generate at most 7 aliases. Aliases must be semantically distinct ways real listings/searches
   may express the same product: compact model spelling, common transliteration, common abbreviation,
   or a known model code ONLY when that code is already present in the user query or certain from
   the product name. Do not generate word-order permutations just to make the list longer.
8. Never invent GTIN, EAN, UPC, SKU, MPN, article numbers, or manufacturer codes.
9. Do not put used/refurbished/accessory variants into aliases for a new main product.
10. Output JSON only. No markdown, explanation, comments, or extra keys.

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
