"""Read-only bol Retailer API adapter for Admin order imports.

The first integration deliberately uses one credential pair from the runtime
environment. Connecting binds that account to one organization. Access tokens
are short-lived and cached in memory; neither credentials nor bearer tokens are
stored in PostgreSQL or written to logs.
"""
from __future__ import annotations

import datetime
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChannelConnection
from app.services.channel_import import (
    NormalizedChannelOrder,
    NormalizedLine,
    import_channel_order,
)


ACCEPT_V10 = "application/vnd.retailer.v10+json"
USER_AGENT = "Wijnpick/1.0"
_TOKEN_EXPIRY_SAFETY_SECONDS = 30
_MAX_ORDER_PAGES = 100
_HTTP_TIMEOUT_SECONDS = 20.0
_SHIPMENT_CURSOR_OVERLAP = datetime.timedelta(minutes=5)
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()


class BolError(RuntimeError):
    """Base error safe to translate to an operator-facing API response."""


class BolConfigurationError(BolError):
    pass


class BolAuthenticationError(BolError):
    pass


class BolAPIError(BolError):
    pass


@dataclass
class BolSyncSummary:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unmatched: int = 0
    reserved_sku_ids: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class BolOrderUpdate:
    payload: dict
    shipped_at: datetime.datetime | None = None


@dataclass(frozen=True)
class BolOrderBatch:
    orders: list[BolOrderUpdate]
    newest_shipment_at: datetime.datetime | None = None


def clear_token_cache() -> None:
    """Clear process-local bearer tokens (primarily useful for tests/reconnect)."""
    with _token_lock:
        _token_cache.clear()


class BolClient:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_url: str | None = None,
        api_base_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id if client_id is not None else settings.bol_client_id
        self.client_secret = (
            client_secret if client_secret is not None else settings.bol_client_secret
        )
        self.token_url = (token_url or settings.bol_token_url).rstrip("/")
        self.api_base_url = (api_base_url or settings.bol_api_base_url).rstrip("/")
        # Tests may inject a client. Production uses httpx's one-shot helpers so
        # every request owns and closes its resources instead of leaving a
        # client/connection pool behind for every BolClient instance.
        self._http = http_client

    @property
    def configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.token_url
            and self.api_base_url
        )

    def _access_token(self, *, force_refresh: bool = False) -> str:
        if not self.configured:
            raise BolConfigurationError("bol API-credentials ontbreken in de serveromgeving")

        with _token_lock:
            cached = _token_cache.get(self.client_id)
            if not force_refresh and cached and cached[1] > time.monotonic():
                return cached[0]

        # Never keep the cache lock held across network I/O. Concurrent cache
        # misses may briefly request two tokens; that is safer than serialising
        # every worker behind a potentially slow 20-second HTTP request.
        try:
            request = self._http.post if self._http is not None else httpx.post
            kwargs = {
                "data": {"grant_type": "client_credentials"},
                "auth": (self.client_id, self.client_secret),
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
            }
            if self._http is None:
                kwargs["timeout"] = _HTTP_TIMEOUT_SECONDS
            response = request(self.token_url, **kwargs)
        except httpx.HTTPError as exc:
            raise BolAPIError("bol authenticatieservice is tijdelijk niet bereikbaar") from exc

        if response.status_code in (401, 403):
            raise BolAuthenticationError("bol Client ID of Client secret is ongeldig")
        if response.status_code >= 400:
            raise BolAPIError(
                f"bol authenticatieservice gaf HTTP {response.status_code}"
            )
        try:
            payload = response.json()
            token = str(payload["access_token"])
            expires_in = max(1, int(payload.get("expires_in", 300)))
        except (KeyError, TypeError, ValueError) as exc:
            raise BolAPIError("bol gaf een ongeldig authenticatieantwoord") from exc
        if not token:
            raise BolAPIError("bol gaf geen access token terug")

        usable_for = max(1, expires_in - _TOKEN_EXPIRY_SAFETY_SECONDS)
        with _token_lock:
            _token_cache[self.client_id] = (
                token,
                time.monotonic() + usable_for,
            )
        return token

    def validate_credentials(self) -> None:
        """Authenticate without performing any Retailer API write."""
        self._access_token(force_refresh=True)

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        for attempt in range(2):
            token = self._access_token(force_refresh=attempt == 1)
            try:
                request = self._http.get if self._http is not None else httpx.get
                kwargs = {
                    "params": params,
                    "headers": {
                        "Accept": ACCEPT_V10,
                        "Authorization": f"Bearer {token}",
                        "User-Agent": USER_AGENT,
                    },
                }
                if self._http is None:
                    kwargs["timeout"] = _HTTP_TIMEOUT_SECONDS
                response = request(
                    f"{self.api_base_url}{path}",
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise BolAPIError("bol Retailer API is tijdelijk niet bereikbaar") from exc

            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in (401, 403):
                raise BolAuthenticationError("bol heeft de API-toegang geweigerd")
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                suffix = f"; probeer over {retry_after} seconden opnieuw" if retry_after else ""
                raise BolAPIError(f"bol rate limit bereikt{suffix}")
            if response.status_code >= 500:
                raise BolAPIError("bol Retailer API is tijdelijk niet beschikbaar")
            if response.status_code >= 400:
                raise BolAPIError(f"bol Retailer API gaf HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise BolAPIError("bol gaf een ongeldig API-antwoord") from exc
            if not isinstance(payload, dict):
                raise BolAPIError("bol gaf een onverwacht API-antwoord")
            return payload
        raise BolAuthenticationError("bol heeft de API-toegang geweigerd")

    def _fetch_order_ids(self, *, status: str):
        for page in range(1, _MAX_ORDER_PAGES + 1):
            payload = self._get(
                "/orders",
                params={"fulfilment-method": "FBR", "status": status, "page": page},
            )
            orders = payload.get("orders") or []
            if not isinstance(orders, list):
                raise BolAPIError("bol gaf een ongeldige orderlijst")
            if not orders:
                return
            for summary in orders:
                order_id = str((summary or {}).get("orderId") or "").strip()
                if not order_id:
                    raise BolAPIError("bol-order mist een orderId")
                yield order_id
        raise BolAPIError(
            f"bol-orderlijst overschrijdt de veiligheidslimiet van {_MAX_ORDER_PAGES} pagina's"
        )

    def fetch_open_orders(self):
        """Yield full open FBR order payloads, including VVB orders."""
        for order_id in self._fetch_order_ids(status="OPEN"):
            yield self._get(f"/orders/{quote(order_id, safe='')}")

    def _fetch_recent_shipments(
        self, *, since: datetime.datetime | None
    ) -> tuple[dict[str, datetime.datetime], datetime.datetime | None]:
        """Return recently shipped FBR order ids and their latest shipment time.

        The first call has no cursor and backfills the API's three-month shipment
        window. Later calls stop once they cross a small overlap before the last
        seen shipment, avoiding both full rescans and timestamp-boundary misses.
        """
        cutoff = _as_utc(since) - _SHIPMENT_CURSOR_OVERLAP if since else None
        shipped_at_by_order: dict[str, datetime.datetime] = {}
        newest: datetime.datetime | None = None

        for page in range(1, _MAX_ORDER_PAGES + 1):
            payload = self._get(
                "/shipments",
                params={"fulfilment-method": "FBR", "page": page},
            )
            shipments = payload.get("shipments") or []
            if not isinstance(shipments, list):
                raise BolAPIError("bol gaf een ongeldige verzendlijst")
            if not shipments:
                return shipped_at_by_order, newest

            reached_cursor = False
            for shipment in shipments:
                order_id = str(
                    ((shipment or {}).get("order") or {}).get("orderId") or ""
                ).strip()
                shipped_at = _parse_datetime((shipment or {}).get("shipmentDateTime"))
                if not order_id or shipped_at is None:
                    raise BolAPIError("bol-zending mist orderId of shipmentDateTime")
                shipped_at = _as_utc(shipped_at)
                if newest is None or shipped_at > newest:
                    newest = shipped_at
                if cutoff is not None and shipped_at < cutoff:
                    reached_cursor = True
                    continue
                previous = shipped_at_by_order.get(order_id)
                if previous is None or shipped_at > previous:
                    shipped_at_by_order[order_id] = shipped_at

            if reached_cursor:
                return shipped_at_by_order, newest

        raise BolAPIError(
            f"bol-verzendlijst overschrijdt de veiligheidslimiet van {_MAX_ORDER_PAGES} pagina's"
        )

    def fetch_order_updates(
        self, *, shipped_since: datetime.datetime | None = None
    ) -> BolOrderBatch:
        """Fetch open orders plus new/recent shipment history as one order set."""
        shipment_dates, newest_shipment_at = self._fetch_recent_shipments(
            since=shipped_since
        )
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for order_id in self._fetch_order_ids(status="OPEN"):
            if order_id not in seen:
                seen.add(order_id)
                ordered_ids.append(order_id)
        for order_id in shipment_dates:
            if order_id not in seen:
                seen.add(order_id)
                ordered_ids.append(order_id)

        return BolOrderBatch(
            orders=[
                BolOrderUpdate(
                    payload=self._get(f"/orders/{quote(order_id, safe='')}"),
                    shipped_at=shipment_dates.get(order_id),
                )
                for order_id in ordered_ids
            ],
            newest_shipment_at=newest_shipment_at,
        )


def _as_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def to_normalized(
    payload: dict, *, shipped_at: datetime.datetime | None = None
) -> NormalizedChannelOrder:
    """Map one full bol v10 order to the shared channel order shape."""
    order_id = str(payload.get("orderId") or "").strip()
    if not order_id:
        raise BolAPIError("bol-order mist een orderId")

    details = payload.get("shipmentDetails") or {}
    customer_name = " ".join(
        part.strip()
        for part in (details.get("firstName") or "", details.get("surname") or "")
        if part and part.strip()
    ) or (details.get("company") or None)

    lines: list[NormalizedLine] = []
    shipped_total = 0
    current_total = 0
    cancelled_total = 0
    latest_change: str | None = None
    for item in payload.get("orderItems") or []:
        product = item.get("product") or {}
        quantity = _as_int(item.get("quantity"))
        shipped = min(quantity, _as_int(item.get("quantityShipped")))
        cancelled = min(quantity - shipped, _as_int(item.get("quantityCancelled")))
        current = max(0, quantity - cancelled)
        unfulfilled = max(0, current - shipped)
        shipped_total += shipped
        cancelled_total += cancelled
        current_total += current
        changed = item.get("latestChangedDateTime")
        if changed and (latest_change is None or changed > latest_change):
            latest_change = changed
        lines.append(
            NormalizedLine(
                ean=str(product.get("ean") or "").strip() or None,
                quantity=current,
                title=str(product.get("title") or ""),
                external_id=str(item.get("orderItemId") or "").strip() or None,
                unfulfilled_quantity=unfulfilled,
            )
        )

    if current_total == 0 and cancelled_total > 0:
        financial_status = "cancelled"
        fulfillment_status = "unfulfilled"
        cancelled_at = latest_change or payload.get("orderPlacedDateTime") or "cancelled"
    else:
        financial_status = "paid"
        cancelled_at = None
        if current_total > 0 and shipped_total >= current_total:
            fulfillment_status = "fulfilled"
        elif shipped_total > 0:
            fulfillment_status = "partially_fulfilled"
        else:
            fulfillment_status = "unfulfilled"

    return NormalizedChannelOrder(
        external_id=order_id,
        reference=order_id,
        ordered_at=_parse_datetime(payload.get("orderPlacedDateTime")),
        customer_name=customer_name,
        financial_status=financial_status,
        fulfillment_status=fulfillment_status,
        shipped_at=shipped_at,
        cancelled_at=cancelled_at,
        lines=lines,
    )


def sync_bol(
    db: Session, connection: ChannelConnection, client: BolClient | None = None
) -> BolSyncSummary:
    """Import open orders and recent FBR/VVB shipment history idempotently."""
    db.flush()
    connection = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.id == connection.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if connection.channel != "bol":
        raise BolConfigurationError("Verbinding is geen bol-kanaal")
    if connection.mode != "observe":
        raise BolConfigurationError("bol-koppeling moet in observe-modus staan")

    client = client or BolClient()
    summary = BolSyncSummary()
    shipped_since = _parse_datetime(connection.cursor)
    batch = client.fetch_order_updates(shipped_since=shipped_since)
    for update in batch.orders:
        result = import_channel_order(
            db,
            connection,
            to_normalized(update.payload, shipped_at=update.shipped_at),
        )
        summary.fetched += 1
        if result.created:
            summary.created += 1
        else:
            summary.updated += 1
        summary.unmatched += len(result.unmatched_eans)
        summary.reserved_sku_ids.update(result.reserved_sku_ids)

    if batch.newest_shipment_at is not None and (
        shipped_since is None
        or _as_utc(batch.newest_shipment_at) > _as_utc(shipped_since)
    ):
        connection.cursor = batch.newest_shipment_at.isoformat()

    connection.last_synced_at = datetime.datetime.utcnow()
    db.flush()
    return summary
