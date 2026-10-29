"""GET /v1/account and GET /v1/account/{id}."""
import uuid

import pytest

from bss_helpers import assert_client_error, assert_contract, assert_status, describe

SEARCH = "/v1/account"
BY_ID = "/v1/account/{id}"
SAFE_FIELDS = {"id", "href", "@type", "@baseType", "@schemaLocation"}


def by_id(account_id: str) -> str:
    return f"/v1/account/{account_id}"


# ------------------------------------------------------------ GET /v1/account
@pytest.mark.smoke
def test_search_by_account_number(api, data):
    """BSS-010 Search by AccountNumber returns the matching account(s)"""
    number = data("BSS_ACCOUNT_NUMBER")
    resp = api.get(SEARCH, params={"AccountNumber": number})
    assert_status(resp, 200)
    assert_contract(SEARCH, "GET", resp)
    accounts = resp.json()
    assert accounts, f"no account returned for AccountNumber={number}"
    assert all(a.get("accountNumber") == number for a in accounts), describe(resp)


def test_search_by_alias_number(api, data):
    """BSS-011 Search by AliasNumber returns accounts that carry the alias"""
    alias = data("BSS_ALIAS_NUMBER")
    resp = api.get(SEARCH, params={"AliasNumber": alias})
    assert_status(resp, 200)
    assert_contract(SEARCH, "GET", resp)
    accounts = resp.json()
    assert accounts, f"no account returned for AliasNumber={alias}"
    assert all(alias in (a.get("aliases") or []) for a in accounts), describe(resp)


def test_search_by_alias_and_number_agree(api, data):
    """BSS-012 AliasNumber and AccountNumber of the same account return the same account id"""
    number, alias = data("BSS_ACCOUNT_NUMBER"), data("BSS_ALIAS_NUMBER")
    a = api.get(SEARCH, params={"AccountNumber": number})
    b = api.get(SEARCH, params={"AliasNumber": alias})
    assert_status(a, 200)
    assert_status(b, 200)
    assert {x["id"] for x in a.json()} & {x["id"] for x in b.json()}, (
        "alias and account number point to different accounts – check BSS_ALIAS_NUMBER belongs to BSS_ACCOUNT_NUMBER")


@pytest.mark.negative
def test_search_without_any_parameter(api):
    """BSS-013 Search without AliasNumber/Filed/AccountNumber is rejected with 400 (swagger: at least one must be provided)"""
    resp = api.get(SEARCH)
    assert_client_error(SEARCH, "GET", resp, 400)
    assert_status(resp, 400)


@pytest.mark.negative
def test_search_without_correlation_id(api, data):
    """BSS-014 Search without required header X-Correlation-ID is rejected with 4xx"""
    resp = api.get(SEARCH, params={"AccountNumber": data("BSS_ACCOUNT_NUMBER")}, correlation=False)
    assert_client_error(SEARCH, "GET", resp, 400)


@pytest.mark.negative
def test_search_unknown_account_number(api):
    """BSS-015 Unknown AccountNumber returns an empty list (or 404), never 5xx"""
    resp = api.get(SEARCH, params={"AccountNumber": "NOTEXIST" + uuid.uuid4().hex[:10]})
    assert_status(resp, 200, 404)
    assert_contract(SEARCH, "GET", resp)
    if resp.status_code == 200:
        assert resp.json() == [], describe(resp)


@pytest.mark.negative
@pytest.mark.parametrize("value", ["", " ", "' OR '1'='1", "1;DROP TABLE x", "<script>alert(1)</script>",
                                   "%", "*", "A" * 5000], ids=["empty", "space", "sqli", "sqli2", "xss",
                                                                "percent", "wildcard", "5000chars"])
def test_search_malformed_account_number(api, value):
    """BSS-016 Malformed/injection AccountNumber never causes 5xx and never returns accounts"""
    resp = api.get(SEARCH, params={"AccountNumber": value})
    assert resp.status_code < 500, describe(resp)
    if resp.status_code == 200:
        assert resp.json() == [], "malformed AccountNumber returned data:\n" + describe(resp)
    assert_contract(SEARCH, "GET", resp)


@pytest.mark.security
def test_search_without_auth(api, data):
    """BSS-017 Search without the auth header is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set – auth scheme not known yet (swagger has no securitySchemes).")
    resp = api.get(SEARCH, params={"AccountNumber": data("BSS_ACCOUNT_NUMBER")}, auth=False)
    assert_status(resp, 401)
    assert_contract(SEARCH, "GET", resp)


@pytest.mark.security
def test_search_with_invalid_auth(api, data):
    """BSS-018 Search with an invalid token is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set.")
    name, value = api.auth_header
    scheme = value.split(" ", 1)[0] + " " if " " in value else ""
    resp = api.get(SEARCH, params={"AccountNumber": data("BSS_ACCOUNT_NUMBER")}, auth=False,
                   headers={name: scheme + "invalid-" + uuid.uuid4().hex})
    assert_status(resp, 401)


# ------------------------------------------------------- GET /v1/account/{id}
@pytest.mark.smoke
def test_get_account_by_id(api, data):
    """BSS-020 Get account by id returns 200 with the same id"""
    account_id = data("BSS_ACCOUNT_ID")
    resp = api.get(by_id(account_id))
    assert_status(resp, 200)
    assert_contract(BY_ID, "GET", resp)
    assert resp.json().get("id") == account_id, describe(resp)


def test_search_and_get_by_id_are_consistent(api, data):
    """BSS-021 Account found by AccountNumber is returned identically by GET /v1/account/{id}"""
    found = api.get(SEARCH, params={"AccountNumber": data("BSS_ACCOUNT_NUMBER")})
    assert_status(found, 200)
    assert found.json(), describe(found)
    first = found.json()[0]
    resp = api.get(by_id(first["id"]))
    assert_status(resp, 200)
    detail = resp.json()
    for key in ("id", "accountNumber", "name"):
        assert detail.get(key) == first.get(key), f"{key}: search={first.get(key)!r} by-id={detail.get(key)!r}"


@pytest.mark.parametrize("fields", ["accountNumber", "accountNumber,name", "accountBalance"])
def test_get_account_fields_filter(api, data, fields):
    """BSS-022 'Fields' limits the response to the requested fields"""
    resp = api.get(by_id(data("BSS_ACCOUNT_ID")), params={"Fields": fields})
    assert_status(resp, 200)
    assert_contract(BY_ID, "GET", resp)
    body = resp.json()
    wanted = set(fields.split(","))
    missing = wanted - body.keys()
    extra = body.keys() - wanted - SAFE_FIELDS
    assert not missing, f"requested fields missing: {missing}"
    assert not extra, f"fields not requested but returned: {sorted(extra)}"


@pytest.mark.negative
def test_get_account_fields_unknown(api, data):
    """BSS-023 Unknown field name in 'Fields' is rejected (4xx) or ignored, never 5xx"""
    resp = api.get(by_id(data("BSS_ACCOUNT_ID")), params={"Fields": "noSuchField"})
    assert resp.status_code < 500, describe(resp)
    assert_contract(BY_ID, "GET", resp)


@pytest.mark.negative
def test_get_account_unknown_id(api):
    """BSS-024 Unknown account id returns 404 (not documented in swagger), never 200/5xx"""
    resp = api.get(by_id("NOTEXIST" + uuid.uuid4().hex[:10]))
    assert_client_error(BY_ID, "GET", resp, 404)


@pytest.mark.negative
def test_get_account_without_correlation_id(api, data):
    """BSS-025 Get by id without required header X-Correlation-ID is rejected with 4xx"""
    resp = api.get(by_id(data("BSS_ACCOUNT_ID")), correlation=False)
    assert_client_error(BY_ID, "GET", resp, 400)


@pytest.mark.security
def test_get_account_by_id_without_auth(api, data):
    """BSS-026 Get by id without the auth header is rejected with 401"""
    if not api.auth_header:
        pytest.skip("BSS_AUTH_HEADER not set.")
    resp = api.get(by_id(data("BSS_ACCOUNT_ID")), auth=False)
    assert_status(resp, 401)


@pytest.mark.negative
@pytest.mark.parametrize("raw_id", ["%2E%2E%2Fpayment", "' OR 1=1--", "A" * 2000], ids=["traversal", "sqli", "2000chars"])
def test_get_account_malformed_id(api, raw_id):
    """BSS-027 Malformed account id is rejected with 4xx, never 5xx"""
    resp = api.get("/v1/account/" + raw_id)
    assert 400 <= resp.status_code < 500, describe(resp)
