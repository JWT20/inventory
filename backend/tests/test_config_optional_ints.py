"""An optional setting that is not configured must not stop the process."""

import pytest
from pydantic import ValidationError

from app.config import Settings


BASE = {
    "database_url": "postgresql://test:test@localhost:5432/test",
    "secret_key": "test-secret-key-not-for-production",
    "admin_password": "test-admin-password",
}


@pytest.mark.parametrize(
    "field", ["veloyd_legacy_organization_id", "advice_stock_organization_id"]
)
def test_a_blank_value_reads_as_unset(field):
    """docker-compose substitutes "" for a variable that is simply not set."""
    settings = Settings(**BASE, **{field: ""})

    assert getattr(settings, field) is None


@pytest.mark.parametrize(
    "field", ["veloyd_legacy_organization_id", "advice_stock_organization_id"]
)
def test_a_configured_value_still_arrives(field):
    settings = Settings(**BASE, **{field: "2"})

    assert getattr(settings, field) == 2


def test_nonsense_is_still_refused():
    """Blank means unset; anything else must still fail loudly."""
    with pytest.raises(ValidationError):
        Settings(**BASE, veloyd_legacy_organization_id="organisatie twee")
