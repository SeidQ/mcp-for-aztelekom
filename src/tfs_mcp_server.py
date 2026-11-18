#!/usr/bin/env python3
"""MCP server for defect automation on Azure DevOps Server (on-prem TFS).

Tools: get_work_item, create_defect, bulk_create_from_csv, query_wiql,
list_recent_defects, update_status, add_comment, update_fields, export_report_xlsx,
list_comments, update_comment, delete_comment, clear_fields, str_replace_field,
delete_work_item, bulk_delete_work_items, restore_work_item, list_deleted_work_items,
list_attachments, add_attachment, delete_attachment, list_links, add_link, remove_link,
get_history, assign_to, add_screenshot,
get_bug_template, get_severity_list, validate_defect, validate_new_defects,
list_defects_by_environment  (rules: config/*.json + defect_checks.py),
create_defect_full, reopen_defect (one-call workflows),
severity_rules, due_dates, rca_status, rca_set, defect_summary (read-only defect manager,
logic in defect_manager.py).
DEFAULT list format (query_wiql, list_recent_defects, list_defects_by_environment):
ID, Summary, Severity, Assignee, Created Date (Baku time); full=True for all columns.

Config via environment variables (see README section in the chat):
  TFS_PAT              Personal access token (Work Items: Read & Write)
  TFS_BASE_URL         default https://tfsservice.suntech.com.pl/tfs/DefaultCollection
  TFS_PROJECT          default "905. AZTelekom OSS and BSS Support"
  TFS_API_VERSION      default 5.0
  TFS_BUG_TYPE         work item type for NEW defects, default "Aztelekom Bug"
  TFS_BUG_TYPES        types included in lists/queries, default "Bug,Aztelekom Bug"
  TFS_REPRO_FIELD      default Microsoft.VSTS.TCM.ReproSteps
  TFS_SEVERITY_FIELD   default Microsoft.VSTS.Common.Severity
  TFS_SYMPTOM_FIELD   default Microsoft.VSTS.CMMI.Symptom
  TFS_COMMENTS_API_VERSION  default 6.0-preview.3 (list/delete comments; try 5.1-preview.3 on older servers)
  Delete safety (all destructive tools):
  TFS_APPROVAL         dialog (default) | off. "dialog": every real delete/clear/remove opens a
                       Yes/No window on THIS machine; nothing happens without a click.
                       3+ items or a permanent delete also require typing a number.
  TFS_APPROVAL_TIMEOUT seconds to answer the dialog (default 120, then it counts as "No")
  TFS_ALLOW_DELETE     true (default) | false: false blocks delete_work_item / bulk delete
  TFS_DELETE_TYPES     work item types that may be deleted (default = TFS_BUG_TYPES)
  TFS_MAX_DELETE_PER_CALL   default 10;  TFS_MAX_DELETE_PER_HOUR  default 30
  TFS_STRONG_APPROVAL_FROM  default 3 (items from which typing is required)
  TFS_AUDIT_LOG        default tfs_delete_audit.log next to this file
  TFS_VERIFY_SSL       true | false | path to CA bundle (default true)
  TFS_AUTH             pat (default) | ntlm | basic  (ntlm/basic need TFS_USER, TFS_PASSWORD)
  TFS_SCREENSHOT_DIR   folder with screenshots, used by add_screenshot(source="latest")
                       (add_screenshot(source="clipboard") needs: pip install pillow)
"""
import csv
import html
import io
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import quote
from typing import Union

import requests
from mcp.server.fastmcp import FastMCP


def _load_secrets():
    """secrets.env (skriptin yanında): KEY=VALUE sətirləri. Parollar yalnız bu faylda saxlanılır;
    faylda olan dəyər Claude konfiqurasiyasındakı env dəyərini əvəz edir."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip():
                    os.environ[k.strip()] = v.strip()


_load_secrets()

BASE_URL = os.environ.get(
    "TFS_BASE_URL", "https://tfsservice.suntech.com.pl/tfs/DefaultCollection"
).rstrip("/")
PROJECT = os.environ.get("TFS_PROJECT", "905. AZTelekom OSS and BSS Support")
API = os.environ.get("TFS_API_VERSION", "5.0")
BUG_TYPE = os.environ.get("TFS_BUG_TYPE", "Aztelekom Bug")  # type used when creating defects
BUG_TYPES = [t.strip() for t in os.environ.get("TFS_BUG_TYPES", "Bug,Aztelekom Bug").split(",") if t.strip()]
if BUG_TYPE not in BUG_TYPES:
    BUG_TYPES.append(BUG_TYPE)
BUG_TYPES_SQL = ", ".join("'" + t.replace("'", "''") + "'" for t in BUG_TYPES)
# Template yoxlamasında (validate_new_defects, defect_monitor.py) nəzərə alınmayan state-lər
SKIP_STATES = [s.strip() for s in os.environ.get("TFS_VALIDATION_SKIP_STATES", "Closed").split(",") if s.strip()]
REPRO_FIELD = os.environ.get("TFS_REPRO_FIELD", "Microsoft.VSTS.TCM.ReproSteps")
DESC_FIELD = "System.Description"
SEVERITY_FIELD = os.environ.get("TFS_SEVERITY_FIELD", "Microsoft.VSTS.Common.Severity")
AUTH_MODE = os.environ.get("TFS_AUTH", "pat").lower()
SYMPTOM_FIELD = os.environ.get("TFS_SYMPTOM_FIELD", "Microsoft.VSTS.CMMI.Symptom")
# New defects are assigned here when create_defect gets no assigned_to
DEFAULT_ASSIGNEE = os.environ.get("TFS_DEFAULT_ASSIGNEE", "SUNTECH\\Suntech_AZTelekom")
COMMENTS_API = os.environ.get("TFS_COMMENTS_API_VERSION", "6.0-preview.3")

APPROVAL_MODE = os.environ.get("TFS_APPROVAL", "dialog").lower()
APPROVAL_TIMEOUT = int(os.environ.get("TFS_APPROVAL_TIMEOUT", "120"))
DELETE_TYPES = {t.strip() for t in os.environ.get("TFS_DELETE_TYPES", ",".join(BUG_TYPES)).split(",") if t.strip()}
MAX_PER_CALL = int(os.environ.get("TFS_MAX_DELETE_PER_CALL", "10"))
MAX_PER_HOUR = int(os.environ.get("TFS_MAX_DELETE_PER_HOUR", "30"))
STRONG_FROM = int(os.environ.get("TFS_STRONG_APPROVAL_FROM", "3"))
AUDIT_LOG = os.environ.get(
    "TFS_AUDIT_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tfs_delete_audit.log")
)

# aliases accepted by clear_fields; any other value is used as a field reference name
FIELD_ALIASES = {
    "symptom": SYMPTOM_FIELD,
    "description": DESC_FIELD,
    "repro_steps": REPRO_FIELD,
    "tags": "System.Tags",
}
# add_link: what the TARGET is to the work item (or pass a raw link type reference name)
LINK_TYPES = {
    "related": "System.LinkTypes.Related",
    "parent": "System.LinkTypes.Hierarchy-Reverse",
    "child": "System.LinkTypes.Hierarchy-Forward",
    "predecessor": "System.LinkTypes.Dependency-Reverse",
    "successor": "System.LinkTypes.Dependency-Forward",
    "tested_by": "Microsoft.VSTS.Common.TestedBy-Forward",
    "tests": "Microsoft.VSTS.Common.TestedBy-Reverse",
}
# noisy fields hidden in get_history
IGNORED_UPDATE_FIELDS = {
    "System.Rev", "System.AuthorizedDate", "System.RevisedDate", "System.ChangedDate",
    "System.Watermark", "System.AuthorizedAs", "System.PersonId",
}
# core fields that clear_fields / str_replace_field refuse to touch
PROTECTED_FIELDS = {
    f.lower()
    for f in (
        "System.Id", "System.Rev", "System.Title", "System.State", "System.Reason",
        "System.WorkItemType", "System.TeamProject", "System.AreaPath",
        "System.IterationPath", "System.CreatedDate", "System.CreatedBy",
        "System.ChangedDate", "System.ChangedBy", "System.History",
    )
}

LIST_FIELDS = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.AssignedTo",
    "System.AreaPath",
    "System.IterationPath",
    "System.CreatedDate",
    "System.ChangedDate",
    "System.Tags",
    SEVERITY_FIELD,
]

SERVER_INSTRUCTIONS = """AZTelekom TFS defects. OUTPUT RULES - follow them in EVERY answer, show nothing extra unless the user asks:
1. Bug info / bug list ("bug göstər", "bug getir", "tell me about #ID") -> ONE table with exactly these columns:
   ID | Summary | Severity | Assignee | Created Date. No state, no description, no comments, no RCA/SLA.
   get_work_item (default) and the list tools already return exactly these columns - just show them as a table.
2. Due date questions -> use due_dates; table: ID | Summary | Severity | Assignee | Created Date | Due Date |
   Due Date Changed (times) | Overdue.
3. RCA / root cause questions -> use rca_status; table: ID | Summary | Severity | Assignee | Created Date |
   RCA - in comment | RCA - Root Cause field (always both columns, separately).
4. Severity check -> severity_rules + bug text; open PROD defects only (severity is required only for PROD);
   General table + a "Suggested" column for the wrong ones, list the correct ones separately.
5. Template check -> validate_defect / validate_new_defects; General table + a "Problem" column. Report only:
   empty Found in Environment field, empty/placeholder required sections, title not starting with [Name].
   A missing [ENV] or [Module] tag in the title is NOT a problem.
6. Dates are Baku time (UTC+4). Defects = work item types Bug and Aztelekom Bug.
7. Answer in the language the user wrote in. Comments are plain text (**bold**); the [Name Surname] prefix is added
   automatically (TFS_AUTHOR_NAME)."""

mcp = FastMCP("tfs-defects", instructions=SERVER_INSTRUCTIONS)
_session_cache = None


# ---------------------------------------------------------------- HTTP layer
def _session():
    global _session_cache
    if _session_cache is not None:
        return _session_cache
    s = requests.Session()
    verify = os.environ.get("TFS_VERIFY_SSL", "true")
    if verify.lower() == "false":
        s.verify = False
    elif verify.lower() == "true":
        s.verify = True
    else:
        s.verify = verify
    if AUTH_MODE == "ntlm":
        from requests_ntlm import HttpNtlmAuth

        s.auth = HttpNtlmAuth(os.environ["TFS_USER"], os.environ["TFS_PASSWORD"])
    elif AUTH_MODE == "basic":
        s.auth = (os.environ["TFS_USER"], os.environ["TFS_PASSWORD"])
    else:
        pat = os.environ.get("TFS_PAT") or os.environ.get("TFS_PASSWORD")   # extension: PAT parol sahəsində
        if not pat:
            raise RuntimeError("TFS_PAT is not set")
        s.auth = ("", pat)
    _session_cache = s
    return s


READ_TIMEOUT = int(os.environ.get("TFS_READ_TIMEOUT", "25"))     # MCP bridge cuts at ~60 s
WRITE_TIMEOUT = int(os.environ.get("TFS_WRITE_TIMEOUT", "60"))
READ_RETRIES = int(os.environ.get("TFS_READ_RETRIES", "2"))      # extra attempts, reads only
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _call(method, path, params=None, json_body=None, content_type=None, data=None,
          allow_redirects=True, read_only=None):
    """read_only: GET by default; POST queries (wiql, workitemsbatch) pass True. Only read-only calls
    are retried (429 / 5xx / timeout / connection error) - a write is never sent twice."""
    url = f"{BASE_URL}/{quote(PROJECT)}/_apis/{path}"
    p = {"api-version": API}
    p.update(params or {})
    headers = {"Content-Type": content_type} if content_type else {}
    if read_only is None:
        read_only = method.upper() == "GET" or (
            method.upper() == "POST" and path in ("wit/wiql", "wit/workitemsbatch"))
    attempts = 1 + (READ_RETRIES if read_only else 0)
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            r = _session().request(
                method, url, params=p, json=json_body, data=data, headers=headers,
                timeout=READ_TIMEOUT if read_only else WRITE_TIMEOUT, allow_redirects=allow_redirects,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            if last:
                raise RuntimeError(f"TFS {method} {path}: {type(e).__name__} after {attempts} attempt(s)") from e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in _RETRY_STATUS and not last:
            try:
                wait = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            time.sleep(min(10.0, wait or 1.5 * (attempt + 1)))
            continue
        break
    # a redirect on a write call is never a success (requests turns a redirected DELETE/PATCH
    # into a GET, which "succeeds" without changing anything)
    if 300 <= r.status_code < 400:
        raise RuntimeError(
            f"TFS redirect {r.status_code} -> {r.headers.get('Location')} "
            f"(the {method} request was not executed)"
        )
    if not r.ok:
        raise RuntimeError(f"TFS {r.status_code}: {r.text[:800]}")
    if r.status_code == 204 or not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(
            "TFS returned a non-JSON response (usually an auth problem: check TFS_AUTH / TFS_USER / TFS_PASSWORD)"
        )


def _item_url(item_id):
    return f"{BASE_URL}/{quote(PROJECT)}/_workitems/edit/{item_id}"


def _patch(item_id, ops):
    return _call(
        "PATCH",
        f"wit/workitems/{item_id}",
        json_body=ops,
        content_type="application/json-patch+json",
        allow_redirects=False,
    )


def _op(field, value):
    return {"op": "add", "path": f"/fields/{field}", "value": value}


def _op_remove(field):
    return {"op": "remove", "path": f"/fields/{field}"}


# ---------------------------------------------------------------- author name (shared TFS account)
# Hamı eyni AZTelekom hesabı ilə yazır; real müəllif [Ad Soyad] prefiksindən görünür.
# TFS_AUTHOR_NAME verilibsə comment-lərə və yeni defekt başlıqlarına avtomatik əlavə olunur.
_PREFIX_RE = re.compile(r"^\s*(?:<[^>]+>\s*)*\[([^\]]{2,60})\]")
_NOT_NAME = {"prod", "preprod", "pre-prod", "stg", "dr", "uat", "test"}


def _author_name():
    return (os.environ.get("TFS_AUTHOR_NAME") or "").strip()


def _author_text(text):
    """Comment text: '[Ad Soyad] ...' prefiksi yoxdursa əlavə edir."""
    name = _author_name()
    if not name:
        return text or ""
    t = text or ""
    m = _PREFIX_RE.match(t)
    if m and m.group(1).strip().lower() not in _NOT_NAME:
        return t
    return f"[{name}] {t}".rstrip()


def _author_title(title):
    """Defekt başlığı reporter adı ilə başlamırsa '[Ad Soyad] ' əlavə edir."""
    name = _author_name()
    if not name:
        return title
    m = _PREFIX_RE.match(title or "")
    if m and m.group(1).strip().lower() not in _NOT_NAME and not m.group(1).strip().upper().startswith(("PROD", "PREPROD", "STG")):
        return title
    return f"[{name}] {title}"


def _still_exists(item_id):
    """True if the work item can still be read (i.e. it was NOT deleted)."""
    try:
        _call("GET", f"wit/workitems/{item_id}", params={"fields": "System.Id"})
        return True
    except RuntimeError as e:
        if "TFS 404" in str(e):
            return False
        raise


# ------------------------------------------------------------ HTML builders
def _html_text(text):
    return html.escape(text or "").replace("\n", "<br>")


def _html_steps(steps):
    raw = (steps or "").replace(" | ", "\n")
    items = [re.sub(r"^\d+[\.\)]\s*", "", s.strip()) for s in raw.splitlines() if s.strip()]
    if not items:
        return ""
    return "<ol>" + "".join(f"<li>{html.escape(i)}</li>" for i in items) + "</ol>"


def _text_value(item_id, field, text, mode):
    """HTML value for a text field: replace it, or append after the current value."""
    mode = (mode or "replace").lower()
    if mode not in ("replace", "append"):
        raise ValueError("mode must be 'replace' or 'append'")
    new_html = _html_text(text)
    if mode == "append":
        cur = _call("GET", f"wit/workitems/{item_id}", params={"fields": field})
        old = (cur.get("fields", {}).get(field) or "").strip()
        if old:
            new_html = f"{old}<br><br>{new_html}"
    return new_html


def _section(title, body_html=""):
    return f"<b>{html.escape(title)}</b><br>{body_html}<br><br>"


# Optional sections are always written; when not filled they keep the template placeholder
# (validate_defect treats a placeholder in an optional section as empty, not as an error).
OPTIONAL_PLACEHOLDERS = {
    "Test Data": "[If exists, unique issue]",
    "Supporting Logs/Trace": "[Any request/response payloads, error messages]",
    "Related Work": "[Linked parent/child items, requirements]",
}


def _optional_section(title, text):
    return _section(title, _html_text(text or OPTIONAL_PLACEHOLDERS[title]))


def _insert_screenshot_html(old, img_html):
    """Put new evidence at the end of the 'Screenshots / Attachments' section, i.e. right
    before the 'Supporting Logs/Trace' heading when it exists, otherwise at the end."""
    m = re.search(r"<b>\s*Supporting Logs\s*/\s*Trace\s*:?\s*</b>", old or "", re.I)
    if not m:
        return f"{old}<br>{img_html}" if old else img_html
    return f"{old[:m.start()]}{img_html}<br>{old[m.start():]}"


def _env_value(raw):
    """Found in Environment value (PROD / PREPROD / DR ...) taken from config/environments.json."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    allowed = [v for e in _dc.load_config()["env"]["environments"] for v in e["tfs_values"]]
    hit = next((v for v in allowed if v.lower() == raw.lower()), None)
    if not hit:
        raise ValueError(f"environment must be one of: {', '.join(allowed)} (got {raw!r})")
    return hit


def _build_ops(d):
    """Every defect is written in the bug template layout (config/bug_template.md) into the
    Symptom field, so it always passes validate_defect. Description / Repro Steps stay empty."""
    summary = (d.get("summary") or "").strip()
    if not summary:
        raise ValueError("summary is required")
    summary = _author_title(summary)
    ops = [_op("System.Title", summary)]

    desc = "<br><br>".join(_html_text(p) for p in (d.get("description"), d.get("symptom")) if p)
    body = _section("Description / Symptom", desc)
    body += _section("Preconditions", _html_text(d.get("preconditions")))
    body += _optional_section("Test Data", d.get("test_data"))
    body += _section("STR", _html_steps(d.get("steps")))
    body += _section("Expected Result", _html_text(d.get("expected")))
    body += _section("Actual Result", _html_text(d.get("actual")))
    body += _section("Screenshots / Attachments")  # filled later via add_screenshot / add_attachment
    body += _optional_section("Supporting Logs/Trace", d.get("logs"))
    body += _optional_section("Related Work", d.get("related"))
    ops.append(_op(SYMPTOM_FIELD, body))

    env = _env_value(d.get("environment"))
    if env:
        ops.append(_op(_dc.load_config()["fields"]["found_in_environment"], env))

    if d.get("severity"):
        ops.append(_op(SEVERITY_FIELD, d["severity"]))
    if d.get("area_path"):
        ops.append(_op("System.AreaPath", d["area_path"]))
    if d.get("iteration_path"):
        ops.append(_op("System.IterationPath", d["iteration_path"]))
    assignee = (d.get("assigned_to") or DEFAULT_ASSIGNEE).strip()
    if assignee:
        ops.append(_op("System.AssignedTo", assignee))
    if d.get("tags"):
        ops.append(_op("System.Tags", d["tags"].replace(",", ";")))
    return ops


def _template_issues(ops):
    """Run the validator on the ops before saving (attachments are added later, so that
    section is treated as satisfied)."""
    fields = {o["path"].split("/fields/", 1)[1]: o["value"] for o in ops}
    res = _dc.validate_fields(0, fields, attach_fn=lambda: True)
    return [{"level": i["level"], "message": i["message"]} for i in res["issues"]]


def _create(d, dry_run):
    ops = _build_ops(d)
    issues = _template_issues(ops)
    if dry_run:
        return {"dry_run": True, "title": d["summary"], "ops": ops, "template_issues": issues}
    res = _call(
        "POST",
        f"wit/workitems/${quote(BUG_TYPE)}",
        json_body=ops,
        content_type="application/json-patch+json",
        allow_redirects=False,
    )
    return {"id": res["id"], "title": d["summary"], "url": _item_url(res["id"]),
            "template_issues": issues}


# ------------------------------------------------------------------- queries
def _name(v):
    if isinstance(v, dict):
        return v.get("displayName") or v.get("uniqueName") or ""
    return v or ""


def _wq(value):
    """String value for a WIQL literal ('...'): single quotes doubled."""
    return str(value).replace("'", "''")


_batch_ok = os.environ.get("TFS_USE_BATCH", "true").lower() != "false"


def _fetch_batch(ids, field_list):
    """Work items by id, in the given order. POST wit/workitemsbatch (200 per call); deleted / missing
    ids are skipped instead of failing the whole call. Falls back to GET wit/workitems (100 per call)
    if the server does not support the batch endpoint."""
    global _batch_ok
    ids = [int(i) for i in ids]
    items = []
    if _batch_ok:
        try:
            for i in range(0, len(ids), 200):
                res = _call("POST", "wit/workitemsbatch",
                            json_body={"ids": ids[i : i + 200], "fields": list(field_list), "errorPolicy": "omit"},
                            content_type="application/json")
                items.extend(v for v in res.get("value", []) if v)
            return items
        except RuntimeError as e:
            if not re.search(r"TFS (400|404|405)\b", str(e)):
                raise
            _batch_ok, items = False, []   # endpoint not available -> GET from now on
    for i in range(0, len(ids), 100):
        res = _call("GET", "wit/workitems",
                    params={"ids": ",".join(map(str, ids[i : i + 100])), "fields": ",".join(field_list),
                            "errorPolicy": "omit"})
        items.extend(v for v in res.get("value", []) if v)
    return items


def _fetch_items(ids):
    items = _fetch_batch(ids, LIST_FIELDS)
    rows = []
    for it in items:
        f = it.get("fields", {})
        rows.append(
            {
                "id": it["id"],
                "title": f.get("System.Title", ""),
                "type": f.get("System.WorkItemType", ""),
                "state": f.get("System.State", ""),
                "severity": f.get(SEVERITY_FIELD, ""),
                "assigned_to": _name(f.get("System.AssignedTo")),
                "area": f.get("System.AreaPath", ""),
                "iteration": f.get("System.IterationPath", ""),
                "created": (f.get("System.CreatedDate", "") or "")[:19].replace("T", " "),
                "changed": (f.get("System.ChangedDate", "") or "")[:19].replace("T", " "),
                "tags": f.get("System.Tags", ""),
                "url": _item_url(it["id"]),
            }
        )
    return rows


def _run_wiql(wiql, top):
    res = _call("POST", "wit/wiql", json_body={"query": wiql})
    ids = [w["id"] for w in res.get("workItems", [])][:top]
    return _fetch_items(ids) if ids else []


def _recent_wiql(days, state=None):
    q = (
        "SELECT [System.Id] FROM WorkItems "
        "WHERE [System.TeamProject] = @project "
        f"AND [System.WorkItemType] IN ({BUG_TYPES_SQL}) "
        f"AND [System.ChangedDate] >= @Today - {int(days)} "
    )
    if state:
        q += f"AND [System.State] = '{_wq(state)}' "
    return q + "ORDER BY [System.ChangedDate] DESC"


# --------------------------------------------------------------------- tools
@mcp.tool()
def get_work_item(item_id: int, full: bool = False) -> dict:
    """One work item. DEFAULT = General template (show exactly this as a table):
    ID, Summary, Severity, Assignee, Created Date (Baku time).
    full=True: ALL raw fields (Symptom HTML, state, environment ...) - only when the details are really needed
    (e.g. reading the bug text for a severity check)."""
    if not full:
        rows = _fetch_items([int(item_id)])
        if not rows:
            raise RuntimeError(f"Work item {item_id} not found")
        import defect_manager as _dmc
        return {**_dmc.compact_row(rows[0]), "url": rows[0]["url"]}
    res = _call("GET", f"wit/workitems/{item_id}")
    return {"id": res["id"], "url": _item_url(item_id), "fields": res.get("fields", {})}


@mcp.tool()
def create_defect(
    summary: str,
    steps: str = "",
    expected: str = "",
    actual: str = "",
    environment: str = "",
    description: str = "",
    severity: str = "",
    area_path: str = "",
    iteration_path: str = "",
    assigned_to: str = "",
    tags: str = "",
    symptom: str = "",
    preconditions: str = "",
    test_data: str = "",
    logs: str = "",
    related: str = "",
    layout_in_symptom: bool = True,
    dry_run: bool = False,
) -> dict:
    """Create a defect in the bug template layout (see get_bug_template). Everything goes into
    the Symptom field: Description / Symptom (description + symptom), Preconditions,
    Test Data, STR, Expected Result, Actual Result, Screenshots / Attachments
    (empty: add the evidence with add_screenshot / add_attachment), Supporting Logs/Trace,
    Related Work. Test Data / Supporting Logs/Trace / Related Work are ALWAYS written; when
    test_data / logs / related are empty they keep the template placeholder text.
    related: linked parent/child items, requirements (e.g. 'Related to #330196'). environment fills the Found in Environment field (PROD / PREPROD / STG / DR).
    Steps: one per line or separated by ' | '. Severity e.g. '1 - Critical'.
    assigned_to: empty = default assignee Suntech_AZTelekom (env TFS_DEFAULT_ASSIGNEE).
    layout_in_symptom is kept for compatibility and ignored. The result has template_issues
    (what validate_defect would report). dry_run=true previews without creating."""
    return _create(
        dict(
            summary=summary,
            steps=steps,
            expected=expected,
            actual=actual,
            environment=environment,
            description=description,
            severity=severity,
            area_path=area_path,
            iteration_path=iteration_path,
            assigned_to=assigned_to,
            tags=tags,
            symptom=symptom,
            preconditions=preconditions,
            test_data=test_data,
            logs=logs,
            related=related,
        ),
        dry_run,
    )


@mcp.tool()
def bulk_create_from_csv(csv_path: str, dry_run: bool = True) -> dict:
    """Create defects from a CSV. Columns: summary (required), environment, description,
    steps, expected, actual, severity, area_path, iteration_path, assigned_to, tags,
    symptom, preconditions, test_data, logs, related. All defects use the bug template layout in the
    Symptom field; environment must be PROD / PREPROD / STG / DR (Found in Environment field).
    dry_run defaults to true: review the preview first, then call again with dry_run=false."""
    created, errors = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            d = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                created.append(_create(d, dry_run))
            except Exception as e:  # keep going, report per row
                errors.append({"csv_line": n, "summary": d.get("summary", ""), "error": str(e)})
    return {"dry_run": dry_run, "ok": len(created), "failed": len(errors),
            "created": created if not dry_run else [c["title"] for c in created],
            "errors": errors}


def _compact(rows):
    import defect_manager as _dmc
    return [_dmc.compact_row(r) for r in rows]


@mcp.tool()
def query_wiql(wiql: str, top: int = 200, full: bool = False) -> list:
    """Run a WIQL query (SELECT [System.Id] FROM WorkItems WHERE ...) and return the items.
    DEFAULT format: ID, Summary, Severity, Assignee, Created Date (Baku time). full=True: all columns
    (type, state, area, iteration, changed, tags, url)."""
    rows = _run_wiql(wiql, top)
    return rows if full else _compact(rows)


@mcp.tool()
def list_recent_defects(days: int = 7, state: str = "", top: int = 200, full: bool = False) -> list:
    """List defects changed in the last N days, optionally filtered by state.
    DEFAULT format: ID, Summary, Severity, Assignee, Created Date (Baku time). full=True: all columns."""
    rows = _run_wiql(_recent_wiql(days, state or None), top)
    return rows if full else _compact(rows)


def _upload_image(source):
    """Upload a screenshot to the TFS attachment store WITHOUT linking it to the item
    (same as pasting an image in the web editor). Returns (url, info)."""
    data, name = _grab_screenshot(source)
    up = _call(
        "POST",
        "wit/attachments",
        params={"fileName": name},
        data=data,
        content_type="application/octet-stream",
        allow_redirects=False,
    )
    return up["url"], {"screenshot": name, "size_kb": round(len(data) / 1024, 1)}


def _img_html(url):
    return f'<div><img src="{html.escape(url, quote=True)}" alt="Image"></div>'


def _comment_html(text, screenshot=""):
    """Plain text -> comment HTML; with screenshot, the image is placed INSIDE the comment, under the text."""
    # **bold** in the plain text becomes <b>bold</b>
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _html_text(_author_text(text)))
    if not screenshot:
        return body, {}
    url, info = _upload_image(screenshot)
    return (body + "<br>" if body else "") + _img_html(url), info


@mcp.tool()
def update_status(
    item_id: int, state: str, comment: str = "", reason: str = "", screenshot: str = ""
) -> dict:
    """Change the state of a work item (e.g. Active, Resolved, Closed); optional comment and reason.
    comment is PLAIN TEXT (no HTML; **text** = bold). screenshot: '' (none), 'latest' (newest image in
    TFS_SCREENSHOT_DIR), 'clipboard', or a file name in TFS_SCREENSHOT_DIR / full path -
    the image is shown INSIDE the comment, not as a separate attachment."""
    ops = [_op("System.State", state)]
    if reason:
        ops.append(_op("System.Reason", reason))
    info = {}
    if comment or screenshot:
        body, info = _comment_html(comment, screenshot)
        ops.append(_op("System.History", body))
    res = _patch(item_id, ops)
    return {"id": item_id, "state": res["fields"].get("System.State"), **info, "url": _item_url(item_id)}


@mcp.tool()
def add_comment(item_id: int, text: str, screenshot: str = "") -> dict:
    """Add a comment (Discussion entry) to a work item. text is PLAIN TEXT (no HTML; **text** = bold).
    screenshot: '' (none), 'latest' (newest image in TFS_SCREENSHOT_DIR), 'clipboard',
    or a file name in TFS_SCREENSHOT_DIR / full path - the image is shown INSIDE the comment."""
    body, info = _comment_html(text, screenshot)
    _patch(item_id, [_op("System.History", body)])
    return {"id": item_id, **info, "url": _item_url(item_id)}


@mcp.tool()
def update_fields(
    item_id: int,
    title: str = "",
    description: str = "",
    description_mode: str = "replace",
    symptom: str = "",
    symptom_mode: str = "replace",
    repro_steps: str = "",
    severity: str = "",
    assigned_to: str = "",
    area_path: str = "",
    iteration_path: str = "",
    tags: str = "",
    extra_fields: str = "",
    comment: str = "",
    dry_run: bool = False,
) -> dict:
    """Edit fields of an EXISTING work item. Only the arguments you pass are changed.
    description_mode: 'replace' (default) overwrites Description, 'append' adds the text
    after the current Description. symptom / symptom_mode ('replace' | 'append') do the same
    for the Symptom field. repro_steps edits Repro Steps (Microsoft.VSTS.TCM.ReproSteps).
    tags: ';' or ',' separated (replaces existing tags). extra_fields: JSON object of
    {"Field.RefName": value} for any other field. comment: optional History entry.
    Set dry_run=true to preview the patch without saving."""
    ops = []
    if title:
        ops.append(_op("System.Title", title.strip()))
    if description:
        ops.append(_op(DESC_FIELD, _text_value(item_id, DESC_FIELD, description, description_mode)))
    if symptom:
        ops.append(_op(SYMPTOM_FIELD, _text_value(item_id, SYMPTOM_FIELD, symptom, symptom_mode)))
    if repro_steps:
        ops.append(_op(REPRO_FIELD, _html_text(repro_steps)))
    if severity:
        ops.append(_op(SEVERITY_FIELD, severity))
    if assigned_to:
        ops.append(_op("System.AssignedTo", assigned_to))
    if area_path:
        ops.append(_op("System.AreaPath", area_path))
    if iteration_path:
        ops.append(_op("System.IterationPath", iteration_path))
    if tags:
        ops.append(_op("System.Tags", tags.replace(",", ";")))
    if extra_fields:
        extra = json.loads(extra_fields)
        if not isinstance(extra, dict):
            raise ValueError("extra_fields must be a JSON object")
        for k, v in extra.items():
            ops.append(_op(k, v))
    if comment:
        ops.append(_op("System.History", _html_text(_author_text(comment))))
    if not ops:
        raise ValueError("nothing to update: pass at least one field")
    if dry_run:
        return {"dry_run": True, "id": item_id, "ops": ops}
    res = _patch(item_id, ops)
    return {
        "id": item_id,
        "updated": [o["path"].split("/fields/")[1] for o in ops],
        "rev": res.get("rev"),
        "url": _item_url(item_id),
    }


# ------------------------------------------------- comments / delete / clear
# ---- delete safety: out-of-band human approval, quotas, audit log
# The model can fill in any tool argument, so a flag or a "confirm" parameter is not a
# real second factor. The approval below is a window opened on the user's own screen by a
# separate process: the model cannot see it, click it or answer it.
_DIALOG_SCRIPT = r"""
import sys, json
d = json.loads(sys.stdin.read())
try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    root = tk.Tk()
except Exception:
    sys.exit(3)
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
root.update()
ok = messagebox.askyesno(d["title"], d["message"], icon="warning", default="no", parent=root)
if ok and d.get("expect"):
    typed = simpledialog.askstring(d["title"], "Type  " + d["expect"] + "  to confirm:", parent=root)
    ok = (typed or "").strip() == d["expect"]
sys.exit(0 if ok else 1)
"""
_deleted_at = []  # monotonic times of items deleted by this server process


def _audit(event, **data):
    try:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, "project": PROJECT, **data}
        os.makedirs(os.path.dirname(os.path.abspath(AUDIT_LOG)), exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the audit log is best effort


def _approve(action, lines, expect=None):
    """True only if the human explicitly approved. Raises if no dialog can be shown."""
    if APPROVAL_MODE == "off":
        return True
    message = f"{action}\n\n" + "\n".join(lines) + f"\n\nProject: {PROJECT}\n\nAllow this?"
    payload = json.dumps({"title": "TFS approval", "message": message, "expect": expect})
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window
    try:
        r = subprocess.run(
            [sys.executable, "-c", _DIALOG_SCRIPT],
            input=payload, text=True, capture_output=True, timeout=APPROVAL_TIMEOUT, **kw,
        )
    except subprocess.TimeoutExpired:
        return False
    if r.returncode == 3:
        raise RuntimeError(
            "No confirmation dialog can be shown on this machine (tkinter missing or no display). "
            "Nothing was changed. Install tkinter, or set TFS_APPROVAL=off to disable approvals."
        )
    return r.returncode == 0


def _gate(action, lines, expect=None, target=None):
    """Ask the human, log the answer, raise PermissionError unless approved."""
    if not _approve(action, lines, expect):
        _audit("denied", action=action, target=target)
        raise PermissionError(f"{action}: not approved in the confirmation dialog. Nothing was changed.")
    _audit("approved", action=action, target=target)


def _delete_allowed():
    if os.environ.get("TFS_ALLOW_DELETE", "true").lower() == "false":
        raise PermissionError("Deleting work items is disabled (TFS_ALLOW_DELETE=false).")


def _check_quota(n):
    now = time.monotonic()
    _deleted_at[:] = [t for t in _deleted_at if now - t < 3600]
    if n > MAX_PER_CALL:
        raise ValueError(f"{n} items in one call; the limit is {MAX_PER_CALL} (TFS_MAX_DELETE_PER_CALL)")
    if len(_deleted_at) + n > MAX_PER_HOUR:
        raise ValueError(
            f"hourly delete limit reached: {len(_deleted_at)} deleted in the last hour, "
            f"limit {MAX_PER_HOUR} (TFS_MAX_DELETE_PER_HOUR)"
        )


def _short(v, n=300):
    v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return v if len(v) <= n else v[:n] + "..."


def _resolve_field(raw):
    """Alias or reference name -> reference name; refuses core fields."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("field is empty")
    ref = FIELD_ALIASES.get(raw.lower(), raw)
    if ref.lower() in PROTECTED_FIELDS:
        raise ValueError(f"{ref} is a protected field")
    return ref


@mcp.tool()
def list_comments(item_id: int, top: int = 200) -> dict:
    """List the comments (Discussion entries) of a work item with their ids.
    Use the id with update_comment / delete_comment."""
    res = _call(
        "GET",
        f"wit/workItems/{item_id}/comments",
        params={"api-version": COMMENTS_API, "$top": top},
    )
    comments = [
        {
            "id": c.get("id"),
            "author": _name(c.get("createdBy")),
            "created": (c.get("createdDate") or "")[:19].replace("T", " "),
            "text": c.get("text", ""),
        }
        for c in res.get("comments", [])
        if not c.get("isDeleted")
    ]
    return {"id": item_id, "count": len(comments), "comments": comments, "url": _item_url(item_id)}


@mcp.tool()
def update_comment(item_id: int, comment_id: int, text: str, screenshot: str = "") -> dict:
    """Replace the text of one comment (get comment_id from list_comments). text is PLAIN TEXT (**text** = bold).
    screenshot: '' (none), 'latest', 'clipboard', or a file name / full path - the image is
    shown INSIDE the comment, under the text."""
    body, info = _comment_html(text, screenshot)
    _call(
        "PATCH",
        f"wit/workItems/{item_id}/comments/{comment_id}",
        params={"api-version": COMMENTS_API},
        json_body={"text": body},
        content_type="application/json",
        allow_redirects=False,
    )
    return {"updated": True, "item_id": item_id, "comment_id": comment_id, **info, "url": _item_url(item_id)}


@mcp.tool()
def delete_comment(item_id: int, comment_id: int, dry_run: bool = True) -> dict:
    """Delete one comment of a work item (get comment_id from list_comments).
    dry_run defaults to true: shows the comment that would be deleted.
    dry_run=false opens a Yes/No confirmation window on the user's screen; without a
    click nothing is deleted. Tell the user to expect it."""
    path = f"wit/workItems/{item_id}/comments/{comment_id}"
    cur = _call("GET", path, params={"api-version": COMMENTS_API})
    comment = {
        "id": comment_id,
        "author": _name(cur.get("createdBy")),
        "text": _short(cur.get("text", "")),
    }
    if dry_run:
        return {"dry_run": True, "item_id": item_id, "comment": comment}
    target = {"item_id": item_id, "comment_id": comment_id}
    _gate(
        "Delete a comment",
        [f"Work item #{item_id}, comment {comment_id} by {comment['author']}:", _short(comment["text"], 150)],
        target=target,
    )
    _call("DELETE", path, params={"api-version": COMMENTS_API}, allow_redirects=False)
    _audit("done", action="delete_comment", target=target)
    return {"deleted": True, "item_id": item_id, "comment": comment, "url": _item_url(item_id)}


@mcp.tool()
def clear_fields(item_id: int, fields: str, dry_run: bool = True) -> dict:
    """Clear (empty) fields of an EXISTING work item, e.g. the Symptom of a defect.
    fields: comma-separated. Aliases: symptom, description, repro_steps, tags; or any
    field reference name (e.g. Microsoft.VSTS.CMMI.Symptom). Core fields (Title, State,
    Area/Iteration Path, History, ...) are refused. Fields that are already empty are skipped.
    dry_run defaults to true: shows the values that would be cleared.
    dry_run=false opens a Yes/No confirmation window on the user's screen; without a
    click nothing is cleared. Tell the user to expect it."""
    names = [_resolve_field(raw) for raw in fields.split(",") if raw.strip()]
    if not names:
        raise ValueError("fields is empty")
    cur = _call("GET", f"wit/workitems/{item_id}", params={"fields": ",".join(names)})
    current = cur.get("fields", {})
    by_lower = {k.lower(): k for k in current}
    to_clear = [by_lower[n.lower()] for n in names if n.lower() in by_lower]
    if not to_clear:
        return {"id": item_id, "cleared": [], "note": "all given fields are already empty"}
    if dry_run:
        return {
            "dry_run": True,
            "id": item_id,
            "would_clear": {k: _short(current[k]) for k in to_clear},
        }
    target = {"item_id": item_id, "fields": to_clear}
    _gate(
        "Clear fields",
        [f"Work item #{item_id}:"] + [f"{k}: {_short(current[k], 80)}" for k in to_clear],
        target=target,
    )
    res = _patch(item_id, [_op_remove(k) for k in to_clear])
    _audit("done", action="clear_fields", target=target, rev=res.get("rev"))
    return {"id": item_id, "cleared": to_clear, "rev": res.get("rev"), "url": _item_url(item_id)}


@mcp.tool()
def str_replace_field(
    item_id: int,
    field: str,
    old_str: str,
    new_str: str = "",
    replace_all: bool = False,
    dry_run: bool = False,
) -> dict:
    """Replace text inside ONE field in place (e.g. add ' :)' to the end of a sentence in
    Symptom) without resending the whole value. field: alias (symptom, description,
    repro_steps, tags) or field reference name. old_str is matched against the stored HTML
    of the field and must occur exactly once (unless replace_all=true). new_str is inserted
    as given (HTML allowed, not escaped); empty new_str deletes old_str.
    Set dry_run=true to preview."""
    if not old_str:
        raise ValueError("old_str is empty")
    ref = _resolve_field(field)
    cur = _call("GET", f"wit/workitems/{item_id}", params={"fields": ref})
    fields = cur.get("fields", {})
    key = next((k for k in fields if k.lower() == ref.lower()), None)
    value = fields.get(key) if key else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{ref} is empty or not a text field")
    n = value.count(old_str)
    if n == 0:
        raise ValueError("old_str not found (it is matched against the stored HTML of the field)")
    if n > 1 and not replace_all:
        raise ValueError(f"old_str occurs {n} times: make it longer or pass replace_all=true")
    i = value.index(old_str)
    new_value = value.replace(old_str, new_str)
    result = {
        "id": item_id,
        "field": key,
        "replaced": n if replace_all else 1,
        "before": value[max(0, i - 60) : i + len(old_str) + 60],
        "after": new_value[max(0, i - 60) : i + len(new_str) + 60],
    }
    if dry_run:
        return {"dry_run": True, **result}
    res = _patch(item_id, [_op(key, new_value)])
    return {**result, "rev": res.get("rev"), "url": _item_url(item_id)}


def _preview_item(item_id, destroy):
    """Read a work item and check the delete guards (project, type). Deletes nothing."""
    cur = _call(
        "GET",
        f"wit/workitems/{item_id}",
        params={"fields": "System.Title,System.WorkItemType,System.State,System.TeamProject"},
    )
    f = cur.get("fields", {})
    if f.get("System.TeamProject") != PROJECT:
        raise ValueError(f"#{item_id} is in project '{f.get('System.TeamProject')}', not '{PROJECT}'")
    kind = f.get("System.WorkItemType", "")
    if kind not in DELETE_TYPES:
        raise ValueError(
            f"#{item_id} is a {kind}: only {', '.join(sorted(DELETE_TYPES))} can be deleted (TFS_DELETE_TYPES)"
        )
    return {
        "id": item_id,
        "title": f.get("System.Title", ""),
        "type": kind,
        "state": f.get("System.State", ""),
        "permanent": destroy,
    }


def _delete_flow(infos, destroy):
    """Quota, human approval, delete, VERIFY, audit for already previewed items."""
    n = len(infos)
    _check_quota(n)
    strong = destroy or n >= STRONG_FROM
    expect = (str(infos[0]["id"]) if n == 1 else str(n)) if strong else None
    lines = [f"#{i['id']} [{i['type']}] {_short(i['title'], 70)}" for i in infos[:15]]
    if n > 15:
        lines.append(f"... and {n - 15} more")
    lines.append("PERMANENT: cannot be restored." if destroy else "Goes to the Recycle Bin (can be restored).")
    ids = [i["id"] for i in infos]
    _gate(f"Delete {n} work item{'s' if n != 1 else ''}", lines, expect, target={"ids": ids, "permanent": destroy})
    done, errors = [], []
    for i in infos:
        try:
            # allow_redirects=False: a redirected DELETE would turn into a GET and do nothing
            res = _call(
                "DELETE",
                f"wit/workitems/{i['id']}",
                params={"destroy": "true"} if destroy else None,
                allow_redirects=False,
            )
            # never trust the HTTP answer alone: the item must really be gone
            if _still_exists(i["id"]):
                raise RuntimeError(
                    "TFS answered OK but the work item still exists (not deleted). "
                    f"Response: {_short(res, 200)}"
                )
            done.append({**i, "deleted_date": (res or {}).get("deletedDate", "")})
            _deleted_at.append(time.monotonic())
        except Exception as e:  # keep going, report per id
            errors.append({"id": i["id"], "error": str(e)})
    _audit(
        "done",
        action="delete_work_items",
        ids=[d["id"] for d in done],
        failed=[e["id"] for e in errors],
        permanent=destroy,
    )
    return done, errors


_APPROVAL_NOTE = "dry_run=false opens a confirmation window on the user's screen"


@mcp.tool()
def delete_work_item(
    item_id: int, destroy: bool = False, confirm_id: int = 0, dry_run: bool = True
) -> dict:
    """Delete a work item (defect). Default: moves it to the Recycle Bin
    (restore_work_item brings it back). destroy=true deletes it PERMANENTLY (no restore)
    and also needs confirm_id equal to item_id.
    Only items of the allowed type(s) in the configured project can be deleted.
    dry_run defaults to true: shows what would be deleted.
    dry_run=false opens a confirmation window on the user's screen (Yes/No; a permanent
    delete also needs the id typed). Without approval nothing is deleted: tell the user
    to expect the window and never retry to get around a refusal."""
    _delete_allowed()
    if not dry_run and destroy and confirm_id != item_id:
        raise ValueError("permanent delete needs confirm_id equal to item_id")
    info = _preview_item(item_id, destroy)
    _check_quota(1)
    if dry_run:
        return {"dry_run": True, **info, "next": _APPROVAL_NOTE}
    done, errors = _delete_flow([info], destroy)
    if errors:
        raise RuntimeError(errors[0]["error"])
    return {"deleted": True, **done[0]}


@mcp.tool()
def bulk_delete_work_items(item_ids: str, dry_run: bool = True) -> dict:
    """Delete several work items (Recycle Bin only, never permanent). item_ids: ids
    separated by comma, space or newline; at most TFS_MAX_DELETE_PER_CALL (default 10)
    per call and TFS_MAX_DELETE_PER_HOUR (default 30) per hour. Items of other types or
    projects are refused. dry_run defaults to true: lists what would be deleted.
    dry_run=false opens ONE confirmation window on the user's screen listing all items
    (3 or more items also need the count typed). Without approval nothing is deleted:
    tell the user to expect the window and never retry to get around a refusal."""
    _delete_allowed()
    ids = []
    for t in re.split(r"[,\s;]+", item_ids or ""):
        if not t:
            continue
        if not t.isdigit():
            raise ValueError(f"not a work item id: {t}")
        if int(t) not in ids:
            ids.append(int(t))
    if not ids:
        raise ValueError("item_ids is empty")
    if len(ids) > MAX_PER_CALL:
        raise ValueError(f"{len(ids)} ids in one call; the limit is {MAX_PER_CALL} (TFS_MAX_DELETE_PER_CALL)")
    infos, errors = [], []
    for i in ids:
        try:
            infos.append(_preview_item(i, False))
        except Exception as e:  # report per id
            errors.append({"id": i, "error": str(e)})
    _check_quota(len(infos))
    if dry_run or not infos:
        return {
            "dry_run": dry_run,
            "ok": len(infos),
            "failed": len(errors),
            "items": infos,
            "errors": errors,
            **({"next": _APPROVAL_NOTE} if dry_run and infos else {}),
        }
    done, derrors = _delete_flow(infos, False)
    return {
        "dry_run": False,
        "ok": len(done),
        "failed": len(errors) + len(derrors),
        "items": done,
        "errors": errors + derrors,
    }


@mcp.tool()
def restore_work_item(item_id: int) -> dict:
    """Restore a work item from the Recycle Bin (only if it was deleted without destroy)."""
    _call(
        "PATCH",
        f"wit/recyclebin/{item_id}",
        json_body={"IsDeleted": False},
        content_type="application/json",
        allow_redirects=False,
    )
    return {"restored": True, "id": item_id, "url": _item_url(item_id)}


@mcp.tool()
def list_deleted_work_items(top: int = 50) -> list:
    """List the work items currently in the Recycle Bin of the project, newest first."""
    res = _call("GET", "wit/recyclebin")
    rows = [
        {
            "id": x.get("id"),
            "type": x.get("type", ""),
            "title": x.get("name", ""),
            "deleted": (x.get("deletedDate") or "")[:19].replace("T", " "),
            "deleted_by": _name(x.get("deletedBy")),
        }
        for x in res.get("value", [])
    ]
    rows.sort(key=lambda r: r["deleted"], reverse=True)
    return rows[:top]


# ------------------------------------------------------ attachments / links
def _relations(item_id):
    res = _call("GET", f"wit/workitems/{item_id}", params={"$expand": "relations"})
    return res.get("relations") or []


def _attach_id(url):
    return (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _target_id(url):
    m = re.search(r"/workitems/(\d+)$", (url or "").split("?")[0], re.I)
    return int(m.group(1)) if m else None


@mcp.tool()
def list_attachments(item_id: int) -> dict:
    """List the files attached to a work item (attachment_id is used by delete_attachment)."""
    out = []
    for r in _relations(item_id):
        if r.get("rel") != "AttachedFile":
            continue
        a = r.get("attributes", {})
        out.append(
            {
                "attachment_id": _attach_id(r.get("url")),
                "name": a.get("name", ""),
                "size": a.get("resourceSize"),
                "comment": a.get("comment", ""),
                "added": (a.get("authorizedDate") or "")[:19].replace("T", " "),
            }
        )
    return {"id": item_id, "count": len(out), "attachments": out, "url": _item_url(item_id)}


@mcp.tool()
def add_attachment(item_id: int, file_path: str, comment: str = "") -> dict:
    """Upload a local file (e.g. a screenshot) and attach it to a work item.
    file_path is a path on the machine where this MCP server runs."""
    if not os.path.isfile(file_path):
        raise ValueError(f"file not found: {file_path}")
    name = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        up = _call(
            "POST",
            "wit/attachments",
            params={"fileName": name},
            data=fh.read(),
            content_type="application/octet-stream",
            allow_redirects=False,
        )
    rel = {"rel": "AttachedFile", "url": up["url"], "attributes": {"comment": comment}}
    res = _patch(item_id, [{"op": "add", "path": "/relations/-", "value": rel}])
    return {
        "id": item_id,
        "attached": name,
        "attachment_id": up.get("id"),
        "rev": res.get("rev"),
        "url": _item_url(item_id),
    }


@mcp.tool()
def delete_attachment(item_id: int, attachment_id: str, dry_run: bool = True) -> dict:
    """Remove an attachment from a work item (attachment_id from list_attachments).
    Images pasted inside a field's HTML (<img> in Symptom/Description) are not touched:
    use str_replace_field for those. dry_run defaults to true: shows what would be removed.
    dry_run=false opens a Yes/No confirmation window on the user's screen; without a
    click nothing is removed. Tell the user to expect it."""
    wanted = (attachment_id or "").strip().lower()
    hits = [
        (i, r)
        for i, r in enumerate(_relations(item_id))
        if r.get("rel") == "AttachedFile" and _attach_id(r.get("url")).lower() == wanted
    ]
    if not hits:
        raise ValueError("attachment not found on this work item (see list_attachments)")
    i, r = hits[0]
    info = {"attachment_id": _attach_id(r["url"]), "name": r.get("attributes", {}).get("name", "")}
    if dry_run:
        return {"dry_run": True, "id": item_id, **info}
    _gate("Remove an attachment", [f"Work item #{item_id}: {info['name']}"], target={"item_id": item_id, **info})
    res = _patch(item_id, [{"op": "remove", "path": f"/relations/{i}"}])
    _audit("done", action="delete_attachment", target={"item_id": item_id, **info}, rev=res.get("rev"))
    return {"removed": True, "id": item_id, **info, "rev": res.get("rev"), "url": _item_url(item_id)}


@mcp.tool()
def list_links(item_id: int) -> dict:
    """List the links of a work item (related, parent, child, hyperlinks, commits, ...).
    Attachments are listed by list_attachments."""
    out = []
    for r in _relations(item_id):
        if r.get("rel") == "AttachedFile":
            continue
        a = r.get("attributes", {})
        out.append(
            {
                "rel": r.get("rel", ""),
                "name": a.get("name", ""),
                "target_id": _target_id(r.get("url")),
                "url": r.get("url", ""),
                "comment": a.get("comment", ""),
            }
        )
    return {"id": item_id, "count": len(out), "links": out, "url": _item_url(item_id)}


@mcp.tool()
def add_link(item_id: int, target_id: int, link_type: str = "related", comment: str = "") -> dict:
    """Link a work item to another work item. link_type says what the TARGET is to this
    item: related, parent, child, predecessor, successor, tested_by, tests
    (or a raw link type reference name, e.g. System.LinkTypes.Related)."""
    key = (link_type or "related").strip()
    rel = LINK_TYPES.get(key.lower()) or (key if "." in key else None)
    if not rel:
        raise ValueError(f"unknown link_type '{key}': use one of {', '.join(LINK_TYPES)}")
    value = {"rel": rel, "url": f"{BASE_URL}/_apis/wit/workItems/{target_id}"}
    if comment:
        value["attributes"] = {"comment": comment}
    res = _patch(item_id, [{"op": "add", "path": "/relations/-", "value": value}])
    return {"id": item_id, "linked": target_id, "rel": rel, "rev": res.get("rev"), "url": _item_url(item_id)}


@mcp.tool()
def remove_link(
    item_id: int, target_id: int = 0, url: str = "", rel: str = "", dry_run: bool = True
) -> dict:
    """Remove one link from a work item. Give target_id (linked work item) or url
    (hyperlink / artifact link). If several links match, add rel (e.g. 'related', 'parent'
    or the reference name) to pick one. Attachments are removed with delete_attachment.
    dry_run defaults to true: shows the link that would be removed.
    dry_run=false opens a Yes/No confirmation window on the user's screen; without a
    click nothing is removed. Tell the user to expect it."""
    if not target_id and not url:
        raise ValueError("pass target_id or url")
    want_rel = (rel or "").strip().lower()
    hits = []
    for i, r in enumerate(_relations(item_id)):
        if r.get("rel") == "AttachedFile":
            continue
        ru = r.get("url", "")
        same = (
            ru.rstrip("/").lower() == url.strip().rstrip("/").lower()
            if url
            else _target_id(ru) == target_id
        )
        names = {r.get("rel", "").lower(), r.get("attributes", {}).get("name", "").lower()}
        if same and (not want_rel or want_rel in names):
            hits.append((i, r))
    if not hits:
        raise ValueError("link not found on this work item (see list_links)")
    if len(hits) > 1:
        kinds = ", ".join(r.get("rel", "") for _, r in hits)
        raise ValueError(f"{len(hits)} links match ({kinds}): pass rel to pick one")
    i, r = hits[0]
    info = {"rel": r.get("rel", ""), "target_id": _target_id(r.get("url")), "link_url": r.get("url", "")}
    if dry_run:
        return {"dry_run": True, "id": item_id, **info}
    _gate(
        "Remove a link",
        [f"Work item #{item_id}: {info['rel']} -> {info['target_id'] or info['link_url']}"],
        target={"item_id": item_id, **info},
    )
    res = _patch(item_id, [{"op": "remove", "path": f"/relations/{i}"}])
    _audit("done", action="remove_link", target={"item_id": item_id, **info}, rev=res.get("rev"))
    return {"removed": True, "id": item_id, **info, "rev": res.get("rev"), "url": _item_url(item_id)}


# ------------------------------------------------------------ history / assign
@mcp.tool()
def get_history(item_id: int, top: int = 50) -> list:
    """Revision history of a work item, newest first: who changed what and when
    (old -> new values of the changed fields, links added/removed)."""
    updates, skip = [], 0
    while skip < 1000:
        res = _call(
            "GET", f"wit/workItems/{item_id}/updates", params={"$top": 200, "$skip": skip}
        )
        vals = res.get("value", [])
        updates.extend(vals)
        if len(vals) < 200:
            break
        skip += 200
    rows = []
    for u in updates:
        fields = u.get("fields", {}) or {}
        when = (fields.get("System.ChangedDate") or {}).get("newValue") or u.get("revisedDate") or ""
        rel = u.get("relations", {}) or {}
        rows.append(
            {
                "rev": u.get("rev"),
                "by": _name(u.get("revisedBy")),
                "date": when[:19].replace("T", " "),
                "changes": {
                    k: {
                        "old": _short(_name(v.get("oldValue")), 200),
                        "new": _short(_name(v.get("newValue")), 200),
                    }
                    for k, v in fields.items()
                    if k not in IGNORED_UPDATE_FIELDS and isinstance(v, dict)
                },
                "links_added": len(rel.get("added", []) or []),
                "links_removed": len(rel.get("removed", []) or []),
            }
        )
    rows.reverse()
    return rows[:top]


@mcp.tool()
def assign_to(item_id: int, user: str = "", unassign: bool = False, comment: str = "") -> dict:
    """Assign a work item to a user (display name or unique name as TFS knows it).
    unassign=true clears the assignee. comment: optional History entry."""
    if unassign:
        value = ""
    elif user.strip():
        value = user.strip()
    else:
        raise ValueError("pass user or unassign=true")
    ops = [_op("System.AssignedTo", value)]
    if comment:
        ops.append(_op("System.History", _html_text(_author_text(comment))))
    res = _patch(item_id, ops)
    return {
        "id": item_id,
        "assigned_to": _name(res.get("fields", {}).get("System.AssignedTo")),
        "url": _item_url(item_id),
    }


# ------------------------------------------------------------- screenshots
SCREENSHOT_DIR = os.environ.get("TFS_SCREENSHOT_DIR", "")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


def _grab_screenshot(source):
    """(bytes, file_name) of an image taken from the clipboard or from the newest image
    file in TFS_SCREENSHOT_DIR. Runs on the machine where this MCP server runs."""
    source_raw = source or "clipboard"
    source = source_raw.strip().lower()
    if source == "latest":
        if not SCREENSHOT_DIR or not os.path.isdir(SCREENSHOT_DIR):
            raise ValueError("source='latest' needs TFS_SCREENSHOT_DIR set to an existing folder")
        files = [
            os.path.join(SCREENSHOT_DIR, f)
            for f in os.listdir(SCREENSHOT_DIR)
            if f.lower().endswith(_IMAGE_EXT)
        ]
        if not files:
            raise ValueError(f"no image files in {SCREENSHOT_DIR}")
        path = max(files, key=os.path.getmtime)
        with open(path, "rb") as fh:
            return fh.read(), os.path.basename(path)
    if source != "clipboard":
        raw = source_raw.strip().strip('"')
        cands = [raw]
        if SCREENSHOT_DIR:
            cands.append(os.path.join(SCREENSHOT_DIR, raw))
        for c in cands:
            if os.path.isfile(c) and c.lower().endswith(_IMAGE_EXT):
                with open(c, "rb") as fh:
                    return fh.read(), os.path.basename(c)
        raise ValueError("source must be 'clipboard', 'latest', or an existing image file name/path")
    try:
        from PIL import ImageGrab
    except ImportError:
        raise RuntimeError("Pillow is not installed for the Python that runs this server: python -m pip install pillow")
    img = ImageGrab.grabclipboard()
    if isinstance(img, list):  # files copied in Explorer instead of an image
        paths = [p for p in img if str(p).lower().endswith(_IMAGE_EXT)]
        if not paths:
            raise ValueError("the clipboard holds files, but none of them is an image")
        with open(paths[0], "rb") as fh:
            return fh.read(), os.path.basename(paths[0])
    if img is None:
        raise ValueError("the clipboard has no image: take a screenshot (Win+Shift+S) and try again")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"


@mcp.tool()
def add_screenshot(
    item_id: int,
    source: str = "clipboard",
    target: str = "symptom",
    comment: str = "",
    dry_run: bool = True,
) -> dict:
    """Take a screenshot from the user's PC and add it to a work item.
    source: 'clipboard' (image in the clipboard, e.g. after Win+Shift+S) or 'latest'
    (newest image file in TFS_SCREENSHOT_DIR).
    target: 'symptom' (default) puts the picture INSIDE the Symptom field, at the end of the
    'Screenshots / Attachments' section (it is also attached to the item); 'attachment' only attaches the file;
    'comment' posts a new Discussion comment with `comment` as plain text and the picture inside it
    (no separate attachment).
    dry_run defaults to true: reads the image and reports its name/size, changes nothing.
    Call again with dry_run=false to upload and save."""
    target = (target or "symptom").strip().lower()
    if target not in ("symptom", "attachment", "comment"):
        raise ValueError("target must be 'symptom', 'attachment' or 'comment'")
    data, name = _grab_screenshot(source)
    info = {"id": item_id, "file": name, "size_kb": round(len(data) / 1024, 1), "target": target}
    if dry_run:
        return {"dry_run": True, **info, "next": "call again with dry_run=false to upload"}
    if target == "comment":
        up = _call(
            "POST",
            "wit/attachments",
            params={"fileName": name},
            data=data,
            content_type="application/octet-stream",
            allow_redirects=False,
        )
        body = _html_text(_author_text(comment))
        res = _patch(item_id, [_op("System.History", (body + "<br>" if body else "") + _img_html(up["url"]))])
        return {**info, "rev": res.get("rev"), "url": _item_url(item_id)}
    up = _call(
        "POST",
        "wit/attachments",
        params={"fileName": name},
        data=data,
        content_type="application/octet-stream",
        allow_redirects=False,
    )
    ops = [
        {
            "op": "add",
            "path": "/relations/-",
            "value": {"rel": "AttachedFile", "url": up["url"], "attributes": {"comment": comment}},
        }
    ]
    if target == "symptom":
        cur = _call("GET", f"wit/workitems/{item_id}", params={"fields": SYMPTOM_FIELD})
        old = (cur.get("fields", {}).get(SYMPTOM_FIELD) or "").strip()
        img = f'<div><img src="{html.escape(up["url"], quote=True)}" alt="Image"></div>'
        ops.append(_op(SYMPTOM_FIELD, _insert_screenshot_html(old, img)))
    res = _patch(item_id, ops)
    return {**info, "attachment_id": up.get("id"), "rev": res.get("rev"), "url": _item_url(item_id)}


@mcp.tool()
def export_report_xlsx(output_path: str, wiql: str = "", days: int = 30) -> dict:
    """Export defects to Excel: 'Dashboard' sheet (stat cards + charts) and 'Raw Data' sheet.
    Without wiql, uses defects changed in the last `days` days."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, PieChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = _run_wiql(wiql or _recent_wiql(days), 5000)

    wb = Workbook()
    ws = wb.active
    ws.title = "Dashboard"
    raw = wb.create_sheet("Raw Data")

    # ---- Raw Data
    headers = ["ID", "Title", "Type", "State", "Severity", "Assigned To", "Area",
               "Iteration", "Created", "Changed", "Tags", "Link"]
    keys = ["id", "title", "type", "state", "severity", "assigned_to", "area",
            "iteration", "created", "changed", "tags", "url"]
    raw.append(headers)
    for c in raw[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for r in rows:
        raw.append([r[k] for k in keys])
        raw.cell(row=raw.max_row, column=12).hyperlink = r["url"]
    raw.freeze_panes = "A2"
    raw.auto_filter.ref = raw.dimensions
    for i, w in enumerate([8, 60, 10, 12, 14, 22, 30, 30, 19, 19, 20, 12], start=1):
        raw.column_dimensions[get_column_letter(i)].width = w

    # ---- Dashboard
    states = Counter(r["state"] or "-" for r in rows)
    sev = Counter(r["severity"] or "-" for r in rows)
    assignees = Counter(r["assigned_to"] or "Unassigned" for r in rows).most_common(10)
    done = {"Closed", "Done", "Removed"}
    cards = [
        ("Total", len(rows), "1F4E78"),
        ("Open", sum(1 for r in rows if r["state"] not in done | {"Resolved"}), "C55A11"),
        ("Resolved", states.get("Resolved", 0), "7F6000"),
        ("Closed", sum(1 for r in rows if r["state"] in done), "548235"),
    ]
    ws["B1"] = "Defect Dashboard"
    ws["B1"].font = Font(size=16, bold=True)
    for i, (label, value, color) in enumerate(cards):
        c1 = 2 + i * 3
        l1, l2 = get_column_letter(c1), get_column_letter(c1 + 1)
        ws.merge_cells(f"{l1}3:{l2}3")
        ws.merge_cells(f"{l1}4:{l2}5")
        ws[f"{l1}3"] = label
        ws[f"{l1}4"] = value
        for ref in (f"{l1}3", f"{l1}4"):
            ws[ref].fill = PatternFill("solid", fgColor=color)
            ws[ref].font = Font(color="FFFFFF", bold=True, size=11 if ref.endswith("3") else 22)
            ws[ref].alignment = Alignment(horizontal="center", vertical="center")
        for col in (l1, l2):
            for row in (3, 4, 5):
                ws[f"{col}{row}"].fill = PatternFill("solid", fgColor=color)

    def table(col, title, data):
        ws.cell(row=7, column=col, value=title).font = Font(bold=True)
        ws.cell(row=7, column=col + 1, value="Count").font = Font(bold=True)
        for n, (k, v) in enumerate(data, start=8):
            ws.cell(row=n, column=col, value=k)
            ws.cell(row=n, column=col + 1, value=v)
        return 7, 7 + len(data)

    s_hdr, s_end = table(2, "State", list(states.items()))
    v_hdr, v_end = table(5, "Severity", list(sev.items()))
    a_hdr, a_end = table(8, "Assigned To", assignees)

    def chart(kind, title, col, hdr, end, anchor):
        ch = PieChart() if kind == "pie" else BarChart()
        ch.title = title
        ch.add_data(Reference(ws, min_col=col + 1, min_row=hdr, max_row=end), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=col, min_row=hdr + 1, max_row=end))
        ch.width, ch.height = 11, 7
        if kind != "pie":
            ch.legend = None
        ws.add_chart(ch, anchor)

    if rows:
        chart("bar", "By State", 2, s_hdr, s_end, "B20")
        chart("pie", "By Severity", 5, v_hdr, v_end, "H20")
        chart("bar", "By Assignee", 8, a_hdr, a_end, "B36")
    for i in range(1, 14):
        ws.column_dimensions[get_column_letter(i)].width = 13

    wb.save(output_path)
    return {"saved": output_path, "defects": len(rows)}


# ------------------------------------------------- validation / environment tools
# Logic lives in defect_checks.py (pure functions) + config/*.json next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import defect_checks as _dc  # noqa: E402

STATE_FILE = os.environ.get(
    "TFS_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "validation_state.json"),
)


def _utcnow():
    return datetime.utcnow().replace(microsecond=0)


def _parse_ts(s):
    """'2026-10-15T05:24:07.64Z' / '2026-10-15 06:00' / '2026-10-15' -> naive UTC datetime."""
    t = (s or "").strip().replace(" ", "T")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date/time: {s!r} (use YYYY-MM-DD HH:MM)")


def _validation_field_list():
    f = _dc.load_config()["fields"]
    names = [f["title"], f["state"], f["created"], f["severity"], f["found_in_environment"],
             f["template_body"], "Microsoft.VSTS.TCM.ReproSteps", "System.Description"]
    return list(dict.fromkeys(names))


def _fetch_raw(ids, field_list):
    return _fetch_batch(ids, field_list)


def _has_attachment(item_id):
    return any(r.get("rel") == "AttachedFile" for r in _relations(item_id))


def validate_ids(ids):
    """Validate work items by id -> list of result dicts (see defect_checks.validate_fields)."""
    out = []
    for it in _fetch_raw(list(ids), _validation_field_list()):
        res = _dc.validate_fields(
            it["id"], it.get("fields", {}), attach_fn=lambda i=it["id"]: _has_attachment(i)
        )
        res["url"] = _item_url(it["id"])
        out.append(res)
    return out


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"full_run_done": False, "last_run": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def run_validation(full_run=False, since="", top=3000):
    """Validate defects. First call (or full_run=True): ALL defects. Afterwards: only defects
    created after the previous run. Returns (report, new_state); the caller decides when to
    save_state(new_state) (the tool does it right away, defect_monitor.py after the e-mail)."""
    state = load_state()
    started = _utcnow()
    full = (bool(full_run) or not state.get("full_run_done")) and not since
    q = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
         f"AND [System.WorkItemType] IN ({BUG_TYPES_SQL}) ")
    if SKIP_STATES:  # Closed defektlər yoxlanılmır
        q += "AND [System.State] NOT IN (" + ", ".join("'" + s.replace("'", "''") + "'" for s in SKIP_STATES) + ") "
    since_dt = None
    if not full:
        since_dt = _parse_ts(since) if since else _parse_ts(state["last_run"])
        q += f"AND [System.CreatedDate] >= '{(since_dt - timedelta(days=1)).date().isoformat()}' "
    q += "ORDER BY [System.CreatedDate] ASC"
    ids = [w["id"] for w in _call("POST", "wit/wiql", json_body={"query": q}).get("workItems", [])]
    results = validate_ids(ids[:top] if full else ids)
    if since_dt is not None:  # WIQL compares dates only; refine to the exact time here
        results = [r for r in results if r["created"] and _parse_ts(r["created"]) > since_dt]
    if SKIP_STATES:
        results = [r for r in results if (r.get("state") or "") not in SKIP_STATES]
    problems = [r for r in results if r["status"] != "ok"]
    report = {
        "mode": "full" if full else "new_since",
        "since": since_dt.isoformat(sep=" ") if since_dt else None,
        "checked": len(results),
        "ok": len(results) - len(problems),
        "with_errors": sum(1 for r in results if r["status"] == "error"),
        "with_warnings_only": sum(1 for r in results if r["status"] == "warning"),
        "problems": problems,
    }
    new_state = {"full_run_done": True, "last_run": started.isoformat(),
                 "last_mode": report["mode"], "last_checked": len(results)}
    return report, new_state


@mcp.tool()
def get_bug_template() -> dict:
    """Return the defect template (config/bug_template.md) with the required and optional
    section names. Use it when writing a new defect."""
    return _dc.get_template()


@mcp.tool()
def get_severity_list() -> list:
    """Return the allowed severity levels: TFS value, label and guidelines for each."""
    return _dc.get_severity_list()


@mcp.tool()
def validate_defect(item_id: int) -> dict:
    """Check one defect against the template (required sections filled, not left as
    placeholders), the severity list and the Found in Environment field (filled, valid,
    consistent with the [PROD]/[PREPROD] tag in the title)."""
    res = validate_ids([item_id])
    if not res:
        raise RuntimeError(f"Work item {item_id} not found")
    return res[0]


@mcp.tool()
def validate_new_defects(
    full_run: bool = False, since: str = "", mark_checked: bool = True, max_report: int = 50
) -> dict:
    """Validate defects. The first run (or full_run=true) checks ALL defects; later runs only
    check defects created after the previous run. since='YYYY-MM-DD HH:MM' (UTC) overrides the
    start. mark_checked=false does not move the 'last run' marker (dry check).
    The report lists only defects with problems (max_report of them)."""
    report, new_state = run_validation(full_run=full_run, since=since)
    if mark_checked:
        save_state(new_state)
    probs = report["problems"]
    report["problems"] = probs[: max(1, int(max_report))]
    report["not_listed"] = max(0, len(probs) - len(report["problems"]))
    report["state_saved"] = bool(mark_checked)
    return report


@mcp.tool()
def list_defects_by_environment(
    env: str, days: int = 30, state: str = "", top: int = 200, with_validation: bool = False,
    full: bool = False,
) -> list:
    """List defects found in 'prod' / 'preprod' / 'stg' / 'dr'. Looks at the Found in Environment field
    ('PREPROD, PROD' counts in both); if it is empty, the [PROD]/[PREPROD] tag in the title is used.
    days = changed in the last N days (0 = no date limit).
    DEFAULT format: ID, Summary, Severity, Assignee, Created Date (Baku time).
    full=True: state + environment details (env_status etc.); with_validation=true (implies full) adds the
    full validate_defect result."""
    cfg = _dc.load_config()
    envs = {e["key"]: e for e in cfg["env"]["environments"]}
    env = (env or "").strip().lower()
    if env not in envs:
        raise ValueError(f"env must be one of: {', '.join(envs)}")
    fld = cfg["fields"]["found_in_environment"]
    conds = [f"[{fld}] CONTAINS '{v}'" for v in envs[env]["tfs_values"]]   # 'PREPROD, PROD' de tutulsun
    conds += [f"[System.Title] CONTAINS '{t.strip('[]')}'" for t in envs[env]["title_tags"]]
    q = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
         f"AND [System.WorkItemType] IN ({BUG_TYPES_SQL}) ")
    if days:
        q += f"AND [System.ChangedDate] >= @Today - {int(days)} "
    if state:
        q += f"AND [System.State] = '{_wq(state)}' "
    q += f"AND ({' OR '.join(conds)}) ORDER BY [System.ChangedDate] DESC"
    ids = [w["id"] for w in _call("POST", "wit/wiql", json_body={"query": q}).get("workItems", [])]
    out = []
    # CONTAINS is only a pre-filter (it also matches PREPROD for 'PROD'); exact check below.
    for res in validate_ids(ids[:top] if top else ids):
        e = res["environment"]
        title_env = next((x["key"] for x in cfg["env"]["environments"]
                          if any(t.lower() in res["title"].lower() for t in x["title_tags"])), None)
        field_envs = e.get("env_list", []) if e["env_source"] == "field" else []
        if env not in field_envs and env != title_env:
            continue
        row = {"id": res["id"], "title": res["title"], "state": res["state"],
               "severity": res["severity"]["value"], "created": res["created"],
               "url": res["url"], **e}
        if with_validation:
            row["validation"] = res
        out.append(row)
    if full or with_validation:
        return out
    meta = {r["id"]: r for r in _fetch_items([r["id"] for r in out])} if out else {}
    return [_compact([meta[r["id"]]])[0] for r in out if r["id"] in meta]


# ------------------------------------------------- workflow tools (28.10.2026)
# Bir neçə çağırışı birinə yığır: create_defect + add_attachment xN + Symptom-a şəkil + validate_defect,
# və reopen: state + şərh (şəkil daxil) + assign. Qaydalar köhnə alətlərlə eynidir (bug template, şablon yoxlaması).
def _split_shot(entry):
    """'path' və ya 'path|caption' -> (path, caption)."""
    if isinstance(entry, dict):
        return str(entry.get("path") or "").strip(), str(entry.get("caption") or "").strip()
    raw = str(entry or "")
    path, _, cap = raw.partition("|")
    return path.strip().strip('"'), cap.strip()


def _as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            return json.loads(s)
        return [x for x in re.split(r"[\n;]", s) if x.strip()]
    return list(v)


def _read_shots(screenshots):
    """Bütün faylları ƏVVƏLCƏDƏN oxuyur (biri yoxdursa heç nə yaradılmır). [(bytes, name, caption)]."""
    out = []
    for e in _as_list(screenshots):
        path, cap = _split_shot(e)
        if not path:
            continue
        data, name = _grab_screenshot(path)
        out.append((data, name, cap))
    return out


def _upload_bytes(data, name):
    up = _call("POST", "wit/attachments", params={"fileName": name}, data=data,
               content_type="application/octet-stream", allow_redirects=False)
    return up


def _evidence_html(url, caption):
    cap = f"<b>{html.escape(caption)}:</b><br>" if caption else ""
    return f'{cap}<div><img src="{html.escape(url, quote=True)}" alt="Image"></div>'


def _find_same_title(summary, days=2):
    t = (summary or "").replace("'", "''")
    q = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
         f"AND [System.WorkItemType] IN ({BUG_TYPES_SQL}) AND [System.Title] = '{t}' "
         f"AND [System.CreatedDate] >= @Today - {int(days)} ORDER BY [System.Id] DESC")
    return _run_wiql(q, 5)


@mcp.tool()
def create_defect_full(
    summary: str,
    steps: str = "",
    expected: str = "",
    actual: str = "",
    environment: str = "",
    description: str = "",
    severity: str = "",
    screenshots: Union[str, list, None] = None,
    preconditions: str = "",
    test_data: str = "",
    logs: str = "",
    related: str = "",
    symptom: str = "",
    area_path: str = "",
    iteration_path: str = "",
    assigned_to: str = "",
    tags: str = "",
    allow_duplicate: bool = False,
    dry_run: bool = False,
) -> dict:
    """create_defect + ekran şəkilləri + validate_defect BİR çağırışda (6-8 çağırış -> 1).
    Sahələr create_defect ilə eynidir (bug template, hamısı Symptom-da, assigned_to boş = Suntech_AZTelekom).
    screenshots: siyahı və ya ';'/yeni sətirlə ayrılmış; hər element 'yol' və ya 'yol|başlıq'
      (yol: tam yol və ya TFS_SCREENSHOT_DIR-dəki fayl adı; YALNIZ marked versiyaları verin).
      Hər şəkil (1) item-ə attachment kimi bağlanır və (2) Symptom-un 'Screenshots / Attachments' bölməsinə
      başlığı ilə birlikdə <img> kimi qoyulur - eyni sırada. PLACEHOLDER / str_replace_field lazım deyil.
    Təhlükəsizlik: bütün fayllar əvvəl oxunur (biri yoxdursa heç nə yaradılmır); son 2 gündə eyni başlıqlı
      defekt varsa YARADILMIR və onun id-si qaytarılır (bridge timeout-dan sonra təkrar çağırış dublikat
      yaratmasın) - allow_duplicate=True bunu söndürür. dry_run=True: heç nə yükləmədən/yaratmadan önizləmə.
    Nəticədə: id, url, attachments, template_issues və yaradılandan sonra real validate_defect nəticəsi."""
    d = dict(summary=summary, steps=steps, expected=expected, actual=actual, environment=environment,
             description=description, severity=severity, area_path=area_path, iteration_path=iteration_path,
             assigned_to=assigned_to, tags=tags, symptom=symptom, preconditions=preconditions,
             test_data=test_data, logs=logs, related=related)
    ops = _build_ops(d)                      # səhv environment / boş summary burada dayanır
    issues = _template_issues(ops)
    shots = _read_shots(screenshots)
    if not allow_duplicate:
        same = _find_same_title(_author_title(summary))
        if same:
            return {"created": False, "reason": "eyni başlıqlı defekt son 2 gündə artıq var (dublikat yaradılmadı)",
                    "existing": [{"id": r["id"], "state": r["state"], "created": r["created"], "url": r["url"]}
                                 for r in same],
                    "next": "lazımdırsa allow_duplicate=true ilə yenidən çağırın"}
    if dry_run:
        return {"dry_run": True, "title": summary, "template_issues": issues,
                "screenshots": [{"file": n, "caption": c, "size_kb": round(len(b) / 1024, 1)} for b, n, c in shots],
                "ops": ops}
    sym_i = next(i for i, o in enumerate(ops) if o["path"] == f"/fields/{SYMPTOM_FIELD}")
    attached, rel_ops = [], []
    for data, name, cap in shots:
        up = _upload_bytes(data, name)
        ops[sym_i]["value"] = _insert_screenshot_html(ops[sym_i]["value"], _evidence_html(up["url"], cap))
        rel_ops.append({"op": "add", "path": "/relations/-",
                        "value": {"rel": "AttachedFile", "url": up["url"], "attributes": {"comment": cap}}})
        attached.append({"file": name, "caption": cap, "attachment_id": up.get("id")})
    res = _call("POST", f"wit/workitems/${quote(BUG_TYPE)}", json_body=ops + rel_ops,
                content_type="application/json-patch+json", allow_redirects=False)
    item_id = res["id"]
    out = {"created": True, "id": item_id, "title": summary, "url": _item_url(item_id),
           "attachments": attached, "template_issues": issues}
    try:
        v = validate_ids([item_id])
        out["validation"] = v[0] if v else "tapılmadı"
    except Exception as e:  # noqa: BLE001  (defekt yaradılıb; yoxlama xətası onu ləğv etmir)
        out["validation"] = f"validate_defect alınmadı: {e}"
    return out


def _merge_images(shots, out_name):
    """Bir neçə şəkli başlıqları ilə üst-üstə bir PNG-yə yığır (bir şərhdə bir şəkil qaydası)."""
    try:
        from PIL import Image as _PImg, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Bir neçə şəkli birləşdirmək üçün Pillow lazımdır: python -m pip install pillow")
    font = None
    for fname in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):   # Azərbaycan hərfləri (ş, ə, ğ) üçün
        try:
            font = ImageFont.truetype(fname, 22)
            break
        except Exception:  # noqa: BLE001
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=22)
        except TypeError:
            font = ImageFont.load_default()
    imgs = [(_PImg.open(io.BytesIO(b)).convert("RGB"), c) for b, _, c in shots]
    pad, cap_h = 12, 34
    w = max(i.width for i, _ in imgs) + 2 * pad
    h = sum(i.height + (cap_h if c else 0) + pad for i, c in imgs) + pad
    canvas = _PImg.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(canvas)
    y = pad
    for img, cap in imgs:
        if cap:
            draw.text((pad, y + 6), cap, fill=(0, 0, 0), font=font)
            y += cap_h
        canvas.paste(img, (pad, y))
        draw.rectangle([pad - 1, y - 1, pad + img.width, y + img.height], outline=(160, 160, 160))
        y += img.height + pad
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    folder = SCREENSHOT_DIR if SCREENSHOT_DIR and os.path.isdir(SCREENSHOT_DIR) else os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(folder, out_name)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return buf.getvalue(), out_name, path


# Avtomatik reopen keçidləri (tfs_query_rules: AZTelekom hesabı 'Active'-i Ready for Testing / In Progress-dən qoya bilmir)
_REOPEN_AUTO = {"Ready for Testing": "In Progress", "Resolved": "Active"}


@mcp.tool()
def reopen_defect(
    item_id: int,
    comment: str,
    screenshots: Union[str, list, None] = None,
    target_state: str = "",
    assign_to_user: str = "",
    dry_run: bool = False,
) -> dict:
    """Defekti yenidən açır BİR çağırışda (update_status + add_comment + assign_to -> 1, eyni revision-da).
    target_state boş = avtomatik: 'Ready for Testing' -> 'In Progress'; 'Resolved' -> 'Active' (Reason 'Not fixed',
      ResolvedReason təmizlənir). Başqa vəziyyətdə target_state açıq verilməlidir (heç nə dəyişdirilmir).
    comment: PLAIN TEXT, **mətn** = qalın (Reason, Note, What works now, Steps, Actual, Expected ... qaydası ilə).
    screenshots: 'yol' və ya 'yol|başlıq' siyahısı. Bir şərhdə BİR şəkil: bir neçə şəkil verilsə başlıqları ilə
      üst-üstə bir PNG-yə birləşdirilir (Pillow), həmin şəkil şərhin İÇİNDƏ göstərilir.
    assign_to_user boş = Suntech_AZTelekom (fərdi vendor adları TFS-də rədd olunur).
    dry_run=True: nə dəyişəcəyini göstərir, heç nə yükləmir/saxlamır."""
    cur = _call("GET", f"wit/workitems/{item_id}",
                params={"fields": "System.State,System.Title,System.AssignedTo"}).get("fields", {})
    state = cur.get("System.State", "")
    target = target_state.strip() or _REOPEN_AUTO.get(state, "")
    if not target:
        raise ValueError(f"#{item_id} vəziyyəti '{state}': avtomatik reopen keçidi yoxdur, target_state verin. "
                         "Heç nə dəyişdirilmədi.")
    if not (comment or "").strip():
        raise ValueError("comment boş ola bilməz (reopen səbəbi yazılmalıdır).")
    shots = _read_shots(screenshots)
    assignee = (assign_to_user or DEFAULT_ASSIGNEE).strip()
    plan = {"id": item_id, "title": cur.get("System.Title"), "state": f"{state} -> {target}",
            "assign_to": assignee, "images": [n for _, n, _ in shots]}
    if dry_run:
        return {"dry_run": True, **plan, "merged": len(shots) > 1}
    shot_path = ""
    if len(shots) == 1:
        data, name, _ = shots[0]
        url = _upload_bytes(data, name)["url"]
    elif len(shots) > 1:
        data, name, shot_path = _merge_images(shots, f"reopen_{item_id}_{datetime.now():%Y%m%d_%H%M%S}.png")
        url = _upload_bytes(data, name)["url"]
    else:
        url = ""
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _html_text(_author_text(comment)))
    if url:
        body += "<br>" + _img_html(url)
    ops = [_op("System.State", target)]
    if target == "Active" and state == "Resolved":
        ops += [_op("System.Reason", "Not fixed"), _op("Microsoft.VSTS.Common.ResolvedReason", "")]
    ops += [_op("System.AssignedTo", assignee), _op("System.History", body)]
    res = _patch(item_id, ops)
    f = res.get("fields", {})
    out = {**plan, "state_now": f.get("System.State"), "assigned_now": _name(f.get("System.AssignedTo")),
           "rev": res.get("rev"), "url": _item_url(item_id)}
    if shot_path:
        out["merged_image"] = shot_path
    return out


if __name__ == "__main__":
    mcp.run()

# ======================================================================
# DEFECT MANAGER (28.10.2026): default list format, due dates, RCA, reopen, SLA, next move.
# YALNIZ OXUYUR - TFS-də heç nə dəyişmir. Məntiq defect_manager.py-dadır (pure functions).
# ======================================================================
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402
import defect_manager as _dm  # noqa: E402

DM_CACHE_FILE = os.environ.get(
    "TFS_DM_CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "dm_cache.json"))
DM_TIME_BUDGET = int(os.environ.get("TFS_DM_TIME_BUDGET", "40"))   # bridge ~60 s-də kəsir
DM_WORKERS = int(os.environ.get("TFS_DM_WORKERS", "6"))
DM_META_FIELDS = [
    "System.Id", "System.Title", "System.WorkItemType", "System.State", "System.Reason", "System.Rev",
    "System.AssignedTo", "System.CreatedDate", "System.ChangedDate", SEVERITY_FIELD,
    "Microsoft.VSTS.CMMI.FoundInEnvironment", "Microsoft.VSTS.CMMI.RootCause", "Microsoft.VSTS.CMMI.Blocked",
    "Microsoft.VSTS.Common.ResolvedDate", "Microsoft.VSTS.Common.ResolvedReason", "Microsoft.VSTS.Common.ClosedDate",
]
_dm_lock = threading.Lock()
_dm_cache = None


def _dm_cache_load():
    global _dm_cache
    if _dm_cache is None:
        try:
            with open(DM_CACHE_FILE, encoding="utf-8") as fh:
                _dm_cache = json.load(fh)
        except (OSError, ValueError):
            _dm_cache = {}
    return _dm_cache


def _dm_cache_save():
    if _dm_cache is None:
        return
    os.makedirs(os.path.dirname(DM_CACHE_FILE), exist_ok=True)
    tmp = DM_CACHE_FILE + ".tmp"
    with _dm_lock:
        data = json.dumps(_dm_cache, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, DM_CACHE_FILE)


def _dm_meta(ids):
    out = {}
    for it in _fetch_raw(list(ids), DM_META_FIELDS):
        f = it.get("fields", {})
        out[it["id"]] = {
            "id": it["id"], "title": f.get("System.Title", ""), "type": f.get("System.WorkItemType", ""),
            "state": f.get("System.State", ""), "reason": f.get("System.Reason", ""),
            "rev": f.get("System.Rev") or it.get("rev"),
            "severity": f.get(SEVERITY_FIELD, ""), "assigned_to": _name(f.get("System.AssignedTo")),
            "created": f.get("System.CreatedDate", ""), "changed": f.get("System.ChangedDate", ""),
            "env_field": f.get("Microsoft.VSTS.CMMI.FoundInEnvironment", "") or "",
            "root_cause": f.get("Microsoft.VSTS.CMMI.RootCause", "") or "",
            "blocked": f.get("Microsoft.VSTS.CMMI.Blocked", "") or "",
            "resolved_date": f.get("Microsoft.VSTS.Common.ResolvedDate", ""),
            "resolved_reason": f.get("Microsoft.VSTS.Common.ResolvedReason", ""),
        }
    return out


def _dm_raw_comments(item_id):
    out, token = [], None
    for _ in range(10):
        params = {"api-version": COMMENTS_API, "$top": 200}
        if token:
            params["continuationToken"] = token
        res = _call("GET", f"wit/workItems/{item_id}/comments", params=params)
        for c in res.get("comments", []):
            if c.get("isDeleted"):
                continue
            out.append({"author": _name(c.get("createdBy")), "created": c.get("createdDate", ""),
                        "text": (c.get("text") or "")[:6000]})
        token = res.get("continuationToken")
        if not token:
            break
    return out


def _dm_raw_updates(item_id):
    ups, skip = [], 0
    while skip < 2000:
        res = _call("GET", f"wit/workItems/{item_id}/updates", params={"$top": 200, "$skip": skip})
        vals = res.get("value", [])
        for u in vals:
            f = u.get("fields") or {}
            keep = {k: f[k] for k in ("System.State", "System.Reason", "System.ChangedDate") if k in f}
            if "System.State" in keep:
                ups.append({"fields": keep, "revisedBy": {"displayName": _name(u.get("revisedBy"))},
                            "revisedDate": u.get("revisedDate", "")})
        if len(vals) < 200:
            break
        skip += 200
    return ups


def _dm_details(item_id, rev):
    """Comments + state history of one item; cached by revision (any change -> new rev)."""
    cache = _dm_cache_load()
    key = str(item_id)
    with _dm_lock:
        hit = cache.get(key)
    if hit and hit.get("rev") == rev:
        return hit
    data = {"rev": rev, "comments": _dm_raw_comments(item_id), "updates": _dm_raw_updates(item_id)}
    with _dm_lock:
        cache[key] = data
    return data


def _dm_load_many(metas):
    """Fetch details in parallel within the time budget. -> ({id: details}, [not processed]).
    Unfinished fetches keep running in the background and land in the cache for the next call."""
    start = time.time()
    got, todo, skipped = {}, [], []
    cache = _dm_cache_load()
    for m in metas:
        hit = cache.get(str(m["id"]))
        if hit and hit.get("rev") == m["rev"]:
            got[m["id"]] = hit
        else:
            todo.append(m)
    if todo:
        ex = ThreadPoolExecutor(max_workers=DM_WORKERS)
        futs = {ex.submit(_dm_details, m["id"], m["rev"]): m["id"] for m in todo}
        try:
            for fu in as_completed(futs, timeout=max(1, DM_TIME_BUDGET - (time.time() - start))):
                iid = futs[fu]
                try:
                    got[iid] = fu.result()
                except Exception as e:  # noqa: BLE001
                    skipped.append({"id": iid, "error": str(e)[:200]})
        except Exception:  # noqa: BLE001  (TimeoutError: budget used up)
            pass
        ex.shutdown(wait=False, cancel_futures=True)
        for fu, iid in futs.items():
            if iid not in got and not any(x["id"] == iid for x in skipped):
                skipped.append({"id": iid, "error": "time budget – call again (finished items are cached)"})
    _dm_cache_save()
    return got, skipped


def _dm_env_keys(meta):
    return [k.lower() for k in _dc.split_envs(meta.get("env_field", ""))]


def _dm_select(ids="", env="", only_open=True, wiql="", top=60):
    """Pick the defects: explicit ids, a WIQL query, or open Bug + Aztelekom Bug defects
    (optionally only one environment, taken from the Environment field; 'PREPROD, PROD' counts in both)."""
    if ids:
        id_list = [int(x) for x in re.findall(r"\d+", str(ids))]
    elif wiql:
        res = _call("POST", "wit/wiql", json_body={"query": wiql})
        id_list = [w["id"] for w in res.get("workItems", [])]
    else:
        q = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
             f"AND [System.WorkItemType] IN ({BUG_TYPES_SQL}) ")
        if only_open:
            q += "AND [System.State] NOT IN ('Closed', 'Done', 'Removed') "
        if env:
            q += f"AND [Microsoft.VSTS.CMMI.FoundInEnvironment] CONTAINS '{_wq(env)}' "
        q += "ORDER BY [System.CreatedDate] ASC"
        res = _call("POST", "wit/wiql", json_body={"query": q})
        id_list = [w["id"] for w in res.get("workItems", [])]
    metas = list(_dm_meta(id_list).values()) if id_list else []
    if env:
        e = env.strip().lower()
        metas = [m for m in metas if e in _dm_env_keys(m)]
    metas.sort(key=lambda m: m["id"])
    total = len(metas)
    return metas[: max(1, int(top))] if top else metas, total


def _dm_scope_text(only_open, env, total, shown):
    return (f"types: {', '.join(BUG_TYPES)}; states: "
            f"{'open (all except Closed / Done / Removed)' if only_open else 'all'}; "
            f"environment: {env.upper() + ' (Environment field)' if env else 'all'}; "
            f"{shown} of {total} shown")


def _dm_analyse(meta, det, overrides=None, now=None):
    changes = _dm.state_changes(det.get("updates", []))
    comments = det.get("comments", [])
    deliveries = _dm.deliveries_of(changes) or [_dm.parse_utc(meta.get("resolved_date"))]
    due = _dm.due_info(meta["id"], comments, meta["state"], deliveries, now)
    ov = (overrides or {}).get(str(meta["id"]))
    rca = _dm.rca_info(comments, meta.get("root_cause"), ov, meta.get("changed", ""))
    return changes, comments, due, rca


@mcp.tool()
def severity_rules() -> dict:
    """Full severity rules (Acceptance Criteria Table 6 + contract Exhibit 4): definitions, TFS values,
    assessment tests (one subscriber vs systematic, billing/customer impact, workaround), SLA per severity
    (PROD only) and the rule that severity is required ONLY for PROD defects."""
    cfg = _dc.load_config()["severity"]
    return {k: v for k, v in cfg.items() if k != "heuristics"}


DUE_PREFILTER = os.environ.get("TFS_DUE_PREFILTER", "true").lower() != "false"


def _due_prefilter(ids):
    """Ids whose History (discussion) contains the words 'due date' - one WIQL instead of fetching the
    comments of every defect. None = not available (or disabled) -> caller checks every defect.
    Verified 29.10.2026 on the 61 open PROD defects: same 22 ids as reading all comments."""
    if not DUE_PREFILTER or not ids:
        return None
    found = set()
    try:
        for i in range(0, len(ids), 200):
            chunk = ",".join(str(int(x)) for x in ids[i : i + 200])
            q = (f"SELECT [System.Id] FROM WorkItems WHERE [System.Id] IN ({chunk}) "
                 "AND [System.History] CONTAINS WORDS 'due date'")
            found |= {w["id"] for w in _call("POST", "wit/wiql", json_body={"query": q}).get("workItems", [])}
    except RuntimeError:
        return None
    return found


@mcp.tool()
def due_dates(ids: str = "", env: str = "", only_open: bool = True, wiql: str = "", top: int = 60,
              only_overdue: bool = False) -> dict:
    """Due Date report (read-only). DEFAULT columns (always use this format for due-date questions):
    ID, Summary, Severity, Assignee, Created Date, Due Date, Due Date Changed (times), Overdue.
    There is NO due-date field in TFS: the date comes from comments of ANY author that contain 'due date'
    (case-insensitive) - the FIRST date written after the phrase (dd.mm.yyyy / dd.mm.yy / dd.mm = comment year);
    several such comments -> the most recent comment wins. 'Due Date Changed' = how many times a different
    date was given. Overdue: 'Yes (N d)' when the date is before today and the defect is still open on the
    vendor side; 'No (delivered)' / 'No (delivered late, N d)' once moved to Resolved / Ready for Testing.
    'none given' = no 'due date' comment with a date (never guessed). details[] has the author + comment date.
    ids: '329996, 326839' (optional); env: 'prod' / 'preprod' / 'dr' (Environment field); wiql: own query.
    Default = open Bug + Aztelekom Bug defects. Comments are cached per revision, so a second run is fast;
    items not finished within ~40 s are listed in not_processed - just call again."""
    metas, total = _dm_select(ids, env, only_open, wiql, top)
    hits = _due_prefilter([m["id"] for m in metas])
    need = metas if hits is None else [m for m in metas if m["id"] in hits]
    got, skipped = _dm_load_many(need)
    empty = {"comments": [], "updates": []}
    rows, details = [], []
    for m in metas:
        if hits is not None and m["id"] not in hits:
            det = empty                 # no comment with 'due date' -> nothing to fetch
        elif m["id"] in got:
            det = got[m["id"]]
        else:
            continue
        _ch, _co, due, _rca = _dm_analyse(m, det)
        if only_overdue and not str(due["overdue"]).startswith("Yes"):
            continue
        rows.append(_dm.due_row(m, due))
        if due.get("due") or due.get("history"):
            details.append({"ID": m["id"], "given_by": due["given_by"], "given_on": due["given_on"],
                            "source": due["source"], "confidence": due["confidence"], "quote": due["quote"],
                            "history": due["history"]})
    over = sum(1 for r in rows if str(r["Overdue"]).startswith("Yes"))
    none = sum(1 for r in rows if r["Due Date"] == "none given")
    return {"fetched_at_baku": _dm.baku(datetime.utcnow()),
            "counted": _dm_scope_text(only_open, env, total, len(metas)),
            "summary": {"rows": len(rows), "overdue": over, "no_due_date": none},
            "rows": rows, "details": details, "not_processed": skipped,
            "note": "Due date = first date after 'due date' in the latest such comment; see details (quote)."}


@mcp.tool()
def rca_status(ids: str = "", env: str = "", wiql: str = "", top: int = 60, bucket: int = 0) -> dict:
    """RCA status of OPEN defects (read-only). Buckets: 1 = RCA not provided (ball: vendor);
    2 = RCA provided, not confirmed by us (ball: us – review it); 3 = RCA provided and confirmed (waiting for
    fix delivery / retest). RootCause field 'Unknown' = not provided; the real RCA is usually in vendor comments
    (keywords: root cause, RCA, caused by ... = high confidence; because / due to ... = low confidence).
    DEFAULT row format (user preference): ID, Summary, Severity, Assignee, Created Date,
    RCA – in comment (written – who, when / not written / not written (no comments)),
    RCA – Root Cause field (not selected (Unknown) / selected: <value>), then RCA bucket + Confidence.
    Also returns counts per bucket x state and x environment. Low-confidence items: check the quote and,
    if needed, record a manual verdict with rca_set (stored locally, reused until the item changes).
    bucket=1/2/3 shows only that bucket."""
    metas, total = _dm_select(ids, env, True, wiql, top)
    got, skipped = _dm_load_many(metas)
    overrides = _dm.load_rca_overrides()
    rows, by_state, by_env, low = [], Counter(), Counter(), []
    for m in metas:
        if m["id"] not in got:
            continue
        _ch, _co, _due, rca = _dm_analyse(m, got[m["id"]], overrides)
        if bucket and rca["bucket"] != int(bucket):
            continue
        r = _dm.compact_row(m)
        comments = got[m["id"]].get("comments", [])
        if rca.get("source") == "vendor comment":
            in_comment = f"written – {rca.get('provided_by', '')}, {rca.get('provided_on', '')}"
        elif not comments:
            in_comment = "not written (no comments)"
        else:
            in_comment = "not written"
        rc = (m.get("root_cause") or "").strip()
        in_field = "not selected (Unknown)" if rc.lower() in ("", "unknown") else f"selected: {rc}"
        r.update({"RCA – in comment": in_comment, "RCA – Root Cause field": in_field,
                  "RCA": f"{rca['bucket']}. {rca['label']}", "Confidence": rca.get("confidence", "")})
        if rca.get("confirmed_by"):
            r["Confirmed"] = f"{rca['confirmed_by']} {rca['confirmed_on']}"
        rows.append(r)
        if rca.get("confidence") == "low":
            low.append({"ID": m["id"], "quote": rca.get("quote", "")})
        by_state[(rca["bucket"], _dm.STATE_GROUP.get(m["state"].lower(), m["state"]))] += 1
        for e in (_dm_env_keys(m) or ["(not set)"]):
            by_env[(rca["bucket"], e.upper())] += 1
    return {"fetched_at_baku": _dm.baku(datetime.utcnow()),
            "counted": _dm_scope_text(True, env, total, len(metas)),
            "buckets": {f"{b}. {_dm.RCA_LABELS[b]}": sum(1 for r in rows if r["RCA"].startswith(f"{b}."))
                        for b in (1, 2, 3)},
            "by_state": [{"bucket": b, "state": s, "count": n} for (b, s), n in sorted(by_state.items())],
            "by_environment": [{"bucket": b, "env": e, "count": n} for (b, e), n in sorted(by_env.items())],
            "rows": rows, "low_confidence": low, "not_processed": skipped,
            "note": "RCA detection is keyword-based (heuristic)."}


@mcp.tool()
def rca_set(item_id: int, bucket: int, note: str = "") -> dict:
    """Record a MANUAL RCA verdict for one defect (1 = not provided, 2 = provided not confirmed,
    3 = provided and confirmed). Stored locally in config/rca_overrides.json (NOT in TFS) and reused
    until the defect changes in TFS."""
    if int(bucket) not in (1, 2, 3):
        raise ValueError("bucket must be 1, 2 or 3")
    meta = _dm_meta([item_id]).get(item_id)
    if not meta:
        raise RuntimeError(f"Work item {item_id} not found")
    saved = _dm.save_rca_override(item_id, bucket, note, meta["changed"])
    return {"id": item_id, "saved": saved, "label": _dm.RCA_LABELS[int(bucket)]}


@mcp.tool()
def defect_summary(item_id: int, max_thread: int = 30) -> dict:
    """'Tell me about #ID' (read-only), in the defect-manager format:
    header (ID, title, Severity, Environment field, State, Assignee, Created / last changed),
    chronological thread (comments + state changes, our side vs vendor, real author from the [Name] prefix),
    Due date (value, who gave it, when; or 'none given'), RCA (bucket, who/when, confirmed by us or not),
    Reopen count (+ Closed -> Active), SLA status if PROD (reaction met/breached, resolution in/out of SLA),
    Next move (who owns it: vendor / us, and the concrete action)."""
    meta = _dm_meta([item_id]).get(item_id)
    if not meta:
        raise RuntimeError(f"Work item {item_id} not found")
    det = _dm_details(item_id, meta["rev"])
    _dm_cache_save()
    changes, comments, due, rca = _dm_analyse(meta, det, _dm.load_rca_overrides())
    reopen = _dm.reopen_info(changes)

    thread = []
    for c in comments:
        txt = _dm.plain(c.get("text", ""))
        side, name = _dm.who(c.get("author"), txt)
        body = _dm._NAME_PREFIX.sub("", txt, count=1) if side == "us" else txt
        thread.append((_dm.parse_utc(c.get("created")), side, name, _dm.short(body, 220)))
    for when, old, new, by, reason in changes:
        side = "us" if (by or "").strip().lower() in _dm.OUR_ACCOUNTS else "vendor"
        thread.append((when, side, by, f"STATE {old or '–'} -> {new}" + (f" ({reason})" if reason else "")))
    thread.sort(key=lambda x: x[0] or datetime.min)
    thread_rows = [{"when": _dm.baku(w), "side": s, "who": n, "what": t} for w, s, n, t in thread]
    if len(thread_rows) > max_thread:
        thread_rows = thread_rows[:3] + [{"when": "", "side": "", "who": "",
                                          "what": f"... {len(thread_rows) - max_thread} older entries skipped ..."}] \
            + thread_rows[-(max_thread - 3):]

    env_keys = _dm_env_keys(meta)
    sla = None
    if "prod" in env_keys:
        sevs = _dc.load_config()["severity"]["severities"]
        sev_cfg = next((s for s in sevs if s["tfs_value"].lower() == (meta["severity"] or "").lower()), None)
        sla = _dm.sla_info(sev_cfg, _dm.parse_utc(meta["created"]), changes, comments) if sev_cfg else \
            {"status": "unclear – severity missing or invalid on a PROD defect"}
    return {
        "header": {"ID": meta["id"], "Title": meta["title"], "Type": meta["type"],
                   "Severity": meta["severity"] or "–", "Environment": meta["env_field"] or "(not set)",
                   "State": meta["state"] + (f" ({meta['reason']})" if meta["reason"] else ""),
                   "Blocked": meta["blocked"] or "–", "Assignee": meta["assigned_to"] or "–",
                   "Created": _dm.baku(_dm.parse_utc(meta["created"])),
                   "Last changed": _dm.baku(_dm.parse_utc(meta["changed"]))},
        "thread": thread_rows,
        "due_date": {"value": due["due"] or "none given", "given_by": due["given_by"], "given_on": due["given_on"],
                     "source": due["source"], "changed_times": due["changes"], "overdue": due["overdue"],
                     "quote": due["quote"]},
        "rca": rca,
        "reopen": reopen,
        "sla": sla if sla is not None else "n/a (not PROD)",
        "next_move": _dm.next_move(meta["state"], meta["blocked"], rca, due, meta["reason"]),
        "fetched_at_baku": _dm.baku(datetime.utcnow()),
        "url": _item_url(item_id),
    }
