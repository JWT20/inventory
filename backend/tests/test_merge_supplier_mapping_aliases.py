"""Folding a second spelling of one supplier back into a single mapping memory."""

import pytest

from app.models import SKU, SupplierSKUMapping
from scripts.merge_supplier_mapping_aliases import merge


def _sku(db, org, code: str, *, is_bottle: bool = False) -> SKU:
    sku = SKU(sku_code=code, name=f"Wijn {code}", organization_id=org.id, is_bottle=is_bottle)
    db.add(sku)
    db.flush()
    return sku


def _mapping(db, org, name: str, code: str, sku: SKU) -> SupplierSKUMapping:
    mapping = SupplierSKUMapping(
        organization_id=org.id, supplier_name=name, supplier_code=code, sku_id=sku.id
    )
    db.add(mapping)
    db.flush()
    return mapping


def _names(db, org) -> dict[tuple[str, int], str]:
    return {
        (m.supplier_code, m.sku_id): m.supplier_name
        for m in db.query(SupplierSKUMapping).filter_by(organization_id=org.id)
    }


@pytest.fixture
def anfors(db, sample_org):
    """Alias-only code, a duplicate, and a code that points at two products."""
    doos = _sku(db, sample_org, "ALLEEN-ALIAS")
    shared = _sku(db, sample_org, "GEDEELD")
    oud = _sku(db, sample_org, "OUD-DOOS")
    nieuw = _sku(db, sample_org, "NIEUW-FLES", is_bottle=True)

    _mapping(db, sample_org, "ANFORS", "AAA111", doos)
    _mapping(db, sample_org, "ANFORS", "BBB222", shared)
    _mapping(db, sample_org, "ANFORS-IMPERIAL", "BBB222", shared)
    _mapping(db, sample_org, "ANFORS", "CCC333", oud)
    _mapping(db, sample_org, "ANFORS-IMPERIAL", "CCC333", nieuw)
    db.commit()
    return {"oud": oud, "nieuw": nieuw}


def test_dry_run_changes_nothing_and_reports_the_pair(db, sample_org, anfors):
    multi = merge(
        db,
        organization_id=sample_org.id,
        alias="Anfors",
        canonical="Anfors-Imperial",
        keep={},
        apply_changes=False,
    )

    assert multi == 1
    assert set(_names(db, sample_org).values()) == {"ANFORS", "ANFORS-IMPERIAL"}
    assert db.query(SupplierSKUMapping).count() == 5


def test_apply_renames_alias_only_codes_and_drops_redundant_rows(db, sample_org, anfors):
    merge(
        db,
        organization_id=sample_org.id,
        alias="Anfors",
        canonical="Anfors-Imperial",
        keep={},
        apply_changes=True,
    )

    rows = db.query(SupplierSKUMapping).filter_by(organization_id=sample_org.id).all()
    by_code = {(r.supplier_code, r.supplier_name) for r in rows}
    # Alias-only code moved over; the duplicate collapsed to one row.
    assert ("AAA111", "ANFORS-IMPERIAL") in by_code
    assert ("BBB222", "ANFORS-IMPERIAL") in by_code
    assert ("BBB222", "ANFORS") not in by_code
    # One code carrying a case and a bottle product is legitimate: both move to
    # the canonical name and inbound offers them as a choice.
    assert ("CCC333", "ANFORS") not in by_code
    ccc = [r for r in rows if r.supplier_code == "CCC333"]
    assert {r.sku_id for r in ccc} == {anfors["oud"].id, anfors["nieuw"].id}
    assert {r.supplier_name for r in ccc} == {"ANFORS-IMPERIAL"}


def test_keep_alias_prunes_a_link_that_is_genuinely_wrong(db, sample_org, anfors):
    multi = merge(
        db,
        organization_id=sample_org.id,
        alias="Anfors",
        canonical="Anfors-Imperial",
        keep={"CCC333": "alias"},
        apply_changes=True,
    )

    assert multi == 0
    remaining = (
        db.query(SupplierSKUMapping)
        .filter_by(organization_id=sample_org.id, supplier_code="CCC333")
        .all()
    )
    assert len(remaining) == 1
    assert remaining[0].supplier_name == "ANFORS-IMPERIAL"
    assert remaining[0].sku_id == anfors["oud"].id


def test_rerunning_after_apply_is_a_no_op(db, sample_org, anfors):
    merge(
        db,
        organization_id=sample_org.id,
        alias="Anfors",
        canonical="Anfors-Imperial",
        keep={"CCC333": "canonical"},
        apply_changes=True,
    )
    before = _names(db, sample_org)

    multi = merge(
        db,
        organization_id=sample_org.id,
        alias="Anfors",
        canonical="Anfors-Imperial",
        keep={},
        apply_changes=True,
    )

    assert multi == 0
    assert _names(db, sample_org) == before
