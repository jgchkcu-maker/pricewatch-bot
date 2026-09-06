from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field


def normalize_query(text: str) -> str:
    """Normalize a human/LLM query into a marketplace-friendly token string.

    We intentionally convert punctuation and separators to spaces instead of deleting
    them so forms such as ``8/256``, ``8+256`` and ``8-256`` become ``8 256``.
    Alphanumeric model tokens (for example ``ddf485z``) stay intact.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    chars = [char if char.isalnum() else " " for char in normalized]
    return " ".join("".join(chars).split())


def _normalized_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_query(value)
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _identity_attributes(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        normalized_key = normalize_query(key)
        normalized_value = normalize_query(value)
        if normalized_key and normalized_value:
            result[normalized_key] = normalized_value
    return result


@dataclass(frozen=True, slots=True)
class SearchPlan:
    canonical_name: str
    primary_query: str
    product_type: str | None = None
    aliases: tuple[str, ...] = ()
    required_tokens: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    identity_attributes: Mapping[str, str] = field(default_factory=dict)
    condition: str = "new"

    def __post_init__(self) -> None:
        canonical_name = self.canonical_name.strip()
        if not canonical_name:
            raise ValueError("canonical_name must not be empty")

        primary = normalize_query(self.primary_query)
        if not primary:
            raise ValueError("primary_query must contain searchable characters")

        product_type = normalize_query(self.product_type) if self.product_type else None
        aliases = tuple(alias for alias in _normalized_tuple(self.aliases) if alias != primary)
        required_tokens = _normalized_tuple(self.required_tokens)
        excluded_terms = _normalized_tuple(self.excluded_terms)
        identity_attributes = _identity_attributes(self.identity_attributes)
        condition = normalize_query(self.condition)
        if condition not in {"new", "used", "refurbished", "any"}:
            raise ValueError("condition must be one of: new, used, refurbished, any")

        object.__setattr__(self, "canonical_name", canonical_name)
        object.__setattr__(self, "primary_query", primary)
        object.__setattr__(self, "product_type", product_type)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "required_tokens", required_tokens)
        object.__setattr__(self, "excluded_terms", excluded_terms)
        object.__setattr__(self, "identity_attributes", identity_attributes)
        object.__setattr__(self, "condition", condition)


def queries_for_cycle(
    plan: SearchPlan,
    cycle: int,
    *,
    alias_every_cycles: int = 2,
) -> tuple[str, ...]:
    """Return fast-discovery queries for one monitoring cycle.

    The primary query is always executed, preserving the four-minute freshness
    target. One alias is added periodically and rotated round-robin. Full alias
    sweeps belong to the slower deep-discovery job rather than the hot path.
    """
    if cycle < 0:
        raise ValueError("cycle must be non-negative")
    if alias_every_cycles <= 0:
        raise ValueError("alias_every_cycles must be positive")

    queries = [plan.primary_query]
    if not plan.aliases or (cycle + 1) % alias_every_cycles != 0:
        return tuple(queries)

    alias_slot = (cycle + 1) // alias_every_cycles - 1
    alias = plan.aliases[alias_slot % len(plan.aliases)]
    if alias != plan.primary_query:
        queries.append(alias)
    return tuple(queries)
