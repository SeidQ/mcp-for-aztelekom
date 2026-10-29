"""POST /v1/payment – creates real payments.

All tests here are skipped unless BSS_ALLOW_WRITE=1 and BSS_WRITE_ACCOUNT_NUMBER is a
TEST account. Negative tests are gated too: if validation is broken they would
create a payment.
"""
import uuid
from datetime import datetime, timezone

import pytest

from bss_helpers import as_list, assert_client_error, assert_contract, assert_status, describe
from bss_schema import request_schema, schema_errors

PATH = "/v1/payment"
REQUIRED = ["description", "date", "totalAmount", "accountNumber", "transactionId", "balancingEnabled"]

pytestmark = pytest.mark.write


def new_payment(account: str, value: float = 1.0, check_schema: bool = True) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    body = {
        "description": "AUTOTEST payment",
        "date": now,
        "totalAmount": {"unit": "AZN", "value": value},
        "accountNumber": account,
        "transactionId": str(uuid.uuid4()),
        "additionalDescription": "created by tests/bss_api – safe to reverse",
        "balancingEnabled": False,
    }
    if check_schema:
        errors = schema_errors(body, request_schema(PATH, "POST"))
        assert not errors, f"test payload does not match swagger request schema: {errors}"
    return body


@pytest.mark.smoke
def test_create_payment(api, write_enabled):
    """BSS-050 Valid payment is created (201) and echoes the request"""
    body = new_payment(write_enabled)
    resp = api.post(PATH, json=body)
    assert_status(resp, 201)
    assert_contract(PATH, "POST", resp)
    created = resp.json()
    assert created.get("id"), "no id in response"
    assert created["transactionId"].lower() == body["transactionId"]
    assert created["accountNumber"] == body["accountNumber"]
    assert created["totalAmount"]["unit"] == "AZN"
    assert created["totalAmount"]["value"] == pytest.approx(body["totalAmount"]["value"])
    assert created.get("balanceProcessingStatus"), "balanceProcessingStatus is empty"


def test_created_payment_is_readable(api, write_enabled):
    """BSS-051 Created payment is returned by GET /v1/payment/{id} and by TransactionId"""
    body = new_payment(write_enabled)
    created = api.post(PATH, json=body)
    assert_status(created, 201)
    pid = created.json()["id"]
    one = api.get(f"/v1/payment/{pid}")
    assert_status(one, 200)
    assert one.json()["transactionId"].lower() == body["transactionId"]
    found = api.get(PATH, params={"TransactionId": body["transactionId"]})
    assert_status(found, 200)
    assert pid in {p.get("id") for p in as_list(found.json())}, describe(found)


def test_duplicate_transaction_id_is_not_booked_twice(api, write_enabled):
    """BSS-052 Same transactionId posted twice does not create a second payment (idempotency)"""
    body = new_payment(write_enabled)
    first = api.post(PATH, json=body)
    assert_status(first, 201)
    second = api.post(PATH, json=body)
    if second.status_code in (200, 201):
        assert second.json().get("id") == first.json()["id"], (
            "duplicate transactionId created a NEW payment:\n" + describe(second))
    else:
        assert 400 <= second.status_code < 500, describe(second)  # 409 Conflict expected
    found = api.get(PATH, params={"TransactionId": body["transactionId"]})
    assert len(as_list(found.json())) == 1, "more than one payment for one transactionId:\n" + describe(found)


@pytest.mark.parametrize("value", [0.01, 999999.99], ids=["min", "large"])
def test_create_payment_amount_boundaries(api, write_enabled, value):
    """BSS-053 Amount boundary values are accepted (0.01 = minimum per swagger)"""
    # Swagger: minimum 0.01 with exclusiveMinimum: true, which literally means > 0.01, so the
    # swagger itself rejects 0.01. Business intent is most likely >= 0.01 (open question Q-3),
    # so the payload is sent without the local schema check and the API decides.
    resp = api.post(PATH, json=new_payment(write_enabled, value, check_schema=False))
    assert_status(resp, 201)


@pytest.mark.negative
@pytest.mark.parametrize("field", REQUIRED)
def test_create_payment_missing_required_field(api, write_enabled, field):
    """BSS-054 Missing required field is rejected with 400"""
    body = new_payment(write_enabled)
    body.pop(field)
    resp = api.post(PATH, json=body)
    assert_client_error(PATH, "POST", resp, 400)


def _mutate(body: dict, path: str, value):
    target = body
    *parents, last = path.split(".")
    for p in parents:
        target = target[p]
    if value is _DROP:
        target.pop(last)
    else:
        target[last] = value


_DROP = object()

INVALID = [
    ("totalAmount.value", 0, "zero"),
    ("totalAmount.value", -10, "negative"),
    ("totalAmount.value", 0.001, "below-min"),
    ("totalAmount.value", "10", "string-amount"),
    ("totalAmount.value", None, "null-amount"),
    ("totalAmount.unit", "USD", "usd"),
    ("totalAmount.unit", "azn", "lowercase-unit"),
    ("totalAmount.unit", _DROP, "no-unit"),
    ("totalAmount.value", _DROP, "no-value"),
    ("transactionId", "not-a-uuid", "bad-uuid"),
    ("transactionId", "", "empty-uuid"),
    ("date", "2026-13-40T00:00:00Z", "bad-date"),
    ("date", "yesterday", "word-date"),
    ("balancingEnabled", "yes", "string-bool"),
    ("accountNumber", "", "empty-account"),
    ("description", None, "null-description"),
]


@pytest.mark.negative
@pytest.mark.parametrize("path,value", [(p, v) for p, v, _ in INVALID], ids=[i for *_, i in INVALID])
def test_create_payment_invalid_value(api, write_enabled, path, value):
    """BSS-055 Invalid field value is rejected with 400"""
    body = new_payment(write_enabled)
    _mutate(body, path, value)
    resp = api.post(PATH, json=body)
    assert_client_error(PATH, "POST", resp, 400)


@pytest.mark.negative
def test_create_payment_unknown_account(api, write_enabled):
    """BSS-056 Payment to a non-existing account is rejected with 4xx"""
    body = new_payment("NOTEXIST" + uuid.uuid4().hex[:10])
    resp = api.post(PATH, json=body)
    assert_client_error(PATH, "POST", resp, 400, 404, 422)


@pytest.mark.negative
@pytest.mark.parametrize("raw,ctype", [("", "application/json"), ("{bad json", "application/json"),
                                       ("[]", "application/json"), ("a=1", "application/x-www-form-urlencoded")],
                         ids=["empty", "malformed", "array", "form"])
def test_create_payment_bad_body(api, write_enabled, raw, ctype):
    """BSS-057 Empty / malformed / wrong content-type body is rejected with 4xx"""
    resp = api.post(PATH, data=raw, headers={"Content-Type": ctype})
    assert_client_error(PATH, "POST", resp, 400, 415)


@pytest.mark.negative
def test_create_payment_without_correlation_id(api, write_enabled):
    """BSS-058 POST without required header X-Correlation-ID is rejected with 4xx"""
    resp = api.post(PATH, json=new_payment(write_enabled), correlation=False)
    assert_client_error(PATH, "POST", resp, 400)


@pytest.mark.security
def test_create_payment_without_auth(api, write_enabled):
    """BSS-059 POST without the auth header is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set.")
    resp = api.post(PATH, json=new_payment(write_enabled), auth=False)
    assert_status(resp, 401)

