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
    assert org["months"] == [
        {"month": "2026-03", "boxes": 10, "bottles": 0, "items": 0}
    ]


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
        {"month": "2026-04", "boxes": 7, "bottles": 0, "items": 0},
        {"month": "2026-03", "boxes": 17, "bottles": 0, "items": 0},
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
    assert org["months"] == [
        {"month": "2026-03", "boxes": 4, "bottles": 2, "items": 0}
    ]


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
        {"month": "2026-04", "boxes": 0, "bottles": 0, "items": 6}
    ]


def test_active_and_pending_orders_excluded(client, db, courier_token, sample_org):
    _seed(db, sample_org, "ACT", "active", booked=4, finalized_at=None)
    _seed(db, sample_org, "PND", "pending_approval", booked=0, finalized_at=None)

    resp = client.get(
        "/api/orders/reports/monthly-boxes",
        headers=auth_header(courier_token),
    )
    assert resp.json()["organizations"] == []


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
