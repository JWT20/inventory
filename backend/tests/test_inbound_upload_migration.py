import importlib.util
from pathlib import Path


def test_reconciliation_excludes_shipments_already_linked_to_an_attempt(monkeypatch):
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "049_reconcile_inbound_upload_history.py"
    )
    spec = importlib.util.spec_from_file_location("migration_049", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert len(statements) == 1
    sql = str(statements[0])
    assert "NOT EXISTS" in sql
    assert "linked.shipment_id = s.id" in sql
