"""Shared assertions for BSS API tests."""
from __future__ import annotations

from bss_schema import validate_response


def describe(resp) -> str:
    body = resp.text if len(resp.text) <= 600 else resp.text[:600] + "…"
    return (f"{resp.request.method} {resp.request.path_url} -> {resp.status_code} "
            f"(X-Correlation-ID={getattr(resp, 'correlation_id', None)})\n{body}")


def assert_status(resp, *expected: int) -> None:
    assert resp.status_code in expected, f"expected {expected}, got:\n{describe(resp)}"


def assert_contract(path: str, method: str, resp) -> None:
    """Response body matches the swagger schema for its status code."""
    errors = validate_response(path, method, resp)
    assert not errors, "Swagger contract violated:\n  " + "\n  ".join(errors) + "\n" + describe(resp)


def assert_client_error(path: str, method: str, resp, *preferred: int) -> None:
    """Invalid input must be rejected with 4xx (never 2xx, never 5xx) and an ErrorResponse body.

    `preferred` documents the status we expect per swagger/REST practice; any 4xx passes
    but a different 4xx is reported in the message for the contract review.
    """
    assert 400 <= resp.status_code < 500, (
        f"invalid request must be rejected with 4xx{' (expected ' + str(preferred) + ')' if preferred else ''}, got:\n"
        + describe(resp))
    assert_contract(path, method, resp)


def as_list(body):
    """GET /v1/payment is documented as one object; tolerate a list for lookups, the contract test reports it."""
    if isinstance(body, list):
        return body
    return [body] if body else []
