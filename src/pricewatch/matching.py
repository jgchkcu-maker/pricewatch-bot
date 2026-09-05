from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pricewatch.marketplaces import SearchCandidate
from pricewatch.search_plan import SearchPlan, normalize_query


class MatchStatus(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class MatchDecision:
    status: MatchStatus
    reason: str


def _phrase_present(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


def _compact(text: str) -> str:
    return "".join(char for char in text if char.isalnum())


def _value_present(text: str, value: str) -> bool:
    return _phrase_present(text, value) or _compact(value) in _compact(text)


def match_candidate(plan: SearchPlan, candidate: SearchCandidate) -> MatchDecision:
    title = normalize_query(candidate.title)
    attributes = {
        normalize_query(key): normalize_query(value)
        for key, value in candidate.attributes.items()
    }
    combined = " ".join(part for part in (title, *attributes.values()) if part)

    for excluded in plan.excluded_terms:
        if _phrase_present(combined, excluded):
            return MatchDecision(MatchStatus.REJECT, f"excluded term matched: {excluded}")

    missing_required = [
        token for token in plan.required_tokens if not _phrase_present(combined, token)
    ]
    if missing_required:
        return MatchDecision(
            MatchStatus.AMBIGUOUS,
            f"missing required evidence: {', '.join(missing_required)}",
        )

    missing_attributes: list[str] = []
    for key, expected in plan.identity_attributes.items():
        actual = attributes.get(key)
        if actual is not None:
            if _compact(actual) != _compact(expected):
                return MatchDecision(
                    MatchStatus.REJECT,
                    f"identity attribute contradiction for {key}: {actual} != {expected}",
                )
            continue

        if not _value_present(combined, expected):
            missing_attributes.append(key)

    if missing_attributes:
        return MatchDecision(
            MatchStatus.AMBIGUOUS,
            f"missing identity evidence: {', '.join(missing_attributes)}",
        )

    return MatchDecision(MatchStatus.ACCEPT, "required identity evidence matched")
