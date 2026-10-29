"""Local mock of BSS Microservice v1 that behaves as the swagger describes.

Only for checking the test suite itself (no real data):
    python tests/bss_api/mock_bss_server.py 8765
    set BSS_ENV_FILE=tests/bss_api/mock.env & python -m pytest tests/bss_api
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "Bearer mock-token"
ACCOUNTS = {
    "ACC-1": {"id": "ACC-1", "href": "/v1/account/ACC-1", "name": "Test Customer",
              "accountNumber": "100200300", "aliases": ["994121234567"],
              "accountBalance": {"amount": {"unit": "AZN", "value": 12.5}},
              "accountStateItems": [{"type": "invoice", "title": "Oct", "side": "debit", "amount": 10,
                                     "unbalancedAmount": 0, "date": "2026-10-01T00:00:00Z",
                                     "dueDate": "2026-10-20T00:00:00Z", "invoiceNumber": "INV-1"}]},
}
PAYMENTS = {
    "PAY-1": {"id": "PAY-1", "href": "/v1/payment/PAY-1", "description": "seed", "date": "2026-10-20T09:00:00Z",
              "totalAmount": {"unit": "AZN", "value": 5}, "accountNumber": "100200300",
              "transactionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "additionalDescription": "",
              "balanceProcessingStatus": "PROCESSED"},
}


def error(code: int, message: str) -> tuple[int, dict]:
    return code, {"code": str(code), "reason": message, "message": message, "status": str(code)}


def is_uuid(v) -> bool:
    try:
        uuid.UUID(str(v))
        return len(str(v)) == 36
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _guard(self, path: str):
        if self.headers.get("Authorization") != TOKEN:
            return error(401, "Unauthorized")
        if path != "/v1" and not self.headers.get("X-Correlation-ID"):
            return error(400, "X-Correlation-ID header is required")
        return None

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        self._send(*(self._guard(url.path) or self.route_get(url.path, q)))

    def route_get(self, path: str, q: dict):
        if path == "/v1":
            return 200, {"data": [{"name": "Bss Microservice", "version": "v1", "desc": "mock"}]}
        if path == "/v1/account":
            if not any(k in q for k in ("AliasNumber", "Filed", "AccountNumber")):
                return error(400, "one of AliasNumber, Filed, AccountNumber is required")
            res = list(ACCOUNTS.values())
            if "AccountNumber" in q:
                res = [a for a in res if a["accountNumber"] == q["AccountNumber"]]
            if "AliasNumber" in q:
                res = [a for a in res if q["AliasNumber"] in a["aliases"]]
            return 200, res
        if path.startswith("/v1/account/"):
            acc = ACCOUNTS.get(path.rsplit("/", 1)[1])
            if not acc:
                return error(404, "account not found")
            if q.get("Fields"):
                wanted = q["Fields"].split(",")
                if any(f not in acc for f in wanted):
                    return error(400, "unknown field")
                return 200, {"id": acc["id"], **{f: acc[f] for f in wanted}}
            return 200, acc
        if path == "/v1/payment":
            if "TransactionId" in q and not is_uuid(q["TransactionId"]):
                return error(400, "TransactionId must be a UUID")
            if "Date" in q:
                try:
                    if len(q["Date"]) != 10:
                        raise ValueError
                    date.fromisoformat(q["Date"])
                except ValueError:
                    return error(400, "Date must be YYYY-MM-DD")
            if not q:
                return error(400, "TransactionId or Date is required")
            res = list(PAYMENTS.values())
            if "TransactionId" in q:
                res = [p for p in res if p["transactionId"].lower() == q["TransactionId"].lower()]
            if "Date" in q:
                res = [p for p in res if p["date"].startswith(q["Date"])]
            if not res:
                return error(404, "payment not found")
            return 200, res[0]
        if path.startswith("/v1/payment/"):
            pay = PAYMENTS.get(path.rsplit("/", 1)[1])
            return (200, pay) if pay else error(404, "payment not found")
        return error(404, "not found")

    def do_POST(self):
        url = urlparse(self.path)
        guard = self._guard(url.path)
        if guard:
            return self._send(*guard)
        if url.path != "/v1/payment":
            return self._send(*error(404, "not found"))
        if "json" not in (self.headers.get("Content-Type") or ""):
            return self._send(*error(415, "application/json required"))
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw or b"")
        except ValueError:
            return self._send(*error(400, "malformed JSON"))
        self._send(*self.create(body))

    def create(self, b):
        if not isinstance(b, dict):
            return error(400, "object expected")
        for f in ("description", "date", "totalAmount", "accountNumber", "transactionId", "balancingEnabled"):
            if b.get(f) in (None, ""):
                return error(400, f"{f} is required")
        amt = b["totalAmount"]
        if not isinstance(amt, dict) or amt.get("unit") != "AZN":
            return error(400, "unit must be AZN")
        v = amt.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0.01:
            return error(400, "value must be >= 0.01")
        if not is_uuid(b["transactionId"]):
            return error(400, "transactionId must be a UUID")
        if not isinstance(b["balancingEnabled"], bool) or not isinstance(b["description"], str):
            return error(400, "bad type")
        try:
            datetime.fromisoformat(str(b["date"]).replace("Z", "+00:00"))
            if "T" not in b["date"]:
                raise ValueError
        except ValueError:
            return error(400, "date must be date-time")
        if b["accountNumber"] not in {a["accountNumber"] for a in ACCOUNTS.values()}:
            return error(404, "account not found")
        for p in PAYMENTS.values():
            if p["transactionId"] == b["transactionId"]:
                return error(409, "duplicate transactionId")
        pid = "PAY-" + uuid.uuid4().hex[:8]
        PAYMENTS[pid] = {"id": pid, "href": f"/v1/payment/{pid}", "description": b["description"],
                         "date": b["date"], "totalAmount": {"unit": "AZN", "value": v},
                         "accountNumber": b["accountNumber"], "transactionId": b["transactionId"],
                         "additionalDescription": b.get("additionalDescription", ""),
                         "balanceProcessingStatus": "PROCESSED"}
        return 201, PAYMENTS[pid]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock BSS on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
