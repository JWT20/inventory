"""Tests for POST /receiving/concept-product endpoint."""

import pytest

from app.auth import create_token, hash_password
from app.models import Organization, SKU, User
from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post(client, token, supplier_code, description=None):
    data = {"supplier_code": supplier_code}
    if description is not None:
        data["description"] = description
    return client.post(
        "/api/receiving/concept-product",
        headers=auth_header(token),
        data=data,
    )


# ---------------------------------------------------------------------------
# Additional fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_a(db):
    org = Organization(name="Org A", slug="org-a")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@pytest.fixture
def org_b(db):
    org = Organization(name="Org B", slug="org-b")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@pytest.fixture
def courier_org_a(db, org_a):
    """Courier belonging to org A."""
    user = User(
        username="courier_org_a",
        email="courier_a@local",
        hashed_password=hash_password("pass"),
        role="courier",
        organization_id=org_a.id,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def courier_org_b(db, org_b):
    """Courier belonging to org B."""
    user = User(
        username="courier_org_b",
        email="courier_b@local",
        hashed_password=hash_password("pass"),
        role="courier",
        organization_id=org_b.id,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def courier_a_token(courier_org_a):
    return create_token(courier_org_a.id)


@pytest.fixture
def courier_b_token(courier_org_b):
    return create_token(courier_org_b.id)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestCreateConceptProduct:
    def test_creates_new_concept_sku(self, client, courier_token):
        """A courier can create a new concept SKU; responds 201."""
        resp = _post(client, courier_token, "SUP-001")
        assert resp.status_code == 201
        data = resp.json()
        assert data["sku_code"] == "SUP-001"
        assert data["active"] is False
        assert data["attributes"]["status"] == "concept"
        assert data["attributes"]["source"] == "inbound_scan"

    def test_supplier_code_normalized_to_uppercase(self, client, courier_token):
        resp = _post(client, courier_token, "sup-lowercase")
        assert resp.status_code == 201
        assert resp.json()["sku_code"] == "SUP-LOWERCASE"

    def test_default_name_derived_from_code(self, client, courier_token):
        resp = _post(client, courier_token, "XYZ-42")
        assert resp.status_code == 201
        assert resp.json()["name"] == "Concept XYZ-42"

    def test_custom_description_used_as_name(self, client, courier_token):
        resp = _post(client, courier_token, "XYZ-43", description="My Product")
        assert resp.status_code == 201
        assert resp.json()["name"] == "My Product"

    def test_admin_can_create_concept(self, client, admin_token):
        """Platform admin (warehouse role) can also create concept products."""
        resp = _post(client, admin_token, "ADMIN-001")
        assert resp.status_code == 201

    def test_concept_sku_assigned_to_org(self, client, db, courier_a_token, org_a):
        resp = _post(client, courier_a_token, "ORG-SKU-001")
        assert resp.status_code == 201
        sku = db.query(SKU).filter(SKU.sku_code == "ORG-SKU-001").first()
        assert sku is not None
        assert sku.organization_id == org_a.id

    def test_concept_sku_no_org_for_org_less_courier(self, client, db, courier_token):
        """Courier without org creates an org-less SKU."""
        resp = _post(client, courier_token, "NOORG-001")
        assert resp.status_code == 201
        sku = db.query(SKU).filter(SKU.sku_code == "NOORG-001").first()
        assert sku is not None
        assert sku.organization_id is None


# ---------------------------------------------------------------------------
# Idempotency – existing SKU returns 200
# ---------------------------------------------------------------------------

class TestExistingSkuReturns200:
    def test_existing_sku_same_org_returns_200(self, client, db, courier_a_token, org_a):
        """If code already exists in user's org, return it with 200."""
        sku = SKU(
            sku_code="EXIST-001",
            name="Existing Product",
            organization_id=org_a.id,
            active=True,
        )
        db.add(sku)
        db.commit()

        resp = _post(client, courier_a_token, "EXIST-001")
        assert resp.status_code == 200
        assert resp.json()["sku_code"] == "EXIST-001"
        assert resp.json()["name"] == "Existing Product"

    def test_existing_org_less_sku_returns_200_for_org_less_courier(self, client, db, courier_token):
        """Org-less courier sees existing org-less SKU as 200."""
        sku = SKU(sku_code="NOORG-EXIST", name="No-Org Existing", organization_id=None)
        db.add(sku)
        db.commit()

        resp = _post(client, courier_token, "NOORG-EXIST")
        assert resp.status_code == 200
        assert resp.json()["sku_code"] == "NOORG-EXIST"

    def test_second_call_same_code_returns_200(self, client, courier_token):
        """Second call for the same code returns 200 (idempotent)."""
        resp1 = _post(client, courier_token, "IDEM-001")
        assert resp1.status_code == 201

        resp2 = _post(client, courier_token, "IDEM-001")
        assert resp2.status_code == 200
        assert resp2.json()["sku_code"] == "IDEM-001"


# ---------------------------------------------------------------------------
# Org scoping – cross-org isolation
# ---------------------------------------------------------------------------

class TestOrgScoping:
    def test_cross_org_conflict_returns_409(self, client, db, courier_a_token, org_b):
        """If the code exists in a different org, return 409."""
        sku = SKU(
            sku_code="CROSS-001",
            name="Org B Product",
            organization_id=org_b.id,
        )
        db.add(sku)
        db.commit()

        resp = _post(client, courier_a_token, "CROSS-001")
        assert resp.status_code == 409

    def test_org_a_and_b_cannot_share_code(self, client, db, courier_a_token, courier_b_token, org_a):
        """Org A creates a concept; org B trying same code gets 409."""
        resp = _post(client, courier_a_token, "SHARED-CODE")
        assert resp.status_code == 201

        resp_b = _post(client, courier_b_token, "SHARED-CODE")
        assert resp_b.status_code == 409

    def test_org_a_courier_does_not_see_org_b_sku(self, client, db, courier_a_token, courier_b_token, org_a, org_b):
        """SKU codes are globally unique; org A courier gets 409 when org B already owns that code."""
        sku_b = SKU(
            sku_code="UNIQUE-B",
            name="Only in Org B",
            organization_id=org_b.id,
        )
        db.add(sku_b)
        db.commit()

        # Org A courier can't create the same code either (global uniqueness of sku_code)
        resp = _post(client, courier_a_token, "UNIQUE-B")
        assert resp.status_code == 409

    def test_org_less_courier_cannot_see_org_sku(self, client, db, courier_token, org_a):
        """Org-less courier gets 409 when code is taken by an org."""
        sku = SKU(sku_code="ORGONLY-001", name="Org only", organization_id=org_a.id)
        db.add(sku)
        db.commit()

        resp = _post(client, courier_token, "ORGONLY-001")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/receiving/concept-product", data={"supplier_code": "X"})
        assert resp.status_code == 401

    def test_owner_cannot_create_concept(self, client, owner_token):
        """Org owners are product managers, not warehouse workers."""
        resp = _post(client, owner_token, "OWNER-SKU")
        assert resp.status_code == 403

    def test_merchant_cannot_create_concept(self, client, merchant_token):
        resp = _post(client, merchant_token, "MERCH-SKU")
        assert resp.status_code == 403

    def test_courier_can_create_concept(self, client, courier_token):
        resp = _post(client, courier_token, "COURIER-SKU")
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_empty_supplier_code_rejected(self, client, courier_token):
        resp = client.post(
            "/api/receiving/concept-product",
            headers=auth_header(courier_token),
            data={"supplier_code": "   "},
        )
        assert resp.status_code == 400

    def test_missing_supplier_code_rejected(self, client, courier_token):
        resp = client.post(
            "/api/receiving/concept-product",
            headers=auth_header(courier_token),
            data={},
        )
        assert resp.status_code == 422
