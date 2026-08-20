"""Tests for the monthly booked-boxes report (/orders/reports/monthly-boxes)."""

import datetime

from app.models import Customer, Order, OrderLine, SKU
from tests.conftest import auth_header


def _make_order(db, org, ref, status, finalized_at=None, week="2026-W21"):
    order = Order(
        organization_id=org.id,
        reference=ref,
        status=status,
        delivery_week=week,
        finalized_at=finalized_at,
    )
    db.add(order)
    db.flush()
    return order


def _make_line(db, order, sku, customer, quantity, booked_count):
    line = OrderLine(
        order_id=order.id,
        sku_id=sku.id,
        customer_id=customer.id,
        klant=customer.name,
        quantity=quantity,
        booked_count=booked_count,
    )
    db.add(line)
    db.flush()
    return line


def _month(month, *, boxes=0, bottles=0, items=0, item_orders=0, item_lines=0):
    """One expected month row, so tests only spell out what they care about."""
    return {
        "month": month,
        "boxes": boxes,
        "bottles": bottles,
        "items": items,
        "item_order_count": item_orders,
        "item_line_count": item_lines,
    }


def _seed(
    db,
    org,
    ref,
    status,
    booked,
    quantity=12,
    finalized_at=None,
    product_type="vision",
):
    sku = SKU(
        sku_code=f"SKU-{ref}",
        name=f"Wine {ref}",
        product_type=product_type,
    )
    db.add(sku)
    db.flush()
    customer = Customer(name=f"Cust {ref}", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = _make_order(db, org, ref, status=status, finalized_at=finalized_at)
    _make_line(db, order, sku, customer, quantity=quantity, booked_count=booked)
    db.commit()
    return order


def test_closed_partial_counts_booked_only(client, db, courier_token, sample_org):
    # Order closed at 10/12 should contribute 10 boxes.
    _seed(
        db,
        sample_org,
        "CLOSED",
        status="closed",
        booked=10,
        quantity=12,
        finalized_at=datetime.datetime(2026, 3, 15, 9, 0),
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 200
    orgs = resp.json()["organizations"]
    assert len(orgs) == 1
    org = orgs[0]
    assert org["organization_id"] == sample_org.id
    assert org["total_boxes"] == 10
    assert org["months"] == [_month("2026-03", boxes=10)]


def test_groups_by_finalized_month(client, db, courier_token, sample_org):
    _seed(db, sample_org, "A", "completed", booked=12,
          finalized_at=datetime.datetime(2026, 3, 2))
    _seed(db, sample_org, "B", "closed", booked=5,
          finalized_at=datetime.datetime(2026, 3, 20))
    _seed(db, sample_org, "C", "completed", booked=7,
          finalized_at=datetime.datetime(2026, 4, 1))

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    # Months sorted newest first.
    assert org["months"] == [
        _month("2026-04", boxes=7),
        _month("2026-03", boxes=17),
    ]
    assert org["total_boxes"] == 24


def test_bottles_counted_separately(client, db, courier_token, sample_org):
    box_sku = SKU(sku_code="SKU-MIX-BOX", name="Doos Wijn", product_type="vision")
    bottle_sku = SKU(
        sku_code="SKU-MIX-FLES",
        name="Cava 0,0",
        is_bottle=True,
        product_type="vision",
    )
    db.add_all([box_sku, bottle_sku])
    db.flush()
    customer = Customer(name="Cust Mix", organization_id=sample_org.id)
    db.add(customer)
    db.flush()
    order = _make_order(
        db, sample_org, "MIX", status="completed",
        finalized_at=datetime.datetime(2026, 3, 10),
    )
    _make_line(db, order, box_sku, customer, quantity=4, booked_count=4)
    _make_line(db, order, bottle_sku, customer, quantity=3, booked_count=2)
    db.commit()

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 200
    org = resp.json()["organizations"][0]
    assert org["total_boxes"] == 4
    assert org["total_bottles"] == 2
    assert org["months"] == [_month("2026-03", boxes=4, bottles=2)]
    # Wine-only merchant: no order/line counts at all.
    assert org["total_item_orders"] == 0
    assert org["total_item_lines"] == 0


def test_barcode_products_counted_as_items(client, db, courier_token, sample_org):
    _seed(
        db,
        sample_org,
        "EAN",
        status="shipped",
        booked=6,
        quantity=6,
        finalized_at=datetime.datetime(2026, 4, 3, 9, 0),
        product_type="barcode",
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )

    org = resp.json()["organizations"][0]
    assert org["total_boxes"] == 0
    assert org["total_items"] == 6
    assert org["months"] == [
        _month("2026-04", items=6, item_orders=1, item_lines=1)
    ]


def test_barcode_counts_orders_and_lines(client, db, courier_token, sample_org):
    # One order, two barcode lines, five items: 1 order / 2 lines / 5 items.
    sku_a = SKU(sku_code="SKU-SOK-A", name="Racesok zwart", product_type="barcode")
    sku_b = SKU(sku_code="SKU-SOK-B", name="Racesok wit", product_type="barcode")
    db.add_all([sku_a, sku_b])
    db.flush()
    customer = Customer(name="Cust EAN", organization_id=sample_org.id)
    db.add(customer)
    db.flush()
    order = _make_order(
        db, sample_org, "EAN-MULTI", status="shipped",
        finalized_at=datetime.datetime(2026, 4, 5),
    )
    _make_line(db, order, sku_a, customer, quantity=3, booked_count=3)
    _make_line(db, order, sku_b, customer, quantity=2, booked_count=2)
    db.commit()

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["total_item_orders"] == 1
    assert org["total_item_lines"] == 2
    assert org["total_items"] == 5
    assert org["months"] == [
        _month("2026-04", items=5, item_orders=1, item_lines=2)
    ]


def test_barcode_orders_counted_per_month(client, db, courier_token, sample_org):
    _seed(db, sample_org, "EAN-1", "shipped", booked=2, quantity=2,
          finalized_at=datetime.datetime(2026, 4, 3), product_type="barcode")
    _seed(db, sample_org, "EAN-2", "shipped", booked=1, quantity=1,
          finalized_at=datetime.datetime(2026, 4, 20), product_type="barcode")
    _seed(db, sample_org, "EAN-3", "closed", booked=4, quantity=4,
          finalized_at=datetime.datetime(2026, 5, 1), product_type="barcode")

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["months"] == [
        _month("2026-05", items=4, item_orders=1, item_lines=1),
        _month("2026-04", items=3, item_orders=2, item_lines=2),
    ]
    assert org["total_item_orders"] == 3
    assert org["total_item_lines"] == 3


def test_mixed_order_counts_only_barcode_lines(client, db, courier_token, sample_org):
    # A wine line on the same order must not inflate the item order/line counts.
    box_sku = SKU(sku_code="SKU-MIXB-BOX", name="Doos Wijn", product_type="vision")
    ean_sku = SKU(sku_code="SKU-MIXB-EAN", name="Racesok", product_type="barcode")
    db.add_all([box_sku, ean_sku])
    db.flush()
    customer = Customer(name="Cust MixB", organization_id=sample_org.id)
    db.add(customer)
    db.flush()
    order = _make_order(
        db, sample_org, "MIXB", status="completed",
        finalized_at=datetime.datetime(2026, 3, 12),
    )
    _make_line(db, order, box_sku, customer, quantity=6, booked_count=6)
    _make_line(db, order, ean_sku, customer, quantity=2, booked_count=2)
    db.commit()

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["months"] == [
        _month("2026-03", boxes=6, items=2, item_orders=1, item_lines=1)
    ]


def test_unbooked_barcode_line_not_counted(client, db, courier_token, sample_org):
    # Nothing booked on the second line, so it was not shipped: 1 line, not 2.
    sku_a = SKU(sku_code="SKU-ZERO-A", name="Racesok A", product_type="barcode")
    sku_b = SKU(sku_code="SKU-ZERO-B", name="Racesok B", product_type="barcode")
    db.add_all([sku_a, sku_b])
    db.flush()
    customer = Customer(name="Cust Zero", organization_id=sample_org.id)
    db.add(customer)
    db.flush()
    order = _make_order(
        db, sample_org, "EAN-ZERO", status="shipped",
        finalized_at=datetime.datetime(2026, 4, 8),
    )
    _make_line(db, order, sku_a, customer, quantity=2, booked_count=2)
    _make_line(db, order, sku_b, customer, quantity=1, booked_count=0)
    db.commit()

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["months"] == [
        _month("2026-04", items=2, item_orders=1, item_lines=1)
    ]


def test_active_and_pending_orders_excluded(client, db, courier_token, sample_org):
    _seed(db, sample_org, "ACT", "active", booked=4, finalized_at=None)
    _seed(db, sample_org, "PND", "pending_approval", booked=0, finalized_at=None)

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.json()["organizations"] == []


def test_parked_order_keeps_its_counted_month(client, db, courier_token, sample_org):
    # A channel sync can park an already picked order (cancelled/changed at the
    # webshop). The work was done, so the month it was reported in must not shrink.
    _seed(
        db,
        sample_org,
        "PARKED",
        status="needs_review",
        booked=3,
        quantity=3,
        finalized_at=datetime.datetime(2026, 4, 10),
        product_type="barcode",
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["months"] == [
        _month("2026-04", items=3, item_orders=1, item_lines=1)
    ]


def test_cancelled_with_restock_drops_out(client, db, courier_token, sample_org):
    # cancel_restock zeroes booked_count and deletes the bookings: the goods came
    # back, so nothing was processed.
    _seed(
        db,
        sample_org,
        "RESTOCKED",
        status="cancelled",
        booked=0,
        quantity=3,
        finalized_at=datetime.datetime(2026, 4, 11),
        product_type="barcode",
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.json()["organizations"] == []


def test_month_follows_local_warehouse_time(client, db, courier_token, sample_org):
    # 31 July 22:30 UTC is 1 August 00:30 in Amsterdam (CEST): August work.
    _seed(
        db,
        sample_org,
        "LATE",
        status="shipped",
        booked=2,
        quantity=2,
        finalized_at=datetime.datetime(2026, 7, 31, 22, 30),
        product_type="barcode",
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    org = resp.json()["organizations"][0]
    assert org["months"] == [
        _month("2026-08", items=2, item_orders=1, item_lines=1)
    ]


def test_organization_filter(client, db, courier_token, sample_org):
    other = type(sample_org)(name="Andere Handel", slug="andere")
    db.add(other)
    db.flush()
    _seed(db, sample_org, "MINE", "completed", booked=3,
          finalized_at=datetime.datetime(2026, 5, 1))
    _seed(db, other, "THEIRS", "completed", booked=9,
          finalized_at=datetime.datetime(2026, 5, 1))
    db.commit()

    resp = client.get(
        f"/api/orders/reports/monthly-boxes?organization_id={sample_org.id}",
        headers=auth_header(courier_token),
    )
    orgs = resp.json()["organizations"]
    assert len(orgs) == 1
    assert orgs[0]["organization_id"] == sample_org.id
    assert orgs[0]["total_boxes"] == 3


def test_admin_sees_all_orgs(client, db, admin_token, sample_org):
    _seed(db, sample_org, "ADM", "completed", booked=6,
          finalized_at=datetime.datetime(2026, 2, 1))

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["organizations"][0]["total_boxes"] == 6


def test_owner_sees_only_own_org(client, db, owner_token, sample_org):
    other = type(sample_org)(name="Vreemd", slug="vreemd")
    db.add(other)
    db.flush()
    _seed(db, sample_org, "OWN", "completed", booked=2,
          finalized_at=datetime.datetime(2026, 6, 1))
    _seed(db, other, "OTHER", "completed", booked=8,
          finalized_at=datetime.datetime(2026, 6, 1))
    db.commit()

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(owner_token),
    )
    orgs = resp.json()["organizations"]
    assert len(orgs) == 1
    assert orgs[0]["organization_id"] == sample_org.id
    assert orgs[0]["total_boxes"] == 2


def test_customer_forbidden(client, db, customer_token, sample_org):
    _seed(db, sample_org, "CUSTX", "completed", booked=1,
          finalized_at=datetime.datetime(2026, 1, 1))

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(customer_token),
    )
    assert resp.status_code == 403


def test_shipped_order_counts(client, db, courier_token, sample_org):
    # A shipped (label-verified) barcode order is finalized and counts as items.
    _seed(
        db,
        sample_org,
        "SHIPPED",
        status="shipped",
        booked=4,
        quantity=4,
        finalized_at=datetime.datetime(2026, 4, 2, 9, 0),
        product_type="barcode",
    )

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 200
    orgs = resp.json()["organizations"]
    assert len(orgs) == 1
    assert orgs[0]["total_boxes"] == 0
    assert orgs[0]["total_items"] == 4


class TestWebshopTabVisibility:
    """Whether the webshop tab belongs on screen, per merchant.

    Answered from the wijnadvies connection, not from whether anything has been
    picked. Without the connection there will never be a webshop order; with it
    the tab belongs there even while it is still empty.
    """

    URL = "/api/orders/reports/monthly-boxes"

    def test_a_merchant_without_the_connection_gets_no_tab(
        self, client, db, owner_token, sample_org
    ):
        resp = client.get(
            self.URL,
            params={"organization_id": sample_org.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["webshop_connected"] is False

    def test_a_connected_merchant_gets_the_tab_before_picking_anything(
        self, client, db, owner_token, sample_org
    ):
        from app.models import ChannelConnection

        db.add(
            ChannelConnection(
                organization_id=sample_org.id, channel="advice", mode="observe"
            )
        )
        db.commit()

        resp = client.get(
            self.URL,
            params={"organization_id": sample_org.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["webshop_connected"] is True
        # Nothing picked yet, so the tab stands empty rather than absent.
        assert resp.json()["webshop"] == []

    def test_another_channel_does_not_count(self, client, db, owner_token, sample_org):
        """Shopify and bol orders are counted with the wholesale work."""
        from app.models import ChannelConnection

        db.add(
            ChannelConnection(
                organization_id=sample_org.id, channel="shopify", mode="live"
            )
        )
        db.commit()

        resp = client.get(
            self.URL,
            params={"organization_id": sample_org.id},
            headers=auth_header(owner_token),
        )

        assert resp.json()["webshop_connected"] is False
