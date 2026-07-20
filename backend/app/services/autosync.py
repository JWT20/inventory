"""Background auto-sync poller for live channel connections.

The manual "Nu synchroniseren" button pulls orders on demand; this loop does the
same on a fixed interval so orders land in the pick list without an operator
clicking. It only touches connections in ``live`` mode — observe-mode
connections are never acted on by surprise.

Enabled via ``settings.shopify_autosync_interval_seconds`` (0 = disabled). The
loop is best-effort: one connection's failure never stops the others, and an
iteration error never kills the loop. It runs in the single API process, so a
multi-replica deploy would sync N times per interval — switch to webhooks (or a
dedicated worker) before scaling out.
"""
import asyncio
import logging

from app.config import settings
from app.database import SessionLocal
from app.models import ChannelConnection
from app.services.bol import BolClient, sync_bol
from app.services.channel_credentials import get_access_token
from app.services.inventory_sync import push_inventory_to_channels
from app.services.shopify import ShopifyClient, sync_shopify

logger = logging.getLogger(__name__)


def _sync_live_connections_once() -> None:
    """Pull + import for every live channel connection. Blocking; best-effort."""
    db = SessionLocal()
    try:
        connections = (
            db.query(ChannelConnection)
            .filter(ChannelConnection.mode == "live")
            .all()
        )
        for conn in connections:
            org_id = conn.organization_id
            try:
                if conn.channel == "shopify":
                    client = ShopifyClient(
                        shop_domain=conn.shop_domain,
                        access_token=get_access_token(conn),
                    )
                    if not client.configured:
                        continue
                    summary = sync_shopify(db, conn, client)
                elif conn.channel == "bol":
                    client = BolClient()
                    if not client.configured:
                        continue
                    summary = sync_bol(db, conn, client)
                else:
                    continue
                db.commit()
                for sku_id in summary.reserved_sku_ids:
                    # Self-contained (own session) and never raises.
                    push_inventory_to_channels(sku_id, org_id)
            except Exception:  # one bad connection must not stop the rest
                db.rollback()
                logger.exception(
                    "autosync: sync failed for connection %s (org %s)",
                    conn.id,
                    org_id,
                )
    finally:
        db.close()


async def autosync_loop(interval_seconds: int) -> None:
    """Run :func:`_sync_live_connections_once` every ``interval_seconds``.

    The blocking sync runs in a worker thread so it never stalls the event loop.
    Cancel the task (on shutdown) to stop the loop cleanly.
    """
    logger.info("Channel autosync started (interval=%ss)", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(_sync_live_connections_once)
            except Exception:  # never let one iteration kill the loop
                logger.exception("autosync: iteration failed")
    except asyncio.CancelledError:
        logger.info("Channel autosync stopped")
        raise
