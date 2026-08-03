import httpx
import pytest

from app.models import ReferenceImage, SKU
from app.services.product_status import recompute_active
from app.services.advice_products import (
    AdviceProduct,
    AdviceProductSnapshot,
    AdviceProductSyncError,
    AdviceProductsClient,
    sync_advice_products,
)


class FakeAdviceProductsClient:
    def __init__(self, products):
        self.snapshot = AdviceProductSnapshot(products=products)

    def fetch_snapshot(self):
        return self.snapshot

    def download_image(self, url: str):
        raise AssertionError(f"unexpected image download: {url}")


def _product(
    source_product_id="prd-1",
    *,
    name="Grand Vin",
    active=True,
    image_url=None,
):
    return AdviceProduct(
        source_product_id=source_product_id,
        producer="Château Test",
        name=name,
        vintage="2024",
        color="red",
        active=active,
        image_url=image_url,
    )


def test_product_sync_creates_only_a_linked_bottle(db, sample_org):
    box = SKU(
        sku_code="B2B-DOOS",
        name="Bestaande B2B-doos",
        organization_id=sample_org.id,
        is_bottle=False,
        product_type="vision",
    )
    db.add(box)
    db.commit()

    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product()]),
        import_images=False,
    )

    bottles = db.query(SKU).filter(SKU.is_bottle.is_(True)).all()
    assert summary.created == 1
    assert summary.conflicts == []
    assert len(bottles) == 1
    assert bottles[0].source_product_id == "prd-1"
    assert bottles[0].sku_code == "CHAT-GRAN-ROO-750-FLES"
    assert db.get(SKU, box.id).source_product_id is None


def test_product_sync_is_idempotent_and_keeps_the_sku_code(db, sample_org):
    client = FakeAdviceProductsClient([_product()])
    sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=client,
        import_images=False,
    )
    sku = db.query(SKU).filter(SKU.source_product_id == "prd-1").one()
    original_id = sku.id
    original_code = sku.sku_code

    client.snapshot = AdviceProductSnapshot(
        products=[_product(name="Grand Vin Nieuwe Naam")]
    )
    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=client,
        import_images=False,
    )

    linked = db.query(SKU).filter(SKU.source_product_id == "prd-1").all()
    assert summary.created == 0
    assert summary.updated == 1
    assert len(linked) == 1
    assert linked[0].id == original_id
    assert linked[0].sku_code == original_code
    assert linked[0].attributes_dict["wijnaam"] == "Grand Vin Nieuwe Naam"


def test_product_feed_owns_active_state(db, sample_org):
    """Without the image rule the feed's flag is written through verbatim."""
    sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product(active=False)]),
        import_images=False,
    )
    sku = db.query(SKU).filter(SKU.source_product_id == "prd-1").one()
    assert sku.active is False

    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product(active=True)]),
        import_images=False,
    )
    assert summary.updated == 1
    assert db.get(SKU, sku.id).active is True


def test_photoless_bottle_stays_inactive_for_auto_inactivate_org(db, sample_org):
    """No usable image means not pickable and not sellable, feed or no feed."""
    sample_org.auto_inactivate_no_images = True
    db.commit()

    sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product(active=True)]),
        import_images=False,
    )

    sku = db.query(SKU).filter(SKU.source_product_id == "prd-1").one()
    assert sku.source_active is True
    assert sku.active is False

    db.add(
        ReferenceImage(
            sku_id=sku.id,
            image_path="reference_images/laat-binnengekomen.jpg",
            processing_status="done",
        )
    )
    db.commit()
    recompute_active(sku, db)
    db.commit()
    assert sku.active is True

    # A feed deactivation still wins over a perfectly good photo.
    sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product(active=False)]),
        import_images=False,
    )
    assert db.get(SKU, sku.id).active is False


def test_product_sync_deactivates_missing_linked_bottles(db, sample_org):
    missing = SKU(
        sku_code="OUD-FLES",
        name="Oude fles",
        organization_id=sample_org.id,
        is_bottle=True,
        source_product_id="prd-missing",
        product_type="vision",
        active=True,
    )
    unrelated_box = SKU(
        sku_code="OUD-DOOS",
        name="Oude doos",
        organization_id=sample_org.id,
        is_bottle=False,
        product_type="vision",
        active=True,
    )
    db.add_all([missing, unrelated_box])
    db.commit()

    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product("prd-current")]),
        import_images=False,
    )

    assert summary.deactivated == 1
    assert db.get(SKU, missing.id).active is False
    assert db.get(SKU, unrelated_box.id).active is True


def test_product_sync_reports_code_conflict_without_merging(db, sample_org):
    box = SKU(
        sku_code="CHAT-GRAN-ROO-750-FLES",
        name="Bestaande andere SKU",
        organization_id=sample_org.id,
        is_bottle=False,
        product_type="vision",
    )
    db.add(box)
    db.commit()

    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient([_product()]),
        import_images=False,
    )

    assert summary.created == 0
    assert summary.conflicts == [
        "prd-1: SKU-code CHAT-GRAN-ROO-750-FLES bestaat al"
    ]
    assert db.query(SKU).count() == 1
    assert db.get(SKU, box.id).source_product_id is None


def test_existing_reference_images_are_never_replaced(db, sample_org):
    sku = SKU(
        sku_code="BESTAAND-FLES",
        name="Bestaande fles",
        organization_id=sample_org.id,
        is_bottle=True,
        source_product_id="prd-1",
        product_type="vision",
    )
    db.add(sku)
    db.flush()
    image = ReferenceImage(
        sku_id=sku.id,
        image_path="reference_images/existing.jpg",
        processing_status="done",
    )
    db.add(image)
    db.commit()

    summary = sync_advice_products(
        db,
        organization_id=sample_org.id,
        client=FakeAdviceProductsClient(
            [_product(image_url="https://advies.example/wijn.jpg")]
        ),
        import_images=True,
    )

    assert summary.images_imported == 0
    assert db.query(ReferenceImage).filter(ReferenceImage.sku_id == sku.id).count() == 1


def test_client_rejects_empty_or_duplicate_snapshots():
    responses = [
        {"products": []},
        {
            "products": [
                _product().model_dump(),
                _product().model_dump(),
            ]
        },
    ]

    for payload in responses:
        transport = httpx.MockTransport(
            lambda request, payload=payload: httpx.Response(200, json=payload)
        )
        with httpx.Client(transport=transport) as http_client:
            client = AdviceProductsClient(
                base_url="https://advies.example",
                api_key="secret",
                http_client=http_client,
            )
            with pytest.raises(AdviceProductSyncError):
                client.fetch_snapshot()


def test_client_uses_bearer_authentication():
    def handler(request: httpx.Request):
        assert request.url.path == "/api/integrations/inventory/products"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"products": [_product().model_dump()]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        snapshot = AdviceProductsClient(
            base_url="https://advies.example/",
            api_key="secret",
            http_client=http_client,
        ).fetch_snapshot()

    assert snapshot.products[0].source_product_id == "prd-1"


def test_client_sends_vercel_bypass_only_when_configured():
    def handler(request: httpx.Request):
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["x-vercel-protection-bypass"] == "preview-secret"
        return httpx.Response(200, json={"products": [_product().model_dump()]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        AdviceProductsClient(
            base_url="https://advies.example",
            api_key="secret",
            vercel_bypass_secret="preview-secret",
            http_client=http_client,
        ).fetch_snapshot()
