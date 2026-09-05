from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum

from pricewatch.marketplaces import SearchCandidate
from pricewatch.matching import MatchDecision, MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan, normalize_query
from pricewatch.taxonomy import TaxonomyGateStatus

_IDENTIFIER_KEYS = frozenset({"gtin", "upc", "ean", "mpn"})
_ACCESSORY_TERMS = frozenset(
    {
        "case",
        "cover",
        "glass",
        "screen protector",
        "чехол",
        "стекло",
        "пленка",
        "защитная пленка",
        "подставка",
        "держатель",
    }
)
_VARIANT_KEYS = frozenset(
    {
        "ram",
        "storage",
        "capacity",
        "memory",
        "size",
        "volume",
        "count",
        "socket",
        "interface",
        "edition",
        "generation",
    }
)
_UNIT_PATTERN = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>gb|гб|tb|тб|mb|мб)\b",
    re.IGNORECASE,
)


def _canonical(value: str) -> str:
    normalized = normalize_query(value)
    normalized = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*гб\b", r"\1 gb", normalized)
    normalized = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*тб\b", r"\1 tb", normalized)
    normalized = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*мб\b", r"\1 mb", normalized)
    return " ".join(normalized.split())


def _compact(value: str) -> str:
    return "".join(char for char in _canonical(value) if char.isalnum())


def _present(text: str, value: str) -> bool:
    haystack = _canonical(text)
    needle = _canonical(value)
    return f" {needle} " in f" {haystack} " or _compact(needle) in _compact(haystack)


def _candidate_text(candidate: SearchCandidate) -> str:
    return " ".join((candidate.title, *candidate.attributes.values()))


def _candidate_key(candidate: SearchCandidate) -> tuple[str, str, str | None]:
    return candidate.marketplace, candidate.listing_id, candidate.variation_id


def _normalized_attributes(candidate: SearchCandidate) -> dict[str, str]:
    return {
        normalize_query(key): _canonical(value)
        for key, value in candidate.attributes.items()
    }


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(_canonical(left).split())
    right_tokens = set(_canonical(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _unit_values(value: str) -> set[tuple[str, str]]:
    results: set[tuple[str, str]] = set()
    for match in _UNIT_PATTERN.finditer(value):
        number = match.group("value").replace(",", ".")
        unit = match.group("unit").casefold()
        unit = {"гб": "gb", "тб": "tb", "мб": "mb"}.get(unit, unit)
        results.add((number, unit))
    return results


def _variant_unit_conflict(plan: SearchPlan, candidate: SearchCandidate) -> str | None:
    expected: set[tuple[str, str]] = set()
    for key, value in plan.identity_attributes.items():
        if key in _VARIANT_KEYS:
            expected.update(_unit_values(value))
    if not expected:
        return None

    observed = _unit_values(_candidate_text(candidate))
    if not observed:
        return None

    expected_by_unit: dict[str, set[str]] = {}
    observed_by_unit: dict[str, set[str]] = {}
    for number, unit in expected:
        expected_by_unit.setdefault(unit, set()).add(number)
    for number, unit in observed:
        observed_by_unit.setdefault(unit, set()).add(number)

    for unit, observed_numbers in observed_by_unit.items():
        expected_numbers = expected_by_unit.get(unit)
        if not expected_numbers:
            continue
        extra = observed_numbers - expected_numbers
        missing = expected_numbers - observed_numbers
        if extra and missing:
            return (
                "variant capacity conflict: expected "
                f"{sorted(expected_numbers)} {unit}, observed {sorted(observed_numbers)} {unit}"
            )
    return None


def _identifier_conflict(plan: SearchPlan, candidate: SearchCandidate) -> str | None:
    attributes = _normalized_attributes(candidate)
    for key in _IDENTIFIER_KEYS:
        expected = plan.identity_attributes.get(key)
        actual = attributes.get(key)
        if expected is None or actual is None:
            continue
        if _compact(expected) != _compact(actual):
            return f"identifier conflict for {key}: {_canonical(expected)} != {actual}"
    return None


def _identifier_match(plan: SearchPlan, candidate: SearchCandidate) -> float:
    attributes = _normalized_attributes(candidate)
    compared = 0
    matched = 0
    for key in _IDENTIFIER_KEYS:
        expected = plan.identity_attributes.get(key)
        actual = attributes.get(key)
        if expected is None or actual is None:
            continue
        compared += 1
        matched += int(_compact(expected) == _compact(actual))
    if compared == 0:
        return 0.0
    return matched / compared


def _coverage(values: tuple[str, ...] | list[str], text: str) -> float:
    if not values:
        return 1.0
    matched = sum(1 for value in values if _present(text, value))
    return matched / len(values)


def _identity_coverage(plan: SearchPlan, candidate: SearchCandidate) -> float:
    if not plan.identity_attributes:
        return 1.0
    attributes = _normalized_attributes(candidate)
    combined = _candidate_text(candidate)
    matched = 0
    for key, expected in plan.identity_attributes.items():
        actual = attributes.get(key)
        if actual is not None:
            matched += int(_compact(actual) == _compact(expected))
        elif _present(combined, expected):
            matched += 1
    return matched / len(plan.identity_attributes)


def _model_match(plan: SearchPlan, candidate: SearchCandidate) -> float:
    model_values = [
        value
        for key, value in plan.identity_attributes.items()
        if "model" in key or key in {"generation", "edition"}
    ]
    if not model_values:
        return 0.0
    return _coverage(model_values, _candidate_text(candidate))


def _brand_match(plan: SearchPlan, candidate: SearchCandidate) -> float:
    explicit_brand = plan.identity_attributes.get("brand")
    candidate_brand = next(
        (
            value
            for key, value in candidate.attributes.items()
            if normalize_query(key) in {"brand", "бренд", "марка"}
        ),
        None,
    )
    if explicit_brand is not None:
        if candidate_brand is not None:
            return float(_compact(explicit_brand) == _compact(candidate_brand))
        return float(_present(candidate.title, explicit_brand))
    if candidate_brand is not None and _present(plan.canonical_name, candidate_brand):
        return 1.0
    return 0.0


def _model_token_overlap(plan: SearchPlan, candidate: SearchCandidate) -> float:
    model_values = [
        value for key, value in plan.identity_attributes.items() if "model" in key
    ]
    if not model_values:
        return 0.0
    compact_candidate = _compact(_candidate_text(candidate))
    return max(float(_compact(value) in compact_candidate) for value in model_values)


@dataclass(frozen=True, slots=True)
class MatchFeatureVector:
    taxonomy_agreement: float
    identifier_match: float
    brand_match: float
    model_match: float
    required_coverage: float
    identity_coverage: float
    title_similarity: float
    model_token_overlap: float
    deterministic_accept: float
    deterministic_ambiguous: float

    def as_mapping(self) -> dict[str, float]:
        return {
            "taxonomy_agreement": self.taxonomy_agreement,
            "identifier_match": self.identifier_match,
            "brand_match": self.brand_match,
            "model_match": self.model_match,
            "required_coverage": self.required_coverage,
            "identity_coverage": self.identity_coverage,
            "title_similarity": self.title_similarity,
            "model_token_overlap": self.model_token_overlap,
            "deterministic_accept": self.deterministic_accept,
            "deterministic_ambiguous": self.deterministic_ambiguous,
        }


@dataclass(frozen=True, slots=True)
class HybridMatchDecision:
    status: MatchStatus
    probability: float
    reason: str
    features: MatchFeatureVector
    hard_vetoes: tuple[str, ...] = ()


class HardNegativeBucket(StrEnum):
    SIBLING_MODEL = "sibling_model"
    VARIANT_CONFLICT = "variant_conflict"
    IDENTIFIER_CONFLICT = "identifier_conflict"
    ACCESSORY = "accessory"
    TAXONOMY_CONFLICT = "taxonomy_conflict"


@dataclass(frozen=True, slots=True)
class HardNegative:
    candidate: SearchCandidate
    bucket: HardNegativeBucket
    reason: str


class LearningEvidenceSource(StrEnum):
    SEARCH = "search"
    DETAIL = "detail"
    MANUAL = "manual"
    LLM_JUDGE = "llm_judge"


@dataclass(frozen=True, slots=True)
class LearningEvidence:
    product_name: str
    marketplace: str
    listing_id: str
    variation_id: str | None
    features: MatchFeatureVector
    probability: float
    decision: MatchStatus
    reason: str
    source: LearningEvidenceSource
    source_queries: tuple[str, ...] = ()
    verified_label: bool | None = None


@dataclass(frozen=True, slots=True)
class UncertainMatch:
    product_name: str
    candidate: SearchCandidate
    probability: float
    priority: float
    source_queries: tuple[str, ...]


class UncertainMatchQueue:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str | None], UncertainMatch] = {}

    def add(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        probability: float,
        source_queries: tuple[str, ...],
    ) -> None:
        uncertainty = 1.0 - min(1.0, abs(probability - 0.5) * 2.0)
        title_similarity = _token_similarity(plan.canonical_name, candidate.title)
        priority = min(1.0, 0.75 * uncertainty + 0.25 * title_similarity)
        self._items[_candidate_key(candidate)] = UncertainMatch(
            product_name=plan.canonical_name,
            candidate=candidate,
            probability=probability,
            priority=priority,
            source_queries=source_queries,
        )

    def discard(self, candidate: SearchCandidate) -> None:
        self._items.pop(_candidate_key(candidate), None)

    def items(self) -> tuple[UncertainMatch, ...]:
        return tuple(
            sorted(self._items.values(), key=lambda item: item.priority, reverse=True)
        )


class OnlineMatchModel:
    def __init__(self, *, learning_rate: float = 0.05) -> None:
        if learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        self.learning_rate = learning_rate
        self.weights: dict[str, float] = {
            "intercept": -4.0,
            "taxonomy_agreement": 1.0,
            "identifier_match": 4.0,
            "brand_match": 0.7,
            "model_match": 2.0,
            "required_coverage": 0.8,
            "identity_coverage": 2.2,
            "title_similarity": 0.8,
            "model_token_overlap": 1.0,
            "deterministic_accept": 3.0,
            "deterministic_ambiguous": -0.5,
        }

    def predict(self, features: MatchFeatureVector) -> float:
        score = self.weights["intercept"]
        for key, value in features.as_mapping().items():
            score += self.weights[key] * value
        if score >= 0:
            exp_value = math.exp(-score)
            return 1.0 / (1.0 + exp_value)
        exp_value = math.exp(score)
        return exp_value / (1.0 + exp_value)

    def learn(self, features: MatchFeatureVector, *, matched: bool) -> None:
        probability = self.predict(features)
        error = float(matched) - probability
        self.weights["intercept"] += self.learning_rate * error
        for key, value in features.as_mapping().items():
            self.weights[key] += self.learning_rate * error * value


@dataclass(slots=True)
class QueryPerformance:
    runs: int = 0
    candidate_ids: set[str] = field(default_factory=set)
    accepted_ids: set[str] = field(default_factory=set)
    verified_matches: set[str] = field(default_factory=set)
    verified_rejects: set[str] = field(default_factory=set)

    @property
    def verified_count(self) -> int:
        return len(self.verified_matches) + len(self.verified_rejects)


class QueryPerformanceTracker:
    def __init__(self) -> None:
        self._stats: dict[str, QueryPerformance] = {}

    def _get(self, query: str) -> QueryPerformance:
        normalized = normalize_query(query)
        if not normalized:
            raise ValueError("query must not be empty")
        return self._stats.setdefault(normalized, QueryPerformance())

    def record_discovery(
        self,
        query: str,
        *,
        candidate_ids: set[str],
        accepted_ids: set[str],
    ) -> None:
        stats = self._get(query)
        stats.runs += 1
        stats.candidate_ids.update(candidate_ids)
        stats.accepted_ids.update(accepted_ids)

    def record_verified(self, query: str, candidate_id: str, *, matched: bool) -> None:
        stats = self._get(query)
        target = stats.verified_matches if matched else stats.verified_rejects
        target.add(candidate_id)

    def score(self, query: str) -> float:
        stats = self._stats.get(normalize_query(query))
        if stats is None or stats.runs == 0:
            return 0.0
        value = (
            3.0 * len(stats.verified_matches)
            - 2.0 * len(stats.verified_rejects)
            + 0.25 * len(stats.accepted_ids)
            + 0.05 * len(stats.candidate_ids)
        )
        return value / stats.runs

    def rank(self, queries: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(queries, key=lambda query: self.score(query), reverse=True))

    def select_alias(
        self,
        aliases: tuple[str, ...],
        *,
        slot: int,
        explore_every: int = 4,
    ) -> str | None:
        if slot < 0:
            raise ValueError("slot must be non-negative")
        if explore_every <= 0:
            raise ValueError("explore_every must be positive")
        if not aliases:
            return None

        normalized = tuple(normalize_query(alias) for alias in aliases)
        stats = [self._stats.get(alias) for alias in normalized]
        cold_indexes = [
            index for index, item in enumerate(stats) if item is None or item.runs == 0
        ]
        if cold_indexes:
            index = cold_indexes[slot % len(cold_indexes)]
            return normalized[index]

        if not any(item is not None and item.verified_count > 0 for item in stats):
            return normalized[slot % len(normalized)]

        if (slot + 1) % explore_every == 0:
            min_runs = min(item.runs for item in stats if item is not None)
            least_used = [
                index
                for index, item in enumerate(stats)
                if item is not None and item.runs == min_runs
            ]
            index = least_used[slot % len(least_used)]
            return normalized[index]

        return self.rank(normalized)[0]


class HybridMatchEngine:
    def __init__(
        self,
        *,
        accept_threshold: float = 0.98,
        reject_threshold: float = 0.05,
        model: OnlineMatchModel | None = None,
    ) -> None:
        if not 0 < reject_threshold < accept_threshold < 1:
            raise ValueError("thresholds must satisfy 0 < reject < accept < 1")
        self.accept_threshold = accept_threshold
        self.reject_threshold = reject_threshold
        self.model = model or OnlineMatchModel()
        self.uncertain_queue = UncertainMatchQueue()
        self.hard_negatives: list[HardNegative] = []
        self._hard_negative_keys: set[
            tuple[str, str, str | None, HardNegativeBucket]
        ] = set()
        self.evidence: list[LearningEvidence] = []
        self.query_performance = QueryPerformanceTracker()

    def _features(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        deterministic: MatchDecision,
        taxonomy_status: TaxonomyGateStatus,
    ) -> MatchFeatureVector:
        taxonomy_agreement = {
            TaxonomyGateStatus.PASS: 1.0,
            TaxonomyGateStatus.UNKNOWN: 0.0,
            TaxonomyGateStatus.REJECT: 0.0,
        }[taxonomy_status]
        return MatchFeatureVector(
            taxonomy_agreement=taxonomy_agreement,
            identifier_match=_identifier_match(plan, candidate),
            brand_match=_brand_match(plan, candidate),
            model_match=_model_match(plan, candidate),
            required_coverage=_coverage(plan.required_tokens, _candidate_text(candidate)),
            identity_coverage=_identity_coverage(plan, candidate),
            title_similarity=_token_similarity(plan.canonical_name, candidate.title),
            model_token_overlap=_model_token_overlap(plan, candidate),
            deterministic_accept=float(deterministic.status is MatchStatus.ACCEPT),
            deterministic_ambiguous=float(deterministic.status is MatchStatus.AMBIGUOUS),
        )

    def _mine_hard_negative(
        self,
        candidate: SearchCandidate,
        reason: str,
        bucket: HardNegativeBucket,
    ) -> None:
        key = (*_candidate_key(candidate), bucket)
        if key in self._hard_negative_keys:
            return
        self._hard_negative_keys.add(key)
        self.hard_negatives.append(
            HardNegative(candidate=candidate, bucket=bucket, reason=reason)
        )

    def _hard_vetoes(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        deterministic: MatchDecision,
        taxonomy_status: TaxonomyGateStatus,
    ) -> tuple[tuple[str, HardNegativeBucket], ...]:
        vetoes: list[tuple[str, HardNegativeBucket]] = []
        if taxonomy_status is TaxonomyGateStatus.REJECT:
            vetoes.append(("taxonomy conflict", HardNegativeBucket.TAXONOMY_CONFLICT))

        identifier_conflict = _identifier_conflict(plan, candidate)
        if identifier_conflict is not None:
            vetoes.append((identifier_conflict, HardNegativeBucket.IDENTIFIER_CONFLICT))

        variant_conflict = _variant_unit_conflict(plan, candidate)
        if variant_conflict is not None:
            vetoes.append((variant_conflict, HardNegativeBucket.VARIANT_CONFLICT))

        if deterministic.status is MatchStatus.REJECT:
            reason = deterministic.reason
            if "excluded term matched:" in reason:
                term = reason.split(":", 1)[1].strip()
                bucket = (
                    HardNegativeBucket.ACCESSORY
                    if _canonical(term) in _ACCESSORY_TERMS
                    else HardNegativeBucket.SIBLING_MODEL
                )
                vetoes.append((reason, bucket))
            elif "identity attribute contradiction" in reason:
                key = reason.split(" for ", 1)[1].split(":", 1)[0]
                if key in _IDENTIFIER_KEYS:
                    bucket = HardNegativeBucket.IDENTIFIER_CONFLICT
                    veto_reason = f"identifier conflict: {reason}"
                elif "model" in key or key == "generation":
                    bucket = HardNegativeBucket.SIBLING_MODEL
                    veto_reason = reason
                else:
                    bucket = HardNegativeBucket.VARIANT_CONFLICT
                    veto_reason = reason
                vetoes.append((veto_reason, bucket))

        unique: list[tuple[str, HardNegativeBucket]] = []
        seen: set[str] = set()
        for reason, bucket in vetoes:
            if reason in seen:
                continue
            unique.append((reason, bucket))
            seen.add(reason)
        return tuple(unique)

    def classify(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        *,
        taxonomy_status: TaxonomyGateStatus = TaxonomyGateStatus.UNKNOWN,
        source_queries: tuple[str, ...] = (),
    ) -> HybridMatchDecision:
        deterministic = match_candidate(plan, candidate)
        features = self._features(plan, candidate, deterministic, taxonomy_status)
        vetoes = self._hard_vetoes(plan, candidate, deterministic, taxonomy_status)
        if vetoes:
            for reason, bucket in vetoes:
                self._mine_hard_negative(candidate, reason, bucket)
            return HybridMatchDecision(
                status=MatchStatus.REJECT,
                probability=0.0,
                reason=vetoes[0][0],
                features=features,
                hard_vetoes=tuple(reason for reason, _ in vetoes),
            )

        probability = self.model.predict(features)
        if deterministic.status is MatchStatus.ACCEPT and probability >= self.accept_threshold:
            status = MatchStatus.ACCEPT
            reason = "deterministic identity matched with high probabilistic confidence"
        elif probability <= self.reject_threshold:
            status = MatchStatus.REJECT
            reason = "probabilistic identity confidence below reject threshold"
        else:
            status = MatchStatus.AMBIGUOUS
            reason = "candidate requires more identity evidence"
            self.uncertain_queue.add(plan, candidate, probability, source_queries)

        return HybridMatchDecision(
            status=status,
            probability=probability,
            reason=reason,
            features=features,
        )

    def record_search_evidence(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        decision: HybridMatchDecision,
        *,
        source_queries: tuple[str, ...] = (),
    ) -> None:
        self.evidence.append(
            LearningEvidence(
                product_name=plan.canonical_name,
                marketplace=candidate.marketplace,
                listing_id=candidate.listing_id,
                variation_id=candidate.variation_id,
                features=decision.features,
                probability=decision.probability,
                decision=decision.status,
                reason=decision.reason,
                source=LearningEvidenceSource.SEARCH,
                source_queries=source_queries,
                verified_label=None,
            )
        )

    def learn_verified(
        self,
        plan: SearchPlan,
        candidate: SearchCandidate,
        decision: HybridMatchDecision,
        *,
        matched: bool,
        source: LearningEvidenceSource,
        source_queries: tuple[str, ...] = (),
    ) -> None:
        if source is LearningEvidenceSource.SEARCH:
            raise ValueError("search evidence cannot train the online model")

        self.uncertain_queue.discard(candidate)
        self.evidence.append(
            LearningEvidence(
                product_name=plan.canonical_name,
                marketplace=candidate.marketplace,
                listing_id=candidate.listing_id,
                variation_id=candidate.variation_id,
                features=decision.features,
                probability=decision.probability,
                decision=decision.status,
                reason=decision.reason,
                source=source,
                source_queries=source_queries,
                verified_label=matched,
            )
        )
        self.model.learn(decision.features, matched=matched)
        for query in source_queries:
            self.query_performance.record_verified(
                query,
                candidate.listing_id,
                matched=matched,
            )
