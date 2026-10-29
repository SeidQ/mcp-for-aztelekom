"""Defect validation logic (template sections, severity, Found in Environment).

Pure functions only - no TFS calls here. tfs_mcp_server.py fetches the work items and
passes their `fields` dict in. All rules live in the config/ folder next to this file
(re-read on every call, so editing a json file needs no server restart):

  config/bug_template.md       the template text
  config/severity.json         allowed severities (+ optional keyword heuristics)
  config/validation_rules.json template sections + environment_check levels/messages
  config/environments.json     prod / preprod values, title tags
  config/tfs_fields.json       real TFS field reference names
"""
import html
import json
import os
import re
from html.parser import HTMLParser

CONFIG_DIR = os.environ.get(
    "TFS_CONFIG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
)

MEDIA = "<<MEDIA>>"  # marker left in the text for every image / video / attachment link
_MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".mp4", ".mov", ".avi",
              ".mkv", ".webm", ".pdf")

DEFAULT_COSMETIC = ["typo", "spelling", "grammar", "alignment", "misaligned", "color",
                    "colour", "font", "cosmetic", "spacing"]
DEFAULT_BLOCKING = ["crash", "crashes", "cannot", "can't", "unable", "not working",
                    "blocked", "unhandled", "500"]


# ------------------------------------------------------------------ config
def _read(name, as_json=True):
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as fh:
        return json.load(fh) if as_json else fh.read()


def load_config():
    return {
        "rules": _read("validation_rules.json"),
        "severity": _read("severity.json"),
        "env": _read("environments.json"),
        "fields": _read("tfs_fields.json")["fields"],
    }


def get_template():
    cfg = load_config()
    secs = cfg["rules"]["sections"]
    return {
        "template": _read("bug_template.md", as_json=False),
        "required_sections": [s["title"] for s in secs if s.get("required")],
        "optional_sections": [s["title"] for s in secs if not s.get("required")],
        "body_field": cfg["fields"]["template_body"],
    }


def get_severity_list():
    return load_config()["severity"]["severities"]


# ---------------------------------------------------------------- html -> text
class _Text(HTMLParser):
    BLOCK = {"p", "div", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []  # stack of counters (int) for <ol>, None for <ul>

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "br" or tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "ol":
            self.lists.append(0)
        elif tag == "ul":
            self.lists.append(None)
        elif tag == "li":
            prefix = "- "
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1
                prefix = f"{self.lists[-1]}. "
            self.parts.append("\n" + prefix)
        elif tag in ("img", "video", "source", "iframe", "embed"):
            self.parts.append(f" {MEDIA} ")
        elif tag == "a":
            href = (a.get("href") or "").lower().split("?")[0]
            if "/_apis/wit/attachments/" in href or href.endswith(_MEDIA_EXT):
                self.parts.append(f" {MEDIA} ")

    def handle_endtag(self, tag):
        if tag in ("ol", "ul") and self.lists:
            self.lists.pop()
        if tag in self.BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_lines(value):
    p = _Text()
    p.feed(value or "")
    p.close()
    text = "".join(p.parts).replace("\xa0", " ")
    return [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


# --------------------------------------------------------------- sections
def _alias_re(alias):
    out = []
    for tok in re.split(r"(\s*/\s*|\s+)", alias.strip()):
        if not tok:
            continue
        if "/" in tok:
            out.append(r"\s*/\s*")
        elif tok.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(tok))
    return "".join(out)


def _heading_table(sections):
    pairs = [(a, s["key"]) for s in sections for a in s["aliases"]]
    pairs.sort(key=lambda p: -len(p[0]))  # longest alias first
    return [(re.compile(rf"^(?P<h>{_alias_re(a)})\s*(?P<colon>:)?\s*(?P<rest>.*)$", re.I), k)
            for a, k in pairs]


def split_sections(lines, sections):
    """{key: [content lines]} - a heading is an alias at line start, followed either by ':'
    (content may continue on the same line) or by nothing (content on the next lines)."""
    table = _heading_table(sections)
    out, current = {"_preamble": []}, "_preamble"  # text before the first heading
    for ln in lines:
        hit = None
        for rx, key in table:
            m = rx.match(ln)
            if m and (m.group("colon") or not m.group("rest")):
                hit = (key, m.group("rest").strip())
                break
        if hit:
            current = hit[0]
            out.setdefault(current, [])
            if hit[1]:
                out[current].append(hit[1])
        elif current:
            out[current].append(ln)
    return out


_PLACEHOLDER = re.compile(r"^(\d+[.)]\s*)?\[[^\]]*\](\s*etc\.?)?$", re.I)


def _clean(lines):
    return [ln for ln in lines if not _PLACEHOLDER.match(ln.strip())]


def check_sections(body_html, cfg, attach_fn=None):
    sections = cfg["rules"]["sections"]
    parsed = split_sections(html_to_lines(body_html), sections)
    status, issues = {}, []
    for s in sections:
        key = s["key"]
        if key not in parsed:
            st, content = "missing", []
            if s.get("accepts_text_before_first_heading"):
                pre = [ln for ln in _clean(parsed.get("_preamble", [])) if ln != MEDIA]
                if pre:
                    st = "no_heading"  # description text exists, only its heading is absent
        else:
            content = _clean(parsed[key])
            st = "ok" if content else "empty"
        if st != "ok" and s.get("satisfied_by_tfs_attachment") and attach_fn:
            try:
                if attach_fn():
                    st = "ok"
            except Exception:
                pass
        status[key] = {"title": s["title"], "required": bool(s.get("required")), "status": st}
        if st == "no_heading":
            lvl = cfg["rules"].get("no_heading_level", "warning")  # warning | error | ok
            if lvl != "ok":
                issues.append({"level": lvl, "code": "section_no_heading",
                               "message": f"'{s['title']}' başlığı yoxdur (təsvir mətni var, amma başlıqsız yazılıb)"})
        elif s.get("required") and st != "ok":
            what = "yoxdur" if st == "missing" else "boşdur (placeholder qalıb)"
            issues.append({"level": "error", "code": f"section_{st}",
                           "message": f"'{s['title']}' bölməsi {what}"})
        if st == "ok" and s.get("min_steps"):
            steps = [re.sub(r"^(\d+[.)]|-)\s*", "", c) for c in content if c != MEDIA]
            if len(steps) < s["min_steps"]:
                issues.append({"level": "warning", "code": "few_steps",
                               "message": f"'{s['title']}' bölməsində {len(steps)} addım var "
                                          f"(ən azı {s['min_steps']} gözlənilir)"})
    return status, issues


# --------------------------------------------------------------- severity
def _kw_hits(text, words):
    return [w for w in words if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.I)]


def severity_required(env_keys, cfg):
    """Severity is mandatory only for the environments in severity.json 'required_only_for'
    (default: prod). env_keys: list of env keys detected for the defect."""
    req = [e.lower() for e in cfg["severity"].get("required_only_for", ["prod"])]
    if not env_keys:
        return True   # env unknown -> keep the old strict behaviour
    return any(k in req for k in env_keys)


def check_severity(value, text, cfg, env_keys=None):
    sevs = cfg["severity"]["severities"]
    allowed = [s["tfs_value"] for s in sevs]
    value = (value or "").strip()
    required = severity_required(env_keys or [], cfg)
    info = {"value": value, "status": "ok", "required": required}
    issues = []
    match = next((s for s in sevs if s["tfs_value"].lower() == value.lower()), None)
    if not value:
        if not required:
            info["status"] = "not_required"   # non-PROD: severity lazim deyil
            return info, issues
        info["status"] = "missing"
        issues.append({"level": "error", "code": "severity_missing",
                       "message": "Severity seçilməyib (PROD defektində məcburidir)"})
        return info, issues
    if not match:
        info["status"] = "invalid"
        issues.append({"level": "error" if required else "warning", "code": "severity_invalid",
                       "message": f"Severity '{value}' icazəli siyahıda yoxdur: {', '.join(allowed)}"})
        return info, issues
    info["name"] = match["name"]
    if match["name"] == "Enhancement":
        issues.append({"level": "warning", "code": "severity_enhancement",
                       "message": "Enhancement = yeni tələb (change request), defekt deyil"})
    # Severity heuristic xəbərdarlıqları (too high / too low) söndürülüb:
    # mətndəki açar sözlərə görə severity təxmini çox səs-küy yaradırdı.
    return info, issues


# ------------------------------------------------------------ environment
_ENV_DEFAULT = {
    "field_missing": ("warning", "Found in Environment doldurulmayıb"),
    "field_invalid": ("error", "Found in Environment dəyəri icazəli siyahıda deyil"),
    "mismatch": ("error", "Found in Environment ilə title-dakı env teqi uyğun deyil"),
    "undetermined": ("error", "Env nə sahədə, nə title-da yazılıb"),
    "title_tag_missing": ("warning", "Title-da [PROD]/[PREPROD] teqi yoxdur"),
    "multi_env": ("warning", "Bir defektdə bir neçə environment var (pis praktika; köhnələr bölünmür, PROD varsa PROD SLA)"),
}


_ENV_SPLIT = re.compile(r"[,;/|]+")


def norm_env_token(tok):
    """'Pre-Prod' / 'PRE-PROD' / 'PRE0PROD' / 'pre prod' -> 'PREPROD'; 'Prod' -> 'PROD'."""
    t = re.sub(r"[\s_\-]+", "", (tok or "").strip().upper())
    t = t.replace("PRE0PROD", "PREPROD")
    if t in ("PREPRODUCTION", "PREPROD", "PRPROD"):
        return "PREPROD"
    if t in ("PROD", "PRODUCTION"):
        return "PROD"
    return t


def split_envs(field_value):
    """'PREPROD, PROD' -> ['PREPROD', 'PROD'] (normalised, order kept, no duplicates)."""
    out = []
    for tok in _ENV_SPLIT.split(field_value or ""):
        n = norm_env_token(tok)
        if n and n not in out:
            out.append(n)
    return out


def eval_environment(field_value, title, cfg):
    envs = cfg["env"]["environments"]
    fv = (field_value or "").strip()
    low_title = (title or "").lower()

    title_env = title_tag = None
    for e in envs:
        for tag in e["title_tags"]:
            if tag.lower() in low_title:
                title_env, title_tag = e["key"], tag
                break
        if title_env:
            break

    by_value = {v.upper(): e["key"] for e in envs for v in e["tfs_values"]}
    tokens = split_envs(fv)
    field_envs = [by_value[t] for t in tokens if t in by_value]
    unknown = [t for t in tokens if t not in by_value]
    field_env = None
    if field_envs:
        field_env = "prod" if "prod" in field_envs else field_envs[0]

    if not fv:
        status = "field_missing" if title_env else "undetermined"
    elif unknown or not field_envs:
        status = "field_invalid"
    elif title_env and title_env not in field_envs:
        status = "mismatch"
    elif len(field_envs) > 1:
        status = "multi_env"   # 'PREPROD, PROD': her env-de sayilir, PROD varsa PROD SLA
    else:
        # Sahə düzgündür. Başlıqda [PROD]/[PREPROD] teqi olmasa belə problem sayılmır.
        status = "ok"

    detected = field_env or title_env
    source = "field" if field_env else ("title" if title_env else None)
    env_keys = field_envs or ([title_env] if title_env else [])
    info = {"env_detected": detected, "env_source": source, "env_field_value": fv,
            "env_title_tag": title_tag, "env_status": status, "env_list": env_keys}

    issues = []
    if status != "ok":
        rules = {c["status"]: c for c in cfg["rules"].get("environment_check", {}).get("checks", [])}
        level, msg = _ENV_DEFAULT[status]
        if status in rules:
            level, msg = rules[status]["level"], rules[status]["message"]
        extra = {"mismatch": f" (sahə: {fv}, title: {title_tag})",
                 "field_invalid": f" (yazılıb: '{fv}')",
                 "multi_env": f" ({fv})"}.get(status, "")
        issues.append({"level": level, "code": f"env_{status}", "message": msg + extra})
    return info, issues


# ------------------------------------------------------------------ master
def validate_fields(item_id, fields, attach_fn=None):
    """Run every check on one work item's `fields` dict. attach_fn() -> bool is only called
    when the Screenshots/Attachments section is not satisfied by the body itself."""
    cfg = load_config()
    f = cfg["fields"]
    title = fields.get(f["title"], "") or ""
    body = fields.get(f["template_body"], "") or ""
    source = f["template_body"]
    if not body.strip():  # defect created through create_defect default layout
        alt = [fields.get(x, "") or "" for x in ("Microsoft.VSTS.TCM.ReproSteps", "System.Description")]
        if any(a.strip() for a in alt):
            body, source = "<br>".join(alt), "ReproSteps+Description"

    sec_status, issues = check_sections(body, cfg, attach_fn)
    plain = title + " " + " ".join(html_to_lines(body))
    env_info, env_issues = eval_environment(fields.get(f["found_in_environment"]), title, cfg)
    sev_info, sev_issues = check_severity(fields.get(f["severity"]), plain, cfg, env_info["env_list"])
    issues += sev_issues + env_issues

    errors = sum(1 for i in issues if i["level"] == "error")
    warnings = sum(1 for i in issues if i["level"] == "warning")
    return {
        "id": item_id, "title": title, "state": fields.get(f["state"], ""),
        "created": (fields.get(f["created"], "") or "")[:19].replace("T", " "),
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "errors": errors, "warnings": warnings, "issues": issues,
        "severity": sev_info, "environment": env_info,
        "sections": sec_status, "body_source": source,
    }
