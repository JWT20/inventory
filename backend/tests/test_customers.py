"""Tests for customer access boundaries."""

from app.auth import create_token
from app.models import Customer, CustomerSKU, SKU
from tests.conftest import auth_header


class TestCustomerRoleAccess:
    def _link_customer_user(self, db, customer_user, sample_org):
        own = Customer(name="eigen klant", organization_id=sample_org.id)
        other = Customer(name="andere klant", organization_id=sample_org.id)
        db.add_all([own, other])
        db.commit()
        db.refresh(own)
        db.refresh(other)
        customer_user.customer_id = own.id
        db.commit()
        return own, other, create_token(customer_user.id)

    def test_customer_lists_only_own_customer(
        self, client, db, customer_user, sample_org
    ):
        own, _other, token = self._link_customer_user(db, customer_user, sample_org)

        resp = client.get("/api/customers", headers=auth_header(token))

        assert resp.status_code == 200
        data = resp.json()
        assert [c["id"] for c in data] == [own.id]

    def test_customer_cannot_create_customer(self, client, customer_token):
        resp = client.post(
            "/api/customers",
            json={"name": "nieuwe klant"},
            headers=auth_header(customer_token),
        )

        assert resp.status_code == 403

    def test_customer_cannot_read_other_customer(
        self, client, db, customer_user, sample_org
    ):
        _own, other, token = self._link_customer_user(db, customer_user, sample_org)

        resp = client.get(f"/api/customers/{other.id}", headers=auth_header(token))

        assert resp.status_code == 403

    def test_customer_cannot_read_other_customer_skus(
        self, client, db, customer_user, sample_org
    ):
        _own, other, token = self._link_customer_user(db, customer_user, sample_org)
        sku = SKU(sku_code="WINE-CUST-SKU", name="Andere klant wijn")
        db.add(sku)
        db.commit()
        db.add(CustomerSKU(customer_id=other.id, sku_id=sku.id))
        db.commit()

        resp = client.get(
            f"/api/customers/{other.id}/skus",
            headers=auth_header(token),
        )

        assert resp.status_code == 403


class TestCustomerSKUSortOrder:
    def _make_customer_with_skus(self, db, sample_org, sku_names):
        customer = Customer(name="sort klant", organization_id=sample_org.id)
        db.add(customer)
        db.commit()
        db.refresh(customer)
        skus = []
        for i, name in enumerate(sku_names):
            sku = SKU(sku_code=f"SORT-{i}", name=name)
            db.add(sku)
            skus.append(sku)
        db.commit()
        for i, sku in enumerate(skus, start=1):
            db.add(
                CustomerSKU(
                    customer_id=customer.id, sku_id=sku.id, sort_order=i
                )
            )
        db.commit()
        return customer, skus

    def test_list_returns_skus_in_sort_order(
        self, client, db, owner_token, sample_org
    ):
        customer, skus = self._make_customer_with_skus(
            db, sample_org, ["Zinfandel", "Albariño", "Merlot"]
        )

        resp = client.get(
            f"/api/customers/{customer.id}/skus",
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200
        assert [row["sku_id"] for row in resp.json()] == [s.id for s in skus]

    def test_reorder_changes_returned_order(
        self, client, db, owner_token, sample_org
    ):
        customer, skus = self._make_customer_with_skus(
            db, sample_org, ["A", "B", "C"]
        )
        new_order = [skus[2].id, skus[0].id, skus[1].id]

        resp = client.put(
            f"/api/customers/{customer.id}/skus/reorder",
            json={"sku_ids": new_order},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 204
        listing = client.get(
            f"/api/customers/{customer.id}/skus",
            headers=auth_header(owner_token),
        ).json()
        assert [row["sku_id"] for row in listing] == new_order

    def test_reorder_rejects_unknown_sku(
        self, client, db, owner_token, sample_org
    ):
        customer, skus = self._make_customer_with_skus(db, sample_org, ["X", "Y"])

        resp = client.put(
            f"/api/customers/{customer.id}/skus/reorder",
            json={"sku_ids": [skus[0].id, 99999]},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 400

    def test_added_sku_lands_at_end(
        self, client, db, owner_token, sample_org
    ):
        customer, skus = self._make_customer_with_skus(db, sample_org, ["A", "B"])
        new_sku = SKU(sku_code="NEW-SKU", name="Aardbei")
        db.add(new_sku)
        db.commit()

        resp = client.post(
            f"/api/customers/{customer.id}/skus",
            json={"sku_ids": [new_sku.id]},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 201

        listing = client.get(
            f"/api/customers/{customer.id}/skus",
            headers=auth_header(owner_token),
        ).json()
        # Despite alphabetical name placing it first, sort_order must put it last.
        assert listing[-1]["sku_id"] == new_sku.id
