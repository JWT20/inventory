import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _create_fastapi_app


@pytest.mark.parametrize(
    ("domain", "expected_urls", "expected_status"),
    [
        (
            "api.example.com",
            {"docs": None, "redoc": None, "openapi": None},
            404,
        ),
        (
            "",
            {
                "docs": "/docs",
                "redoc": "/redoc",
                "openapi": "/openapi.json",
            },
            200,
        ),
    ],
)
def test_generated_api_documentation_follows_environment(
    monkeypatch, domain, expected_urls, expected_status
):
    monkeypatch.setattr(settings, "domain", domain)
    test_app = _create_fastapi_app()

    assert test_app.docs_url == expected_urls["docs"]
    assert test_app.redoc_url == expected_urls["redoc"]
    assert test_app.openapi_url == expected_urls["openapi"]

    client = TestClient(test_app, raise_server_exceptions=True)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == expected_status
