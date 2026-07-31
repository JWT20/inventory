import httpx
import pytest

from app.services.veloyd import (
    VeloydClient,
    VeloydError,
    VeloydLabelMismatch,
    verify_veloyd_label,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://app.veloyd.nl")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)


def test_veloyd_tracking_lookup_returns_reference_and_tracking(monkeypatch):
    def _get(url, headers, timeout):
        assert url.endswith("/parcel/get/tracktrace/V793AUDS9F4MB")
        assert headers == {"Authorization": "Bearer test-key"}
        return FakeResponse(
            200,
            {
                "parcel": {
                    "reference": "#1262",
                    "trackTrace": "V793AUDS9F4MB",
                    "trackTraceLink": "https://tracking.example/V793AUDS9F4MB",
                    "carrier": "Break Away",
                }
            },
        )

    monkeypatch.setattr("app.services.veloyd.httpx.get", _get)
    label = verify_veloyd_label(
        "V793AUDS9F4MB",
        "1262",
        client=VeloydClient(api_key="test-key"),
    )

    assert label.reference == "1262"
    assert label.shopify_tracking_info == {
        "number": "V793AUDS9F4MB",
        "url": "https://tracking.example/V793AUDS9F4MB",
        "company": "Break Away",
    }


def test_veloyd_valid_label_for_other_order_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(
            200,
            {"parcel": {"reference": "1263", "trackTrace": "VOTHER"}},
        ),
    )

    with pytest.raises(VeloydLabelMismatch, match="andere order"):
        verify_veloyd_label(
            "VOTHER", "1262", client=VeloydClient(api_key="test-key")
        )


def test_veloyd_unauthorized_key_has_actionable_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(401, {"error": "User not authorized"}),
    )

    with pytest.raises(VeloydError, match="ongeldig of nog niet geactiveerd"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VTEST")


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (
            400,
            {
                "error": "UserError",
                "description": "Parcel with trackTrace: VUNKNOWN not found",
            },
        ),
        (404, {"error": "NotFound"}),
    ],
)
def test_veloyd_unknown_tracking_is_label_mismatch(
    monkeypatch, status_code, payload
):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(status_code, payload),
    )

    with pytest.raises(VeloydLabelMismatch, match="niet bekend bij Veloyd"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VUNKNOWN")


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (400, {"error": "UserError", "description": "Invalid request"}),
        (500, {"error": "InternalServerError"}),
    ],
)
def test_veloyd_other_upstream_errors_remain_service_errors(
    monkeypatch, status_code, payload
):
    monkeypatch.setattr(
        "app.services.veloyd.httpx.get",
        lambda *args, **kwargs: FakeResponse(status_code, payload),
    )

    with pytest.raises(VeloydError, match="kon het label niet controleren"):
        VeloydClient(api_key="test-key").parcel_by_tracking_number("VTEST")
