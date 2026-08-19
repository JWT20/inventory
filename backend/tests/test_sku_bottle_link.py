"""Tests for the box → bottle link on a SKU.

The link is what later lets one picked box land as loose bottles in the shop or
webshop pool, so the invariants that make that conversion interpretable are
enforced here: only a box may carry a link, and it may only point at a bottle of
the same merchant.
"""

import pytest

from app.models import SKU
from tests.conftest import auth_header


@pytest.fixture
def box_sku(db, sample_org):
    sku = SKU(
        sku_code="BOX-001",
        name="Test Wine doos",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=False,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


@pytest.fixture
def bottle_sku(db, sample_org):
    sku = SKU(
        sku_code="FLES-001",
        name="Test Wine fles",
        organization_id=sample_org.id,
        product_type="vision",
        is_bottle=True,
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


class TestLinkBottleToBox:
    def test_link_and_read_back(self, client, owner_token, box_sku, bottle_sku):
        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": bottle_sku.id},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bottle_sku_id"] == bottle_sku.id
        assert body["bottle_sku_code"] == "FLES-001"
        assert body["bottle_sku_name"] == "Test Wine fles"

    def test_unlink_with_null(self, client, db, owner_token, box_sku, bottle_sku):
        box_sku.bottle_sku_id = bottle_sku.id
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": None},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["bottle_sku_id"] is None

    def test_untouched_when_field_omitted(
        self, client, db, owner_token, box_sku, bottle_sku
    ):
        box_sku.bottle_sku_id = bottle_sku.id
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"active": True},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 200
        assert resp.json()["bottle_sku_id"] == bottle_sku.id

    def test_unlinked_box_reports_null(self, client, owner_token, box_sku):
        resp = client.get(
            f"/api/skus/{box_sku.id}", headers=auth_header(owner_token)
        )
        assert resp.status_code == 200
        assert resp.json()["bottle_sku_id"] is None
        assert resp.json()["bottle_sku_name"] is None


class TestBottleLinkRejections:
    def test_bottle_may_not_carry_a_link(
        self, client, owner_token, bottle_sku, db, sample_org
    ):
        other = SKU(
            sku_code="FLES-002",
            name="Andere fles",
            organization_id=sample_org.id,
            product_type="vision",
            is_bottle=True,
        )
        db.add(other)
        db.commit()

        resp = client.patch(
            f"/api/skus/{bottle_sku.id}",
            json={"bottle_sku_id": other.id},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400
        assert "doosproduct" in resp.json()["detail"]

    def test_target_must_be_a_bottle(self, client, owner_token, box_sku, db, sample_org):
        other_box = SKU(
            sku_code="BOX-002",
            name="Andere doos",
            organization_id=sample_org.id,
            product_type="vision",
            is_bottle=False,
        )
        db.add(other_box)
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": other_box.id},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400
        assert "geen flesproduct" in resp.json()["detail"]

    def test_cannot_link_to_itself(self, client, owner_token, box_sku):
        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": box_sku.id},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400

    def test_target_from_another_merchant_is_refused(
        self, client, owner_token, box_sku, db
    ):
        from app.models import Organization

        other_org = Organization(name="Andere handelaar", slug="andere-handelaar")
        db.add(other_org)
        db.commit()
        foreign_bottle = SKU(
            sku_code="FLES-X",
            name="Fles elders",
            organization_id=other_org.id,
            product_type="vision",
            is_bottle=True,
        )
        db.add(foreign_bottle)
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": foreign_bottle.id},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400
        assert "niet gevonden" in resp.json()["detail"]

    def test_unknown_bottle_is_refused(self, client, owner_token, box_sku):
        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": 999999},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400

    def test_flipping_a_linked_box_into_a_bottle_is_refused(
        self, client, db, owner_token, box_sku, bottle_sku
    ):
        box_sku.bottle_sku_id = bottle_sku.id
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"is_bottle": True},
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400
        assert "doosproduct" in resp.json()["detail"]

    def test_the_refusal_says_how_to_get_out_of_it(
        self, client, db, owner_token, box_sku, bottle_sku
    ):
        """The picker of the toggle must not be left guessing what to fix."""
        box_sku.bottle_sku_id = bottle_sku.id
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"is_bottle": True, "bottle_sku_id": bottle_sku.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 400
        assert "haal eerst de koppeling weg" in resp.json()["detail"]

    def test_unlinking_and_flipping_together_is_allowed(
        self, client, db, owner_token, box_sku, bottle_sku
    ):
        """Clearing the link in the same save is the way out, not a loophole."""
        box_sku.bottle_sku_id = bottle_sku.id
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"is_bottle": True, "bottle_sku_id": None},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["is_bottle"] is True
        assert resp.json()["bottle_sku_id"] is None


class TestCreateWithBottleLink:
    def test_create_box_with_link(self, client, owner_token, bottle_sku):
        resp = client.post(
            "/api/skus",
            json={
                "category": "wine",
                "attributes": {
                    "producent": "Château Test",
                    "wijnaam": "Doos Vin",
                    "wijntype": "Rood",
                    "volume": "750",
                },
                "is_bottle": False,
                "bottle_sku_id": bottle_sku.id,
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 201
        assert resp.json()["bottle_sku_id"] == bottle_sku.id

    def test_create_bottle_with_link_is_refused(self, client, owner_token, bottle_sku):
        resp = client.post(
            "/api/skus",
            json={
                "category": "wine",
                "attributes": {
                    "producent": "Château Test",
                    "wijnaam": "Fles Vin",
                    "wijntype": "Rood",
                    "volume": "750",
                },
                "is_bottle": True,
                "bottle_sku_id": bottle_sku.id,
            },
            headers=auth_header(owner_token),
        )
        assert resp.status_code == 400


def test_deleting_the_bottle_clears_the_link(
    client, db, owner_token, box_sku, bottle_sku
):
    """The box survives its bottle being removed from the catalog, unlinked."""
    box_sku.bottle_sku_id = bottle_sku.id
    db.commit()

    resp = client.delete(
        f"/api/skus/{bottle_sku.id}", headers=auth_header(owner_token)
    )
    assert resp.status_code == 204

    db.expire_all()
    assert db.get(SKU, box_sku.id).bottle_sku_id is None


class TestCategoryMustMatch:
    """A box holds bottles of its own kind, so the two sides agree on what
    kind that is — otherwise a pick turns a case of socks into six wines."""

    def test_a_box_from_another_category_is_refused(
        self, client, db, owner_token, sample_org, bottle_sku
    ):
        bottle_sku.category = "wine"
        sokken = SKU(
            sku_code="SOK-001",
            name="Sokken doos",
            organization_id=sample_org.id,
            product_type="barcode",
            category="textiel",
            is_bottle=False,
        )
        db.add(sokken)
        db.commit()

        resp = client.patch(
            f"/api/skus/{sokken.id}",
            json={"bottle_sku_id": bottle_sku.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 400
        assert "categorie" in resp.json()["detail"]

    def test_the_same_category_is_allowed(
        self, client, db, owner_token, sample_org, box_sku, bottle_sku
    ):
        box_sku.category = "wine"
        bottle_sku.category = "wine"
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": bottle_sku.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text

    def test_a_product_without_a_category_is_not_a_mismatch(
        self, client, db, owner_token, box_sku, bottle_sku
    ):
        """Missing data is not evidence; refusing there blocks fine links."""
        box_sku.category = None
        bottle_sku.category = "wine"
        db.commit()

        resp = client.patch(
            f"/api/skus/{box_sku.id}",
            json={"bottle_sku_id": bottle_sku.id},
            headers=auth_header(owner_token),
        )

        assert resp.status_code == 200, resp.text
