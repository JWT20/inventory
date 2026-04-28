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
