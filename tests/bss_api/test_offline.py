"""Offline checks of the swagger and of the test helpers (no API needed)."""
import copy

import pytest

from bss_schema import SPEC, operations, request_schema, response_schema, schema_errors
from test_payments_write import INVALID, PATH, _mutate, new_payment

pytestmark = pytest.mark.offline

EXPECTED_OPERATIONS = {("/v1", "GET"), ("/v1/account", "GET"), ("/v1/account/{id}", "GET"),
                       ("/v1/payment", "GET"), ("/v1/payment", "POST"), ("/v1/payment/{id}", "GET")}


def test_swagger_operations_are_covered():
    """BSS-000 Every swagger operation is known to the suite (new endpoint = add tests)"""
    assert set(operations()) == EXPECTED_OPERATIONS


@pytest.mark.parametrize("path,method", sorted(EXPECTED_OPERATIONS))
def test_success_schema_is_resolvable(path, method):
    """BSS-061 Success response schema of each operation converts to JSON Schema"""
    status = 201 if method == "POST" else 200
    assert response_schema(path, method, status) is not None


def test_request_schema_rejects_invalid_examples():
    """BSS-060 Swagger request schema rejects the invalid payloads used in BSS-055"""
    schema = request_schema(PATH, "POST")
    base = new_payment("TEST")
    assert not schema_errors(base, schema)
    for path, value, name in INVALID:
        if name in ("empty-account", "null-description", "no-unit", "no-value"):
            continue  # not expressible in the swagger (no minLength/nullable/required inside totalAmount)
        body = copy.deepcopy(base)
        _mutate(body, path, value)
        assert schema_errors(body, schema), f"swagger schema accepts invalid case '{name}'"


def test_required_correlation_header_declared():
    """BSS-062 X-Correlation-ID is declared required on every operation except GET /v1"""
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            names = {p["name"] for p in op.get("parameters", []) if p.get("required")}
            if path != "/v1":
                assert "X-Correlation-ID" in names, f"{method.upper()} {path}"
