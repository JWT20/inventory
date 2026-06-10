"""Tests for order management."""

from tests.conftest import auth_header
from app.models import Customer, CustomerSKU, Order, OrderLine, ReferenceImage, SKU


class TestCreateOrder:
    def test_owner_creates_order(self, client, db, owner_user, owner_token, sample_org):
        # Create customer and SKU
        customer = Customer(name="test klant", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-002", name="Test Wine 2")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 5}],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["organization_name"] == sample_org.name
        assert len(data["lines"]) == 1
        assert data["total_boxes"] == 5
        assert data["total_bottles"] == 0
        # New orders await merchant approval; the delivery week is assigned
        # at approval time.
        assert data["status"] == "pending_approval"
        assert data["delivery_week"] is None

    def test_mixed_order_splits_box_and_bottle_totals(
        self, client, db, owner_user, owner_token, sample_org
    ):
        customer = Customer(name="fles klant", organization_id=sample_org.id)
        box_sku = SKU(sku_code="BOX-001", name="Doos Wijn")
        bottle_sku = SKU(sku_code="FLES-001", name="Cava 0,0", is_bottle=True)
        db.add_all([customer, box_sku, bottle_sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {"customer_id": customer.id, "sku_id": box_sku.id, "quantity": 3},
                    {"customer_id": customer.id, "sku_id": bottle_sku.id, "quantity": 2},
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_boxes"] == 3
        assert data["booked_boxes"] == 0
        assert data["total_bottles"] == 2
        assert data["booked_bottles"] == 0
        lines_by_code = {l["sku_code"]: l for l in data["lines"]}
        assert lines_by_code["BOX-001"]["is_bottle"] is False
        assert lines_by_code["FLES-001"]["is_bottle"] is True

    def test_customer_creates_order(self, client, db, customer_user, customer_token, sample_org):
        customer = Customer(name="klant record", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-003", name="Test Wine 3")
        db.add_all([customer, sku])
        db.commit()
        customer_user.customer_id = customer.id
        db.add(CustomerSKU(customer_id=customer.id, sku_id=sku.id))
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 2}],
            },
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created_by_name"] == "customer"

    def test_courier_cannot_create_order(self, client, courier_token):
        resp = client.post(
            "/api/orders",
            json={"lines": [{"customer_id": 1, "sku_id": 1, "quantity": 1}]},
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_order_response_hides_prices_when_customer_disables_them(
        self, client, db, owner_user, owner_token, sample_org
    ):
        hidden_customer = Customer(
            name="verborgen klant",
            organization_id=sample_org.id,
            show_prices=False,
        )
        visible_customer = Customer(
            name="zichtbare klant",
            organization_id=sample_org.id,
            show_prices=True,
        )
        sku = SKU(sku_code="WINE-200", name="Prijs Test", default_price=10)
        db.add_all([hidden_customer, visible_customer, sku])
        db.commit()

        db.add_all([
            CustomerSKU(customer_id=hidden_customer.id, sku_id=sku.id, unit_price=12),
            CustomerSKU(customer_id=visible_customer.id, sku_id=sku.id, unit_price=11),
        ])
        db.commit()

        hidden_resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {"customer_id": hidden_customer.id, "sku_id": sku.id, "quantity": 2},
                ],
            },
            headers=auth_header(owner_token),
        )
        assert hidden_resp.status_code == 200
        hidden_data = hidden_resp.json()
        hidden_line = hidden_data["lines"][0]
        assert hidden_line["customer_id"] == hidden_customer.id
        assert hidden_line["show_prices"] is False
        assert hidden_line["effective_price"] is None
        assert hidden_line["line_total"] is None
        assert hidden_data["visible_total"] is None
        assert hidden_data["hidden_lines_count"] == 1

        visible_resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {"customer_id": visible_customer.id, "sku_id": sku.id, "quantity": 3},
                ],
            },
            headers=auth_header(owner_token),
        )
        assert visible_resp.status_code == 200
        visible_data = visible_resp.json()
        visible_line = visible_data["lines"][0]
        assert visible_line["customer_id"] == visible_customer.id
        assert visible_line["show_prices"] is True
        assert visible_line["effective_price"] == 11.0
        assert visible_line["line_total"] == 33.0
        assert visible_data["visible_total"] == 33.0
        assert visible_data["hidden_lines_count"] == 0

    def test_order_rejects_multiple_customers(
        self, client, db, owner_token, sample_org
    ):
        a = Customer(name="klant A", organization_id=sample_org.id)
        b = Customer(name="klant B", organization_id=sample_org.id)
        sku = SKU(sku_code="WINE-MULTI", name="Multi klant wijn")
        db.add_all([a, b, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {"customer_id": a.id, "sku_id": sku.id, "quantity": 1},
                    {"customer_id": b.id, "sku_id": sku.id, "quantity": 1},
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400


class TestDeliveryDayScoping:
    """Orders can only use days configured as possible delivery days."""

    def test_monday_rejected_for_normal_customer(
        self, client, db, owner_token, sample_org
    ):
        customer = Customer(
            name="gewone klant", organization_id=sample_org.id, delivery_day="thursday"
        )
        customer.delivery_days = ["wednesday", "thursday", "friday"]
        sku = SKU(sku_code="WINE-DD-1", name="Leverdag wijn 1")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {
                        "customer_id": customer.id,
                        "sku_id": sku.id,
                        "quantity": 1,
                        "delivery_day": "monday",
                    }
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400

    def test_tuesday_allowed_for_special_customer(
        self, client, db, owner_token, sample_org
    ):
        customer = Customer(
            name="speciale klant", organization_id=sample_org.id, delivery_day="tuesday"
        )
        customer.delivery_days = ["monday", "tuesday"]
        sku = SKU(sku_code="WINE-DD-2", name="Leverdag wijn 2")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {
                        "customer_id": customer.id,
                        "sku_id": sku.id,
                        "quantity": 1,
                        "delivery_day": "tuesday",
                    }
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["lines"][0]["delivery_day"] == "tuesday"

    def test_standard_day_still_works_for_normal_customer(
        self, client, db, owner_token, sample_org
    ):
        customer = Customer(
            name="wo klant", organization_id=sample_org.id, delivery_day="thursday"
        )
        customer.delivery_days = ["wednesday", "thursday", "friday"]
        sku = SKU(sku_code="WINE-DD-3", name="Leverdag wijn 3")
        db.add_all([customer, sku])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [
                    {
                        "customer_id": customer.id,
                        "sku_id": sku.id,
                        "quantity": 1,
                        "delivery_day": "wednesday",
                    }
                ],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["lines"][0]["delivery_day"] == "wednesday"


class TestWeeklyPickPhotos:
    def test_weekly_pick_photos_returns_only_open_lines_for_week(
        self, client, db, owner_user, owner_token, sample_org
    ):
        customer = Customer(name="week klant", organization_id=sample_org.id)
        open_sku = SKU(sku_code="WINE-WEEK-OPEN", name="Open wijn")
        done_sku = SKU(sku_code="WINE-WEEK-DONE", name="Klaar wijn")
        other_week_sku = SKU(sku_code="WINE-WEEK-OTHER", name="Andere week wijn")
        db.add_all([customer, open_sku, done_sku, other_week_sku])
        db.commit()

        db.add(ReferenceImage(sku_id=open_sku.id, image_path="reference_images/open.jpg"))
        order = Order(
            organization_id=sample_org.id,
            created_by=owner_user.id,
            reference="ORD-WEEK-PHOTOS",
            status="active",
            delivery_week="2026-W21",
        )
        other_order = Order(
            organization_id=sample_org.id,
            created_by=owner_user.id,
            reference="ORD-WEEK-OTHER",
            status="active",
            delivery_week="2026-W22",
        )
        db.add_all([order, other_order])
        db.commit()

        open_line = OrderLine(
            order_id=order.id,
            sku_id=open_sku.id,
            customer_id=customer.id,
            quantity=4,
            booked_count=2,
        )
        done_line = OrderLine(
            order_id=order.id,
            sku_id=done_sku.id,
            customer_id=customer.id,
            quantity=3,
            booked_count=3,
        )
        other_week_line = OrderLine(
            order_id=other_order.id,
            sku_id=other_week_sku.id,
            customer_id=customer.id,
            quantity=2,
            booked_count=0,
        )
        db.add_all([open_line, done_line, other_week_line])
        db.commit()

        resp = client.get(
            "/api/orders/weekly-pick-photos?week=2026-W21",
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert [item["wine_name"] for item in data] == ["Open wijn"]
        assert data[0]["order_line_id"] == open_line.id
        assert data[0]["image_url"] == "/api/thumbnails/320/reference_images/open.jpg"

    def test_weekly_pick_photos_lists_open_customers_for_sku(
        self, client, db, owner_user, owner_token, sample_org
    ):
        sku = SKU(sku_code="WINE-CUSTOMERS", name="Klantwijn")
        cust_a = Customer(name="Bram", organization_id=sample_org.id)
        cust_b = Customer(name="Anna", organization_id=sample_org.id)
        cust_done = Customer(name="Cor", organization_id=sample_org.id)
        db.add_all([sku, cust_a, cust_b, cust_done])
        db.commit()

        order = Order(
            organization_id=sample_org.id,
            created_by=owner_user.id,
            reference="ORD-CUSTOMERS",
            status="active",
            delivery_week="2026-W21",
        )
        db.add(order)
        db.commit()
        db.add_all([
            OrderLine(order_id=order.id, sku_id=sku.id, customer_id=cust_a.id, quantity=3, booked_count=1),
            OrderLine(order_id=order.id, sku_id=sku.id, customer_id=cust_b.id, quantity=2, booked_count=0),
            # Fully picked for this customer — must not appear.
            OrderLine(order_id=order.id, sku_id=sku.id, customer_id=cust_done.id, quantity=2, booked_count=2),
        ])
        db.commit()

        resp = client.get(
            "/api/orders/weekly-pick-photos?week=2026-W21",
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # Sorted case-insensitively, only customers with open boxes.
        assert data[0]["customers"] == ["Anna", "Bram"]


class TestCustomerSkuRestrictions:
    """Customers may only order SKUs already assigned to their linked customer."""

    def _link_customer(self, db, customer_user, sample_org, assigned_sku=None):
        customer = Customer(name="gekoppelde klant", organization_id=sample_org.id)
        db.add(customer)
        db.commit()
        db.refresh(customer)
        customer_user.customer_id = customer.id
        if assigned_sku is not None:
            db.add(CustomerSKU(customer_id=customer.id, sku_id=assigned_sku.id))
        db.commit()
        return customer

    def test_customer_create_order_with_assigned_sku_succeeds(
        self, client, db, customer_user, customer_token, sample_org
    ):
        sku = SKU(sku_code="WINE-CR-OK", name="Toegewezen wijn")
        db.add(sku)
        db.commit()
        customer = self._link_customer(db, customer_user, sample_org, assigned_sku=sku)

        resp = client.post(
            "/api/orders",
            json={
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 2}],
            },
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 200

    def test_customer_create_order_with_unassigned_sku_forbidden(
        self, client, db, customer_user, customer_token, sample_org
    ):
        unassigned = SKU(sku_code="WINE-CR-NO", name="Niet toegewezen")
        db.add(unassigned)
        db.commit()
        customer = self._link_customer(db, customer_user, sample_org)

        resp = client.post(
            "/api/orders",
            json={
                "lines": [
                    {"customer_id": customer.id, "sku_id": unassigned.id, "quantity": 1}
                ],
            },
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 403
        assert "nieuwe wijnen" in resp.json()["detail"].lower()

    def test_customer_without_linked_customer_cannot_create_order(
        self, client, db, customer_token
    ):
        sku = SKU(sku_code="WINE-CR-NOLINK", name="Geen klantkoppeling")
        db.add(sku)
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "lines": [
                    {"customer_id": 999, "sku_id": sku.id, "quantity": 1}
                ],
            },
            headers=auth_header(customer_token),
        )

        assert resp.status_code == 403
        assert "gekoppeld" in resp.json()["detail"].lower()

    def test_customer_add_line_with_assigned_sku_succeeds(
        self, client, db, customer_user, customer_token, sample_org
    ):
        sku = SKU(sku_code="WINE-AL-OK", name="Toegewezen wijn 2")
        db.add(sku)
        db.commit()
        customer = self._link_customer(db, customer_user, sample_org, assigned_sku=sku)

        resp = client.post(
            "/api/orders",
            json={
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 1}],
            },
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 200
        order_id = resp.json()["id"]

        resp = client.post(
            f"/api/orders/{order_id}/lines",
            json={"customer_id": customer.id, "sku_id": sku.id, "quantity": 3},
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 200

    def test_customer_add_line_with_unassigned_sku_forbidden(
        self, client, db, customer_user, customer_token, sample_org
    ):
        assigned = SKU(sku_code="WINE-AL-A", name="Toegewezen")
        unassigned = SKU(sku_code="WINE-AL-X", name="Niet toegewezen")
        db.add_all([assigned, unassigned])
        db.commit()
        customer = self._link_customer(
            db, customer_user, sample_org, assigned_sku=assigned
        )

        resp = client.post(
            "/api/orders",
            json={
                "lines": [{"customer_id": customer.id, "sku_id": assigned.id, "quantity": 1}],
            },
            headers=auth_header(customer_token),
        )
        order_id = resp.json()["id"]

        resp = client.post(
            f"/api/orders/{order_id}/lines",
            json={"customer_id": customer.id, "sku_id": unassigned.id, "quantity": 1},
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 403
        assert "nieuwe wijnen" in resp.json()["detail"].lower()

    def test_owner_can_still_add_any_sku(
        self, client, db, owner_user, owner_token, sample_org
    ):
        customer = Customer(name="willekeurige klant", organization_id=sample_org.id)
        sku_a = SKU(sku_code="WINE-OW-A", name="Wijn A")
        sku_b = SKU(sku_code="WINE-OW-B", name="Wijn B")
        db.add_all([customer, sku_a, sku_b])
        db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku_a.id, "quantity": 1}],
            },
            headers=auth_header(owner_token),
        )
        order_id = resp.json()["id"]

        # Owner adds an unassigned SKU — should succeed.
        resp = client.post(
            f"/api/orders/{order_id}/lines",
            json={"customer_id": customer.id, "sku_id": sku_b.id, "quantity": 2},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200


class TestListOrders:
    def test_list_orders_unauthenticated(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code == 401

    def test_list_orders_empty(self, client, owner_token):
        resp = client.get("/api/orders", headers=auth_header(owner_token))
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetOrder:
    def test_get_nonexistent_order(self, client, owner_token):
        resp = client.get("/api/orders/9999", headers=auth_header(owner_token))
        assert resp.status_code == 404


class TestApproveOrder:
    def _create_order(self, client, db, owner_token, sample_org, sku_code, with_image=False):
        customer = Customer(name=f"klant {sku_code}", organization_id=sample_org.id)
        sku = SKU(sku_code=sku_code, name=f"Wijn {sku_code}")
        db.add_all([customer, sku])
        db.commit()
        if with_image:
            db.add(ReferenceImage(sku_id=sku.id, image_path=f"reference_images/{sku_code}.jpg"))
            db.commit()

        resp = client.post(
            "/api/orders",
            json={
                "organization_id": sample_org.id,
                "lines": [{"customer_id": customer.id, "sku_id": sku.id, "quantity": 3}],
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        return resp.json()["id"]

    def test_approve_with_images_activates_and_sets_delivery_week(
        self, client, db, owner_token, sample_org
    ):
        import datetime

        order_id = self._create_order(
            client, db, owner_token, sample_org, "WINE-004", with_image=True
        )

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        today = datetime.date.today()
        assert data["delivery_week"] == (
            f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
        )

    def test_approve_without_images_goes_to_pending_images(
        self, client, db, owner_token, sample_org
    ):
        order_id = self._create_order(client, db, owner_token, sample_org, "WINE-005")

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending_images"
        assert data["delivery_week"] is not None

        # Second approve activates explicitly (images stay optional; missing
        # photos are captured at scan time).
        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_approve_with_explicit_week(self, client, db, owner_token, sample_org):
        order_id = self._create_order(
            client, db, owner_token, sample_org, "WINE-104", with_image=True
        )

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            json={"week": "2026-W30"},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["delivery_week"] == "2026-W30"

    def test_approve_with_invalid_week_rejected(
        self, client, db, owner_token, sample_org
    ):
        order_id = self._create_order(client, db, owner_token, sample_org, "WINE-105")

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            json={"week": "week-30"},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400

    def test_courier_sees_pending_approval_read_only(
        self, client, db, owner_token, courier_token, sample_org
    ):
        order_id = self._create_order(client, db, owner_token, sample_org, "WINE-106")

        resp = client.get("/api/orders", headers=auth_header(courier_token))
        assert resp.status_code == 200
        match = next(o for o in resp.json() if o["id"] == order_id)
        assert match["status"] == "pending_approval"

        resp = client.get(f"/api/orders/{order_id}", headers=auth_header(courier_token))
        assert resp.status_code == 200

    def test_courier_cannot_approve_order(
        self, client, db, owner_token, courier_token, sample_org
    ):
        order_id = self._create_order(client, db, owner_token, sample_org, "WINE-006")

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(courier_token),
        )
        assert resp.status_code == 403

    def test_customer_cannot_approve_own_order(
        self, client, db, owner_token, customer_token, sample_org
    ):
        order_id = self._create_order(client, db, owner_token, sample_org, "WINE-007")

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(customer_token),
        )
        assert resp.status_code == 403

    def test_active_order_cannot_be_approved_again(
        self, client, db, owner_token, sample_org
    ):
        order_id = self._create_order(
            client, db, owner_token, sample_org, "WINE-008", with_image=True
        )
        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(owner_token),
        )
        assert resp.json()["status"] == "active"

        resp = client.post(
            f"/api/orders/{order_id}/approve",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400


class TestSKUCodeGeneration:
    def test_sku_code_format(self):
        from app.schemas import generate_wine_sku_code

        attrs = {
            "producent": "Château Grand",
            "wijnaam": "Cru Rouge",
            "wijntype": "Rood",
            "volume": "750",
        }
        assert generate_wine_sku_code(attrs) == "CHAT-CRUR-ROO-750"

    def test_sku_code_with_spaces(self):
        from app.schemas import generate_wine_sku_code

        attrs = {
            "producent": "Domaine Belle",
            "wijnaam": "Blanc Premier",
            "wijntype": "Wit",
            "volume": "750",
        }
        assert generate_wine_sku_code(attrs) == "DOMA-BLAN-WIT-750"

    def test_display_name(self):
        from app.schemas import generate_wine_display_name

        attrs = {
            "producent": "Château Grand",
            "wijnaam": "Cru Rouge",
            "wijntype": "Rood",
            "volume": "750",
        }
        assert generate_wine_display_name(attrs) == "Château Grand Cru Rouge Rood"
