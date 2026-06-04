"""Courier tariff resolution.

Given a booking's context (courier, customer, organization, product service
type and date), find the most-specific matching CourierRateCard. The resolver
returns *why* it matched (scope + rate_card_id) so billing disputes are
traceable.

Precedence (most specific wins), per the design in plan.courier-rate-cards.md:

    courier+customer+service  > courier+customer
    > courier+org+service     > courier+org
    > courier+service         > courier(default)
    > global(default)

Courier-specificity dominates: any card naming this courier beats one that does
not. Below the courier dimension, customer beats organization, and a service
match is the finest tie-breaker.
"""

import datetime
import logging
from dataclasses import dataclass

from app.models import CATEGORY_SERVICE_MAP, DEFAULT_SERVICE_UNIT, CourierRateCard

logger = logging.getLogger(__name__)


@dataclass
class ResolvedRate:
    rate_card_id: int | None
    scope: str
    charge_cents: int
    platform_cents: int
    courier_cents: int
    unit_type: str


def service_for_category(category: str | None) -> tuple[str, str]:
    """Map a SKU category to its (service_type, unit_type)."""
    key = (category or "").strip().lower()
    if key not in CATEGORY_SERVICE_MAP:
        if key:
            logger.warning(
                "Unknown SKU category %r for billing; using default service", category
            )
        return DEFAULT_SERVICE_UNIT
    return CATEGORY_SERVICE_MAP[key]


def _matches(card: CourierRateCard, *, courier_id, customer_id, organization_id,
             service_type, on_date) -> bool:
    """A card is a candidate if every set scope field equals the input and the
    date falls in [effective_from, effective_until)."""
    if card.courier_id is not None and card.courier_id != courier_id:
        return False
    if card.customer_id is not None and card.customer_id != customer_id:
        return False
    if card.organization_id is not None and card.organization_id != organization_id:
        return False
    if card.service_type is not None and card.service_type != service_type:
        return False
    if card.effective_from is not None and on_date < card.effective_from:
        return False
    if card.effective_until is not None and on_date >= card.effective_until:
        return False
    return True


def _specificity(card: CourierRateCard) -> int:
    """Higher = more specific. Weights keep the precedence table ordering."""
    score = 0
    if card.courier_id is not None:
        score += 100
    if card.customer_id is not None:
        score += 40
    elif card.organization_id is not None:
        score += 20
    if card.service_type is not None:
        score += 1
    return score


def _scope_name(card: CourierRateCard) -> str:
    prefix = "courier" if card.courier_id is not None else "global"
    if card.customer_id is not None:
        base = "customer"
    elif card.organization_id is not None:
        base = "org"
    else:
        base = prefix
    if card.service_type is not None:
        return f"{base}_service"
    return f"{base}_default"


def resolve_rate(
    cards: list[CourierRateCard],
    *,
    courier_id: int,
    customer_id: int | None,
    organization_id: int | None,
    service_type: str,
    on_date: datetime.date,
) -> ResolvedRate | None:
    """Pick the most specific matching card, or None if nothing matches."""
    candidates = [
        c for c in cards
        if _matches(
            c,
            courier_id=courier_id,
            customer_id=customer_id,
            organization_id=organization_id,
            service_type=service_type,
            on_date=on_date,
        )
    ]
    if not candidates:
        return None

    # Most specific wins; deterministic tie-break (overlap guard should make
    # exact ties impossible, but never resolve non-deterministically).
    best = max(
        candidates,
        key=lambda c: (
            _specificity(c),
            c.effective_from or datetime.date.min,
            c.id,
        ),
    )
    return ResolvedRate(
        rate_card_id=best.id,
        scope=_scope_name(best),
        charge_cents=best.charge_cents,
        platform_cents=best.platform_cents,
        courier_cents=best.courier_cents,
        unit_type=best.unit_type,
    )


def ranges_overlap(from_a, until_a, from_b, until_b) -> bool:
    """Do two [from, until) ranges overlap? None until = +infinity."""
    a_end = until_a or datetime.date.max
    b_end = until_b or datetime.date.max
    return from_a < b_end and from_b < a_end
