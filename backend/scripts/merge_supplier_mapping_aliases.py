"""Merge supplier→SKU mappings that were learned under two spellings of one supplier.

Inbound remembers a mapping per (supplier_name, supplier_code). The name comes
from the pakbon itself, so one supplier that writes "Anfors" on one document and
"Anfors-Imperial" on the next ends up with two separate memories. Codes learned
under the old spelling then stop auto-matching, and a code that exists under
both spellings pointing at different SKUs blocks the unique-code fallback as
well — every such line has to be linked by hand again.

This script folds an alias into the canonical name for one organization:

  A. code only under the alias        → renamed to the canonical name
  B. code under both, same SKU        → alias row deleted (redundant)
  C. code under both, different SKU   → left alone and reported; it needs a
                                        human decision, resolved by passing
                                        --keep CODE=canonical|alias

Dry run by default: it prints what it would do and changes nothing. Add --apply
to commit. Idempotent — re-running after an --apply is a no-op.

Usage (inside the backend container, or from backend/ with deps installed):

    python -m scripts.merge_supplier_mapping_aliases \
        --organization-id 1 --alias "Anfors" --canonical "Anfors-Imperial"

    python -m scripts.merge_supplier_mapping_aliases \
        --organization-id 1 --alias "Anfors" --canonical "Anfors-Imperial" \
        --keep AFF300125=alias --keep AFI090325=canonical --apply
"""
import argparse
import sys

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SKU, SupplierSKUMapping


def _normalize_name(value: str) -> str:
    """Same normalization the inbound router applies before storing a mapping."""
    return " ".join((value or "").strip().split()).upper()


def _normalize_code(value: str) -> str:
    return (value or "").strip().upper()


def _describe(db: Session, sku_id: int) -> str:
    sku = db.get(SKU, sku_id)
    if not sku:
        return f"SKU {sku_id} (bestaat niet meer)"
    unit = "fles" if sku.is_bottle else "doos"
    state = "" if sku.active else ", inactief"
    return f"{sku.sku_code} — {sku.name} ({unit}{state})"


def _parse_keep(values: list[str]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for raw in values:
        code, _, side = raw.partition("=")
        side = side.strip().lower()
        if side not in ("canonical", "alias"):
            raise SystemExit(
                f"--keep {raw!r}: kies 'canonical' of 'alias', bijv. --keep AFF300125=alias"
            )
        decisions[_normalize_code(code)] = side
    return decisions


def merge(
    db: Session,
    *,
    organization_id: int,
    alias: str,
    canonical: str,
    keep: dict[str, str],
    apply_changes: bool,
) -> int:
    """Return the number of conflicts left undecided."""
    alias_name = _normalize_name(alias)
    canonical_name = _normalize_name(canonical)
    if not alias_name or not canonical_name:
        raise SystemExit("Zowel --alias als --canonical moeten een naam bevatten.")
    if alias_name == canonical_name:
        raise SystemExit("--alias en --canonical zijn dezelfde naam na normalisatie.")

    rows = (
        db.query(SupplierSKUMapping)
        .filter(
            SupplierSKUMapping.organization_id == organization_id,
            SupplierSKUMapping.supplier_name.in_([alias_name, canonical_name]),
        )
        .all()
    )
    alias_rows = {r.supplier_code: r for r in rows if r.supplier_name == alias_name}
    canonical_rows = {r.supplier_code: r for r in rows if r.supplier_name == canonical_name}

    print(f"Organisatie {organization_id}: {alias_name} ({len(alias_rows)}) → "
          f"{canonical_name} ({len(canonical_rows)})\n")

    renamed: list[str] = []
    removed: list[str] = []
    resolved: list[str] = []
    conflicts: list[str] = []

    for code, alias_row in sorted(alias_rows.items()):
        canonical_row = canonical_rows.get(code)

        if canonical_row is None:
            renamed.append(f"  {code}  → {_describe(db, alias_row.sku_id)}")
            if apply_changes:
                alias_row.supplier_name = canonical_name
            continue

        if canonical_row.sku_id == alias_row.sku_id:
            removed.append(f"  {code}  ({_describe(db, alias_row.sku_id)})")
            if apply_changes:
                db.delete(alias_row)
            continue

        decision = keep.get(code)
        if decision is None:
            conflicts.append(
                f"  {code}\n"
                f"      canonical: {_describe(db, canonical_row.sku_id)}\n"
                f"      alias:     {_describe(db, alias_row.sku_id)}"
            )
            continue

        winner_id = canonical_row.sku_id if decision == "canonical" else alias_row.sku_id
        resolved.append(f"  {code}  → {_describe(db, winner_id)}  (gekozen: {decision})")
        if apply_changes:
            canonical_row.sku_id = winner_id
            db.delete(alias_row)

    def report(title: str, entries: list[str]) -> None:
        print(f"{title}: {len(entries)}")
        for entry in entries:
            print(entry)
        print()

    report("A. Hernoemen naar canonieke naam", renamed)
    report("B. Dubbel, zelfde product — aliasregel verwijderen", removed)
    if resolved:
        report("C. Conflict, opgelost via --keep", resolved)
    report("C. Conflict, keuze nodig (overgeslagen)", conflicts)

    if apply_changes:
        db.commit()
        print("Toegepast.")
    else:
        db.rollback()
        print("Dry run — niets gewijzigd. Voeg --apply toe om door te voeren.")

    return len(conflicts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=int, required=True)
    parser.add_argument("--alias", required=True, help="De naam die verdwijnt, bijv. 'Anfors'")
    parser.add_argument(
        "--canonical", required=True, help="De naam die blijft, bijv. 'Anfors-Imperial'"
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="CODE=canonical|alias",
        help="Beslissing voor een conflicterende code; herhaalbaar.",
    )
    parser.add_argument("--apply", action="store_true", help="Wijzigingen echt doorvoeren")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        remaining = merge(
            db,
            organization_id=args.organization_id,
            alias=args.alias,
            canonical=args.canonical,
            keep=_parse_keep(args.keep),
            apply_changes=args.apply,
        )
    finally:
        db.close()

    # Conflicts left undecided mean the merge is incomplete: those codes keep
    # blocking auto-matching until someone chooses.
    sys.exit(1 if remaining else 0)


if __name__ == "__main__":
    main()
