"""Registering an advice-app delivery order's boxes at the carrier."""

import datetime

import pytest

from app.models import (
    CarrierConnection,
    Order,
    OrderDeliveryAddress,
    OrderLine,
    OrderParcel,
    SKU,
)
from app.services.advice_shipping import (
    AGE_CHECK_OPTION,
    AdviceShippingError,
    create_parcels,
    create_parcels_best_effort,
)
from app.services.veloyd import VeloydError, VeloydNotConnected


class FakeVeloyd:
    """Counts what Veloyd was asked for, and can fail on a chosen box."""

    def __init__(self, fail_on: int | None = None, prefix: str = "parcel"):
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self.prefix = prefix

    def create_parcel(self, *, address, reference, options=None, comment=""):
        self.calls.append(
            {
                "address": address,
                "reference": reference,
                "options": options,
                "comment": comment,
            }
        )
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise VeloydError("Veloyd is tijdelijk niet bereikbaar")
        return f"{self.prefix}-{len(self.calls)}"


def _order(db, org, *, bottles=2, country="NL", email=None, reference="JUR-2026-A1"):
    sku = SKU(
        sku_code=f"WIJN-{bottles}-{country}",
        name="Wijn",
        organization_id=org.id,
        is_bottle=True,
        source_product_id=f"prd_{bottles}_{country}",
    )
    db.add(sku)
    db.flush()
    order = Order(
        organization_id=org.id,
        channel="advice",
        external_id=f"ext-{bottles}-{country}-{reference}",
        reference="ADV-TEST",
        channel_reference=reference,
        status="active",
        inventory_location="webshop",
    )
    db.add(order)
    db.flush()
    db.add(OrderLine(order_id=order.id, sku_id=sku.id, quantity=bottles))
    db.add(
        OrderDeliveryAddress(
            order_id=order.id,
            recipient_name="Pieter Haarman",
            street="Theophile de Bockstraat",
            house_number="89",
            house_number_suffix="H",
            postal_code="1058VB",
            city="Amsterdam",
            country=country,
            phone="+31600000000",
            email=email,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def test_six_bottles_are_one_box(db, sample_org):
    order = _order(db, sample_org, bottles=6)
    veloyd = FakeVeloyd()

    parcels = create_parcels(db, order, client=veloyd)

    assert len(veloyd.calls) == 1
    assert [parcel.sequence for parcel in parcels] == [1]
    assert parcels[0].veloyd_parcel_id == "parcel-1"
    assert parcels[0].tracking_code is None


def test_seven_bottles_become_two_boxes(db, sample_org):
    order = _order(db, sample_org, bottles=7)
    veloyd = FakeVeloyd()

    parcels = create_parcels(db, order, client=veloyd)

    assert len(parcels) == 2
    assert veloyd.calls[1]["comment"].startswith("Doos 2 van 2")


def test_the_box_size_of_the_merchant_is_used(db, sample_org):
    connection = CarrierConnection(
        organization_id=sample_org.id, carrier="veloyd", bottles_per_box=12
    )
    db.add(connection)
    db.commit()
    order = _order(db, sample_org, bottles=12)
    veloyd = FakeVeloyd()

    assert len(create_parcels(db, order, client=veloyd)) == 1


def test_every_parcel_carries_the_age_check_and_the_order_number(db, sample_org):
    order = _order(db, sample_org, bottles=8, reference="JUR-2026-8DP6QY")
    veloyd = FakeVeloyd()

    create_parcels(db, order, client=veloyd)

    for call in veloyd.calls:
        assert call["options"] == [AGE_CHECK_OPTION]
        assert call["reference"] == "JUR-2026-8DP6QY"


def test_the_address_is_passed_the_way_veloyd_asks_for_it(db, sample_org):
    order = _order(db, sample_org, email="pieter@example.nl")
    veloyd = FakeVeloyd()

    create_parcels(db, order, client=veloyd)

    assert veloyd.calls[0]["address"] == {
        "name": "Pieter Haarman",
        "street": "Theophile de Bockstraat",
        "nr": "89",
        "addition": "H",
        "postalCode": "1058VB",
        "city": "Amsterdam",
        "country": "NL",
        "phone": "+31600000000",
        "email": "pieter@example.nl",
    }


def test_without_an_email_veloyd_is_asked_to_mail_nobody(db, sample_org):
    order = _order(db, sample_org)
    veloyd = FakeVeloyd()

    create_parcels(db, order, client=veloyd)

    assert "email" not in veloyd.calls[0]["address"]


def test_a_second_attempt_asks_only_for_the_missing_boxes(db, sample_org):
    """A partially registered order must never be registered twice."""
    order = _order(db, sample_org, bottles=18)
    failing = FakeVeloyd(fail_on=3)

    with pytest.raises(AdviceShippingError):
        create_parcels(db, order, client=failing)

    assert db.query(OrderParcel).filter(OrderParcel.order_id == order.id).count() == 2

    retry = FakeVeloyd(prefix="retry")
    parcels = create_parcels(db, order, client=retry)

    assert len(retry.calls) == 1
    assert retry.calls[0]["comment"].startswith("Doos 3 van 3")
    assert [parcel.sequence for parcel in parcels] == [1, 2, 3]


def test_an_order_that_is_already_registered_calls_nothing(db, sample_org):
    order = _order(db, sample_org, bottles=6)
    create_parcels(db, order, client=FakeVeloyd())

    again = FakeVeloyd(prefix="again")
    parcels = create_parcels(db, order, client=again)

    assert again.calls == []
    assert len(parcels) == 1


def test_a_foreign_address_is_refused_because_of_the_age_check(db, sample_org):
    order = _order(db, sample_org, country="BE")
    veloyd = FakeVeloyd()

    with pytest.raises(AdviceShippingError, match="alleen voor Nederland"):
        create_parcels(db, order, client=veloyd)

    assert veloyd.calls == []


def test_an_order_without_an_address_is_refused(db, sample_org):
    order = _order(db, sample_org)
    order.delivery_address = None
    db.commit()

    with pytest.raises(AdviceShippingError, match="bezorgadres"):
        create_parcels(db, order, client=FakeVeloyd())


def test_a_channel_order_is_never_registered_here(db, sample_org):
    """Veloyd's own webshop links already made those parcels."""
    order = _order(db, sample_org)
    order.channel = "shopify"
    db.commit()

    with pytest.raises(AdviceShippingError, match="advies-orders"):
        create_parcels(db, order, client=FakeVeloyd())


def test_best_effort_swallows_a_carrier_outage(db, sample_org):
    """The advice app is waiting; a lost order is worse than a missing parcel."""
    order = _order(db, sample_org, country="BE")

    create_parcels_best_effort(db, order)

    assert db.query(OrderParcel).filter(OrderParcel.order_id == order.id).count() == 0


# ---------------------------------------------------------------------------
# The endpoints around it
# ---------------------------------------------------------------------------

from app.config import settings  # noqa: E402
from app.models import ChannelConnection, ReferenceImage  # noqa: E402
from tests.conftest import auth_header  # noqa: E402

WRITE_KEY = "test-advice-write-key"
ORDERS_URL = "/api/integrations/advice/orders"


def test_retry_endpoint_registers_the_missing_boxes(
    client, db, owner_token, sample_org, monkeypatch
):
    order = _order(db, sample_org, bottles=13)
    veloyd = FakeVeloyd()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: veloyd,
    )

    resp = client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    assert resp.status_code == 200
    assert [row["sequence"] for row in resp.json()] == [1, 2, 3]
    assert [row["tracking_code"] for row in resp.json()] == [None, None, None]
    assert len(veloyd.calls) == 3


def test_retry_endpoint_is_idempotent(
    client, db, owner_token, sample_org, monkeypatch
):
    order = _order(db, sample_org, bottles=6)
    first = FakeVeloyd()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: first,
    )
    client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    second = FakeVeloyd(prefix="second")
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: second,
    )
    resp = client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    assert resp.status_code == 200
    assert second.calls == []
    assert db.query(OrderParcel).count() == 1


def test_retry_endpoint_refuses_a_closed_order(
    client, db, owner_token, sample_org, monkeypatch
):
    order = _order(db, sample_org)
    order.status = "closed"
    db.commit()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: FakeVeloyd(),
    )

    resp = client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    assert resp.status_code == 409


def test_retry_endpoint_does_not_reach_another_channel(
    client, db, owner_token, sample_org
):
    order = _order(db, sample_org)
    order.channel = "shopify"
    db.commit()

    resp = client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    assert resp.status_code == 404


def test_an_arriving_live_order_is_registered_at_the_carrier(
    client, db, sample_org, monkeypatch
):
    """The carrier prints from Veloyd, so the boxes must exist before pickup."""
    sku = SKU(
        sku_code="WIJN-LIVE",
        name="Wijn",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=True,
        source_product_id="prd_live",
    )
    db.add(sku)
    db.flush()
    db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
    db.add(
        ChannelConnection(
            organization_id=sample_org.id, channel="advice", mode="live"
        )
    )
    db.commit()
    monkeypatch.setattr(settings, "advice_sales_api_key", WRITE_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", sample_org.id)
    veloyd = FakeVeloyd()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: veloyd,
    )

    resp = client.post(
        ORDERS_URL,
        headers={"Authorization": f"Bearer {WRITE_KEY}"},
        json={
            "external_order_id": "adv-live-1",
            "order_reference": "JUR-2026-LIVE1",
            "delivery_address": {
                "recipient_name": "Anna de Vries",
                "street": "Turfsingel",
                "house_number": "8",
                "postal_code": "9712 KR",
                "city": "Groningen",
                "country": "NL",
                "email": "anna@example.nl",
            },
            "lines": [{"source_product_id": "prd_live", "quantity": 8}],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    order = db.query(Order).filter(Order.external_id == "adv-live-1").one()
    assert [parcel.sequence for parcel in order.parcels] == [1, 2]
    assert veloyd.calls[0]["address"]["email"] == "anna@example.nl"


def test_an_observed_order_is_not_registered_at_the_carrier(
    client, db, sample_org, monkeypatch
):
    """Observing means nothing happens outside — least of all a real parcel."""
    sku = SKU(
        sku_code="WIJN-OBS",
        name="Wijn",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=True,
        source_product_id="prd_obs",
    )
    db.add(sku)
    db.commit()
    monkeypatch.setattr(settings, "advice_sales_api_key", WRITE_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", sample_org.id)
    veloyd = FakeVeloyd()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: veloyd,
    )

    resp = client.post(
        ORDERS_URL,
        headers={"Authorization": f"Bearer {WRITE_KEY}"},
        json={
            "external_order_id": "adv-observed-1",
            "order_reference": "JUR-2026-OBS1",
            "delivery_address": {
                "recipient_name": "Anna de Vries",
                "street": "Turfsingel",
                "house_number": "8",
                "postal_code": "9712 KR",
                "city": "Groningen",
                "country": "NL",
            },
            "lines": [{"source_product_id": "prd_obs", "quantity": 2}],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "observed"
    assert veloyd.calls == []


@pytest.mark.parametrize(
    "status", ["observed", "pending_product", "pending_images", "shipped", "closed"]
)
def test_only_a_shippable_order_reaches_the_carrier(db, sample_org, status):
    """Observe mode, an incomplete order and a shipped one all stay put."""
    order = _order(db, sample_org)
    order.status = status
    db.commit()
    veloyd = FakeVeloyd()

    with pytest.raises(AdviceShippingError, match="niet bij de vervoerder"):
        create_parcels(db, order, client=veloyd)

    assert veloyd.calls == []


def test_a_box_already_being_registered_is_not_registered_twice(db, sample_org):
    """The claim row is the mutex: the loser never reaches Veloyd."""
    order = _order(db, sample_org, bottles=6)
    db.add(OrderParcel(order_id=order.id, sequence=1))
    db.commit()
    veloyd = FakeVeloyd()

    with pytest.raises(AdviceShippingError, match="al aangemeld"):
        create_parcels(db, order, client=veloyd)

    assert veloyd.calls == []


def test_a_claim_nobody_finished_is_retried(db, sample_org):
    """A crash mid-call must not leave the order unshippable forever."""
    order = _order(db, sample_org, bottles=6)
    stale = OrderParcel(order_id=order.id, sequence=1)
    db.add(stale)
    db.commit()
    stale.created_at = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    db.commit()
    veloyd = FakeVeloyd()

    parcels = create_parcels(db, order, client=veloyd)

    assert len(veloyd.calls) == 1
    assert parcels[0].veloyd_parcel_id == "parcel-1"


def test_a_refused_box_gives_its_claim_back(db, sample_org):
    """The next attempt must be clean, not blocked by its own failed try."""
    order = _order(db, sample_org, bottles=6)

    with pytest.raises(AdviceShippingError):
        create_parcels(db, order, client=FakeVeloyd(fail_on=1))

    assert db.query(OrderParcel).filter(OrderParcel.order_id == order.id).count() == 0

    retry = FakeVeloyd(prefix="retry")
    parcels = create_parcels(db, order, client=retry)

    assert len(retry.calls) == 1
    assert parcels[0].veloyd_parcel_id == "retry-1"


def test_creating_a_parcel_never_falls_back_to_the_shared_account(
    db, sample_org, monkeypatch
):
    """Shipping under another merchant's sender address is not recoverable."""
    monkeypatch.setattr(settings, "veloyd_api_key", "environment-key")
    monkeypatch.setattr(settings, "veloyd_legacy_organization_id", sample_org.id)
    order = _order(db, sample_org)

    with pytest.raises(VeloydNotConnected):
        create_parcels(db, order)


def test_the_retry_endpoint_refuses_an_observed_order(
    client, db, owner_token, sample_org, monkeypatch
):
    order = _order(db, sample_org)
    order.status = "observed"
    db.commit()
    veloyd = FakeVeloyd()
    monkeypatch.setattr(
        "app.services.advice_shipping.client_for_organization",
        lambda _db, _org_id, **_kwargs: veloyd,
    )

    resp = client.post(
        f"/api/advice-orders/{order.id}/parcels", headers=auth_header(owner_token)
    )

    assert resp.status_code == 409
    assert veloyd.calls == []
