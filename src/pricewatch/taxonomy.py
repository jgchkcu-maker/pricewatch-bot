from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from pricewatch.marketplaces import SearchCandidate


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    return normalized or None


@dataclass(frozen=True, slots=True)
class MarketplaceTaxonomy:
    subject_id: str | None = None
    parent_id: str | None = None
    entity: str | None = None
    category_path: str | None = None

    def __post_init__(self) -> None:
        subject_id = str(self.subject_id).strip() if self.subject_id is not None else None
        parent_id = str(self.parent_id).strip() if self.parent_id is not None else None
        entity = self.entity.strip() if isinstance(self.entity, str) else None
        category_path = (
            self.category_path.strip() if isinstance(self.category_path, str) else None
        )
        object.__setattr__(self, "subject_id", subject_id or None)
        object.__setattr__(self, "parent_id", parent_id or None)
        object.__setattr__(self, "entity", entity or None)
        object.__setattr__(self, "category_path", category_path or None)


@dataclass(frozen=True, slots=True)
class TaxonomyConstraint:
    marketplace: str
    product_type: str
    subject_ids: frozenset[str] = frozenset()
    entities: frozenset[str] = frozenset()
    category_path: str | None = None

    def __post_init__(self) -> None:
        marketplace = _normalize(self.marketplace)
        product_type = _normalize(self.product_type)
        if not marketplace or not product_type:
            raise ValueError("marketplace and product_type must not be empty")
        subject_ids = frozenset(
            normalized
            for value in self.subject_ids
            if (normalized := _normalize(str(value))) is not None
        )
        entities = frozenset(
            normalized
            for value in self.entities
            if (normalized := _normalize(value)) is not None
        )
        category_path = self.category_path.strip() if self.category_path else None
        if category_path is not None and not category_path.startswith("/category/"):
            raise ValueError("category_path must start with /category/")

        object.__setattr__(self, "marketplace", marketplace)
        object.__setattr__(self, "product_type", product_type)
        object.__setattr__(self, "subject_ids", subject_ids)
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "category_path", category_path)


class TaxonomyGateStatus(StrEnum):
    PASS = "pass"
    REJECT = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TaxonomyGateDecision:
    status: TaxonomyGateStatus
    reason: str


class TaxonomyRegistry:
    def __init__(self, constraints: tuple[TaxonomyConstraint, ...] = ()) -> None:
        self._constraints: dict[tuple[str, str], TaxonomyConstraint] = {}
        for constraint in constraints:
            self.register(constraint)

    @classmethod
    def with_default_seeds(cls) -> TaxonomyRegistry:
        return cls(
            (
                TaxonomyConstraint(
                    marketplace="wildberries",
                    product_type="tablet",
                    subject_ids=frozenset({"107"}),
                    entities=frozenset({"планшеты"}),
                ),
                TaxonomyConstraint(
                    marketplace="ozon",
                    product_type="tablet",
                    category_path="/category/planshety-15525/",
                ),
            )
        )

    def register(self, constraint: TaxonomyConstraint) -> None:
        key = (constraint.product_type, constraint.marketplace)
        self._constraints[key] = constraint

    def resolve(self, product_type: str | None, marketplace: str) -> TaxonomyConstraint | None:
        normalized_product_type = _normalize(product_type)
        normalized_marketplace = _normalize(marketplace)
        if not normalized_product_type or not normalized_marketplace:
            return None
        return self._constraints.get((normalized_product_type, normalized_marketplace))


def taxonomy_gate(
    candidate: SearchCandidate,
    constraint: TaxonomyConstraint | None,
) -> TaxonomyGateDecision:
    if constraint is None:
        return TaxonomyGateDecision(TaxonomyGateStatus.UNKNOWN, "no taxonomy constraint")
    if _normalize(candidate.marketplace) != constraint.marketplace:
        raise ValueError("candidate marketplace does not match taxonomy constraint")

    taxonomy = candidate.taxonomy
    if taxonomy is None:
        return TaxonomyGateDecision(TaxonomyGateStatus.UNKNOWN, "candidate has no taxonomy")

    if constraint.subject_ids and taxonomy.subject_id is not None:
        subject_id = _normalize(taxonomy.subject_id)
        if subject_id in constraint.subject_ids:
            return TaxonomyGateDecision(TaxonomyGateStatus.PASS, "native subject id matched")
        return TaxonomyGateDecision(
            TaxonomyGateStatus.REJECT,
            f"native subject id contradicted constraint: {taxonomy.subject_id}",
        )

    if constraint.entities and taxonomy.entity is not None:
        entity = _normalize(taxonomy.entity)
        if entity in constraint.entities:
            return TaxonomyGateDecision(TaxonomyGateStatus.PASS, "native entity matched")
        return TaxonomyGateDecision(
            TaxonomyGateStatus.REJECT,
            f"native entity contradicted constraint: {taxonomy.entity}",
        )

    return TaxonomyGateDecision(
        TaxonomyGateStatus.UNKNOWN,
        "candidate taxonomy has no comparable configured field",
    )


def _taxonomy_signature(taxonomy: MarketplaceTaxonomy | None) -> tuple[str, str] | None:
    if taxonomy is None:
        return None
    if taxonomy.subject_id:
        return ("subject_id", taxonomy.subject_id)
    if taxonomy.entity:
        normalized = _normalize(taxonomy.entity)
        return ("entity", normalized) if normalized else None
    if taxonomy.category_path:
        return ("category_path", taxonomy.category_path)
    return None


class TaxonomyObservationAccumulator:
    def __init__(self) -> None:
        self._evidence: dict[tuple[str, str, tuple[str, str]], set[str]] = defaultdict(set)

    def observe(self, product_type: str, candidate: SearchCandidate) -> None:
        normalized_product_type = _normalize(product_type)
        normalized_marketplace = _normalize(candidate.marketplace)
        signature = _taxonomy_signature(candidate.taxonomy)
        if not normalized_product_type or not normalized_marketplace or signature is None:
            return
        self._evidence[(normalized_product_type, normalized_marketplace, signature)].add(
            candidate.listing_id
        )

    def propose(
        self,
        product_type: str,
        marketplace: str,
        *,
        minimum_distinct: int = 3,
    ) -> TaxonomyConstraint | None:
        if minimum_distinct <= 0:
            raise ValueError("minimum_distinct must be positive")
        normalized_product_type = _normalize(product_type)
        normalized_marketplace = _normalize(marketplace)
        if not normalized_product_type or not normalized_marketplace:
            return None

        candidates: list[tuple[int, tuple[str, str]]] = []
        for (ptype, market, signature), listing_ids in self._evidence.items():
            if ptype == normalized_product_type and market == normalized_marketplace:
                candidates.append((len(listing_ids), signature))
        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_count, best_signature = candidates[0]
        if best_count < minimum_distinct:
            return None
        if len(candidates) > 1 and candidates[1][0] == best_count:
            return None

        field, value = best_signature
        kwargs: dict[str, object] = {
            "marketplace": normalized_marketplace,
            "product_type": normalized_product_type,
        }
        if field == "subject_id":
            kwargs["subject_ids"] = frozenset({value})
        elif field == "entity":
            kwargs["entities"] = frozenset({value})
        else:
            kwargs["category_path"] = value
        return TaxonomyConstraint(**kwargs)
