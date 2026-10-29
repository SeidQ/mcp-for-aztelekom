"""pytest fixtures for BSS Microservice v1 API tests.

Configuration: tests/bss_api/bss.env (KEY=VALUE, gitignored) or environment
variables (environment wins). See bss.env.example for every key.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).parent
RESULTS_DIR = HERE / "results"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].split("\t#", 1)[0]  # inline comment
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_FILE_CFG = _load_env_file(Path(os.environ.get("BSS_ENV_FILE", HERE / "bss.env")))


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _FILE_CFG.get(key) or default


def cfg_flag(key: str) -> bool:
    return cfg(key, "0").lower() in ("1", "true", "yes")


class BssClient:
    """Thin requests wrapper: base URL, X-Correlation-ID per call, optional auth header."""

    def __init__(self) -> None:
        self.base_url = cfg("BSS_BASE_URL").rstrip("/")
        self.timeout = float(cfg("BSS_TIMEOUT", "30"))
        self.session = requests.Session()
        self.session.verify = cfg("BSS_VERIFY_SSL", "1").lower() not in ("0", "false", "no")
        self.auth_header: tuple[str, str] | None = None
        raw = cfg("BSS_AUTH_HEADER")  # e.g. "Authorization: Bearer <token>"
        if raw and ":" in raw:
            name, _, value = raw.partition(":")
            self.auth_header = (name.strip(), value.strip())
        self.item = None  # current pytest item, for the report

    def request(self, method: str, path: str, *, params=None, json=None, data=None,
                headers=None, correlation: bool = True, auth: bool = True) -> requests.Response:
        hdrs = {"Accept": "application/json"}
        corr = None
        if correlation:
            corr = str(uuid.uuid4())
            hdrs["X-Correlation-ID"] = corr
        if auth and self.auth_header:
            hdrs[self.auth_header[0]] = self.auth_header[1]
        hdrs.update(headers or {})
        start = time.perf_counter()
        resp = self.session.request(method, self.base_url + path, params=params, json=json,
                                    data=data, headers=hdrs, timeout=self.timeout)
        resp.elapsed_ms = round((time.perf_counter() - start) * 1000)
        resp.correlation_id = corr
        if self.item is not None:
            self.item.user_properties.append(
                ("call", f"{method} {resp.request.path_url} -> {resp.status_code} "
                         f"({resp.elapsed_ms} ms, X-Correlation-ID={corr})"))
        return resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


@pytest.fixture(scope="session")
def _client() -> BssClient:
    if not cfg("BSS_BASE_URL"):
        pytest.skip("BSS_BASE_URL is not set (tests/bss_api/bss.env or environment).")
    return BssClient()


@pytest.fixture
def api(_client: BssClient, request) -> BssClient:
    _client.item = request.node
    yield _client
    _client.item = None


@pytest.fixture
def data():
    """data('BSS_ACCOUNT_NUMBER') -> value, or skip the test if it is not configured."""
    def _get(key: str) -> str:
        value = cfg(key)
        if not value:
            pytest.skip(f"Test data {key} is not set in bss.env.")
        return value
    return _get


@pytest.fixture
def write_enabled():
    if not cfg_flag("BSS_ALLOW_WRITE"):
        pytest.skip("Write tests (POST /v1/payment) are off. Set BSS_ALLOW_WRITE=1 only on a test environment.")
    if not cfg("BSS_WRITE_ACCOUNT_NUMBER"):
        pytest.skip("BSS_WRITE_ACCOUNT_NUMBER (test account for payments) is not set.")
    return cfg("BSS_WRITE_ACCOUNT_NUMBER")


@pytest.fixture
def max_ms() -> int:
    return int(cfg("BSS_MAX_MS", "3000"))


# ---------------------------------------------------------------- Excel report
_RESULTS: list[dict] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        doc = (item.function.__doc__ or "").strip().splitlines()
        title = doc[0] if doc else item.name
        if "[" in item.name:
            title += " " + item.name[item.name.index("["):]
        message = ""
        if rep.skipped and isinstance(rep.longrepr, tuple):
            message = rep.longrepr[2]
        elif rep.failed:
            message = getattr(rep, "longreprtext", str(rep.longrepr))[-1500:]
        _RESULTS.append({
            "id": title.split(" ", 1)[0],
            "title": title,
            "markers": ", ".join(sorted({m.name for m in item.iter_markers()} - {"parametrize"})),
            "outcome": {"passed": "Pass", "failed": "Fail", "skipped": "Skipped"}[rep.outcome],
            "seconds": round(rep.duration, 3),
            "calls": "\n".join(v for k, v in item.user_properties if k == "call"),
            "message": message,
        })


def pytest_sessionfinish(session, exitstatus):
    if not _RESULTS or cfg_flag("BSS_NO_REPORT"):
        return
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    cols = ["id", "title", "markers", "outcome", "seconds", "calls", "message"]
    ws.append(["ID", "Test", "Markers", "Result", "Seconds", "Requests (X-Correlation-ID)", "Failure / skip reason"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
    colors = {"Pass": "C6EFCE", "Fail": "FFC7CE", "Skipped": "FFEB9C"}
    for row in _RESULTS:
        ws.append([row[c] for c in cols])
        ws.cell(ws.max_row, 4).fill = PatternFill("solid", fgColor=colors[row["outcome"]])
    for col, width in zip("ABCDEFG", (10, 70, 18, 9, 9, 80, 90)):
        ws.column_dimensions[col].width = width
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    summary = wb.create_sheet("Summary", 0)
    summary.append(["BSS Microservice v1 – API test run", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    summary.append(["Base URL", cfg("BSS_BASE_URL")])
    summary.append([])
    for name in ("Pass", "Fail", "Skipped"):
        summary.append([name, sum(r["outcome"] == name for r in _RESULTS)])
    summary.append(["Total", len(_RESULTS)])
    summary.column_dimensions["A"].width = 38
    summary.column_dimensions["B"].width = 40
    out = RESULTS_DIR / f"bss_api_results_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    wb.save(out)
    session.config._bss_report = out


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    out = getattr(config, "_bss_report", None)
    if out:
        terminalreporter.write_line(f"Excel report: {out}")
