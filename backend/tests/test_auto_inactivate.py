"""Tests for the auto_inactivate_no_images organization rule.

Covers:
  - The recompute_active helper (unit, against the in-memory DB).
  - The Organization PATCH endpoint accepting the new flag.
  - The activate_order endpoint no longer requiring reference images.
  - The DELETE /api/skus/{id}/images/{img_id} endpoint flipping active.
"""

from app.models import (
    Customer,
    CustomerSKU,
    Order,
    OrderLine,
    Organization,
    ReferenceImage,
    SKU,
)
from app.services.product_status import recompute_active

from tests.conftest import auth_header


def _make_org(db, *, auto_inactivate: bool, slug: str = "wijn-van-jurjen") -> Organization:
    org = Organization(
        name="Wijn van Jurjen",
        slug=slug,
        auto_inactivate_no_images=auto_inactivate,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_sku(db, org: Organization | None, code: str, *, active: bool = True) -> SKU:
    sku = SKU(
        sku_code=code,
        name=code,
        active=active,
        organization_id=org.id if org else None,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _add_image(db, sku: SKU, status: str = "done") -> ReferenceImage:
    img = ReferenceImage(
        sku_id=sku.id,
        image_path=f"reference_images/{sku.id}/{status}.jpg",
        processing_status=status,
    )
    db.add(img)
    db.commit()
    db.refresh(sku)
    return img


# ---------------------------------------------------------------------------
# recompute_active helper
# ---------------------------------------------------------------------------

class TestRecomputeActive:
    def test_opted_in_no_images_is_inactive(self, db):
        org = _make_org(db, auto_inactivate=True)
        sku = _make_sku(db, org, "S-1", active=True)
        recompute_active(sku, db)
        db.commit()
        assert sku.active is False

    def test_opted_in_done_image_is_active(self, db):
        org = _make_org(db, auto_inactivate=True)
        sku = _make_sku(db, org, "S-2", active=False)
        _add_image(db, sku, status="done")
        recompute_active(sku, db)
        db.commit()
        assert sku.active is True

    def test_opted_in_pending_image_is_active_optimistically(self, db):
        org = _make_org(db, auto_inactivate=True)
        sku = _make_sku(db, org, "S-3", active=False)
        _add_image(db, sku, status="pending")
        recompute_active(sku, db)
        db.commit()
        assert sku.active is True

    def test_opted_in_failed_image_is_inactive(self, db):
        org = _make_org(db, auto_inactivate=True)
        sku = _make_sku(db, org, "S-4", active=True)
        _add_image(db, sku, status="failed")
        recompute_active(sku, db)
        db.commit()
        assert sku.active is False

    def test_non_opted_in_org_is_left_alone(self, db):
        org = _make_org(db, auto_inactivate=False, slug="other-org")
        sku = _make_sku(db, org, "S-5", active=True)
        recompute_active(sku, db)
        db.commit()
        # No images, but rule does not apply → manual flag preserved.
        assert sku.active is True

    def test_no_org_is_left_alone(self, db):
        sku = _make_sku(db, None, "S-6", active=True)
        recompute_active(sku, db)
        db.commit()
        assert sku.active is True


# ---------------------------------------------------------------------------
# PATCH /api/auth/organizations/{id}
# ---------------------------------------------------------------------------

class TestOrganizationFlagToggle:
    def test_admin_can_toggle_flag(self, client, db, admin_token):
        org = _make_org(db, auto_inactivate=False, slug="org-toggle")

        resp = client.patch(
            f"/api/auth/organizations/{org.id}",
            json={"auto_inactivate_no_images": True},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["auto_inactivate_no_images"] is True

        db.refresh(org)
        assert org.auto_inactivate_no_images is True

    def test_response_includes_flag(self, client, db, admin_token):
        org = _make_org(db, auto_inactivate=True, slug="org-flagged")

        resp = client.get(
            "/api/auth/organizations",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 200
        match = next(o for o in resp.json() if o["id"] == org.id)
        assert match["auto_inactivate_no_images"] is True


# ---------------------------------------------------------------------------
# Order activation no longer requires reference images
# ---------------------------------------------------------------------------

class TestOrderActivationWithoutImages:
    def _setup_order(self, db, org: Organization) -> Order:
        customer = Customer(name="Restaurant X", organization_id=org.id)
        sku = SKU(sku_code="ORDER-NOIMG", name="Order Wine", organization_id=org.id)
        db.add_all([customer, sku])
        db.commit()

        order = Order(
            organization_id=org.id,
            reference="ORD-1",
            status="draft",
        )
        db.add(order)
        db.flush()
        line = OrderLine(
            order_id=order.id,
            sku_id=sku.id,
            customer_id=customer.id,
            klant=customer.name,
            quantity=3,
            booked_count=0,
        )
        db.add(line)
        db.add(CustomerSKU(customer_id=customer.id, sku_id=sku.id))
        db.commit()
        db.refresh(order)
        return order

    def test_activation_succeeds_without_images(self, client, db, owner_user, owner_token, sample_org):
        order = self._setup_order(db, sample_org)

        resp = client.post(
            f"/api/orders/{order.id}/activate",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        db.refresh(order)
        assert order.status == "active"


# ---------------------------------------------------------------------------
# Reference-image deletion triggers recompute for opted-in orgs
# ---------------------------------------------------------------------------

class TestImageDeletionTriggersRecompute:
    def test_deleting_last_image_demotes_sku(self, client, db, owner_token, sample_org):
        sample_org.auto_inactivate_no_images = True
        db.commit()

        sku = _make_sku(db, sample_org, "DEL-1", active=False)
        img = _add_image(db, sku, status="done")
        recompute_active(sku, db)
        db.commit()
        assert sku.active is True

        resp = client.delete(
            f"/api/skus/{sku.id}/images/{img.id}",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 204

        db.refresh(sku)
        assert sku.active is False

    def test_deleting_image_in_non_opted_in_org_leaves_active_alone(
        self, client, db, owner_token, sample_org
    ):
        # sample_org defaults to auto_inactivate=False
        sku = _make_sku(db, sample_org, "DEL-2", active=True)
        img = _add_image(db, sku, status="done")

        resp = client.delete(
            f"/api/skus/{sku.id}/images/{img.id}",
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 204

        db.refresh(sku)
        # Manual flag preserved: even though there are no images, the rule
        # does not apply, so the merchant's choice stands.
        assert sku.active is True
