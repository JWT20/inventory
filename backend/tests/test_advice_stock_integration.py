from app.config import settings
from app.models import InventoryBalance, Organization, SKU


API_KEY = "test-advice-stock-key"


def _configure(monkeypatch, organization_id: int) -> None:
    monkeypatch.setattr(settings, "advice_stock_api_key", API_KEY)
    monkeypatch.setattr(settings, "advice_stock_organization_id", organization_id)


def _headers(key: str = API_KEY) -> dict[str, str]:
    return {"X-Inventory-Key": key}


def test_stock_endpoint_requires_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "advice_stock_api_key", "")
    monkeypatch.setattr(settings, "advice_stock_organization_id", None)

    response = client.get("/api/integrations/advice/stock", headers=_headers())

    assert response.status_code == 503


def test_stock_endpoint_rejects_missing_or_invalid_key(
    client, sample_org, monkeypatch
):
    _configure(monkeypatch, sample_org.id)

    missing = client.get("/api/integrations/advice/stock")
    invalid = client.get(
        "/api/integrations/advice/stock",
        headers=_headers("wrong-key"),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_stock_endpoint_returns_only_configured_organization_bottles(
    client, db, sample_org, monkeypatch
):
    other_org = Organization(name="Andere handelaar", slug="andere-handelaar")
    db.add(other_org)
    db.flush()

    available_bottle = SKU(
        sku_code="WIJN-001",
        name="Beschikbare wijn",
        organization_id=sample_org.id,
        is_bottle=True,
        product_type="vision",
    )
    zero_bottle = SKU(
        sku_code="WIJN-002",
        name="Wijn zonder voorraadregel",
        organization_id=sample_org.id,
        is_bottle=True,
        product_type="vision",
    )
    reserved_bottle = SKU(
        sku_code="WIJN-003",
        name="Volledig gereserveerde wijn",
        organization_id=sample_org.id,
        is_bottle=True,
        product_type="vision",
    )
    box = SKU(
        sku_code="DOOS-001",
        name="Doos",
        organization_id=sample_org.id,
        is_bottle=False,
        product_type="vision",
    )
    other_bottle = SKU(
        sku_code="ANDER-001",
        name="Wijn van andere organisatie",
        organization_id=other_org.id,
        is_bottle=True,
        product_type="vision",
    )
    db.add_all([available_bottle, zero_bottle, reserved_bottle, box, other_bottle])
    db.flush()
    db.add_all(
        [
            InventoryBalance(
                sku_id=available_bottle.id,
                organization_id=sample_org.id,
                quantity_on_hand=12,
                quantity_reserved=4,
            ),
            InventoryBalance(
                sku_id=reserved_bottle.id,
                organization_id=sample_org.id,
                quantity_on_hand=3,
                quantity_reserved=3,
            ),
            InventoryBalance(
                sku_id=box.id,
                organization_id=sample_org.id,
                quantity_on_hand=9,
                quantity_reserved=0,
            ),
            InventoryBalance(
                sku_id=other_bottle.id,
                organization_id=other_org.id,
                quantity_on_hand=99,
                quantity_reserved=0,
            ),
        ]
    )
    db.commit()
    _configure(monkeypatch, sample_org.id)

    response = client.get(
        "/api/integrations/advice/stock",
        params={"organization_id": other_org.id},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [
            {"sku_code": "WIJN-001", "quantity_available": 8},
            {"sku_code": "WIJN-002", "quantity_available": 0},
            {"sku_code": "WIJN-003", "quantity_available": 0},
        ]
    }


def test_stock_endpoint_fails_for_unknown_configured_organization(
    client, monkeypatch
):
    _configure(monkeypatch, 999_999)

    response = client.get("/api/integrations/advice/stock", headers=_headers())

    assert response.status_code == 503
