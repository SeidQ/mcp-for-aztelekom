"""Defect manager logic (28.10.2026): default list format, due dates, RCA status, reopen count,
SLA (PROD) and "who owns the next move". Pure functions only - no TFS calls here;
tfs_mcp_server.py fetches comments / history and passes them in.

Rules come from the "defect-manager" spec (Azercell / AZTelekom):
  * our side = comments / changes made through the shared account AZTelekom (real author is the
    [Name] prefix in the text); every other author is the vendor (Suntech).
  * Due date: there is no TFS field -> taken from comments of ANY author that contain "due date":
    first date after the phrase, the most recent such comment wins; before today = overdue.
  * RCA field Microsoft.VSTS.CMMI.RootCause = "Unknown" means NOT provided; real RCA is in comments.
  * Uncertain results are flagged ("unclear", confidence "low"), never guessed.
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, date

CONFIG_DIR = os.environ.get(
    "TFS_CONFIG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
)
# per-user data (rca_overrides.json): TFS_DATA_DIR, otherwise the config folder
DATA_DIR = os.environ.get("TFS_DATA_DIR") or CONFIG_DIR
BAKU = timedelta(hours=4)          # Asia/Baku, no DST
OUR_ACCOUNTS = {a.strip().lower() for a in os.environ.get("TFS_OUR_ACCOUNTS", "AZTelekom").split(",") if a.strip()}

# ---------------------------------------------------------------- states (2 workflows)
# Bug:           Proposed -> Active -> Resolved -> Closed
# Aztelekom Bug: Proposed -> Active -> In Progress -> Ready for Testing -> In testing -> Done
#                (+ Answered, Blocked, Deferred – Next Phase). All 'Bug' items were migrated to
#                'Aztelekom Bug' on 04.11.2026 (Resolved -> Ready for Testing).
VENDOR_STATES = {"proposed", "new", "active", "in progress", "committed", "approved"}
OUR_STATES = {"resolved", "ready for testing", "in testing", "answered"}   # ball on AZTelekom
PARKED_STATES = {"blocked", "deferred – next phase", "deferred - next phase"}
CLOSED_STATES = {"closed", "done", "removed"}
STATE_GROUP = {   # normalised view used in reports
    "proposed": "Proposed", "new": "Proposed",
    "active": "Active", "in progress": "Active", "committed": "Active", "approved": "Active",
    "resolved": "Resolved", "ready for testing": "Resolved", "in testing": "Resolved", "answered": "Resolved",
    "blocked": "Blocked", "deferred – next phase": "Deferred", "deferred - next phase": "Deferred",
    "closed": "Closed", "done": "Closed", "removed": "Closed",
}
RETEST_DONE_REASONS = {"verified", "validation test passed", "resolution verified and accepted"}
CANCELLED_REASONS = {"rejected", "rejected (not an issue)", "closed in error", "deferred"}


def side_of(state):
    s = (state or "").strip().lower()
    if s in VENDOR_STATES:
        return "vendor"
    if s in OUR_STATES:
        return "us"
    if s in CLOSED_STATES:
        return "none"
    if s in PARKED_STATES:
        return "parked"
    return "unclear"


def closed_kind(state, reason):
    if (state or "").strip().lower() not in CLOSED_STATES:
        return ""
    r = (reason or "").strip().lower()
    if r in RETEST_DONE_REASONS:
        return "Retest Done"
    if r in CANCELLED_REASONS:
        return "Cancelled"
    return "Closed – unclear"


# ---------------------------------------------------------------- time helpers
def parse_utc(s):
    """TFS timestamp ('2026-10-15T05:24:07.64Z' / '2026-10-15 05:24:07') -> naive UTC datetime."""
    t = (s or "").strip().replace(" ", "T").rstrip("Z")[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def baku(dt_utc, with_time=True):
    if dt_utc is None:
        return ""
    d = dt_utc + BAKU
    return d.strftime("%Y-%m-%d %H:%M") if with_time else d.strftime("%Y-%m-%d")


def baku_today(now_utc=None):
    return ((now_utc or datetime.utcnow()) + BAKU).date()


def _holidays():
    try:
        with open(os.path.join(CONFIG_DIR, "holidays.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        vals = data.get("holidays", data) if isinstance(data, dict) else data
        return {date.fromisoformat(str(v)[:10]) for v in vals}
    except (OSError, ValueError, TypeError):
        return set()


def working_hours(start_utc, end_utc, holidays=None):
    """Hours between two UTC datetimes inside 09:00-18:00 Baku, Mon-Fri, minus holidays."""
    if not start_utc or not end_utc or end_utc <= start_utc:
        return 0.0
    hol = _holidays() if holidays is None else holidays
    s, e = start_utc + BAKU, end_utc + BAKU
    total = 0.0
    day = s.date()
    while day <= e.date():
        if day.weekday() < 5 and day not in hol:
            ws = datetime.combine(day, datetime.min.time()) + timedelta(hours=9)
            we = ws + timedelta(hours=9)
            a, b = max(ws, s), min(we, e)
            if b > a:
                total += (b - a).total_seconds() / 3600
        day += timedelta(days=1)
    return total


# ---------------------------------------------------------------- text helpers
_TAG = re.compile(r"<[^>]+>")


def plain(text_html):
    t = re.sub(r"(?i)<br\s*/?>|</(div|p|li|tr|h\d)>", "\n", text_html or "")
    t = _TAG.sub(" ", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


_NAME_PREFIX = re.compile(r"^\s*\[([^\]]{2,60})\]\s*:?")


def who(author, text_plain):
    """-> (side, display name). Our comments come through AZTelekom with a [Name] prefix."""
    a = (author or "").strip()
    if a.lower() in OUR_ACCOUNTS:
        m = _NAME_PREFIX.match(text_plain or "")
        return "us", (m.group(1).strip() if m else a)
    return "vendor", a or "?"


def short(text, n=220):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


# ---------------------------------------------------------------- due dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
# Rule (.kiro/steering/tfs-due-dates.md): only comments that contain "due date" count (any author);
# the FIRST date written after the phrase is the due date; the most recent such comment wins.
_DATE_PATTERNS = [
    ("dmy", re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?![\d])(?!\.\d)")),
    ("ymd", re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")),
    ("d_mon_y", re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?" + _MON + r"\b\.?,?\s*(\d{4})?", re.I)),
    ("mon_d_y", re.compile(r"\b" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:,?\s*(\d{4}))?", re.I)),
    ("dm", re.compile(r"(?<![\d.:])(\d{1,2})[./](\d{1,2})(?![\d:])(?![./]\d)")),   # '13.10' (no year)
]
DUE_PHRASE = re.compile(r"\bdue[\s\-]*date\b", re.I)
_SENT_SPLIT = re.compile(r"\n+|(?<=[.!?])\s+(?=[A-Z\[(])")   # used by rca_info


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def find_dates(text, ref_year):
    """[(position, date)] of every calendar date in the text, ordered by position.
    Year missing -> ref_year (the year the comment was written)."""
    out = []
    for kind, rx in _DATE_PATTERNS:
        for m in rx.finditer(text):
            g = m.groups()
            if kind == "dmy":
                y = int(g[2]) + 2000 if len(g[2]) == 2 else g[2]   # '22.10.26' -> 2026
                d = _mk(y, g[1], g[0])
            elif kind == "ymd":
                d = _mk(g[0], g[1], g[2])
            elif kind == "d_mon_y":
                d = _mk(g[2] or ref_year, _MONTHS[g[1][:3].lower()], g[0])
            elif kind == "mon_d_y":
                d = _mk(g[2] or ref_year, _MONTHS[g[0][:3].lower()], g[1])
            else:  # dm
                d = _mk(ref_year, g[1], g[0])
            if d:
                out.append((m.start(), -(m.end() - m.start()), d))
    out.sort()   # earliest position first; at the same position the longest match wins
    return [(p, d) for p, _neg_len, d in out]


def due_from_comment(text_plain, comment_dt_utc):
    """-> (due_date or None, quote, confidence). The comment must contain 'due date'; the first date
    written after the first 'due date' is taken ('Due date: 31.10.2026 ... previously given due date
    13.10.2026' -> 31.10.2026). Past dates count too (they make the defect overdue)."""
    txt = text_plain or ""
    m = DUE_PHRASE.search(txt)
    if not m:
        return None, "", ""
    ref_year = (comment_dt_utc + BAKU).year
    tail = txt[m.start():]
    hits = find_dates(tail, ref_year)
    if not hits:
        return None, "", ""
    pos, d = hits[0]
    return d, tail[: pos + 12], "high"


def due_info(item_id, comments, state, deliveries=None, now_utc=None):
    """comments: [{author, created (UTC str), text (html)}] oldest or newest first.
    -> {due, given_by, given_on, source, changes, history, overdue, confidence, quote}
    Due date = the most recent comment that contains 'due date' with a date (any author)."""
    now_utc = now_utc or datetime.utcnow()
    seq = []
    for c in sorted(comments, key=lambda c: c.get("created") or ""):
        txt = plain(c.get("text", ""))
        _side, name = who(c.get("author"), txt)
        cdt = parse_utc(c.get("created"))
        if not cdt:
            continue
        d, quote, conf = due_from_comment(txt, cdt)
        if d:
            seq.append({"due": d, "by": name, "on": cdt, "quote": short(quote, 160), "confidence": conf})
    changes = 0
    for a, b in zip(seq, seq[1:]):
        if a["due"] != b["due"]:
            changes += 1
    out = {"due": None, "given_by": "", "given_on": "", "source": "none given", "changes": changes,
           "history": [{"due": s["due"].isoformat(), "by": s["by"], "on": baku(s["on"])} for s in seq],
           "confidence": "", "quote": ""}
    if seq:
        last = seq[-1]
        out.update(due=last["due"], given_by=last["by"], given_on=baku(last["on"]),
                   source="comment", confidence=last["confidence"], quote=last["quote"])
    given = parse_utc(out["given_on"].replace(" ", "T")) - BAKU if out["given_on"] else None
    out["overdue"] = overdue_text(out["due"], state, deliveries, now_utc, given)
    if out["due"]:
        out["due"] = out["due"].isoformat()
    return out


def overdue_text(due, state, deliveries, now_utc, given_utc=None):
    """deliveries: datetimes (UTC) when the vendor moved the defect to our side / closed it
    (a single datetime is accepted too). The due date is judged against the FIRST delivery after it was
    given; if the defect was reopened after that delivery and no new date was given, it says so."""
    if not due:
        return "–"
    if deliveries and not isinstance(deliveries, (list, tuple)):
        deliveries = [deliveries]
    deliveries = sorted(d for d in (deliveries or []) if d)
    side = side_of(state)
    today = baku_today(now_utc)
    if side == "parked":
        return f"– ({state})"
    after = [d for d in deliveries if not given_utc or d >= given_utc]
    if after and side == "vendor":
        # delivered once, reopened since and no new date: still open, so the date decides
        note = " – reopened after delivery, no new date"
        return (f"Yes ({(today - due).days} d)" if due < today else "No") + note
    if after:
        rd = (after[0] + BAKU).date()
        return "No (delivered)" if rd <= due else f"No (delivered late, {(rd - due).days} d)"
    if side == "vendor":
        return f"Yes ({(today - due).days} d)" if due < today else "No"
    return "No (delivered)"


def deliveries_of(changes):
    """All moments the vendor handed the defect over (to Resolved / Ready for Testing / Closed)."""
    return [w for w, old, new, _b, _r in changes if w and side_of(new) in ("us", "none") and side_of(old) == "vendor"]


# ---------------------------------------------------------------- state history
def state_changes(updates):
    """updates: TFS /updates value list -> [(when_utc, old, new, by, reason)] chronological."""
    out = []
    for u in updates:
        f = u.get("fields") or {}
        st = f.get("System.State")
        if not isinstance(st, dict) or "newValue" not in st:
            continue
        when = parse_utc((f.get("System.ChangedDate") or {}).get("newValue") or u.get("revisedDate"))
        by = u.get("revisedBy") or {}
        by = (by.get("displayName") or by.get("uniqueName") or "") if isinstance(by, dict) else str(by)
        reason = (f.get("System.Reason") or {}).get("newValue", "") if isinstance(f.get("System.Reason"), dict) else ""
        out.append((when, st.get("oldValue") or "", st.get("newValue") or "", by, reason))
    out.sort(key=lambda x: x[0] or datetime.min)
    return out


def reopen_info(changes):
    """Reopen = our side -> vendor side (Resolved/Ready for Testing -> Active/In Progress).
    Closed -> vendor state is reported separately."""
    reopens = [c for c in changes if side_of(c[1]) == "us" and side_of(c[2]) == "vendor"]
    from_closed = [c for c in changes if side_of(c[1]) == "none" and side_of(c[2]) == "vendor"]
    return {"count": len(reopens), "closed_to_active": len(from_closed),
            "dates": [baku(c[0]) for c in reopens],
            "history_missing": not changes}


def last_delivery(changes):
    """Last time the vendor moved it to our side / closed (for 'delivered late')."""
    for when, _old, new, _by, _r in reversed(changes):
        if side_of(new) in ("us", "none"):
            return when
    return None


# ---------------------------------------------------------------- RCA
RCA_STRONG = re.compile(r"\b(root[\s-]*cause|rca|caused by|the cause (?:was|is)|was caused|is caused)\b", re.I)
RCA_WEAK = re.compile(r"\b(the reason (?:was|is|for)|because|due to|happened since|the issue (?:was|is) that|"
                      r"the problem (?:was|is) that|occurred as)\b", re.I)
RCA_ASK = re.compile(r"\b(please|could you|can you|kindly)\b.*\b(root[\s-]*cause|rca|reason)\b", re.I)
CONFIRM = re.compile(r"\b(rca (?:is )?(?:accepted|confirmed|ok)|root cause (?:is )?(?:accepted|confirmed|clear)|"
                     r"accept(?:ed)? the (?:rca|root cause|explanation)|agree(?:d)?|confirmed|"
                     r"thanks? (?:you )?for the (?:explanation|rca|clarification)|clear now|understood|noted)\b", re.I)


def rca_info(comments, rca_field="", override=None, changed=""):
    """-> {bucket 1/2/3, label, provided_by, provided_on, confirmed_by, confirmed_on, confidence, quote, source}"""
    if override and override.get("changed") == changed and override.get("bucket"):
        b = int(override["bucket"])
        return {"bucket": b, "label": RCA_LABELS[b], "confidence": "manual",
                "note": override.get("note", ""), "source": "rca_overrides.json"}
    seq = sorted(comments, key=lambda c: c.get("created") or "")
    rca = None
    for c in seq:
        txt = plain(c.get("text", ""))
        side, name = who(c.get("author"), txt)
        if side != "vendor":
            continue
        sents = [s for s in _SENT_SPLIT.split(txt) if "?" not in s]
        strong = next((s for s in sents if RCA_STRONG.search(s) and not RCA_ASK.search(s)), None)
        weak = None if strong else next((s for s in sents if RCA_WEAK.search(s)), None)
        if strong or weak:
            rca = {"on": parse_utc(c.get("created")), "by": name, "quote": short(strong or weak, 180),
                   "confidence": "high" if strong else "low"}
    field_ok = (rca_field or "").strip() and (rca_field or "").strip().lower() not in ("unknown", "n/a", "-")
    if not rca and not field_ok:
        return {"bucket": 1, "label": RCA_LABELS[1], "confidence": "high" if seq else "low",
                "source": "comments + RootCause field"}
    if not rca:
        rca = {"on": None, "by": "", "quote": f"RootCause field: {rca_field}", "confidence": "high"}
    conf = None
    for c in seq:
        cdt = parse_utc(c.get("created"))
        if rca["on"] and (not cdt or cdt <= rca["on"]):
            continue
        txt = plain(c.get("text", ""))
        side, name = who(c.get("author"), txt)
        if side != "us":
            continue
        if any(CONFIRM.search(s) for s in _SENT_SPLIT.split(txt) if "?" not in s):
            conf = {"on": cdt, "by": name}
    b = 3 if conf else 2
    res = {"bucket": b, "label": RCA_LABELS[b], "provided_by": rca["by"], "provided_on": baku(rca["on"]),
           "quote": rca["quote"], "confidence": rca["confidence"] if not conf else "low",
           "source": "vendor comment" if rca["on"] else "RootCause field"}
    if conf:
        res.update(confirmed_by=conf["by"], confirmed_on=baku(conf["on"]))
    return res


RCA_LABELS = {1: "RCA not provided", 2: "RCA provided, not confirmed by us",
              3: "RCA provided and confirmed"}


def load_rca_overrides():
    try:
        with open(os.path.join(DATA_DIR, "rca_overrides.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_rca_override(item_id, bucket, note, changed):
    data = load_rca_overrides()
    data[str(item_id)] = {"changed": changed, "bucket": int(bucket), "note": note}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "rca_overrides.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return data[str(item_id)]


# ---------------------------------------------------------------- SLA (PROD only)
def sla_info(sev_cfg, created_utc, changes, comments, now_utc=None, holidays=None):
    """sev_cfg: the severity.json entry of the CURRENT severity. -> dict or None."""
    now_utc = now_utc or datetime.utcnow()
    sla = (sev_cfg or {}).get("sla")
    if not sla or not created_utc:
        return None
    hol = _holidays() if holidays is None else holidays
    office = sla.get("mode") == "8x5"

    def dur(a, b):
        if office:
            return working_hours(a, b, hol)
        return max(0.0, (b - a).total_seconds() / 3600)

    # reaction: first vendor comment or first state change made by a vendor account
    firsts = []
    for c in comments:
        txt = plain(c.get("text", ""))
        if who(c.get("author"), txt)[0] == "vendor":
            t = parse_utc(c.get("created"))
            if t:
                firsts.append(t)
    for when, _o, _n, by, _r in changes:
        if by and by.strip().lower() not in OUR_ACCOUNTS and when:
            firsts.append(when)
    firsts = [t for t in firsts if t >= created_utc]
    react_at = min(firsts) if firsts else None
    react_limit = sla.get("reaction_hours") or sla.get("reaction_working_days", 0) * 9
    react_used = dur(created_utc, react_at or now_utc)
    reaction = {"limit": _limit_txt(sla, "reaction"), "at": baku(react_at),
                "used_h": round(react_used, 1),
                "status": ("met" if react_used <= react_limit else "breached") if react_at
                else ("pending – breached" if react_used > react_limit else "pending")}

    # resolution clock: time spent in vendor states, paused on our side
    segs, cur_state, cur_t = [], None, created_utc
    first_state = changes[0][1] if changes and changes[0][1] else "Proposed"
    cur_state = first_state
    for when, _old, new, _by, _r in changes:
        if when and when > cur_t:
            segs.append((cur_state, cur_t, when))
            cur_t = when
        cur_state = new
    if side_of(cur_state) != "none":
        segs.append((cur_state, cur_t, now_utc))
    used = sum(dur(a, b) for st, a, b in segs if side_of(st) == "vendor")
    workaround = any("workaround" in plain(c.get("text", "")).lower()
                     and who(c.get("author"), plain(c.get("text", "")))[0] == "vendor" for c in comments)
    limit_h = sla.get("resolution_hours") or sla.get("resolution_working_days", 0) * 9
    limit_txt = _limit_txt(sla, "resolution")
    if workaround and sla.get("with_workaround_full_fix_calendar_days"):
        limit_h = sla["with_workaround_full_fix_calendar_days"] * 24
        limit_txt = f"{sla['with_workaround_full_fix_calendar_days']} calendar days (workaround given)"
    open_now = side_of(cur_state) == "vendor"
    resolution = {"limit": limit_txt, "used_h": round(used, 1),
                  "used": (f"{round(used / 9, 1)} working days" if office else f"{round(used, 1)} h"),
                  "status": ("out of SLA" if used > limit_h else "in SLA") + (" (clock running)" if open_now else " (clock paused)"
                                                                               if side_of(cur_state) == "us" else ""),
                  "workaround_given": workaround}
    return {"mode": sla.get("mode"), "reaction": reaction, "resolution": resolution,
            "penalty_relevant": bool(sla.get("penalty")),
            "note": "heuristic: vendor = any author except AZTelekom; current severity used"}


def _limit_txt(sla, kind):
    if sla.get(f"{kind}_hours") is not None:
        return f"{sla[f'{kind}_hours']} h (24x7)"
    return f"{sla.get(f'{kind}_working_days')} working days (8x5)"


# ---------------------------------------------------------------- next move
def next_move(state, blocked="", rca=None, due=None, reason=""):
    side = side_of(state)
    st = (state or "").strip()
    if side == "none":
        return {"owner": "nobody", "action": f"{st} ({closed_kind(st, reason) or 'closed'}) – nothing to do"}
    if side == "parked":
        return {"owner": "both", "action": f"{st} – agree the unblock condition / next phase date"}
    if side == "us":
        if str(blocked).lower() in ("yes", "true", "1"):
            return {"owner": "us (blocked)", "action": "retest blocked – see the Blocked reason / predecessor link; unblock, then retest"}
        if rca and rca.get("bucket") == 2:
            return {"owner": "us", "action": "review & confirm the vendor's RCA, then retest"}
        return {"owner": "us", "action": "retest (or answer the vendor's question) and close / reopen"}
    if side == "vendor":
        if st.lower() in ("proposed", "new"):
            return {"owner": "vendor", "action": "confirm validity / accept the defect"}
        parts = ["fix"]
        if rca and rca.get("bucket") == 1:
            parts.append("provide RCA")
        if not (due or {}).get("due"):
            parts.append("give a due date")
        elif str((due or {}).get("overdue", "")).startswith("Yes"):
            parts.append("due date passed – give a new date")
        elif "no new date" in str((due or {}).get("overdue", "")):
            parts.append("give a new due date (reopened after the last one)")
        return {"owner": "vendor", "action": ", ".join(parts)}
    return {"owner": "unclear", "action": f"unknown state '{st}'"}


# ---------------------------------------------------------------- default formats
def compact_row(row):
    """Default list format: ID, Summary, Severity, Assignee, Created Date (Baku)."""
    return {"ID": row.get("id"), "Summary": row.get("title", ""),
            "Severity": row.get("severity") or "–", "Assignee": row.get("assigned_to") or "–",
            "Created Date": baku(parse_utc(row.get("created")))}


def due_row(row, due):
    r = compact_row(row)
    r.update({"Due Date": due.get("due") or due.get("source") or "none given",
              "Due Date Changed (times)": due.get("changes", 0),
              "Overdue": due.get("overdue", "–")})
    return r
