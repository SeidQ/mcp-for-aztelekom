# BSS Microservice v1 – API automation

pytest suite built from `swagger_bss_v1.json` (6 operations, 100 test cases).

## Run

```powershell
copy tests\bss_api\bss.env.example tests\bss_api\bss.env   # fill BSS_BASE_URL + test data
python -m pytest tests\bss_api                              # all (write tests skipped by default)
python -m pytest tests\bss_api -m smoke                     # only happy paths
python -m pytest tests\bss_api -m "not write and not performance"
```

Each run writes `tests\bss_api\results\bss_api_results_<time>.xlsx`: result per test, every request with its `X-Correlation-ID` (for log search), failure reason.

Tests whose data key is empty are **skipped** with the key name. Nothing fails because of missing config.

## Safety

`POST /v1/payment` creates real payments. All write tests, negative ones included, run only with `BSS_ALLOW_WRITE=1` and a test account in `BSS_WRITE_ACCOUNT_NUMBER`. `bss.env` is gitignored (it may hold a token).

## Coverage

| File | Endpoint | IDs |
|---|---|---|
| test_info.py | GET /v1 | BSS-001…002 |
| test_accounts.py | GET /v1/account, /v1/account/{id} | BSS-010…027 |
| test_payments_read.py | GET /v1/payment, /v1/payment/{id} | BSS-030…045 |
| test_payments_write.py | POST /v1/payment | BSS-050…059 |
| test_performance.py | read endpoints, BSS_MAX_MS | BSS-090 |
| test_offline.py | swagger self-checks (no API) | BSS-000, 060…062 |

Each response is validated against the swagger schema (`bss_schema.py`). `BSS_STRICT_SCHEMA=1` also fails on undocumented fields.

## Self-check with the mock

`mock_bss_server.py` behaves as the swagger says. Use it to check the suite, not the API:

```powershell
python tests\bss_api\mock_bss_server.py 8765          # separate terminal
$env:BSS_ENV_FILE="tests\bss_api\mock.env"; python -m pytest tests\bss_api
```

## Open swagger questions

1. **Auth:** no `securitySchemes`, but 401 is documented. Which header/token? (`BSS_AUTH_HEADER`)
2. **Base URL:** no `servers` block.
3. **Amount minimum:** `minimum: 0.01` + `exclusiveMinimum: true` means **> 0.01**, so 0.01 is invalid. Is the intent ≥ 0.01? (BSS-053)
4. **`Filed` vs `Fields`:** `GET /v1/account` has `Filed`, `/v1/account/{id}` has `Fields`. Typo? What does `Filed` do?
5. **`GET /v1/payment` documents 201.** Copy-paste error, since GET does not create.
6. **`GET /v1/payment` returns one object.** Searching by `Date` will normally match many payments. Should it be an array?
7. **`GET /v1/payment` with no parameters:** swagger says both are optional. What is returned?
8. **Missing responses:** no 404 for `/{id}` endpoints, and no 400 for `POST /v1/payment` or `GET /v1/payment` although they validate input. The tests expect 404/400 (any 4xx passes).
9. **Duplicate `transactionId` on POST:** is it idempotent or does it return 409? (BSS-052)
10. **`totalAmount.unit/value`** are not marked required inside the object.
