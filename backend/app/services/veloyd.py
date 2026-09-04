"""Talk to the Veloyd account of one organization.

Veloyd runs at the carrier, who keeps a client account per merchant: its own
sender address, its own tariffs, its own invoice. Dockscan serves several of
those merchants from one process, so which account a call goes to is part of
the call, never a process-wide setting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CarrierConnection, User
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_carrier_api_key,
    has_carrier_api_key,
)

#: The only carrier today. Stored on ``CarrierConnection.carrier``.
VELOYD_CARRIER = "veloyd"


class VeloydError(RuntimeError):
    """Veloyd could not verify the scanned label."""


class VeloydLabelMismatch(VeloydError):
    """The label is valid, but belongs to another order."""


class VeloydNotConnected(VeloydError):
    """This organization has no Veloyd account of its own."""


class VeloydParcelPrinted(VeloydError):
    """The carrier already printed this label, so Veloyd will not drop it."""


@dataclass(frozen=True)
class VeloydLabel:
    reference: str
    tracking_number: str
    tracking_url: str | None = None
    carrier: str | None = None
    # Veloyd's own parcel id. For a parcel Dockscan created it identifies the
    # exact box, which the order number cannot do once an order ships in two.
    parcel_id: str | None = None

    @property
    def shopify_tracking_info(self) -> dict[str, str]:
        info = {"number": self.tracking_number}
        if self.tracking_url:
            info["url"] = self.tracking_url
        if self.carrier:
            info["company"] = self.carrier
        return info


class VeloydClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.veloyd_api_key
        self.base_url = (base_url or settings.veloyd_api_base_url).rstrip("/")

    def parcel_by_tracking_number(self, tracking_number: str) -> VeloydLabel:
        if not self.api_key:
            raise VeloydError("Veloyd API is niet geconfigureerd")

        # Veloyd matches the track-and-trace value case-sensitively: the same
        # code in lowercase comes back as "not found". Scanners deliver upper
        # case, a typed-in code does not.
        encoded = quote(tracking_number.strip().upper(), safe="")
        try:
            response = httpx.get(
                f"{self.base_url}/parcel/get/tracktrace/{encoded}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd is tijdelijk niet bereikbaar") from exc

        if response.status_code == 401:
            raise VeloydError("Veloyd API-sleutel is ongeldig of nog niet geactiveerd")
        if response.status_code == 404 or (
            response.status_code == 400
            and _is_missing_parcel_response(response)
        ):
            raise VeloydLabelMismatch("Label is niet bekend bij Veloyd")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd kon het label niet controleren") from exc

        parcel = (response.json() or {}).get("parcel") or {}
        reference = str(parcel.get("reference") or "").strip().lstrip("#").strip()
        returned_tracking = str(parcel.get("trackTrace") or tracking_number).strip()
        if not reference or not returned_tracking:
            raise VeloydError("Veloyd gaf een onvolledig label terug")

        return VeloydLabel(
            reference=reference,
            tracking_number=returned_tracking,
            tracking_url=parcel.get("trackTraceLink") or None,
            carrier=parcel.get("carrier") or None,
            parcel_id=str(parcel.get("id") or "").strip() or None,
        )

    def create_parcel(
        self,
        *,
        address: dict[str, str],
        reference: str,
        options: list[str] | None = None,
        comment: str = "",
    ) -> str:
        """Register one box at the carrier and return Veloyd's parcel id.

        Deliberately does not fetch the label. Veloyd hands out the
        track-and-trace value on ``parcel/label``, and that same call moves the
        parcel to a state it refuses to remove — so as long as the carrier
        prints, a parcel created here stays cancellable.
        """
        if not self.api_key:
            raise VeloydError("Veloyd API is niet geconfigureerd")

        payload = {
            "parcel": {
                "address": address,
                "options": options or [],
                "reference": reference,
                "comment": comment,
            }
        }
        try:
            response = httpx.post(
                f"{self.base_url}/parcel/create",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd is tijdelijk niet bereikbaar") from exc

        if response.status_code in (401, 403):
            raise VeloydError("Veloyd API-sleutel is ongeldig of nog niet geactiveerd")
        if response.status_code == 400:
            # Veloyd names the offending field; passing it on saves the operator
            # a round trip through the logs.
            raise VeloydError(
                f"Veloyd weigerde de zending: {_error_description(response)}"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd kon de zending niet aanmaken") from exc

        parcel_id = str((response.json() or {}).get("id") or "").strip()
        if not parcel_id:
            raise VeloydError("Veloyd gaf geen zendingnummer terug")
        return parcel_id

    def remove_parcel(self, parcel_id: str) -> None:
        """Drop a parcel the carrier has not printed yet."""
        if not self.api_key:
            raise VeloydError("Veloyd API is niet geconfigureerd")
        try:
            response = httpx.get(
                f"{self.base_url}/parcel/remove/{quote(parcel_id, safe='')}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd is tijdelijk niet bereikbaar") from exc

        if response.status_code == 403:
            raise VeloydParcelPrinted(
                "Veloyd heeft dit label al gedrukt; annuleren kan alleen bij de vervoerder"
            )
        if response.status_code == 401:
            raise VeloydError("Veloyd API-sleutel is ongeldig of nog niet geactiveerd")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd kon de zending niet verwijderen") from exc

    def validate_credentials(self) -> None:
        """Prove the key is accepted, without creating anything.

        ``parcel/options`` is the only endpoint that answers for an account as
        a whole: it needs an address but changes nothing, and an unknown key
        comes back 401 the same way every other endpoint does.
        """
        if not self.api_key:
            raise VeloydError("Veloyd API is niet geconfigureerd")
        probe = {
            "parcel": {
                "address": {
                    "name": "Dockscan",
                    "street": "Teststraat",
                    "nr": "1",
                    "postalCode": "9711AA",
                    "city": "Groningen",
                    "country": "NL",
                }
            }
        }
        try:
            response = httpx.post(
                f"{self.base_url}/parcel/options",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=probe,
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd is tijdelijk niet bereikbaar") from exc
        if response.status_code in (401, 403):
            raise VeloydError("Veloyd API-sleutel is ongeldig of nog niet geactiveerd")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VeloydError("Veloyd kon de sleutel niet controleren") from exc



def normalize_tracking_code(value: str) -> str:
    """Stable key for scanner, Veloyd and webhook variants of one barcode.

    The same physical code arrives spaced, hyphenated or in lower case
    depending on who reports it, so everything that stores or matches one
    reduces it here first.
    """
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def carrier_connection(
    db: Session, organization_id: int | None
) -> CarrierConnection | None:
    if not organization_id:
        return None
    return (
        db.query(CarrierConnection)
        .filter(
            CarrierConnection.organization_id == organization_id,
            CarrierConnection.carrier == VELOYD_CARRIER,
        )
        .first()
    )


def _is_legacy_organization(organization_id: int | None) -> bool:
    """Whether this is the one organization still configured through .env.

    Named explicitly rather than "any organization without a row": the whole
    point of storing keys per organization is that a merchant never ends up on
    someone else's account, and an open-ended fallback quietly reintroduces
    exactly that.
    """
    legacy = settings.veloyd_legacy_organization_id
    return bool(legacy) and organization_id == legacy


def client_for_organization(
    db: Session,
    organization_id: int | None,
    *,
    allow_legacy_fallback: bool = True,
) -> VeloydClient:
    """The Veloyd account of one merchant.

    Only the organization named by ``VELOYD_LEGACY_ORGANIZATION_ID`` may fall
    back to the environment key: it was configured that way before keys were
    stored per organization, and a deploy must not close its label gate halfway
    through a shift. Every other merchant without a stored key is not connected,
    and says so.

    Callers that create something at the carrier pass
    ``allow_legacy_fallback=False``. Reading a label under a stale account is
    recoverable; shipping a parcel under another merchant's sender address and
    invoice is not.
    """
    connection = carrier_connection(db, organization_id)
    if connection is None or not has_carrier_api_key(connection):
        if allow_legacy_fallback and _is_legacy_organization(organization_id):
            return VeloydClient(
                base_url=connection.base_url if connection else None
            )
        raise VeloydNotConnected(
            "Deze organisatie heeft geen eigen Veloyd-account gekoppeld"
        )
    try:
        api_key = get_carrier_api_key(connection)
    except CredentialEncryptionError as exc:
        raise VeloydError(
            "Veloyd-sleutel kan niet veilig worden ontsleuteld"
        ) from exc
    return VeloydClient(api_key=api_key, base_url=connection.base_url or None)


def connected_organization_ids(db: Session) -> list[int]:
    """Every organization that can talk to Veloyd, legacy one included."""
    org_ids = [
        connection.organization_id
        for connection in db.query(CarrierConnection)
        .filter(CarrierConnection.carrier == VELOYD_CARRIER)
        .order_by(CarrierConnection.organization_id)
        .all()
        if has_carrier_api_key(connection)
    ]
    legacy = settings.veloyd_legacy_organization_id
    if legacy and legacy not in org_ids:
        org_ids.append(legacy)
    return org_ids


def clients_for_user(db: Session, user: User) -> list[VeloydClient]:
    """Every Veloyd account this user may resolve a scanned label against.

    An owner or member has exactly one: their own merchant's. A courier and a
    platform admin work across merchants and have no organization of their own,
    so a label they scan may belong to any connected one — and asking only the
    environment account would answer "unknown" for every other merchant's
    parcel. The order is deterministic, so the same scan takes the same route
    every time.
    """
    if user.organization_id:
        try:
            return [client_for_organization(db, user.organization_id)]
        except VeloydNotConnected:
            # Never raises: an empty list is the one answer that means "no
            # account to ask", whether the user has one merchant or many.
            return []

    clients: list[VeloydClient] = []
    for organization_id in connected_organization_ids(db):
        try:
            clients.append(client_for_organization(db, organization_id))
        except VeloydNotConnected:
            continue
    return clients


def _is_missing_parcel_response(response: httpx.Response) -> bool:
    """Recognize Veloyd's undocumented status for an unknown tracking code."""
    try:
        payload = response.json() or {}
    except (TypeError, ValueError):
        return False
    description = str(payload.get("description") or "")
    normalized_description = description.lower()
    return (
        "parcel with tracktrace:" in normalized_description
        and "not found" in normalized_description
    )


def verify_veloyd_label(
    scanned_code: str,
    expected_reference: str,
    *,
    client: VeloydClient | None = None,
) -> VeloydLabel:
    """Resolve ``scanned_code`` and prove that it belongs to the open order."""
    label = (client or VeloydClient()).parcel_by_tracking_number(scanned_code)
    expected = expected_reference.strip().lstrip("#").strip()
    if label.reference != expected:
        raise VeloydLabelMismatch("Label hoort bij een andere order")
    return label


def _error_description(response: httpx.Response) -> str:
    try:
        payload = response.json() or {}
    except (TypeError, ValueError):
        return "onbekende fout"
    return str(payload.get("description") or payload.get("error") or "onbekende fout").strip()
