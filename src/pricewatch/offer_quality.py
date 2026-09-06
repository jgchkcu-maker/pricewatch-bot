from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pricewatch.marketplaces import OfferCondition, OfferSnapshot, SearchCandidate
from pricewatch.search_plan import SearchPlan, normalize_query


class OfferQualityStatus(StrEnum):
    TRUSTED = "trusted"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class OfferQualityReason(StrEnum):
    MISSING_TITLE = "missing_title"
    MISSING_PRICE = "missing_price"
    MISSING_LISTING_ID = "missing_listing_id"
    UNAVAILABLE = "unavailable"
    ACCESSORY_ONLY = "accessory_only"
    EXPLICIT_COUNTERFEIT = "explicit_counterfeit"
    CONDITION_CONFLICT = "condition_conflict"
    PRICE_OUTLIER = "price_outlier"
    NO_PRICE_BASELINE = "no_price_baseline"
    NEEDS_CONFIRMATION = "needs_confirmation"
    UNKNOWN_OPTIONAL_SIGNALS = "unknown_optional_signals"


@dataclass(frozen=True, slots=True)
class OfferQualityPolicy:
    min_reference_samples: int = 3
    extreme_low_ratio: Decimal = Decimal("0.50")
    mad_multiplier: Decimal = Decimal("6")
    required_confirmations: int = 2

    def __post_init__(self) -> None:
        if self.min_reference_samples <= 0:
            raise ValueError("min_reference_samples must be positive")
        if self.extreme_low_ratio <= 0:
            raise ValueError("extreme_low_ratio must be positive")
        if self.mad_multiplier < 0:
            raise ValueError("mad_multiplier must not be negative")
        if self.required_confirmations <= 0:
            raise ValueError("required_confirmations must be positive")


@dataclass(frozen=True, slots=True)
class OfferQualityContext:
    trusted_prices: tuple[Decimal, ...] = ()
    prior_status: OfferQualityStatus | None = None
    prior_confirmation_count: int = 0
    trusted_seller_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class OfferQualityDecision:
    status: OfferQualityStatus
    reason_codes: tuple[OfferQualityReason, ...]
    reference_price: Decimal | None = None
    price_ratio: Decimal | None = None
    confirmation_count: int = 0


_DEFAULT_CONTEXT = OfferQualityContext()
_DEFAULT_POLICY = OfferQualityPolicy()

_ACCESSORY_TARGET_TERMS = frozenset(
    {
        "accessory",
        "accessories",
        "case",
        "cover",
        "чехол",
        "чехлы",
        "кабель",
        "cable",
        "амбушюры",
        "earpads",
        "tips",
        "стекло",
        "glass",
        "корпус",
        "запчасть",
        "spare",
    }
)
_ACCESSORY_CUES = (
    "чехол",
    "case",
    "cover",
    "кабель",
    "cable",
    "амбушюр",
    "ear tip",
    "earpad",
    "стекло",
    "screen protector",
    "корпус",
    "запчаст",
    "spare part",
    "left earbud",
    "right earbud",
    "левый наушник",
    "правый наушник",
)
_ACCESSORY_RELATIONS = (
    " для ",
    " for ",
    " compatible ",
    " compatible with ",
    " совместим ",
    " совместимый ",
    " совместимая ",
    " совместимые ",
)
_BUNDLE_RELATIONS = (
    " с чехл",
    " with case",
    " в комплекте",
    " bundle",
    " комплект с",
)
_COUNTERFEIT_PHRASES = (
    "копия",
    "реплика",
    "replica",
    "fake",
    "подделка",
    "не оригинал",
    "not original",
    "аналог",
    "1 1",
)
_COMPATIBILITY_PHRASES = (
    "compatible",
    "совместим",
)
_USED_PHRASES = (
    " б у ",
    " бу ",
    " used ",
    " second hand ",
)
_REFURBISHED_PHRASES = (
    " refurbished ",
    " renewed ",
    " восстановлен",
    " рефаб",
)


def _padded(text: str) -> str:
    normalized = normalize_query(text)
    return f" {normalized} " if normalized else ""


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _valid_reference_prices(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    return tuple(value for value in values if isinstance(value, Decimal) and value > 0)


def _target_is_accessory(plan: SearchPlan) -> bool:
    product_type_tokens = set(normalize_query(plan.product_type or "").split())
    if product_type_tokens.intersection(_ACCESSORY_TARGET_TERMS):
        return True

    canonical = normalize_query(plan.canonical_name)
    first_token = canonical.split()[0] if canonical else ""
    if first_token in _ACCESSORY_TARGET_TERMS:
        return True

    identity_keys = set(plan.identity_attributes)
    return bool(
        identity_keys.intersection(
            {
                "for model",
                "compatible model",
                "accessory type",
                "case type",
            }
        )
    )


def _looks_accessory_only(plan: SearchPlan, title: str) -> bool:
    if _target_is_accessory(plan):
        return False

    padded = _padded(title)
    if not padded:
        return False
    if any(bundle in padded for bundle in _BUNDLE_RELATIONS):
        return False

    normalized = padded.strip()
    cue = next((term for term in _ACCESSORY_CUES if term in normalized), None)
    if cue is None:
        return False

    if any(relation in padded for relation in _ACCESSORY_RELATIONS):
        return True

    first_words = " ".join(normalized.split()[:3])
    return any(first_words.startswith(term) for term in _ACCESSORY_CUES)


def _has_explicit_counterfeit_wording(plan: SearchPlan, title: str) -> bool:
    if _target_is_accessory(plan):
        return False
    if not plan.identity_attributes.get("brand"):
        return False

    padded = _padded(title)
    if not padded:
        return False
    if any(phrase in padded for phrase in _COUNTERFEIT_PHRASES):
        return True
    return any(phrase in padded for phrase in _COMPATIBILITY_PHRASES)


def _evidenced_condition(snapshot: OfferSnapshot) -> OfferCondition:
    explicit = snapshot.quality_signals.condition
    if explicit is not OfferCondition.UNKNOWN:
        return explicit

    padded = _padded(snapshot.title)
    if any(phrase in padded for phrase in _REFURBISHED_PHRASES):
        return OfferCondition.REFURBISHED
    if any(phrase in padded for phrase in _USED_PHRASES):
        return OfferCondition.USED
    return OfferCondition.UNKNOWN


def _condition_conflicts(plan: SearchPlan, snapshot: OfferSnapshot) -> bool:
    requested = plan.condition
    if requested == "any":
        return False
    observed = _evidenced_condition(snapshot)
    if observed is OfferCondition.UNKNOWN:
        return False
    return observed.value != requested


def _confirmation_count(context: OfferQualityContext) -> int:
    if context.prior_status is OfferQualityStatus.QUARANTINED:
        return max(context.prior_confirmation_count, 0) + 1
    return 1


def _price_reference(
    price: Decimal,
    context: OfferQualityContext,
    policy: OfferQualityPolicy,
) -> tuple[Decimal | None, Decimal | None, bool]:
    trusted = _valid_reference_prices(context.trusted_prices)
    if not trusted:
        return None, None, False

    reference = _median(trusted)
    ratio = price / reference
    extreme_low = ratio < policy.extreme_low_ratio

    if len(trusted) >= policy.min_reference_samples:
        deviations = tuple(abs(value - reference) for value in trusted)
        mad = _median(deviations)
        if mad > 0 and price < reference - policy.mad_multiplier * mad:
            extreme_low = True

    return reference, ratio, extreme_low


def _has_trusted_seller(
    candidate: SearchCandidate,
    snapshot: OfferSnapshot,
    context: OfferQualityContext,
) -> bool:
    seller_ids = {
        value for value in (candidate.seller_id, snapshot.locator.seller_id) if value
    }
    return bool(seller_ids.intersection(context.trusted_seller_ids))


def evaluate_offer_quality(
    plan: SearchPlan,
    candidate: SearchCandidate,
    snapshot: OfferSnapshot,
    context: OfferQualityContext = _DEFAULT_CONTEXT,
    policy: OfferQualityPolicy = _DEFAULT_POLICY,
) -> OfferQualityDecision:
    """Classify one identity-verified exact offer without external side effects."""
    if not candidate.listing_id.strip() or not snapshot.locator.listing_id.strip():
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.MISSING_LISTING_ID,),
        )

    detail_title = snapshot.title.strip()
    if not detail_title:
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.MISSING_TITLE,),
        )

    if snapshot.available is False:
        return OfferQualityDecision(
            OfferQualityStatus.UNAVAILABLE,
            (OfferQualityReason.UNAVAILABLE,),
        )

    price = snapshot.price
    if not isinstance(price, Decimal) or price <= 0:
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.MISSING_PRICE,),
        )

    if _looks_accessory_only(plan, detail_title):
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.ACCESSORY_ONLY,),
        )

    if _has_explicit_counterfeit_wording(plan, detail_title):
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.EXPLICIT_COUNTERFEIT,),
        )

    if _condition_conflicts(plan, snapshot):
        return OfferQualityDecision(
            OfferQualityStatus.REJECTED,
            (OfferQualityReason.CONDITION_CONFLICT,),
        )

    reference, ratio, extreme_low = _price_reference(price, context, policy)
    confirmations = _confirmation_count(context)

    if extreme_low:
        if (
            context.prior_status is OfferQualityStatus.QUARANTINED
            and confirmations >= policy.required_confirmations
        ):
            return OfferQualityDecision(
                OfferQualityStatus.TRUSTED,
                (OfferQualityReason.PRICE_OUTLIER,),
                reference_price=reference,
                price_ratio=ratio,
                confirmation_count=confirmations,
            )
        return OfferQualityDecision(
            OfferQualityStatus.QUARANTINED,
            (OfferQualityReason.PRICE_OUTLIER, OfferQualityReason.NEEDS_CONFIRMATION),
            reference_price=reference,
            price_ratio=ratio,
            confirmation_count=confirmations,
        )

    if reference is None:
        already_trusted = context.prior_status is OfferQualityStatus.TRUSTED
        trusted_seller = _has_trusted_seller(candidate, snapshot, context)
        official_badge = bool(snapshot.quality_signals.authenticity_badges)
        if not (already_trusted or trusted_seller or official_badge):
            if (
                context.prior_status is OfferQualityStatus.QUARANTINED
                and confirmations >= policy.required_confirmations
            ):
                return OfferQualityDecision(
                    OfferQualityStatus.TRUSTED,
                    (OfferQualityReason.NO_PRICE_BASELINE,),
                    confirmation_count=confirmations,
                )
            return OfferQualityDecision(
                OfferQualityStatus.QUARANTINED,
                (
                    OfferQualityReason.NO_PRICE_BASELINE,
                    OfferQualityReason.NEEDS_CONFIRMATION,
                ),
                confirmation_count=confirmations,
            )

    return OfferQualityDecision(
        OfferQualityStatus.TRUSTED,
        (),
        reference_price=reference,
        price_ratio=ratio,
        confirmation_count=confirmations,
    )
