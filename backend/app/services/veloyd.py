"""Talk to the Veloyd account of one organization.

Veloyd runs at the carrier, who keeps a client account per merchant: its own
sender address, its own tariffs, its own invoice. Dockscan serves several of
those merchants from one process, so which account a call goes to is part of
the call, never a process-wide setting.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CarrierConnection, User
from app.services.channel_credentials import (
    CredentialEncryptionError,
    get_carrier_api_key,
)

#: The only carrier today. Stored on ``CarrierConnection.carrier``.
VELOYD_CARRIER = "veloyd"


class VeloydError(RuntimeError):
    """Veloyd could not verify the scanned label."""


class VeloydLabelMismatch(VeloydError):
    """The label is valid, but belongs to another order."""


@dataclass(frozen=True)
class VeloydLabel:
    reference: str
    tracking_number: str
    tracking_url: str | None = None
    carrier: str | None = None

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
        )

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


def client_for_organization(db: Session, organization_id: int | None) -> VeloydClient:
    """The Veloyd account of one merchant.

    Falls back to the environment key while an organization has no row of its
    own. The first merchant on Veloyd was configured that way, and a deploy
    must not close its label gate halfway through a shift; storing that key
    per organization is what retires the fallback.
    """
    connection = carrier_connection(db, organization_id)
    if connection is None:
        return VeloydClient()
    try:
        api_key = get_carrier_api_key(connection)
    except CredentialEncryptionError as exc:
        raise VeloydError(
            "Veloyd-sleutel kan niet veilig worden ontsleuteld"
        ) from exc
    if not api_key:
        return VeloydClient(base_url=connection.base_url or None)
    return VeloydClient(api_key=api_key, base_url=connection.base_url or None)


def client_for_user(db: Session, user: User) -> VeloydClient:
    """The Veloyd account a scanning user acts for.

    An owner or member acts for their own merchant. A courier and a platform
    admin work across merchants and have no organization of their own, so they
    keep the environment key until the label flow can resolve a parcel per
    merchant — which is what the loose-label scan will do once Dockscan itself
    creates the parcels and already knows the tracking code.
    """
    if user.organization_id:
        return client_for_organization(db, user.organization_id)
    return VeloydClient()


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
