"""Resolve a scanned Veloyd label barcode to its visible order reference."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.config import settings


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

        encoded = quote(tracking_number, safe="")
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
