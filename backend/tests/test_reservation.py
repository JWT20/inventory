"""Reservation lifecycle: a live order reserves stock so the channel-visible
available already excludes it; the pick releases the reservation as on_hand
drops, keeping available stable (no oversell window)."""
from app.models import Customer, InventoryBalance, Order, OrderLine, Organization, SKU
from app.services.booking import apply_booking
from app.services.stock import adjust_reservation


def _org(db, slug="res-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking", "channel_orders"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def test_available_stable_across_reserve_then_pick(db):
    org = _org(db)
    sku = SKU(sku_code="R1", name="R1", organization_id=org.id,
              product_type="barcode", ean="8710000099001")
    db.add(sku)
    db.flush()
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=10, quantity_reserved=0))
    customer = Customer(name="K", organization_id=org.id)
    db.add(customer)
    db.flush()
    order = Order(organization_id=org.id, reference="R-RES", status="active",
                  channel="shopify", external_id="X-RES")
    db.add(order)
    db.flush()
    line = OrderLine(order_id=order.id, sku_id=sku.id, customer_id=customer.id,
                     klant="K", quantity=2, booked_count=0)
    db.add(line)
    db.commit()
    db.refresh(line)

    # Activation reserves the open quantity → available drops by the reservation.
    adjust_reservation(db, sku_id=sku.id, organization_id=org.id, delta=2)
    db.commit()
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id, organization_id=org.id).one()
    db.refresh(balance)
    assert balance.quantity_available == 8  # 10 - 2 reserved

    # Pick one unit: on_hand and reserved drop together.
    apply_booking(db, order_id=order.id, order_line_id=line.id, sku_id=sku.id,
                  quantity=1, cap_remaining=None, scanned_by=customer.id,
                  scan_image_path=None, confidence=None)
    db.refresh(balance)
    assert balance.quantity_on_hand == 9
    assert balance.quantity_reserved == 1
    assert balance.quantity_available == 8  # unchanged — no oversell window


def test_release_clamps_at_zero_for_unreserved_product(db):
    """A pick on a product that never reserved (e.g. vision) must not push
    reserved negative — adjust_reservation clamps at 0."""
    org = _org(db, "res-clamp")
    sku = SKU(sku_code="R2", name="R2", organization_id=org.id,
              product_type="barcode", ean="8710000099002")
    db.add(sku)
    db.flush()
    db.add(InventoryBalance(sku_id=sku.id, organization_id=org.id,
                            quantity_on_hand=5, quantity_reserved=0))
    db.commit()

    adjust_reservation(db, sku_id=sku.id, organization_id=org.id, delta=-3)
    balance = db.query(InventoryBalance).filter_by(sku_id=sku.id, organization_id=org.id).one()
    assert balance.quantity_reserved == 0
