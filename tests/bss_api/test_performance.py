"""Response time of read endpoints (threshold BSS_MAX_MS, default 3000 ms)."""
import pytest

pytestmark = pytest.mark.performance

CASES = [
    ("/v1", None, None),
    ("/v1/account", "AccountNumber", "BSS_ACCOUNT_NUMBER"),
    ("/v1/account/{BSS_ACCOUNT_ID}", None, "BSS_ACCOUNT_ID"),
    ("/v1/payment", "TransactionId", "BSS_TRANSACTION_ID"),
    ("/v1/payment/{BSS_PAYMENT_ID}", None, "BSS_PAYMENT_ID"),
]


@pytest.mark.parametrize("path,param,key", CASES, ids=[c[0] for c in CASES])
def test_response_time(api, data, max_ms, path, param, key):
    """BSS-090 Read endpoint answers 200 within BSS_MAX_MS (best of 3)"""
    params = None
    if key:
        value = data(key)
        if param:
            params = {param: value}
        else:
            path = path.replace("{" + key + "}", value)
    times = []
    for _ in range(3):
        resp = api.get(path, params=params)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        times.append(resp.elapsed_ms)
    assert min(times) <= max_ms, f"{path}: {times} ms > {max_ms} ms"
