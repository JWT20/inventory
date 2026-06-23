"""Tests for the Shopify OAuth install flow (Fase 3, Shopify connect).

The callback is public, so its security rests on Shopify's HMAC + our signed
state. Token exchange is monkeypatched (no live HTTP).
"""
import hashlib
import hmac as hmaclib

import app.routers.channels as channels_mod
from app.config import settings
from app.models import ChannelConnection, Organization
from app.routers.channels import _state_signer
from app.services import shopify as shopify_mod
from tests.conftest import auth_header


def _org(db, slug, modules=("inventory", "orders", "channel_orders")):
    org = Organization(name=slug, slug=slug)
    org.modules = list(modules)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _hmac(params: dict, secret: str) -> str:
    pairs = sorted(f"{k}={v}" for k, v in params.items() if k not in ("hmac", "signature"))
    return hmaclib.new(secret.encode(), "&".join(pairs).encode(), hashlib.sha256).hexdigest()


# --- pure helpers ----------------------------------------------------------

def test_is_valid_shop_domain():
    assert shopify_mod.is_valid_shop_domain("racesokken.myshopify.com")
    assert not shopify_mod.is_valid_shop_domain("evil.example.com")
    assert not shopify_mod.is_valid_shop_domain("racesokken.myshopify.com.evil.com")


def test_verify_oauth_hmac(monkeypatch):
    monkeypatch.setattr(settings, "shopify_api_secret", "s3cr3t")
    params = {"shop": "x.myshopify.com", "code": "c", "state": "st"}
    params["hmac"] = _hmac(params, "s3cr3t")
    assert shopify_mod.verify_oauth_hmac(params) is True
    params["hmac"] = "deadbeef"
    assert shopify_mod.verify_oauth_hmac(params) is False


# --- install ---------------------------------------------------------------

def test_install_redirects_to_shopify(client, db, monkeypatch, admin_token, sample_org):
    monkeypatch.setattr(settings, "shopify_api_key", "key123")
    monkeypatch.setattr(settings, "shopify_shop_domain", "racesokken.myshopify.com")
    monkeypatch.setattr(settings, "domain", "dockscan.nl")

    resp = client.get(
        f"/api/channels/shopify/install?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert "racesokken.myshopify.com/admin/oauth/authorize" in loc
    assert "client_id=key123" in loc
    assert "state=" in loc


def test_install_403_for_non_admin(client, db, owner_token, sample_org):
    # Channel endpoints are platform-admin only.
    resp = client.get(
        f"/api/channels/shopify/install?organization_id={sample_org.id}",
        headers=auth_header(owner_token),
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_install_400_when_not_configured(client, db, admin_token, sample_org):
    # No Shopify settings in the test env → 400 (not a redirect).
    resp = client.get(
        f"/api/channels/shopify/install?organization_id={sample_org.id}",
        headers=auth_header(admin_token),
        follow_redirects=False,
    )
    assert resp.status_code == 400


# --- callback --------------------------------------------------------------

def test_callback_stores_token(client, db, monkeypatch):
    monkeypatch.setattr(settings, "shopify_api_secret", "testsecret")
    monkeypatch.setattr(
        channels_mod,
        "exchange_code_for_token",
        lambda shop, code: {"access_token": "shpat_test", "scope": "read_orders,read_products"},
    )
    org = _org(db, "socks-oauth")
    state = _state_signer.dumps({"org_id": org.id, "shop": "racesokken.myshopify.com"})
    params = {"shop": "racesokken.myshopify.com", "code": "abc", "state": state, "timestamp": "1"}
    params["hmac"] = _hmac(params, "testsecret")

    resp = client.get(
        "/api/channels/shopify/oauth/callback", params=params, follow_redirects=False
    )
    assert resp.status_code in (302, 307)

    conn = db.query(ChannelConnection).filter_by(
        organization_id=org.id, channel="shopify"
    ).one()
    assert conn.access_token == "shpat_test"
    assert conn.shop_domain == "racesokken.myshopify.com"
    assert conn.scope == "read_orders,read_products"


def test_callback_rejects_bad_hmac(client, db, monkeypatch):
    monkeypatch.setattr(settings, "shopify_api_secret", "testsecret")
    org = _org(db, "socks-badhmac")
    state = _state_signer.dumps({"org_id": org.id})
    params = {"shop": "racesokken.myshopify.com", "code": "abc", "state": state, "hmac": "deadbeef"}

    resp = client.get("/api/channels/shopify/oauth/callback", params=params)
    assert resp.status_code == 400


def test_callback_rejects_shop_mismatch(client, db, monkeypatch):
    """A validly-signed callback for a different shop than the install started
    for must be rejected (state binds the shop)."""
    monkeypatch.setattr(settings, "shopify_api_secret", "testsecret")
    org = _org(db, "socks-mismatch")
    state = _state_signer.dumps({"org_id": org.id, "shop": "racesokken.myshopify.com"})
    params = {"shop": "attacker.myshopify.com", "code": "abc", "state": state}
    params["hmac"] = _hmac(params, "testsecret")  # valid hmac + valid myshopify host

    resp = client.get("/api/channels/shopify/oauth/callback", params=params)
    assert resp.status_code == 400


def test_callback_rejects_non_myshopify_shop(client, db, monkeypatch):
    monkeypatch.setattr(settings, "shopify_api_secret", "testsecret")
    org = _org(db, "socks-badshop")
    state = _state_signer.dumps({"org_id": org.id})
    params = {"shop": "evil.example.com", "code": "abc", "state": state}
    params["hmac"] = _hmac(params, "testsecret")  # valid hmac, but bad shop

    resp = client.get("/api/channels/shopify/oauth/callback", params=params)
    assert resp.status_code == 400
