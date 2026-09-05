from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pricewatch.marketplaces import SearchCandidate
from pricewatch.search_plan import SearchPlan, normalize_query

_UNIT_PATTERNS = (
    (r"(\d+(?:[.,]\d+)?)\s*(?:гб|gb|гигабайт(?:а|ов)?)\b", r"\1 gb"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:тб|tb|терабайт(?:а|ов)?)\b", r"\1 tb"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:мб|mb|мегабайт(?:а|ов)?)\b", r"\1 mb"),
)


class MatchStatus(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class MatchDecision:
    status: MatchStatus
    reason: str


def _canonical_text(text: str) -> str:
    value = normalize_query(text)
    for pattern, replacement in _UNIT_PATTERNS:
        value = re.sub(pattern, replacement, value)
    return " ".join(value.split())


def _phrase_present(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


def _compact(text: str) -> str:
    return "".join(char for char in text if char.isalnum())


def _value_present(text: str, value: str) -> bool:
    haystack = _canonical_text(text)
    needle = _canonical_text(value)
    return _phrase_present(haystack, needle) or _compact(needle) in _compact(haystack)


def _excluded_present(text: str, expression: str) -> bool:
    haystack = _canonical_text(text)
    needle = _canonical_text(expression)
    if _phrase_present(haystack, needle):
        return True
    # Compact matching is useful for multi-token model names such as
    # ``Pad 7 Pro`` vs ``Pad7 Pro``, but unsafe for short single words like
    # ``case`` because they can occur inside unrelated words.
    return " " in needle and _compact(needle) in _compact(haystack)


def match_candidate(plan: SearchPlan, candidate: SearchCandidate) -> MatchDecision:
    title = _canonical_text(candidate.title)
    attributes = {
        normalize_query(key): _canonical_text(value)
        for key, value in candidate.attributes.items()
    }
    combined = " ".join(part for part in (title, *attributes.values()) if part)

    for excluded in plan.excluded_terms:
        if _excluded_present(combined, excluded):
            return MatchDecision(MatchStatus.REJECT, f"excluded term matched: {excluded}")

    missing_required = [
        token
        for token in plan.required_tokens
        if not _phrase_present(combined, _canonical_text(token))
    ]
    if missing_required:
        return MatchDecision(
            MatchStatus.AMBIGUOUS,
            f"missing required evidence: {', '.join(missing_required)}",
        )

    missing_attributes: list[str] = []
    for key, expected in plan.identity_attributes.items():
        expected_normalized = _canonical_text(expected)
        actual = attributes.get(key)
        if actual is not None:
            if _compact(actual) != _compact(expected_normalized):
                reason = (
                    f"identity attribute contradiction for {key}: "
                    f"{actual} != {expected_normalized}"
                )
                return MatchDecision(MatchStatus.REJECT, reason)
            continue

        if not _value_present(combined, expected_normalized):
            missing_attributes.append(key)

    if missing_attributes:
        return MatchDecision(
            MatchStatus.AMBIGUOUS,
            f"missing identity evidence: {', '.join(missing_attributes)}",
        )

    return MatchDecision(MatchStatus.ACCEPT, "required identity evidence matched")
