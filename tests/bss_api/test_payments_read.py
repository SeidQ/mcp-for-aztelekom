"""GET /v1/payment and GET /v1/payment/{id} (read-only)."""
import uuid

import pytest

from bss_helpers import as_list, assert_client_error, assert_contract, assert_status, describe

SEARCH = "/v1/payment"
BY_ID = "/v1/payment/{id}"


def by_id(payment_id: str) -> str:
    return f"/v1/payment/{payment_id}"


# ------------------------------------------------------------ GET /v1/payment
@pytest.mark.smoke
def test_payment_by_transaction_id(api, data):
    """BSS-030 GET /v1/payment?TransactionId returns that payment (200, swagger schema)"""
    tx = data("BSS_TRANSACTION_ID")
    resp = api.get(SEARCH, params={"TransactionId": tx})
    assert_status(resp, 200)
    assert_contract(SEARCH, "GET", resp)
    payments = as_list(resp.json())
    assert payments, describe(resp)
    assert all(p.get("transactionId", "").lower() == tx.lower() for p in payments), describe(resp)


def test_payment_by_date(api, data):
    """BSS-031 GET /v1/payment?Date returns payments of that date only"""
    day = data("BSS_PAYMENT_DATE")  # YYYY-MM-DD with at least one payment
    resp = api.get(SEARCH, params={"Date": day})
    assert_status(resp, 200)
    assert_contract(SEARCH, "GET", resp)
    payments = as_list(resp.json())
    assert payments, f"no payment for Date={day}"
    assert all(str(p.get("date", "")).startswith(day) for p in payments), describe(resp)


def test_payment_by_transaction_id_and_date(api, data):
    """BSS-032 TransactionId + matching Date returns the payment; + other Date returns nothing"""
    tx, day = data("BSS_TRANSACTION_ID"), data("BSS_PAYMENT_DATE")
    ok = api.get(SEARCH, params={"TransactionId": tx, "Date": day})
    assert_status(ok, 200)
    assert as_list(ok.json()), describe(ok)
    other = api.get(SEARCH, params={"TransactionId": tx, "Date": "1999-01-01"})
    assert other.status_code in (200, 404), describe(other)
    if other.status_code == 200:
        assert not as_list(other.json()), "filter by Date is ignored:\n" + describe(other)


@pytest.mark.negative
def test_payment_search_without_parameters(api):
    """BSS-033 GET /v1/payment without parameters is handled (4xx or 200), never 5xx"""
    resp = api.get(SEARCH)
    assert resp.status_code < 500, describe(resp)
    assert_contract(SEARCH, "GET", resp)


@pytest.mark.negative
@pytest.mark.parametrize("tx", ["abc", "12345", "3fa85f64-5717-4562-b3fc-2c963f66afaZ", "' OR '1'='1"],
                         ids=["word", "number", "bad-hex", "sqli"])
def test_payment_invalid_transaction_id(api, tx):
    """BSS-034 TransactionId that is not a UUID is rejected with 400"""
    resp = api.get(SEARCH, params={"TransactionId": tx})
    assert_client_error(SEARCH, "GET", resp, 400)


@pytest.mark.negative
@pytest.mark.parametrize("day", ["2026-13-01", "2026-02-30", "26.10.2026", "today", "2026-10-26T10:00:00"],
                         ids=["month13", "feb30", "dmy", "word", "datetime"])
def test_payment_invalid_date(api, day):
    """BSS-035 Date that is not YYYY-MM-DD is rejected with 400"""
    resp = api.get(SEARCH, params={"Date": day})
    assert_client_error(SEARCH, "GET", resp, 400)


@pytest.mark.negative
def test_payment_unknown_transaction_id(api):
    """BSS-036 Unknown (valid) TransactionId returns 404 or empty result, never 5xx"""
    resp = api.get(SEARCH, params={"TransactionId": str(uuid.uuid4())})
    assert resp.status_code in (200, 404), describe(resp)
    assert_contract(SEARCH, "GET", resp)
    if resp.status_code == 200:
        assert not as_list(resp.json()), describe(resp)


@pytest.mark.negative
def test_payment_search_without_correlation_id(api, data):
    """BSS-037 GET /v1/payment without required header X-Correlation-ID is rejected with 4xx"""
    resp = api.get(SEARCH, params={"TransactionId": data("BSS_TRANSACTION_ID")}, correlation=False)
    assert_client_error(SEARCH, "GET", resp, 400)


@pytest.mark.security
def test_payment_search_without_auth(api, data):
    """BSS-038 GET /v1/payment without the auth header is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set.")
    resp = api.get(SEARCH, params={"TransactionId": data("BSS_TRANSACTION_ID")}, auth=False)
    assert_status(resp, 401)


# -------------------------------------------------------- GET /v1/payment/{id}
@pytest.mark.smoke
def test_payment_by_id(api, data):
    """BSS-040 GET /v1/payment/{id} returns 200 with the same id"""
    pid = data("BSS_PAYMENT_ID")
    resp = api.get(by_id(pid))
    assert_status(resp, 200)
    assert_contract(BY_ID, "GET", resp)
    assert resp.json().get("id") == pid, describe(resp)


def test_payment_by_id_and_transaction_id_consistent(api, data):
    """BSS-041 Payment by id and by its transactionId are the same payment"""
    resp = api.get(by_id(data("BSS_PAYMENT_ID")))
    assert_status(resp, 200)
    payment = resp.json()
    again = api.get(SEARCH, params={"TransactionId": payment["transactionId"]})
    assert_status(again, 200)
    assert payment["id"] in {p.get("id") for p in as_list(again.json())}, describe(again)


def test_payment_account_exists(api, data):
    """BSS-042 accountNumber of a payment is an existing account"""
    resp = api.get(by_id(data("BSS_PAYMENT_ID")))
    assert_status(resp, 200)
    number = resp.json().get("accountNumber")
    assert number, "payment has no accountNumber"
    acc = api.get("/v1/account", params={"AccountNumber": number})
    assert_status(acc, 200)
    assert acc.json(), f"account {number} of the payment is not found"


@pytest.mark.negative
def test_payment_unknown_id(api):
    """BSS-043 Unknown payment id returns 404 (not documented in swagger), never 200/5xx"""
    resp = api.get(by_id("NOTEXIST" + uuid.uuid4().hex[:10]))
    assert_client_error(BY_ID, "GET", resp, 404)


@pytest.mark.negative
def test_payment_by_id_without_correlation_id(api, data):
    """BSS-044 GET /v1/payment/{id} without required header X-Correlation-ID is rejected with 4xx"""
    resp = api.get(by_id(data("BSS_PAYMENT_ID")), correlation=False)
    assert_client_error(BY_ID, "GET", resp, 400)


@pytest.mark.security
def test_payment_by_id_without_auth(api, data):
    """BSS-045 GET /v1/payment/{id} without the auth header is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set.")
    resp = api.get(by_id(data("BSS_PAYMENT_ID")), auth=False)
    assert_status(resp, 401)
