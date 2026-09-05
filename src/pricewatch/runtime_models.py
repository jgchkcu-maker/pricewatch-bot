from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pricewatch.search_plan import SearchPlan, normalize_query


def _compact_identity(value: str) -> str:
    return "".join(char for char in normalize_query(value) if char.isalnum())


def search_plan_to_payload(plan: SearchPlan) -> dict[str, Any]:
    return {
        "canonical_name": plan.canonical_name,
        "primary_query": plan.primary_query,
        "product_type": plan.product_type,
        "aliases": list(plan.aliases),
        "required_tokens": list(plan.required_tokens),
        "excluded_terms": list(plan.excluded_terms),
        "identity_attributes": dict(plan.identity_attributes),
    }


def search_plan_from_payload(payload: Mapping[str, Any]) -> SearchPlan:
    identity = payload.get("identity_attributes", {})
    if not isinstance(identity, Mapping):
        raise ValueError("identity_attributes must be an object")
    return SearchPlan(
        canonical_name=str(payload["canonical_name"]),
        primary_query=str(payload["primary_query"]),
        product_type=(
            str(payload["product_type"]) if payload.get("product_type") is not None else None
        ),
        aliases=tuple(str(value) for value in payload.get("aliases", [])),
        required_tokens=tuple(str(value) for value in payload.get("required_tokens", [])),
        excluded_terms=tuple(str(value) for value in payload.get("excluded_terms", [])),
        identity_attributes={str(key): str(value) for key, value in identity.items()},
    )


def identity_fingerprint(plan: SearchPlan) -> str:
    """Return a stable exact-intent fingerprint for global product deduplication."""

    attributes = {
        normalize_query(key): _compact_identity(value)
        for key, value in plan.identity_attributes.items()
    }
    payload = {
        "product_type": normalize_query(plan.product_type or ""),
        "identity_attributes": sorted(attributes.items()),
    }
    if not attributes:
        payload["canonical_fallback"] = _compact_identity(plan.canonical_name)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrackedProductRecord:
    id: int
    canonical_name: str
    product_type: str | None
    identity_fingerprint: str
    search_plan: SearchPlan
    lifecycle_state: str
    subscriber_count: int
    next_scan_at: datetime
    last_successful_scan_at: datetime | None


@dataclass(frozen=True, slots=True)
class SubscriptionRecord:
    id: int
    user_id: int
    tracked_product_id: int
    status: str


@dataclass(frozen=True, slots=True)
class UserProductSummary:
    subscription: SubscriptionRecord
    product: TrackedProductRecord
    public_price: str | None = None
    marketplace: str | None = None
    listing_url: str | None = None
    verified_at: datetime | None = None
