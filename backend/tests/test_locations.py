"""Tests for pick-location management (courier-only).

POST/GET/PATCH/DELETE /api/locations and the SKU-link endpoints. Gated to
platform admin + courier via require_warehouse; barcode products and loose
bottles may be linked, whole wine boxes may not.
"""
from app.auth import create_token, hash_password
from app.models import Organization, SKU, User
from tests.conftest import auth_header


def _org(db, slug="loc-org"):
    org = Organization(name=slug, slug=slug)
    org.modules = ["inventory", "orders", "barcode_picking"]
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _barcode_sku(db, org, code="SOK-1", ean="8711111111111"):
    sku = SKU(sku_code=code, name=f"Sok {code}", organization_id=org.id,
              product_type="barcode", ean=ean)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _vision_sku(db, org, code="WIJN-1"):
    sku = SKU(sku_code=code, name=f"Wijn {code}", organization_id=org.id,
              category="wine", product_type="vision")
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def _owner_token(db, org):
    owner = User(username="loc-owner", email="loc-owner@local",
                 hashed_password=hash_password("OwnerPass1!"), role="owner",
                 organization_id=org.id, is_verified=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return create_token(owner.id)


def _create(client, token, code="AB123", **kw):
    return client.post("/api/locations", json={"code": code, **kw},
                       headers=auth_header(token))


def test_courier_creates_and_lists_location(client, db, courier_token):
    resp = _create(client, courier_token, "AB123", rij="A", kast="B", plank="1")
    assert resp.status_code == 201
    assert resp.json()["code"] == "AB123"

    listing = client.get("/api/locations", headers=auth_header(courier_token))
    assert listing.status_code == 200
    codes = [l["code"] for l in listing.json()]
    assert "AB123" in codes


def test_duplicate_code_rejected(client, db, courier_token):
    assert _create(client, courier_token, "DUP1").status_code == 201
    assert _create(client, courier_token, "DUP1").status_code == 409


def test_link_barcode_sku(client, db, courier_token):
    org = _org(db)
    sku = _barcode_sku(db, org)
    loc_id = _create(client, courier_token, "LNK1").json()["id"]

    resp = client.post(f"/api/locations/{loc_id}/skus",
                       json={"sku_id": sku.id}, headers=auth_header(courier_token))
    assert resp.status_code == 200
    assert [s["sku_id"] for s in resp.json()["skus"]] == [sku.id]


def test_link_wine_box_rejected(client, db, courier_token):
    """A box is matched by photo against its order, never off a fixed shelf."""
    org = _org(db, "vis-org")
    sku = _vision_sku(db, org)
    loc_id = _create(client, courier_token, "VIS1").json()["id"]

    resp = client.post(f"/api/locations/{loc_id}/skus",
                       json={"sku_id": sku.id}, headers=auth_header(courier_token))
    assert resp.status_code == 400
    assert "barcode" in resp.json()["detail"].lower()


def test_unlink_sku(client, db, courier_token):
    org = _org(db, "unl-org")
    sku = _barcode_sku(db, org)
    loc_id = _create(client, courier_token, "UNL1").json()["id"]
    client.post(f"/api/locations/{loc_id}/skus", json={"sku_id": sku.id},
                headers=auth_header(courier_token))

    resp = client.delete(f"/api/locations/{loc_id}/skus/{sku.id}",
                         headers=auth_header(courier_token))
    assert resp.status_code == 204

    listing = client.get("/api/locations", headers=auth_header(courier_token))
    loc = next(l for l in listing.json() if l["id"] == loc_id)
    assert loc["skus"] == []


def test_available_skus_exclude_wine_boxes(client, db, courier_token):
    org = _org(db, "avail-org")
    barcode = _barcode_sku(db, org, "SOK-AV", "8712222222222")
    _vision_sku(db, org, "WIJN-AV")

    resp = client.get("/api/locations/available-skus", headers=auth_header(courier_token))
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert barcode.id in ids
    assert all(s["sku_code"] != "WIJN-AV" for s in resp.json())


def test_owner_forbidden(client, db):
    org = _org(db, "forbid-org")
    token = _owner_token(db, org)
    assert _create(client, token, "OWN1").status_code == 403


def _bottle_sku(db, org, code="FLES-1"):
    sku = SKU(sku_code=code, name=f"Fles {code}", organization_id=org.id,
              category="wine", product_type="vision", is_bottle=True)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku


def test_link_loose_bottle(client, db, courier_token):
    """A bottle cannot be scanned, but it does stand somewhere."""
    org = _org(db, "fles-org")
    sku = _bottle_sku(db, org)
    loc_id = _create(client, courier_token, "FLES1").json()["id"]

    resp = client.post(f"/api/locations/{loc_id}/skus",
                       json={"sku_id": sku.id}, headers=auth_header(courier_token))

    assert resp.status_code == 200, resp.text
    linked = resp.json()["skus"][0]
    assert linked["sku_id"] == sku.id
    assert linked["is_bottle"] is True


def test_available_skus_include_loose_bottles(client, db, courier_token):
    org = _org(db, "fles-avail-org")
    bottle = _bottle_sku(db, org, "FLES-AV")
    _vision_sku(db, org, "DOOS-AV")

    resp = client.get("/api/locations/available-skus", headers=auth_header(courier_token))

    codes = {s["sku_code"] for s in resp.json()}
    assert "FLES-AV" in codes
    assert "DOOS-AV" not in codes


class TestBulkCreate:
    URL = "/api/locations/bulk"

    def _body(self, **overrides):
        body = {
            "rijen": ["B", "C"],
            "kasten": ["A", "B", "C", "D", "E"],
            "plank_van": 0,
            "plank_tot": 9,
            "dry_run": True,
        }
        body.update(overrides)
        return body

    def test_dry_run_counts_without_creating(self, client, db, courier_token):
        resp = client.post(self.URL, json=self._body(),
                           headers=auth_header(courier_token))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dry_run"] is True
        assert body["totaal"] == 2 * 5 * 10
        assert body["aangemaakt"] == 0
        from app.models import Location
        assert db.query(Location).count() == 0

    def test_preview_shows_the_codes_it_would_write(self, client, courier_token):
        resp = client.post(self.URL, json=self._body(),
                           headers=auth_header(courier_token))

        first = resp.json()["voorbeeld"][0]
        assert first["code"] == "B-A-00"
        assert (first["rij"], first["kast"], first["plank"]) == ("B", "A", "00")

    def test_writing_creates_every_shelf(self, client, db, courier_token):
        from app.models import Location

        resp = client.post(self.URL, json=self._body(dry_run=False),
                           headers=auth_header(courier_token))

        assert resp.status_code == 200, resp.text
        assert resp.json()["aangemaakt"] == 100
        assert db.query(Location).count() == 100
        assert db.query(Location).filter_by(code="C-E-09").one().plank == "09"

    def test_rerunning_tops_up_instead_of_failing(self, client, db, courier_token):
        from app.models import Location

        client.post(self.URL, json=self._body(plank_tot=4, dry_run=False),
                    headers=auth_header(courier_token))

        again = client.post(self.URL, json=self._body(dry_run=False),
                            headers=auth_header(courier_token))

        assert again.status_code == 200, again.text
        assert again.json()["overgeslagen"] == 50
        assert again.json()["aangemaakt"] == 50
        assert db.query(Location).count() == 100

    def test_shelf_numbers_are_padded_so_they_sort(self, client, courier_token):
        resp = client.post(
            self.URL,
            json=self._body(rijen=["B"], kasten=["A"], plank_van=8, plank_tot=11,
                            plank_cijfers=3),
            headers=auth_header(courier_token),
        )

        assert [item["code"] for item in resp.json()["voorbeeld"]] == [
            "B-A-008", "B-A-009", "B-A-010", "B-A-011",
        ]

    def test_own_code_template(self, client, courier_token):
        resp = client.post(
            self.URL,
            json=self._body(rijen=["B"], kasten=["A"], plank_van=1, plank_tot=1,
                            code_template="{rij}{kast}{plank}"),
            headers=auth_header(courier_token),
        )

        assert resp.json()["voorbeeld"][0]["code"] == "BA01"

    def test_a_template_that_collapses_the_range_is_refused(self, client, courier_token):
        """Leaving out {plank} would turn a thousand shelves into one."""
        resp = client.post(
            self.URL,
            json=self._body(code_template="{rij}-{kast}"),
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 400
        assert "dubbele codes" in resp.json()["detail"]

    def test_a_template_without_any_placeholder_is_refused(self, client, courier_token):
        resp = client.post(self.URL, json=self._body(code_template="SCHAP"),
                           headers=auth_header(courier_token))

        assert resp.status_code == 422

    def test_a_reversed_range_is_refused(self, client, courier_token):
        resp = client.post(self.URL, json=self._body(plank_van=10, plank_tot=2),
                           headers=auth_header(courier_token))

        assert resp.status_code == 422

    def test_an_absurd_rectangle_is_refused(self, client, db, courier_token):
        from app.models import Location

        resp = client.post(
            self.URL,
            json=self._body(plank_van=0, plank_tot=999, dry_run=False),
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 400
        assert "2000" in resp.json()["detail"]
        assert db.query(Location).count() == 0

    def test_owner_forbidden(self, client, db):
        org = _org(db, "bulk-forbid-org")
        token = _owner_token(db, org)

        resp = client.post(self.URL, json=self._body(), headers=auth_header(token))

        assert resp.status_code == 403


class TestLocationIsShownForWine:
    """A bottle is picked by photo, but the picker still has to walk to it."""

    def _linked_bottle(self, client, db, courier_token, code="B-A-01"):
        org = _org(db, "wijnloc-org")
        org.modules = list(org.modules) + ["vision_picking", "week_overview"]
        db.commit()
        sku = _bottle_sku(db, org, "FLES-LOC")
        loc_id = _create(client, courier_token, code).json()["id"]
        client.post(f"/api/locations/{loc_id}/skus", json={"sku_id": sku.id},
                    headers=auth_header(courier_token))
        return org, sku

    def test_order_line_carries_the_shelf(self, client, db, courier_token):
        from app.models import Order, OrderLine

        org, sku = self._linked_bottle(client, db, courier_token)
        order = Order(organization_id=org.id, reference="ORD-LOC", status="active")
        db.add(order)
        db.flush()
        db.add(OrderLine(order_id=order.id, sku_id=sku.id, klant="Klant", quantity=1))
        db.commit()

        resp = client.get(f"/api/orders/{order.id}", headers=auth_header(courier_token))

        assert resp.status_code == 200, resp.text
        assert resp.json()["lines"][0]["pick_location"] == "B-A-01"

    def test_week_photos_carry_the_shelf(self, client, db, courier_token):
        from app.models import Order, OrderLine, ReferenceImage

        org, sku = self._linked_bottle(client, db, courier_token, code="B-A-02")
        db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
        order = Order(organization_id=org.id, reference="ORD-LOC-WK", status="active")
        db.add(order)
        db.flush()
        db.add(OrderLine(order_id=order.id, sku_id=sku.id, klant="Klant", quantity=2))
        db.commit()

        resp = client.get("/api/orders/weekly-pick-photos", headers=auth_header(courier_token))

        assert resp.status_code == 200, resp.text
        item = next(i for i in resp.json() if i["sku_id"] == sku.id)
        assert item["pick_location"] == "B-A-02"

    def test_next_pick_carries_the_shelf(self, client, db, courier_token):
        """The panel the picker walks off is where the shelf matters most."""
        from app.models import Order, OrderLine, ReferenceImage

        org, sku = self._linked_bottle(client, db, courier_token, code="B-A-03")
        db.add(ReferenceImage(sku_id=sku.id, image_path="f.jpg", processing_status="done"))
        order = Order(organization_id=org.id, reference="ORD-NEXT", status="active")
        db.add(order)
        db.flush()
        db.add(OrderLine(order_id=order.id, sku_id=sku.id, klant="Klant", quantity=3))
        db.commit()

        resp = client.get(
            f"/api/orders/{order.id}/next-pick",
            params={"scan_mode": "bottle"},
            headers=auth_header(courier_token),
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["pick_location"] == "B-A-03"

    def test_a_wine_box_has_no_shelf(self, client, db, courier_token):
        """A box is matched per order, so it never reports a fixed spot."""
        from app.models import Order, OrderLine

        org = _org(db, "doosloc-org")
        org.modules = list(org.modules) + ["vision_picking"]
        db.commit()
        box = _vision_sku(db, org, "DOOS-LOC")
        order = Order(organization_id=org.id, reference="ORD-DOOS", status="active")
        db.add(order)
        db.flush()
        db.add(OrderLine(order_id=order.id, sku_id=box.id, klant="Klant", quantity=1))
        db.commit()

        resp = client.get(f"/api/orders/{order.id}", headers=auth_header(courier_token))

        assert resp.json()["lines"][0]["pick_location"] is None
