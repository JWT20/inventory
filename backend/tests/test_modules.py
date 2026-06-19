"""Tests for organization feature modules (Fase 0 scope foundation):

- normalize_modules validation + baseline enforcement
- the /auth/modules/catalog endpoint
- require_module gating on merchant endpoints
- the org-module <-> product_type invariant on SKU creation
"""
import pytest

from app.auth import create_token, hash_password
from app.models import Organization, User
from app.modules import BASELINE_MODULES, VALID_MODULES, normalize_modules
from tests.conftest import auth_header


# --- fixtures / helpers ----------------------------------------------------

def _make_org(db, slug, modules):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_owner(db, org, username):
    user = User(
        username=username,
        email=f"{username}@local",
        hashed_password=hash_password("OwnerPass1!"),
        role="owner",
        organization_id=org.id,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_token(user.id)


WINE_DATA = {
    "category": "wine",
    "attributes": {
        "producent": "Château Test",
        "wijnaam": "Grand Vin",
        "wijntype": "Rood",
        "volume": "750",
    },
}


# --- unit: normalize_modules ----------------------------------------------

class TestNormalizeModules:
    def test_baseline_always_present(self):
        assert set(BASELINE_MODULES).issubset(set(normalize_modules([])))

    def test_keeps_valid_and_adds_baseline(self):
        result = normalize_modules(["vision_picking"])
        assert "vision_picking" in result
        assert set(BASELINE_MODULES).issubset(set(result))

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            normalize_modules(["bogus_module"])

    def test_dedups_and_orders(self):
        result = normalize_modules(["suppliers", "suppliers", "inventory"])
        assert result == [m for m in VALID_MODULES if m in set(result)]
        assert result.count("suppliers") == 1


# --- catalog endpoint ------------------------------------------------------

class TestModuleCatalog:
    def test_admin_gets_catalog(self, client, admin_token):
        resp = client.get(
            "/api/auth/modules/catalog", headers=auth_header(admin_token)
        )
        assert resp.status_code == 200, resp.text
        keys = {m["key"] for m in resp.json()}
        assert keys == set(VALID_MODULES)
        baseline = {m["key"] for m in resp.json() if m["baseline"]}
        assert baseline == set(BASELINE_MODULES)

    def test_non_admin_denied(self, client, owner_token):
        resp = client.get(
            "/api/auth/modules/catalog", headers=auth_header(owner_token)
        )
        assert resp.status_code == 403


# --- org create validation -------------------------------------------------

class TestOrgCreateModules:
    def test_rejects_unknown_module(self, client, admin_token):
        resp = client.post(
            "/api/auth/organizations",
            json={"name": "X", "slug": "x-org", "enabled_modules": ["nope"]},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 422

    def test_normalizes_and_adds_baseline(self, client, admin_token):
        resp = client.post(
            "/api/auth/organizations",
            json={"name": "Y", "slug": "y-org", "enabled_modules": ["vision_picking"]},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 201, resp.text
        mods = set(resp.json()["enabled_modules"])
        assert "vision_picking" in mods
        assert set(BASELINE_MODULES).issubset(mods)

    def test_default_is_barcode_baseline(self, client, admin_token):
        resp = client.post(
            "/api/auth/organizations",
            json={"name": "Z", "slug": "z-org"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 201, resp.text
        mods = set(resp.json()["enabled_modules"])
        assert "barcode_picking" in mods
        assert "channel_orders" in mods
        assert "vision_picking" not in mods


# --- require_module gating -------------------------------------------------

class TestWeekOverviewGating:
    def test_barcode_org_denied_weekly_summary(self, client, db):
        org = _make_org(db, "barcode-co", ["inventory", "orders", "barcode_picking"])
        token = _make_owner(db, org, "barcode-owner")
        resp = client.get(
            "/api/orders/weekly-summary", headers=auth_header(token)
        )
        assert resp.status_code == 403

    def test_week_org_allowed_weekly_summary(self, client, db):
        org = _make_org(db, "wine-co", ["inventory", "orders", "week_overview"])
        token = _make_owner(db, org, "wine-owner")
        resp = client.get(
            "/api/orders/weekly-summary", headers=auth_header(token)
        )
        assert resp.status_code == 200, resp.text

    def test_admin_bypasses_module(self, client, admin_token):
        resp = client.get(
            "/api/orders/weekly-summary", headers=auth_header(admin_token)
        )
        assert resp.status_code != 403


class TestModuleGatedRouters:
    def test_barcode_org_denied_suppliers(self, client, db):
        org = _make_org(db, "barcode-sup", ["inventory", "orders", "barcode_picking"])
        token = _make_owner(db, org, "barcode-sup-owner")
        resp = client.get("/api/suppliers", headers=auth_header(token))
        assert resp.status_code == 403

    def test_barcode_org_denied_customers(self, client, db):
        org = _make_org(db, "barcode-cust", ["inventory", "orders", "barcode_picking"])
        token = _make_owner(db, org, "barcode-cust-owner")
        resp = client.get("/api/customers", headers=auth_header(token))
        assert resp.status_code == 403


# --- product_type invariant ------------------------------------------------

class TestProductTypeInvariant:
    def test_vision_sku_rejected_in_barcode_org(self, client, db):
        org = _make_org(db, "barcode-only", ["inventory", "orders", "barcode_picking"])
        token = _make_owner(db, org, "barcode-only-owner")
        resp = client.post(
            "/api/skus", json=WINE_DATA, headers=auth_header(token)
        )
        assert resp.status_code == 400
        assert "vision_picking" in resp.json()["detail"]

    def test_barcode_sku_rejected_in_vision_org(self, client, db):
        org = _make_org(db, "vision-only", ["inventory", "orders", "vision_picking"])
        token = _make_owner(db, org, "vision-only-owner")
        resp = client.post(
            "/api/skus",
            json={
                "category": "overig",
                "name": "Sok zwart",
                "sku_code": "SOK-INV",
                "product_type": "barcode",
                "ean": "8712345678906",
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 400
        assert "barcode_picking" in resp.json()["detail"]

    def test_matching_module_allows_creation(self, client, db):
        org = _make_org(db, "barcode-ok", ["inventory", "orders", "barcode_picking"])
        token = _make_owner(db, org, "barcode-ok-owner")
        resp = client.post(
            "/api/skus",
            json={
                "category": "overig",
                "name": "Sok wit",
                "sku_code": "SOK-OK",
                "product_type": "barcode",
                "ean": "8712345678906",
            },
            headers=auth_header(token),
        )
        assert resp.status_code == 201, resp.text
