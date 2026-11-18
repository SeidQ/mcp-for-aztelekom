#!/usr/bin/env python3
"""Gündəlik defekt yoxlaması: yeni defektləri template ilə yoxlayır və uyğun olmayanları
defekti açan şəxsə (başlıqdakı [Ad Soyad]) göndərir - Outlook mail və/və ya Teams.

  python defect_monitor.py                  normal: son işə düşmədən bəri açılan defektlər
                                            (ilk dəfə: son 24 saat)
  python defect_monitor.py --dry-run        heç nə göndərmir, nəticəni ekrana və preview html-ə yazır
  python defect_monitor.py --to-me          hamısını yalnız MONITOR_ME ünvanına göndərir (sınaq üçün)
  python defect_monitor.py --since "2026-10-19 09:00"   başlanğıc (Bakı vaxtı), marker dəyişmir
  python defect_monitor.py --remind-ids "330988,330686"  bu defektlər üçün artıq mail göndərilib:
                                            xatırlatma siyahısına əlavə et (hələ də uyğun deyilsə xatırlatma gedir)

XATIRLATMA (28.10.2026): mail göndərilmiş defektlər state/monitor_state.json-da ("sent") saxlanılır.
Hər işə düşmədə onlar yenidən yoxlanılır: hələ də template-ə uyğun deyilsə müəllifə "Xatırlatma" maili
gedir (N-ci xatırlatma, ilk bildirişin tarixi ilə); düzəldilibsə və ya Closed/Done olubsa siyahıdan çıxır.
  MONITOR_REMINDER_HOURS  iki xatırlatma arasında minimum saat (default 20 = gündə bir dəfə)

Yoxlama qaydaları tfs-defects MCP serveri ilə eynidir (config/validation_rules.json və s.).
Kontaktlar: config/contacts.json. Gizli dəyərlər (TFS_USER/TFS_PASSWORD, TEAMS_WEBHOOK_URL): secrets.env.

Ayarlar (secrets.env və ya mühit dəyişəni):
  MONITOR_CHANNELS   outlook,teams (default: outlook). smtp də mümkündür (SMTP_* dəyişənləri ilə)
  MONITOR_ME         sizin mail ünvanınız: --to-me və naməlum müəlliflər üçün
  MONITOR_CC         hər müəllif mailinə CC (məs. QA lead), vergüllə
  MONITOR_SUMMARY_TO ümumi hesabatın göndəriləcəyi ünvanlar, vergüllə (boş = göndərilmir)
  MONITOR_INCLUDE_WARNINGS=1   yalnız xəbərdarlığı olanları da göndər (default: yalnız xətalılar)
  MONITOR_OUTLOOK_TIMEOUT  bir Outlook göndərişi üçün maksimum saniyə (default 90); ilişsə dayandırılır
  TEAMS_WEBHOOK_URL  Teams Workflows "Post to a channel when a webhook request is received" URL-i
Marker (state/monitor_state.json) yalnız bütün göndərişlər uğurlu olanda yenilənir.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--outlook-send":
    # send_outlook-un uşaq prosesi: yalnız Outlook-a mail göndərir (TFS modulu yüklənmir)
    import win32com.client  # pywin32
    with open(sys.argv[2], encoding="utf-8") as _fh:
        _d = json.load(_fh)
    _m = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    _m.To, _m.Subject, _m.HTMLBody = _d["to"], _d["subject"], _d["body"]
    if _d.get("cc"):
        _m.CC = _d["cc"]
    _m.Send()
    sys.exit(0)


def _inherit_mcp_env():
    """tfs-defects MCP serverinin ayarlarını (TFS_AUTH, TFS_URL ...) Claude konfiqurasiyasından götürür,
    ki monitor MCP aləti ilə eyni şəkildə qoşulsun. Parollar secrets.env-dən gəlir."""
    import glob
    paths = [os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")]
    paths += glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude*", "LocalCache",
                                    "Roaming", "Claude", "claude_desktop_config.json"))
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                srv = json.load(fh).get("mcpServers", {}).get("tfs-defects") or {}
        except Exception:
            continue
        for k, v in (srv.get("env") or {}).items():
            os.environ.setdefault(k, str(v))
        return


_inherit_mcp_env()
os.environ.setdefault("TFS_STATE_FILE", os.path.join(HERE, "state", "validation_state.json"))
import tfs_mcp_server as T  # noqa: E402  (secrets.env burada yüklənir)

MONITOR_STATE = os.path.join(HERE, "state", "monitor_state.json")
CONTACTS = os.path.join(HERE, "config", "contacts.json")
PREVIEW = os.path.join(HERE, "defect_monitor_preview.html")
BAKU = dt.timezone(dt.timedelta(hours=4))
ICON = {"error": "&#10060;", "warning": "&#9888;&#65039;"}
_AZ = str.maketrans({"ə": "a", "Ə": "a", "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c",
                     "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u"})


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def log(msg):
    print(f"{dt.datetime.now(BAKU):%Y-%m-%d %H:%M} {msg}", flush=True)


# ---------------------------------------------------------------- authors & contacts
def norm(name):
    s = (name or "").translate(_AZ)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[._\-]+", " ", s)).strip()


def title_author(title):
    m = re.match(r"^\s*\[([^\]]+)\]", title or "")
    return m.group(1).strip() if m else ""


def load_contacts():
    if not os.path.exists(CONTACTS):
        return {}
    with open(CONTACTS, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    book = {}
    for p in data.get("people", []):
        email = (p.get("email") or "").strip()
        if not email:
            continue
        for key in [p["name"], *p.get("aliases", [])]:
            book[norm(key)] = {"name": p["name"], "email": email}
    return book


# ---------------------------------------------------------------- message bodies
def issues_html(p, include_warnings):
    items = [i for i in p["issues"] if i["level"] == "error" or include_warnings]
    return "".join(f"<li>{ICON.get(i['level'], '')} {html.escape(i['message'])}</li>" for i in items)


def table_html(problems, include_warnings):
    rows = "".join(
        f"<tr><td><a href='{html.escape(p['url'])}'>#{p['id']}</a></td>"
        f"<td>{html.escape(p['title'])}</td>"
        f"<td>{html.escape((p.get('severity') or {}).get('value') or '-')}</td>"
        f"<td><ul style='margin:0;padding-left:16px'>{issues_html(p, include_warnings)}</ul></td></tr>"
        for p in problems)
    return ("<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse;"
            "font-family:Segoe UI,Arial;font-size:13px'><tr style='background:#1F3864;color:#fff'>"
            "<th>ID</th><th>Title</th><th>Severity</th><th>Nə düzəldilməlidir</th></tr>" + rows + "</table>")


def reminder_mail(name, problems, include_warnings):
    n = len(problems)
    rows = "".join(
        f"<tr><td><a href='{html.escape(p['url'])}'>#{p['id']}</a></td><td>{html.escape(p['title'])}</td>"
        f"<td>{html.escape(_baku_date(p['reminder']['first']))}</td><td>{p['reminder']['count'] + 1}</td>"
        f"<td><ul style='margin:0;padding-left:16px'>{issues_html(p, include_warnings)}</ul></td></tr>"
        for p in problems)
    table = ("<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse;"
             "font-family:Segoe UI,Arial;font-size:13px'><tr style='background:#C00000;color:#fff'>"
             "<th>ID</th><th>Title</th><th>İlk bildiriş</th><th>Xatırlatma №</th><th>Nə düzəldilməlidir</th></tr>"
             + rows + "</table>")
    subject = f"[TFS] Xatırlatma: {n} defektiniz hələ də template-ə uyğun deyil"
    body = (f"<html><body style='font-family:Segoe UI,Arial;font-size:13px'><p>Salam, {html.escape(name)}!</p>"
            f"<p><b>Xatırlatma:</b> aşağıdakı {n} defekt barədə əvvəl mail göndərilmişdi, amma onlar hələ də "
            "bug template-ə uyğun deyil. Zəhmət olmasa düzəldin:</p>"
            f"{table}<p style='color:#666'>Bu mesaj avtomatik göndərilib (TFS defect monitor). "
            "Defekt düzəldiləndə xatırlatmalar dayanır.</p></body></html>")
    return subject, body


def _baku_date(iso):
    try:
        return f"{dt.datetime.fromisoformat(iso) + dt.timedelta(hours=4):%d.%m.%Y}"
    except Exception:
        return iso or "-"


def person_mail(name, problems, include_warnings, unknown=False):
    n = len(problems)
    intro = (f"<p>Müəllifi kontakt siyahısında tapılmayan {n} defekt template-ə uyğun deyil "
             f"(<b>{html.escape(name)}</b>). Zəhmət olmasa müəllifə ötürün.</p>" if unknown else
             f"<p>Salam, {html.escape(name)}!</p><p>Açdığınız <b>{n}</b> defekt bug template-ə və ya tip qaydasına (Aztelekom Bug) uyğun deyil. "
             "Zəhmət olmasa aşağıdakıları düzəldin:</p>")
    tail = ("<p style='color:#666'>Məcburi bölmələr: Description / Symptom, STR, "
            "Expected Result, Actual Result, Screenshots / Attachments. Severity və Found In Environment "
            "doldurulmalı, başlıqdakı [PROD]/[PREPROD]/[STG] teqi ilə uyğun olmalıdır.<br>"
            "Bu mesaj avtomatik göndərilib (TFS defect monitor).</p>")
    subject = f"[TFS] {n} defektiniz düzəliş tələb edir (template / tip)" if not unknown else f"[TFS] {n} defekt: müəllif naməlum"
    body = (f"<html><body style='font-family:Segoe UI,Arial;font-size:13px'>{intro}"
            f"{table_html(problems, include_warnings)}{tail}</body></html>")
    return subject, body


def wrong_type_mail(name, items, unknown=False):
    n = len(items)
    rows = "".join(
        f"<tr><td><a href='{html.escape(p['url'])}'>#{p['id']}</a></td><td>{html.escape(p['title'])}</td>"
        f"<td>{html.escape(p['type'])}</td><td>{html.escape(p['state'])}</td></tr>" for p in items)
    table = ("<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse;"
             "font-family:Segoe UI,Arial;font-size:13px'><tr style='background:#C00000;color:#fff'>"
             "<th>ID</th><th>Title</th><th>Type</th><th>State</th></tr>" + rows + "</table>")
    intro = (f"<p>Müəllifi kontakt siyahısında tapılmayan {n} defekt yanlış tipdə açılıb "
             f"(<b>{html.escape(name)}</b>). Zəhmət olmasa müəllifə ötürün.</p>" if unknown else
             f"<p>Salam, {html.escape(name)}!</p><p>Açdığınız <b>{n}</b> defekt yanlış tipdə (<b>Bug</b>) açılıb.</p>")
    subject = f"[TFS] {n} defekt yanlış tipdə açılıb (Bug əvəzinə Aztelekom Bug olmalıdır)"
    body = (f"<html><body style='font-family:Segoe UI,Arial;font-size:13px'>{intro}{table}"
            "<p>Defektlər <b>Aztelekom Bug</b> tipində açılmalıdır. Zəhmət olmasa düzgün tipdə yenidən açın "
            "(və ya tipi dəyişin).</p><p style='color:#666'>Bu mesaj avtomatik göndərilib (TFS defect monitor).</p>"
            "</body></html>")
    return subject, body


def wrong_type_defects(since):
    """since (UTC datetime) tarixindən sonra açılmış, tipi yalnız 'Bug' olan (Aztelekom Bug deyil) açıq defektlər."""
    q = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
         "AND [System.WorkItemType] = 'Bug' "
         "AND [System.State] NOT IN ('Closed', 'Done', 'Removed') "
         f"AND [System.CreatedDate] >= '{(since - dt.timedelta(days=1)).date().isoformat()}' "
         "ORDER BY [System.CreatedDate] ASC")
    rows = T._run_wiql(q, 500)
    return [r for r in rows if r["created"] and dt.datetime.fromisoformat(r["created"]) > since]


# ---------------------------------------------------------------- senders
def send_outlook(to, subject, body, cc=""):
    """Outlook-a göndərişi ayrıca prosesdə edir: Outlook ilişsə (cavab vermir, dialoq açıqdır)
    MONITOR_OUTLOOK_TIMEOUT saniyədən (default 90) sonra proses dayandırılır və xəta qaytarılır -
    monitor yarımçıq qalmır, növbəti mailə keçir."""
    import subprocess
    import tempfile
    timeout = int(env("MONITOR_OUTLOOK_TIMEOUT", "90"))
    fd, path = tempfile.mkstemp(suffix=".json", prefix="tfs_mail_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"to": to, "subject": subject, "body": body, "cc": cc}, fh)
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--outlook-send", path],
                           capture_output=True, text=True, timeout=timeout, cwd=HERE)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Outlook {timeout} saniyəyə cavab vermədi (açıq dialoq və ya ilişmə) - göndəriş dayandırıldı")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"exit {r.returncode}").strip()[-400:])


def send_smtp(to, subject, body, cc=""):
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, env("MAIL_FROM") or env("SMTP_USER"), to
    if cc:
        msg["Cc"] = cc
    msg.set_content("HTML dəstəkləyən mail klienti lazımdır.")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        if env("SMTP_USER"):
            s.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
        s.send_message(msg)


def send_teams(groups, unknown, checked, window):
    """Teams Workflows webhook-una Adaptive Card: hər müəllif üçün blok, @mention ilə."""
    import requests
    entities = []
    body = [{"type": "TextBlock", "weight": "Bolder", "size": "Medium", "wrap": True,
                       "text": f"TFS: template-ə uyğun olmayan defektlər ({window})"},
                      {"type": "TextBlock", "isSubtle": True, "wrap": True,
             "text": f"Yoxlanıldı: {checked}, uyğun olmayan: {sum(len(v) for v in groups.values())}"}]
    for key, problems in groups.items():
        person = unknown.get(key) or key
        if isinstance(person, dict):
            tag = f"<at>{person['name']}</at>"
            entities.append({"type": "mention", "text": tag,
                             "mentioned": {"id": person["email"], "name": person["name"]}})
        else:
            tag = f"**{key.lstrip('?')}** (kontakt yoxdur)"
        lines = "\n".join(f"- [#{p['id']}]({p['url']}) {p['title'][:90]} — "
                          + "; ".join(i["message"] for i in p["issues"] if i["level"] == "error")
                          for p in problems)
        body.append({"type": "TextBlock", "wrap": True, "separator": True, "text": f"{tag}\n{lines}"})
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4", "body": body, "msteams": {"entities": entities, "width": "Full"}}
    payload = {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
    r = requests.post(env("TEAMS_WEBHOOK_URL"), json=payload, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Teams webhook {r.status_code}: {r.text[:300]}")


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(MONITOR_STATE, encoding="utf-8") as fh:
            data = json.load(fh)
            data.setdefault("sent", {})
            return data
    except Exception:
        return {"last_run": None, "sent": {}}


def save_state(data):
    os.makedirs(os.path.dirname(MONITOR_STATE), exist_ok=True)
    tmp = MONITOR_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, MONITOR_STATE)


def load_marker():
    return load_state().get("last_run")


def save_marker(ts_utc):
    data = load_state()
    data["last_run"] = ts_utc
    save_state(data)


def mark_sent(ids, now_iso, to_by_id):
    """Göndərilmiş (yeni və ya xatırlatma) defektləri state-də qeyd edir."""
    data = load_state()
    for i in ids:
        e = data["sent"].get(str(i))
        if e:
            e["count"] = int(e.get("count", 1)) + 1
            e["last"] = now_iso
        else:
            data["sent"][str(i)] = {"first": now_iso, "last": now_iso, "count": 1, "to": to_by_id.get(i, "")}
    save_state(data)


def drop_sent(ids):
    data = load_state()
    for i in ids:
        data["sent"].pop(str(i), None)
    save_state(data)


def reminder_candidates(exclude_ids, now_utc, include_w):
    """Əvvəl mail göndərilmiş, hələ də uyğun olmayan defektlər (MONITOR_REMINDER_HOURS keçibsə).
    -> (problems, fixed_ids)"""
    data = load_state()
    gap = dt.timedelta(hours=float(env("MONITOR_REMINDER_HOURS", "20")))
    due, fixed = [], []
    ids = [int(k) for k in data["sent"] if int(k) not in exclude_ids]
    if not ids:
        return [], []
    results = {r["id"]: r for r in T.validate_ids(ids)}
    for i in ids:
        r = results.get(i)
        if r is None or (r.get("state") or "") in ("Closed", "Done", "Removed"):
            fixed.append(i)
            continue
        if not (r["status"] == "error" or (include_w and r["status"] == "warning")):
            fixed.append(i)            # düzəldilib
            continue
        e = data["sent"][str(i)]
        last = dt.datetime.fromisoformat(e.get("last") or e.get("first"))
        if now_utc - last >= gap:
            r["reminder"] = {"count": int(e.get("count", 1)), "first": e.get("first", "")}
            due.append(r)
    return due, fixed


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--to-me", action="store_true")
    ap.add_argument("--since", default="", help="Bakı vaxtı, YYYY-MM-DD HH:MM")
    ap.add_argument("--remind-ids", default="", help="artıq mail göndərilmiş defekt id-ləri (vergüllə)")
    ap.add_argument("--only-wrong-type", action="store_true",
                    help="yalnız yanlış tip (Bug) bildirişlərini göndər, template/xatırlatma maillərini yox")
    ap.add_argument("--no-wrong-type", action="store_true", help="yanlış tip (Bug) bildirişini göndərmə")
    a = ap.parse_args()
    if a.remind_ids:
        data = load_state()
        for i in re.findall(r"\d+", a.remind_ids):
            data["sent"].setdefault(i, {"first": "", "last": "2000-01-01T00:00:00", "count": 1, "to": ""})
        save_state(data)
        log(f"Xatırlatma siyahısına əlavə olundu: {a.remind_ids}")

    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)
    if a.since:
        since = (dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M") - dt.timedelta(hours=4))
    else:
        last = load_marker()
        since = dt.datetime.fromisoformat(last) if last else started - dt.timedelta(days=1)
    since_s = since.strftime("%Y-%m-%d %H:%M")
    window = f"{(since + dt.timedelta(hours=4)):%d.%m %H:%M} – {dt.datetime.now(BAKU):%d.%m %H:%M}"

    report, _ = T.run_validation(since=since_s)          # tfs-defects alətinin markerinə toxunmur
    include_w = env("MONITOR_INCLUDE_WARNINGS") == "1"
    bad = [p for p in report["problems"] if p["status"] == "error" or (include_w and p["status"] == "warning")]
    if a.only_wrong_type:
        bad = []
    # Yanlış tipdə (Bug, Aztelekom Bug əvəzinə) açılmış defektlər: eyni cədvələ əlavə sətir kimi qoşulur
    try:
        wrong = [] if a.no_wrong_type else wrong_type_defects(since)
    except Exception as e:
        wrong = []
        log(f"  YANLIŞ TİP YOXLAMASI XƏTASI: {e}")
    type_issue = {"level": "error", "message": "Defekt yanlış tipdə (Bug) açılıb: Aztelekom Bug olmalıdır"}
    by_id = {p["id"]: p for p in bad}
    for w in wrong:
        if w["id"] in by_id:
            by_id[w["id"]]["issues"].append(type_issue)
        else:
            bad.append({"id": w["id"], "title": w["title"], "url": w["url"], "state": w["state"],
                        "severity": {"value": w.get("severity") or ""}, "issues": [type_issue],
                        "status": "error", "wrong_type_only": True})
    log(f"  yanlış tipdə (Bug) açılmış: {len(wrong)}")
    log(f"Pəncərə {window}: yoxlanıldı {report['checked']}, göndəriləcək {len(bad)}")

    if a.only_wrong_type:
        reminders, fixed = [], []
    else:
        reminders, fixed = reminder_candidates({p["id"] for p in bad}, started, include_w)
    if fixed and not a.dry_run:
        drop_sent(fixed)
        log(f"  düzəldilib / bağlanıb, xatırlatma dayandı: {', '.join('#' + str(i) for i in fixed)}")
    log(f"  xatırlatma göndəriləcək: {len(reminders)}")

    book = load_contacts()
    rgroups = {}
    for p in reminders:
        author = title_author(p["title"])
        person = book.get(norm(author))
        key = person["email"] if person else f"?{author or 'başlıqda ad yoxdur'}"
        rgroups.setdefault(key, []).append(p)
        log(f"  XATIRLATMA #{p['id']} [{author or '?'}] -> {person['email'] if person else 'NAMƏLUM'} "
            f"({p['reminder']['count'] + 1}-ci)")
    groups, people = {}, {}
    for p in reminders:
        author = title_author(p["title"])
        person = book.get(norm(author))
        key = person["email"] if person else f"?{author or 'başlıqda ad yoxdur'}"
        people[key] = person or (author or "başlıqda ad yoxdur")
    for p in bad:
        author = title_author(p["title"])
        person = book.get(norm(author))
        key = person["email"] if person else f"?{author or 'başlıqda ad yoxdur'}"
        groups.setdefault(key, []).append(p)
        people[key] = person or (author or "başlıqda ad yoxdur")
        log(f"  #{p['id']} [{author or '?'}] -> {person['email'] if person else 'NAMƏLUM'}: "
            + "; ".join(i["message"] for i in p["issues"] if i["level"] == "error"))

    channels = {c.strip().lower() for c in env("MONITOR_CHANNELS", "outlook").split(",") if c.strip()}
    me, cc = env("MONITOR_ME"), env("MONITOR_CC")
    sender = send_smtp if "smtp" in channels else send_outlook
    preview, failed = [], 0
    for key, problems in groups.items():
        person = people[key]
        unknown = not isinstance(person, dict)
        name = person if unknown else person["name"]
        to = me if (a.to_me or unknown) else person["email"]
        subject, body = person_mail(name, problems, include_w, unknown=unknown)
        preview.append(f"<h3>To: {html.escape(to or '(MONITOR_ME boşdur)')} | {html.escape(subject)}</h3>{body}")
        if a.dry_run or not ({"outlook", "smtp"} & channels):
            continue
        if not to:
            log(f"  ATLANDI: '{name}' üçün ünvan yoxdur (MONITOR_ME təyin edin)")
            failed += 1
            continue
        try:
            sender(to, subject, body, "" if a.to_me else cc)
            log(f"  mail göndərildi: {to} ({len(problems)} defekt)")
            if not a.to_me:
                mark_sent([p["id"] for p in problems], started.isoformat(), {p["id"]: to for p in problems})
        except Exception as e:
            failed += 1
            log(f"  MAIL XƏTASI ({to}): {e}")

    for key, problems in rgroups.items():
        person = people[key]
        unknown = not isinstance(person, dict)
        name = person if unknown else person["name"]
        to = me if (a.to_me or unknown) else person["email"]
        subject, body = reminder_mail(name, problems, include_w)
        preview.append(f"<h3>To: {html.escape(to or '(MONITOR_ME boşdur)')} | {html.escape(subject)}</h3>{body}")
        if a.dry_run or not ({"outlook", "smtp"} & channels):
            continue
        if not to:
            log(f"  ATLANDI (xatırlatma): '{name}' üçün ünvan yoxdur")
            failed += 1
            continue
        try:
            sender(to, subject, body, "" if a.to_me else cc)
            log(f"  xatırlatma göndərildi: {to} ({len(problems)} defekt)")
            if not a.to_me:
                mark_sent([p["id"] for p in problems], started.isoformat(), {})
        except Exception as e:
            failed += 1
            log(f"  XATIRLATMA XƏTASI ({to}): {e}")

    if groups and "teams" in channels and env("TEAMS_WEBHOOK_URL") and not a.dry_run:
        try:
            send_teams(groups, {k: v for k, v in people.items() if isinstance(v, dict)}, report["checked"], window)
            log("  Teams mesajı göndərildi")
        except Exception as e:
            failed += 1
            log(f"  TEAMS XƏTASI: {e}")

    summary_to = env("MONITOR_SUMMARY_TO")
    if summary_to and bad and not a.dry_run and not a.to_me:
        subject = f"[TFS] Gündəlik defekt yoxlaması: {len(bad)} uyğunsuz ({window})"
        body = (f"<html><body style='font-family:Segoe UI,Arial;font-size:13px'><p>Yoxlanıldı: "
                f"{report['checked']}, uyğun olmayan: {len(bad)}</p>{table_html(bad, include_w)}</body></html>")
        try:
            sender(summary_to, subject, body)
        except Exception as e:
            failed += 1
            log(f"  SUMMARY XƏTASI: {e}")

    with open(PREVIEW, "w", encoding="utf-8") as fh:
        fh.write("<html><meta charset='utf-8'><body>" + ("".join(preview) or "<p>Göndəriləcək defekt yoxdur.</p>")
                 + "</body></html>")
    if a.dry_run or a.since or a.to_me:
        log(f"Marker dəyişmədi ({'dry-run' if a.dry_run else 'since/to-me'}). Preview: {PREVIEW}")
        return 0
    if failed:
        if load_marker() is None:          # ilk işə düşmə: pəncərənin başlanğıcını yadda saxla ki
            save_marker(since.isoformat())  # növbəti dəfə bu defektlər itməsin (24 saatdan kənara düşməsin)
            log(f"{failed} göndəriş uğursuz oldu - marker pəncərənin başlanğıcına yazıldı ({since_s} UTC), "
                "növbəti dəfə yenidən cəhd olunacaq")
        else:
            log(f"{failed} göndəriş uğursuz oldu - marker dəyişmədi, növbəti dəfə yenidən cəhd olunacaq")
        return 1
    save_marker(started.isoformat())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"XƏTA: {type(e).__name__}: {e}")
        sys.exit(1)
