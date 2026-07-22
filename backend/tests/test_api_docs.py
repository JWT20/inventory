import pytest

from app.main import app


def test_generated_api_documentation_is_disabled():
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_generated_api_documentation_routes_are_not_exposed(client, path):
    response = client.get(path)

    assert response.status_code == 404
