"""Tests for box/bottle splits in the weekly order summary."""

from app.models import Customer, InventoryBalance, Order, OrderLine, SKU, Supplier
from tests.conftest import auth_header

WEEK = "2026-W30"


def _seed_mixed_order(db, org):
    box_sku = SKU(sku_code="WS-BOX", name="Doos Wijn")
    bottle_sku = SKU(sku_code="WS-FLES", name="Cava 0,0", is_bottle=True)
    customer = Customer(name="Week Klant", organization_id=org.id)
    db.add_all([box_sku, bottle_sku, customer])
    db.flush()
    order = Order(
        organization_id=org.id,
        reference="WS-1",
        status="active",
        delivery_week=WEEK,
    )
    db.add(order)
    db.flush()
    db.add_all([
        OrderLine(
            order_id=order.id, sku_id=box_sku.id, customer_id=customer.id,
            klant=customer.name, quantity=6, booked_count=0,
        ),
        OrderLine(
            order_id=order.id, sku_id=bottle_sku.id, customer_id=customer.id,
            klant=customer.name, quantity=2, booked_count=0,
        ),
    ])
    db.commit()


def test_supplier_grouping_splits_boxes_and_bottles(
    client, db, owner_token, sample_org
):
    _seed_mixed_order(db, sample_org)

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "supplier"},
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["grand_total_quantity"] == 8
    assert data["grand_total_boxes"] == 6
    assert data["grand_total_bottles"] == 2

    supplier = data["suppliers"][0]
    assert supplier["supplier_total_boxes"] == 6
    assert supplier["supplier_total_bottles"] == 2
    wines = {w["sku_code"]: w for w in supplier["wines"]}
    assert wines["WS-BOX"]["is_bottle"] is False
    assert wines["WS-FLES"]["is_bottle"] is True


def test_supplier_summary_keeps_terminal_orders_and_shows_physical_stock(
    client, db, owner_token, sample_org
):
    supplier = Supplier(name="Verdino", organization_id=sample_org.id)
    sku = SKU(
        sku_code="WS-VERDEJO",
        name="Verdino Verdejo",
        organization_id=sample_org.id,
        supplier=supplier,
    )
    customers = [
        Customer(name=f"Week Klant {i}", organization_id=sample_org.id)
        for i in range(4)
    ]
    db.add_all([supplier, sku, *customers])
    db.flush()

    for index, (status, quantity) in enumerate(
        [("active", 2), ("completed", 3), ("closed", 4), ("cancelled", 100)]
    ):
        order = Order(
            organization_id=sample_org.id,
            reference=f"WS-STATUS-{index}",
            status=status,
            delivery_week=WEEK,
        )
        db.add(order)
        db.flush()
        db.add(
            OrderLine(
                order_id=order.id,
                sku_id=sku.id,
                customer_id=customers[index].id,
                klant=customers[index].name,
                quantity=quantity,
                booked_count=quantity if status == "completed" else 0,
            )
        )

    # Reserved stock must not affect the physical box count in this overview.
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=sample_org.id,
            quantity_on_hand=5,
            quantity_reserved=4,
        )
    )
    db.commit()

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "supplier"},
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200
    wine = resp.json()["suppliers"][0]["wines"][0]
    assert wine["total_quantity"] == 9
    assert wine["current_stock"] == 5
    assert wine["completed_order_count"] == 1
    assert wine["closed_order_count"] == 1


def test_customer_grouping_splits_boxes_and_bottles(
    client, db, owner_token, sample_org
):
    _seed_mixed_order(db, sample_org)

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "customer"},
        headers=auth_header(owner_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["grand_total_boxes"] == 6
    assert data["grand_total_bottles"] == 2

    cust = data["customers"][0]
    assert cust["customer_total_quantity"] == 8
    assert cust["customer_total_boxes"] == 6
    assert cust["customer_total_bottles"] == 2
    lines = {l["sku_code"]: l for l in cust["lines"]}
    assert lines["WS-BOX"]["is_bottle"] is False
    assert lines["WS-FLES"]["is_bottle"] is True


def test_weekly_summary_denies_customer(
    client, db, customer_user, customer_token, sample_org
):
    """The weekly summary exposes pricing across all customers in the org, so
    customer-role users must not reach it (even though the UI hides the tab)."""
    _seed_mixed_order(db, sample_org)
    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "customer"},
        headers=auth_header(customer_token),
    )
    assert resp.status_code == 403


def test_weekly_summary_denies_courier(
    client, db, courier_user, courier_token, sample_org
):
    _seed_mixed_order(db, sample_org)
    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": WEEK, "group_by": "supplier"},
        headers=auth_header(courier_token),
    )
    assert resp.status_code == 403


def _bottle_with_stock(db, org, code, name, *, store=0, webshop=0, active=True):
    from app.models import SKU, InventoryBalance

    sku = SKU(
        sku_code=code,
        name=name,
        organization_id=org.id,
        product_type="vision",
        is_bottle=True,
        active=active,
    )
    db.add(sku)
    db.commit()
    for location, quantity in (("store", store), ("webshop", webshop)):
        if quantity:
            db.add(
                InventoryBalance(
                    sku_id=sku.id,
                    organization_id=org.id,
                    inventory_location=location,
                    quantity_on_hand=quantity,
                )
            )
    db.commit()
    return sku


def test_weekly_summary_reports_shop_and_webshop_stock(
    client, db, owner_token, sample_org
):
    """Both pools separately plus their total: where it is, and whether to reorder."""
    sku = _bottle_with_stock(db, sample_org, "FLES-VERK", "Verkoopwijn", store=4, webshop=7)

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": "2026-W34"},
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["sellable_stock"] if i["sku_id"] == sku.id)
    assert (item["store"], item["webshop"], item["total"]) == (4, 7, 11)


def test_empty_shelves_still_show(client, db, owner_token, sample_org):
    """A product nobody ordered this week is exactly the one you must not lose."""
    sku = _bottle_with_stock(db, sample_org, "FLES-LEEG", "Lege wijn")

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": "2026-W34"},
        headers=auth_header(owner_token),
    )

    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["sellable_stock"] if i["sku_id"] == sku.id)
    assert item["total"] == 0


def test_warehouse_stock_is_not_counted_as_sellable(
    client, db, owner_token, sample_org
):
    from app.models import InventoryBalance

    sku = _bottle_with_stock(db, sample_org, "FLES-MAG", "Magazijnwijn", webshop=2)
    db.add(
        InventoryBalance(
            sku_id=sku.id,
            organization_id=sample_org.id,
            inventory_location="warehouse",
            quantity_on_hand=99,
        )
    )
    db.commit()

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": "2026-W34"},
        headers=auth_header(owner_token),
    )

    item = next(i for i in resp.json()["sellable_stock"] if i["sku_id"] == sku.id)
    assert item["total"] == 2


def test_boxes_and_inactive_bottles_stay_out(client, db, owner_token, sample_org):
    from app.models import SKU

    box = SKU(
        sku_code="DOOS-VERK",
        name="Verkoopdoos",
        organization_id=sample_org.id,
        product_type="vision",
    )
    db.add(box)
    db.commit()
    inactive = _bottle_with_stock(
        db, sample_org, "FLES-UIT", "Uitgezette wijn", store=3, active=False
    )

    resp = client.get(
        "/api/orders/weekly-summary",
        params={"week": "2026-W34"},
        headers=auth_header(owner_token),
    )

    listed = {i["sku_id"] for i in resp.json()["sellable_stock"]}
    assert box.id not in listed
    assert inactive.id not in listed
