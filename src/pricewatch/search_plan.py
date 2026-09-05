from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
import unicodedata


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


@dataclass(frozen=True, slots=True)
class SearchPlan:
    canonical_name: str
    primary_query: str
    aliases: tuple[str, ...] = ()
    required_tokens: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    identity_attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        primary = normalize_query(self.primary_query)
        if not primary:
            raise ValueError("primary_query must contain searchable characters")

        aliases = tuple(
            alias
            for alias in _normalized_tuple(self.aliases)
            if alias != primary
        )
        required_tokens = _normalized_tuple(self.required_tokens)
        excluded_terms = _normalized_tuple(self.excluded_terms)
        identity_attributes = {
            normalize_query(key): normalize_query(value)
            for key, value in self.identity_attributes.items()
            if normalize_query(key) and normalize_query(value)
        }

        object.__setattr__(self, "canonical_name", self.canonical_name.strip())
        object.__setattr__(self, "primary_query", primary)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "required_tokens", required_tokens)
        object.__setattr__(self, "excluded_terms", excluded_terms)
        object.__setattr__(self, "identity_attributes", identity_attributes)


def query_for_cycle(plan: SearchPlan, cycle: int) -> str:
    """Return the query for a scan cycle.

    Even cycles always use the primary query. Odd cycles rotate through aliases,
    which gives the core query at least half of all scans while still expanding
    recall over time.
    """
    if cycle < 0:
        raise ValueError("cycle must be non-negative")
    if cycle % 2 == 0 or not plan.aliases:
        return plan.primary_query
    alias_index = ((cycle - 1) // 2) % len(plan.aliases)
    return plan.aliases[alias_index]
