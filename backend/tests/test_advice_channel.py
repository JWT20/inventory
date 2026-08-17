"""The advice app as an observe-mode channel connection."""

import pytest

from app.models import ChannelConnection, Organization
from app.services.advice_channel import (
    ADVICE_CHANNEL,
    AdviceChannelNotObserving,
    advice_connection,
    assert_advice_observing,
)
from app.services.inventory_sync import (
    _live_bol_connection,
    _live_shopify_connection,
)


def _org(db, name: str = "Wijn van Jurjen", slug: str = "wijn-van-jurjen") -> Organization:
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.commit()
    return org


def test_first_use_creates_an_observing_connection(db):
    org = _org(db)

    connection = advice_connection(db, org.id)

    assert connection.channel == ADVICE_CHANNEL
    assert connection.mode == "observe"
    assert connection.status == "active"
    # No credentials: the advice app authenticates per request with its own key.
    assert connection.access_token_encrypted is None
    assert connection.shop_domain is None


def test_second_use_returns_the_same_connection(db):
    org = _org(db)

    first = advice_connection(db, org.id)
    db.commit()
    second = advice_connection(db, org.id)

    assert second.id == first.id
    assert db.query(ChannelConnection).count() == 1


def test_each_organization_gets_its_own_connection(db):
    one = _org(db)
    other = _org(db, name="Andere winkel", slug="andere-winkel")

    first = advice_connection(db, one.id)
    second = advice_connection(db, other.id)
    db.commit()

    assert first.id != second.id


def test_importing_refuses_while_the_connection_claims_to_be_live(db):
    org = _org(db)
    connection = advice_connection(db, org.id)

    assert_advice_observing(connection)  # observe is the whole point

    connection.mode = "live"
    with pytest.raises(AdviceChannelNotObserving):
        assert_advice_observing(connection)


def test_the_stock_push_never_mistakes_advice_for_a_webshop(db):
    """Even a hand-edited live row must stay out of the Shopify/bol stock push."""
    org = _org(db)
    connection = advice_connection(db, org.id)
    connection.mode = "live"
    db.commit()

    assert _live_shopify_connection(db, org.id) is None
    assert _live_bol_connection(db, org.id) is None
