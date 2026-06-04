"""Unit tests for the courier tariff resolver (no DB)."""

import datetime

from app.models import CourierRateCard
from app.services.rate_resolver import (
    ranges_overlap,
    resolve_rate,
    service_for_category,
)

D = datetime.date


def _card(id, *, courier=None, customer=None, org=None, service=None,
          charge=50, eff_from=D(2000, 1, 1), eff_until=None, unit="box"):
    return CourierRateCard(
        id=id, courier_id=courier, customer_id=customer, organization_id=org,
        service_type=service, unit_type=unit, charge_cents=charge,
        platform_cents=charge // 3, courier_cents=charge - charge // 3,
        effective_from=eff_from, effective_until=eff_until,
    )


def test_service_for_category_mapping():
    assert service_for_category("wine") == ("wine_pick", "box")
    assert service_for_category("book") == ("book_pick", "item")
    assert service_for_category("other") == ("general_pick", "item")


def test_service_for_category_unknown_falls_back():
    assert service_for_category("boek") == ("general_pick", "item")
    assert service_for_category(None) == ("general_pick", "item")
    assert service_for_category("WINE") == ("wine_pick", "box")  # case-insensitive


def test_global_default_is_last_resort():
    cards = [_card(1)]  # all-NULL scope
    r = resolve_rate(cards, courier_id=7, customer_id=3, organization_id=2,
                     service_type="wine_pick", on_date=D(2026, 5, 1))
    assert r.rate_card_id == 1
    assert r.scope == "global_default"


def test_most_specific_wins():
    cards = [
        _card(1, charge=50),  # global default
        _card(2, courier=7, charge=55),  # courier default
        _card(3, courier=7, customer=3, charge=70),  # courier+customer
        _card(4, courier=7, customer=3, service="wine_pick", charge=80),  # +service
    ]
    r = resolve_rate(cards, courier_id=7, customer_id=3, organization_id=2,
                     service_type="wine_pick", on_date=D(2026, 5, 1))
    assert r.rate_card_id == 4
    assert r.scope == "customer_service"
    assert r.charge_cents == 80


def test_customer_beats_org():
    cards = [
        _card(1, courier=7, org=2, charge=60),  # courier+org
        _card(2, courier=7, customer=3, charge=70),  # courier+customer
    ]
    r = resolve_rate(cards, courier_id=7, customer_id=3, organization_id=2,
                     service_type="wine_pick", on_date=D(2026, 5, 1))
    assert r.rate_card_id == 2
    assert r.scope == "customer_default"


def test_courier_specific_beats_generic_customer():
    cards = [
        _card(1, customer=3, charge=70),  # any courier, this customer
        _card(2, courier=7, charge=55),  # this courier, default
    ]
    r = resolve_rate(cards, courier_id=7, customer_id=3, organization_id=2,
                     service_type="wine_pick", on_date=D(2026, 5, 1))
    assert r.rate_card_id == 2


def test_effective_dates_filter():
    cards = [
        _card(1, courier=7, charge=50, eff_from=D(2026, 1, 1), eff_until=D(2026, 6, 1)),
        _card(2, courier=7, charge=70, eff_from=D(2026, 6, 1)),
    ]
    may = resolve_rate(cards, courier_id=7, customer_id=None, organization_id=None,
                       service_type="wine_pick", on_date=D(2026, 5, 15))
    june = resolve_rate(cards, courier_id=7, customer_id=None, organization_id=None,
                        service_type="wine_pick", on_date=D(2026, 6, 15))
    assert may.charge_cents == 50
    assert june.charge_cents == 70  # effective_until is exclusive


def test_catchall_card_uses_category_unit():
    # Global 'box' default must bill a book booking as an 'item'.
    cards = [_card(1, unit="box")]  # service None
    r = resolve_rate(cards, courier_id=7, customer_id=None, organization_id=None,
                     service_type="book_pick", on_date=D(2026, 5, 1), default_unit="item")
    assert r.scope == "global_default"
    assert r.unit_type == "item"


def test_service_card_keeps_its_own_unit():
    cards = [_card(1, service="book_pick", unit="item")]
    r = resolve_rate(cards, courier_id=7, customer_id=None, organization_id=None,
                     service_type="book_pick", on_date=D(2026, 5, 1), default_unit="box")
    assert r.unit_type == "item"


def test_no_match_returns_none():
    cards = [_card(1, courier=99)]
    r = resolve_rate(cards, courier_id=7, customer_id=None, organization_id=None,
                     service_type="wine_pick", on_date=D(2026, 5, 1))
    assert r is None


def test_ranges_overlap():
    assert ranges_overlap(D(2026, 1, 1), D(2026, 6, 1), D(2026, 5, 1), D(2026, 8, 1))
    assert not ranges_overlap(D(2026, 1, 1), D(2026, 6, 1), D(2026, 6, 1), D(2026, 8, 1))
    assert ranges_overlap(D(2026, 1, 1), None, D(2030, 1, 1), None)  # open-ended
    assert not ranges_overlap(D(2026, 1, 1), D(2026, 2, 1), D(2026, 3, 1), None)
