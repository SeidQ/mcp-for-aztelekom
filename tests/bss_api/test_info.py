"""GET /v1 – application info."""
import pytest

from bss_helpers import assert_contract, assert_status

PATH = "/v1"


@pytest.mark.smoke
def test_info_returns_app_metadata(api):
    """BSS-001 GET /v1 returns 200 with name, version 'v1' and description"""
    resp = api.get(PATH)
    assert_status(resp, 200)
    assert_contract(PATH, "GET", resp)
    data = resp.json()["data"]
    assert data, "data[] is empty"
    assert data[0]["version"] == "v1"
    assert data[0]["name"], "name is empty"


@pytest.mark.smoke
def test_info_without_correlation_id(api):
    """BSS-002 GET /v1 does not require X-Correlation-ID (not declared in swagger)"""
    resp = api.get(PATH, correlation=False)
    assert_status(resp, 200)
