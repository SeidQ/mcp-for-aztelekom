"""TestRail MCP server (azercelltelecom.testrail.io).

Credentials come from secrets.env next to this file (never hard-code them):
    TESTRAIL_URL=https://azercelltelecom.testrail.io
    TESTRAIL_USER=<email>
    TESTRAIL_API_KEY=<API key from My Settings -> API Keys>
    TESTRAIL_PROJECT=aztelekom        (default project, matched by name, case-insensitive; or numeric id)

Write tools (add_section, add_case, add_cases_bulk, update_case) default to dry_run=True:
they show what would be sent and change nothing. Call again with dry_run=False to save.
add_case / add_cases_bulk skip a case when the same title already exists in the target section.
"""
import json
import os
import re
import time
from typing import Union

import requests
from mcp.server.fastmcp import FastMCP


def _load_secrets():
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

TR_URL = os.environ.get("TESTRAIL_URL", "https://azercelltelecom.testrail.io").rstrip("/")
TR_USER = os.environ.get("TESTRAIL_USER", "")
TR_KEY = os.environ.get("TESTRAIL_API_KEY", "")
TR_PROJECT = os.environ.get("TESTRAIL_PROJECT", "aztelekom")
API = TR_URL + "/index.php?/api/v2/"

mcp = FastMCP(
    "testrail",
    instructions=(
        "TestRail (Azercell). Default project = TESTRAIL_PROJECT (Aztelekom). "
        "Write tools are dry_run=True by default: show the preview to the user, then call with dry_run=False. "
        "Steps are a list of {content, expected} (Test Case (Text) template, custom_steps_separated). "
        "Answer in the user's language."
    ),
)

_session = None
_cache = {}


def _s():
    global _session
    if _session is None:
        if not TR_USER or not TR_KEY:
            raise RuntimeError("TESTRAIL_USER / TESTRAIL_API_KEY missing in src/secrets.env")
        _session = requests.Session()
        _session.auth = (TR_USER, TR_KEY)
        _session.headers.update({"Content-Type": "application/json"})
    return _session


def _call(method: str, endpoint: str, payload=None, retries: int = 3):
    url = API + endpoint
    for attempt in range(retries):
        r = _s().request(method, url, data=json.dumps(payload) if payload is not None else None, timeout=60)
        if r.status_code == 429 and attempt < retries - 1:  # rate limit
            time.sleep(int(r.headers.get("Retry-After", "5")))
            continue
        if not r.ok:
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text[:500]
            raise RuntimeError(f"TestRail {r.status_code}: {err}")
        return r.json() if r.text else {}
    raise RuntimeError("TestRail: too many retries")


def _get_all(endpoint: str, key: str):
    """Follow TestRail pagination (_links.next)."""
    items, ep = [], endpoint
    while ep:
        data = _call("GET", ep)
        if isinstance(data, list):  # older API style
            return data
        items.extend(data.get(key, []))
        nxt = (data.get("_links") or {}).get("next")
        ep = nxt.split("/api/v2/", 1)[1] if nxt else None
    return items


def _project_id(project: Union[str, int, None] = None) -> int:
    p = project if project not in (None, "") else TR_PROJECT
    if isinstance(p, int) or str(p).isdigit():
        return int(p)
    projects = _cache.get("projects") or _get_all("get_projects", "projects")
    _cache["projects"] = projects
    for x in projects:
        if x["name"].strip().lower() == str(p).strip().lower():
            return x["id"]
    raise RuntimeError(f"Project '{p}' not found. Available: {[x['name'] for x in projects]}")


def _lookup(kind: str):
    if kind not in _cache:
        if kind == "types":
            _cache[kind] = {t["name"].lower(): t["id"] for t in _call("GET", "get_case_types")}
        elif kind == "priorities":
            _cache[kind] = {t["name"].lower(): t["id"] for t in _call("GET", "get_priorities")}
    return _cache[kind]


def _resolve(kind: str, value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    m = _lookup(kind)
    v = str(value).strip().lower()
    if v in m:
        return m[v]
    raise RuntimeError(f"Unknown {kind[:-1]} '{value}'. Allowed: {list(m)}")


def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\n{2,}", "\n", s).strip()


def _steps_payload(steps):
    """Accepts list[{content, expected}] | list[str] | JSON string | 'step1 | step2' text."""
    if steps in (None, "", []):
        return []
    if isinstance(steps, str):
        t = steps.strip()
        if t.startswith("["):
            steps = json.loads(t)
        else:
            steps = [x.strip() for x in re.split(r"\s\|\s|\n", t) if x.strip()]
    out = []
    for st in steps:
        if isinstance(st, dict):
            out.append({"content": str(st.get("content", "")).strip(), "expected": str(st.get("expected", "")).strip()})
        else:
            out.append({"content": str(st).strip(), "expected": ""})
    return [x for x in out if x["content"] or x["expected"]]


def _case_payload(title, preconditions="", steps=None, expected="", type_="", priority="", refs="", template_id=1):
    payload = {"title": title.strip(), "template_id": int(template_id)}
    t = _resolve("types", type_)
    if t:
        payload["type_id"] = t
    p = _resolve("priorities", priority)
    if p:
        payload["priority_id"] = p
    if refs:
        payload["refs"] = refs
    if preconditions:
        payload["custom_preconds"] = preconditions
    sp = _steps_payload(steps)
    if sp:
        payload["custom_steps_separated"] = sp
    if expected:
        payload["custom_expected"] = expected
    return payload


def _short_case(c):
    return {"id": c["id"], "title": c["title"], "section_id": c.get("section_id"),
            "type_id": c.get("type_id"), "priority_id": c.get("priority_id"), "refs": c.get("refs")}


def _section_titles(project_id, section_id):
    cases = _get_all(f"get_cases/{project_id}&section_id={section_id}", "cases")
    return {c["title"].strip().lower(): c["id"] for c in cases}


# ---------------------------------------------------------------- read tools
@mcp.tool()
def whoami() -> dict:
    """Check the connection: current TestRail user and the default project."""
    u = _call("GET", "get_current_user")
    pid = _project_id()
    return {"user": {k: u.get(k) for k in ("id", "name", "email", "role")}, "default_project_id": pid,
            "url": TR_URL}


@mcp.tool()
def list_projects() -> list:
    """All TestRail projects: id, name, suite_mode, completed."""
    return [{"id": p["id"], "name": p["name"], "suite_mode": p.get("suite_mode"), "completed": p.get("is_completed")}
            for p in _get_all("get_projects", "projects")]


@mcp.tool()
def list_sections(project: str = "") -> list:
    """Section tree of a project (default Aztelekom): id, name, parent_id, depth, path."""
    pid = _project_id(project)
    secs = _get_all(f"get_sections/{pid}", "sections")
    by_id = {s["id"]: s for s in secs}

    def path(s):
        parts = [s["name"]]
        while s.get("parent_id"):
            s = by_id[s["parent_id"]]
            parts.insert(0, s["name"])
        return " / ".join(parts)

    return [{"id": s["id"], "name": s["name"], "parent_id": s["parent_id"], "depth": s["depth"], "path": path(s)}
            for s in secs]


@mcp.tool()
def list_cases(section_id: int = 0, project: str = "", title_contains: str = "") -> list:
    """Cases in a project, optionally only one section and/or filtered by title substring."""
    pid = _project_id(project)
    ep = f"get_cases/{pid}" + (f"&section_id={section_id}" if section_id else "")
    cases = _get_all(ep, "cases")
    if title_contains:
        cases = [c for c in cases if title_contains.lower() in c["title"].lower()]
    return [_short_case(c) for c in cases]


@mcp.tool()
def get_case(case_id: int) -> dict:
    """One case with preconditions and steps as plain text."""
    c = _call("GET", f"get_case/{case_id}")
    return {**_short_case(c), "template_id": c.get("template_id"),
            "preconditions": _strip_html(c.get("custom_preconds")),
            "steps": [{"content": _strip_html(s.get("content")), "expected": _strip_html(s.get("expected"))}
                      for s in (c.get("custom_steps_separated") or [])],
            "expected": _strip_html(c.get("custom_expected")),
            "url": f"{TR_URL}/index.php?/cases/view/{case_id}"}


@mcp.tool()
def get_meta() -> dict:
    """Allowed case types and priorities (names -> ids)."""
    return {"types": _lookup("types"), "priorities": _lookup("priorities")}


# ---------------------------------------------------------------- write tools
@mcp.tool()
def add_section(name: str, parent_id: int = 0, project: str = "", description: str = "", dry_run: bool = True) -> dict:
    """Create a section (parent_id=0 -> top level). Skips if a section with the same name exists under the parent."""
    pid = _project_id(project)
    for s in _get_all(f"get_sections/{pid}", "sections"):
        if s["name"].strip().lower() == name.strip().lower() and (s["parent_id"] or 0) == (parent_id or 0):
            return {"created": False, "reason": "already exists", "id": s["id"]}
    payload = {"name": name}
    if parent_id:
        payload["parent_id"] = parent_id
    if description:
        payload["description"] = description
    if dry_run:
        return {"dry_run": True, "project_id": pid, "payload": payload}
    s = _call("POST", f"add_section/{pid}", payload)
    return {"created": True, "id": s["id"], "name": s["name"], "parent_id": s.get("parent_id")}


@mcp.tool()
def add_case(section_id: int, title: str, preconditions: str = "", steps: Union[str, list, None] = None,
             expected: str = "", type: str = "", priority: str = "", refs: str = "",
             allow_duplicate: bool = False, dry_run: bool = True) -> dict:
    """Create one case (template_id=1 'Test Case (Text)' with custom_steps_separated, as existing Aztelekom cases).
    steps: list of {"content","expected"} | list of strings | 'a | b | c' text.
    type: e.g. Functional, Regression, Security, Smoke & Sanity. priority: Low/Medium/High/Critical.
    Skips if the same title already exists in the section (allow_duplicate=True to override)."""
    payload = _case_payload(title, preconditions, steps, expected, type, priority, refs)
    if not allow_duplicate:
        existing = _section_titles(_project_id(), section_id).get(title.strip().lower())
        if existing:
            return {"created": False, "reason": "same title exists in section", "id": existing}
    if dry_run:
        return {"dry_run": True, "section_id": section_id, "payload": payload}
    c = _call("POST", f"add_case/{section_id}", payload)
    return {"created": True, **_short_case(c), "url": f"{TR_URL}/index.php?/cases/view/{c['id']}"}


@mcp.tool()
def add_cases_bulk(section_id: int, cases: Union[str, list], allow_duplicate: bool = False,
                   dry_run: bool = True) -> dict:
    """Create many cases in one section. cases: JSON list (or list) of objects with keys
    title, preconditions, steps, expected, type, priority, refs (same as add_case).
    Existing titles in the section are skipped. dry_run=True: validate and preview only."""
    items = json.loads(cases) if isinstance(cases, str) else cases
    existing = {} if allow_duplicate else _section_titles(_project_id(), section_id)
    created, skipped, errors, preview = [], [], [], []
    seen = set()
    for i, it in enumerate(items, 1):
        title = (it.get("title") or "").strip()
        if not title:
            errors.append({"row": i, "error": "empty title"})
            continue
        key = title.lower()
        if key in existing or (key in seen and not allow_duplicate):
            skipped.append({"row": i, "title": title, "existing_id": existing.get(key)})
            continue
        seen.add(key)
        try:
            payload = _case_payload(title, it.get("preconditions", ""), it.get("steps"), it.get("expected", ""),
                                    it.get("type", ""), it.get("priority", ""), it.get("refs", ""))
        except Exception as e:
            errors.append({"row": i, "title": title, "error": str(e)})
            continue
        if dry_run:
            preview.append({"row": i, "title": title, "steps": len(payload.get("custom_steps_separated", []))})
            continue
        try:
            c = _call("POST", f"add_case/{section_id}", payload)
            created.append({"row": i, "id": c["id"], "title": c["title"]})
        except Exception as e:
            errors.append({"row": i, "title": title, "error": str(e)})
    res = {"dry_run": dry_run, "section_id": section_id, "total": len(items), "skipped": skipped, "errors": errors}
    if dry_run:
        res["would_create"] = preview
    else:
        res["created"] = created
    return res


@mcp.tool()
def update_case(case_id: int, title: str = "", preconditions: str = "", steps: Union[str, list, None] = None,
                expected: str = "", type: str = "", priority: str = "", refs: str = "", dry_run: bool = True) -> dict:
    """Update fields of an existing case. Only the passed (non-empty) fields are changed;
    steps replaces the whole step list."""
    payload = {}
    if title:
        payload["title"] = title
    if preconditions:
        payload["custom_preconds"] = preconditions
    sp = _steps_payload(steps)
    if sp:
        payload["custom_steps_separated"] = sp
    if expected:
        payload["custom_expected"] = expected
    if type:
        payload["type_id"] = _resolve("types", type)
    if priority:
        payload["priority_id"] = _resolve("priorities", priority)
    if refs:
        payload["refs"] = refs
    if not payload:
        return {"updated": False, "reason": "nothing to update"}
    if dry_run:
        return {"dry_run": True, "case_id": case_id, "payload": payload}
    c = _call("POST", f"update_case/{case_id}", payload)
    return {"updated": True, **_short_case(c)}


if __name__ == "__main__":
    mcp.run()
