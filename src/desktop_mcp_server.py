"""sv-desktop MCP server: SunVizion desktop proqramlarını (WPF) Windows UI Automation ilə idarə edir.

Proqramlar: desk_start(app="spa") SpatialServices.AdministratorApp.exe (SV_DESKTOP_EXE),
            desk_start(app="pri") RI.PhysicalResourcesApp.exe - Physical Resource Inventory (SV_PRI_EXE).
Login: SV_DESKTOP_USER/SV_DESKTOP_PASS, verilməyibsə PORTAL_USER/PORTAL_PASS (eyni hesab).

İş qaydası (Claude üçün):
  1. desk_start            -> proqramı açır və ya açıq olana qoşulur, login pəncərəsi gələrsə login olur
  2. desk_tree / desk_find -> pəncərədəki elementlər; hər elementin ref-i olur (e12 kimi)
  3. desk_click / desk_set_text / desk_select / desk_check / desk_read_grid -> ref və ya ad ilə
  4. desk_screenshot       -> sübut üçün şəkil (Claude şəkli görür, fayl SV_DESKTOP_SHOT_DIR-ə yazılır)
  5. UIA-da görünməyən elementlər (Telerik ağac sətirləri, alt tablar, Properties sahələri):
     desk_screenshot -> şəkildə yeri tap -> desk_click_xy(x, y[, right=True]); və ya desk_tree(raw=True)
  6. Modal dialoq açan düymələr (New..., Delete, Change State) artıq tool-u bloklamır: klikdən sonra
     desk_windows ilə dialoqa baxın. Hər hansı çağırış SV_DESKTOP_CALL_TIMEOUT (45 s) keçsə, UIA thread yenilənir.
  7. Sürət: desk_start(mode="main"|"new"|"existing", project=...) login + work context-i bir çağırışda edir;
     desk_batch bir neçə addımı bir çağırışda icra edir (ilk xətada dayanır); desk_screenshot(scale=0.5 / region)
     aralıq addımlar üçün. Diskə həmişə tam ölçülü PNG yazılır; nəticəni təyin edən addımda tam ölçüyə baxın.

Ref-lər yalnız son desk_tree/desk_find nəticəsi üçün keçərlidir; ekran dəyişəndə yenidən oxuyun.
Vəziyyəti dəyişən düymələr (Save, Delete, Remove, Finalize, Activate, Accept, Approve, Commit,
Apply, Execute, Run) yalnız confirm=True ilə basılır.
"""
import functools
import os
import re
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from mcp.server.fastmcp import FastMCP, Image


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

EXE = os.getenv(
    "SV_DESKTOP_EXE",
    r"C:\Program Files (x86)\Suntech\20_0\SpatialServices\SpatialServices.Application"
    r"\SpatialServices.AdministratorApp.exe",
)
# Proqramlar: desk_start(app=...) ilə seçilir, sonrakı desk_* tool-lar həmin proqramla işləyir.
APPS = {
    "spa": EXE,
    "pri": os.getenv(
        "SV_PRI_EXE",
        r"C:\Program Files (x86)\Suntech\20_0\ResourceInventory\ResourceInventory.Application"
        r"\RI.PhysicalResourcesApp.exe"),
}
USER = os.getenv("SV_DESKTOP_USER") or os.getenv("PORTAL_USER", "")
PASS = os.getenv("SV_DESKTOP_PASS") or os.getenv("PORTAL_PASS", "")
SHOT_DIR = os.getenv("SV_DESKTOP_SHOT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "shots")
PROFILE = os.getenv("SV_DESKTOP_PROFILE", "Technical Design")   # login-dən sonra seçilən profil
START_TIMEOUT = int(os.getenv("SV_DESKTOP_START_TIMEOUT", "180"))
MAX_LINES = 400

GUARDED = re.compile(r"\b(save|delete|remove|finali[sz]e|activate|accept|approve|commit|apply|execute|run|"
                     r"yadda saxla|sil)\b", re.I)
LOGIN_BUTTONS = ["Login", "Log in", "Log In", "Sign in", "OK", "Daxil ol", "Zaloguj"]

mcp = FastMCP("sv-desktop")


# --------------------------------------------------------------------------- one UIA thread
def _init_com():
    import comtypes
    comtypes.CoInitialize()


_pool = ThreadPoolExecutor(max_workers=1, initializer=_init_com)
CALL_TIMEOUT = int(os.getenv("SV_DESKTOP_CALL_TIMEOUT", "45"))   # MCP 60 s-dən az olmalıdır


def ui(fn=None, *, timeout: int = 0):
    """Hər tool bir UIA thread-də işləyir. Çağırış limiti keçərsə (məs. modal dialoq UIA çağırışını
    bloklayır), həmin thread atılır və yeni thread yaradılır - sonrakı tool-lar ilişmir.
    Limit: `timeout` (verilibsə) və ya CALL_TIMEOUT; tool-un öz timeout/seconds arqumenti varsa ondan +20 s."""
    if fn is None:
        return lambda f: ui(f, timeout=timeout)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _pool
        job = _start_job.get("fut")
        if job is not None and not job.done() and fn.__name__ not in ("desk_start",):
            raise RuntimeError("desk_start hələ işləyir (proqram açılır / login / work context). "
                               "Bitməsini desk_start_status ilə gözləyin.")
        limit = timeout or CALL_TIMEOUT
        for k in ("timeout", "max_seconds"):
            if isinstance(kwargs.get(k), (int, float)):
                limit = max(limit, kwargs[k] + 20)
        fut = _pool.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=limit)
        except FuturesTimeout:
            old = _pool
            _pool = ThreadPoolExecutor(max_workers=1, initializer=_init_com)
            old.shutdown(wait=False, cancel_futures=True)
            _refs.clear()
            raise RuntimeError(
                f"{fn.__name__} {limit} saniyəyə cavab vermədi - çox güman modal dialoq açılıb və "
                "çağırışı bloklayır. Yeni UIA thread başladıldı; desk_windows ilə açılan pəncərəyə baxın.")
    return wrapper


_refs: dict[str, object] = {}
_state = {"proc": None, "app": "spa", "profile": ""}


def _exe() -> str:
    return APPS.get(_state["app"], EXE)
_start_job: dict = {"fut": None, "result": None, "args": None, "t0": 0.0}
_marks: list = []        # desk_start mərhələlərinin vaxtı


def _mark(label: str):
    if _start_job.get("t0"):
        _marks.append(f"{label} {time.time() - _start_job['t0']:.0f}s")
START_REPLY = int(os.getenv("SV_DESKTOP_START_REPLY", "40"))   # körpü 60 s gözləyir


# --------------------------------------------------------------------------- helpers
def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _pids() -> set[int]:
    import psutil
    name = os.path.basename(_exe()).lower()
    return {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == name}


def _windows():
    """Proqramın görünən üst səviyyə pəncərələri, Z-sırası ilə (ən üstdəki birinci)."""
    pids = _pids()
    if not pids:
        return []
    out = []
    for w in _desktop().windows():
        try:
            if w.element_info.process_id in pids and w.is_visible():
                out.append(w)
        except Exception:
            pass
    return out


def _window(title: str = ""):
    wins = _windows()
    if not wins:
        raise RuntimeError("Proqram açıq deyil. Əvvəl desk_start çağırın.")
    if not title:
        return wins[0]
    for w in wins:
        if title.lower() in (w.window_text() or "").lower():
            return w
    raise RuntimeError(f"'{title}' adlı pəncərə tapılmadı. Açıq pəncərələr: "
                       + "; ".join(repr(w.window_text()) for w in wins))


def _ctype(c) -> str:
    try:
        return c.element_info.control_type or ""
    except Exception:
        return ""


def _value(c) -> str:
    try:
        v = c.iface_value.CurrentValue
        if v:
            return str(v)
    except Exception:
        pass
    try:
        if _ctype(c) in ("CheckBox", "RadioButton"):
            return "☑" if c.get_toggle_state() == 1 else "☐"
    except Exception:
        pass
    return ""


def _describe(c, ref: str) -> str:
    ei = c.element_info
    parts = [f"[{ref}]", _ctype(c) or "?"]
    if ei.name:
        parts.append(repr(ei.name[:80]))
    if ei.automation_id:
        parts.append(f"id={ei.automation_id}")
    v = _value(c)
    if v and v != ei.name:
        parts.append(f"value={v[:80]!r}")
    try:
        if not c.is_enabled():
            parts.append("(disabled)")
    except Exception:
        pass
    try:
        if _ctype(c) == "Edit" and c.iface_value.CurrentIsReadOnly:
            parts.append("(readonly)")
    except Exception:
        pass
    return " ".join(parts)


def _new_ref(c) -> str:
    ref = f"e{len(_refs) + 1}"
    _refs[ref] = c
    return ref


def _resolve(target: str, control_type: str = "", window: str = ""):
    """target: ref (e12), automation id, və ya görünən ad (ad hissəsi də olar)."""
    if re.fullmatch(r"e\d+", target or "") and target in _refs:
        return _refs[target]
    win = _window(window)
    kw = {"control_type": control_type} if control_type else {}
    cands = []
    for c in win.descendants(**kw):
        try:
            if c.is_visible():
                cands.append(c)
        except Exception:
            pass
    low = (target or "").lower()
    for match in (lambda c: c.element_info.automation_id == target,
                  lambda c: (c.element_info.name or "") == target,
                  lambda c: (c.element_info.name or "").lower() == low,
                  lambda c: bool(low) and low in (c.element_info.name or "").lower()):
        for c in cands:
            try:
                if match(c):
                    return c
            except Exception:
                pass
    raise RuntimeError(f"'{target}' elementi tapılmadı. desk_find və ya desk_tree ilə yoxlayın.")


def _edit_for_label(label: str, window: str = ""):
    """Etiketin (Text) sağında və ya altında ən yaxın Edit/ComboBox."""
    win = _window(window)
    labels = [t for t in win.descendants(control_type="Text")
              if t.is_visible() and label.lower() in (t.element_info.name or "").lower()]
    if not labels:
        return None
    lr = labels[0].rectangle()
    best, dist = None, 10 ** 9
    for c in win.descendants():
        if _ctype(c) not in ("Edit", "ComboBox", "Spinner") or not c.is_visible():
            continue
        r = c.rectangle()
        same_row = abs((r.top + r.bottom) / 2 - (lr.top + lr.bottom) / 2) < 15 and r.left >= lr.left
        below = r.top >= lr.bottom - 5 and abs(r.left - lr.left) < 60
        if same_row or below:
            d = abs(r.left - lr.right) + abs(r.top - lr.top) * 2
            if d < dist:
                best, dist = c, d
    return best


def _front(win):
    """Pəncərəni ön plana gətirir (klaviatura və screenshot üçün lazımdır)."""
    try:
        import win32con
        import win32gui
        h = win.handle
        if win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            win.set_focus()
        except Exception:
            pass
        if win32gui.GetForegroundWindow() != h:
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            win32gui.SetWindowPos(h, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            win32gui.SetWindowPos(h, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            try:
                win.set_focus()
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(0.3)


def _select_item(c) -> bool:
    try:
        c.iface_selection_item.Select()
        time.sleep(0.4)
        return True
    except Exception:
        return False


def _press(c, double=False, right=False):
    if not (double or right) and _ctype(c) in ("DataItem", "ListItem", "TreeItem", "TabItem") and _select_item(c):
        return
    if double or right:
        try:
            _front(c.top_level_parent())
        except Exception:
            pass
    if right:
        c.click_input(button="right")
    elif double:
        c.double_click_input()
    elif not _invoke_nowait(c):
        try:
            c.select()
        except Exception:
            c.click_input()
    time.sleep(0.6)


def _invoke_nowait(c, wait: float = 3.0) -> bool:
    """Invoke-u ayrıca thread-də edir. WPF-də düymə modal dialoq açanda Invoke dialoq bağlanana
    qədər geri qayıtmır; burada `wait` saniyə gözləyib davam edirik (dialoq açıq qalır).
    Element başqa COM thread-də yenidən nöqtədən (rect mərkəzi) tapılır."""
    try:
        r = c.rectangle()
        if r.width() <= 0 or r.height() <= 0:
            raise ValueError
        x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        want = c.element_info.name or ""
    except Exception:
        try:
            c.invoke()
            return True
        except Exception:
            return False
    result = {"ok": None, "err": ""}

    def worker():
        try:
            _init_com()
            el = _desktop().from_point(x, y)
            for _ in range(4):                       # şəkil/mətn uşağıdırsa, Invoke dəstəkləyən valideynə qalx
                try:
                    el.iface_invoke
                    if want and (el.element_info.name or "") not in ("", want):
                        raise AttributeError
                    result["ok"] = True
                    el.invoke()
                    return
                except Exception:
                    el = el.parent()
                    if el is None:
                        break
            result["ok"] = False
        except Exception as e:                       # noqa: BLE001
            result["ok"], result["err"] = False, str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(wait)
    if t.is_alive():
        return True            # Invoke getdi, amma bloklanıb -> modal dialoq açılıb
    if result["ok"]:
        return True
    try:                       # nöqtədən tapılmadı: köhnə qayda
        c.invoke()
        return True
    except Exception:
        return False


def _wait_ready(seconds=3.0):
    time.sleep(seconds)


# --------------------------------------------------------------------------- tools
@mcp.tool()
def desk_start(login: bool = True, mode: str = "", project: str = "", app: str = "", profile: str = "") -> str:
    """Proqramı açır (artıq açıqdırsa qoşulur). Login pəncərəsi gəlsə və login=True olsa,
    SV_DESKTOP_USER/PASS (və ya PORTAL_USER/PASS) ilə daxil olur. Proqramın açılması 1-3 dəqiqə çəkə bilər.
    mode verilsə "Select Work Context" də doldurulur və Start basılır:
      mode="main"                       -> Database main version
      mode="new", project="UAT_SS_2010" -> New project (ad yazılır)
      mode="existing", project="..."    -> Existing project (siyahıdan seçilir)
    Sonda ana pəncərənin başlığı yoxlanılır (new/existing üçün proyekt adı başlıqda olmalıdır).
    Hər hansı mərhələ alınmasa dayanır və səbəbi yazır - nəticəni desk_screenshot ilə təsdiqləyin.
    app: "spa" (Spatial Services Administrator, default) və ya "pri" (Physical Resource Inventory,
    RI.PhysicalResourcesApp.exe). Seçilən proqram yadda qalır: sonrakı bütün desk_* tool-ları onunla işləyir
    (proqramlar arasında keçmək üçün desk_start(app=...) yenidən çağırın - açıqdırsa sadəcə qoşulur).
    mode/project yalnız SPA üçündür. profile: login profilinin adının bir hissəsi (boş = SV_DESKTOP_PROFILE).
    Açılış uzun çəkdiyi üçün iş arxa planda gedir: ~40 s-də bitməsə "davam edir" qaytarılır,
    nəticəni desk_start_status ilə alın (o vaxta qədər digər desk_* tool-ları gözləyir)."""
    fut = _start_job.get("fut")
    if fut is not None and not fut.done():
        return "desk_start artıq işləyir " + _job_age() + " - desk_start_status ilə gözləyin."

    def job():
        # Eyni UIA thread-də (_pool) işləyir: UIA COM obyekti ilk yaradıldığı thread-ə bağlıdır;
        # ayrı thread-də yaradılıb thread bitəndə sonrakı bütün tool-lar "Proqram açıq deyil" görürdü (21.10).
        try:
            _start_job["result"] = _do_start(login, mode, project)
        except Exception as e:                       # noqa: BLE001
            _start_job["result"] = f"XƏTA: {e}"

    if app:
        if app.lower() not in APPS:
            return f"app: {', '.join(APPS)} olmalıdır."
        _state["app"] = app.lower()
    _state["profile"] = profile
    if mode and _state["app"] != "spa":
        return "mode/project yalnız app='spa' üçündür."
    _marks.clear()
    _start_job.update(result=None, args=(login, mode, project), t0=time.time(), now="")
    _start_job["fut"] = _pool.submit(job)
    return _job_wait(START_REPLY)


@mcp.tool()
def desk_start_status(wait: int = 40) -> str:
    """desk_start-ın nəticəsi. İş davam edirsə ən çox `wait` saniyə (≤45) gözləyir."""
    if _start_job.get("fut") is None:
        return "desk_start çağırılmayıb."
    return _job_wait(min(max(wait, 0), 45))


def _job_age() -> str:
    return f"({int(time.time() - _start_job['t0'])} s)"


def _job_wait(sec: int) -> str:
    fut = _start_job["fut"]
    try:
        fut.result(timeout=sec)
    except FuturesTimeout:
        return ("DAVAM EDİR " + _job_age() + " [" + ", ".join(_marks) + "] indi açıq: "
                + (_start_job.get("now") or "-") + ". desk_start_status ilə yenidən yoxlayın.")
    except Exception:
        pass
    return f"BİTDİ {_job_age()} [{', '.join(_marks)}]: {_start_job['result']}"


def _do_start(login: bool, mode: str, project: str) -> str:
    out = _start(login)
    if not mode:
        return out
    if "Login dayandırıldı" in out or "hələ açıqdır" in out:
        return out + " | Work context seçilmədi (login tamamlanmayıb)."
    return out + " | " + _work_context(mode, project)


def _work_context(mode: str, project: str) -> str:
    mode = mode.strip().lower()
    if mode not in ("main", "new", "existing"):
        raise RuntimeError("mode: main / new / existing olmalıdır.")
    if mode != "main" and not project:
        raise RuntimeError(f"mode={mode} üçün project adı lazımdır.")
    win, t0 = None, time.time()
    while time.time() - t0 < 90:
        win = next((w for w in _windows() if "select work context" in (w.window_text() or "").lower()), None)
        if win is not None:
            break
        time.sleep(1)
    if win is None:
        return "DAYANDI: 'Select Work Context' 90 saniyəyə açılmadı. Pəncərələr: " + \
            "; ".join(repr(w.window_text()) for w in _windows())
    time.sleep(1)
    radios = sorted((r for r in win.descendants(control_type="RadioButton") if r.is_visible()),
                    key=lambda r: r.rectangle().top)
    if len(radios) < 3:
        return f"DAYANDI: work mode düymələri tapılmadı ({len(radios)} ədəd)."
    radio = radios[{"main": 0, "new": 1, "existing": 2}[mode]]
    _front(win)
    radio.click_input()
    time.sleep(0.5)
    try:
        if radio.get_toggle_state() != 1 and not radio.iface_selection_item.CurrentIsSelected:
            return f"DAYANDI: '{mode}' rejimi seçilmədi."
    except Exception:
        pass
    if mode == "new":
        edit = next((e for e in win.descendants(control_type="Edit")
                     if (e.element_info.automation_id or "").lower() == "newproject"), None)
        if edit is None:
            return "DAYANDI: 'New project' ad sahəsi tapılmadı."
        try:
            edit.iface_value.SetValue(project)
        except Exception:
            edit.set_focus()
            edit.type_keys("^a{BACKSPACE}" + _esc(project), with_spaces=True)
        if (_value(edit) or "") != project:
            return f"DAYANDI: proyekt adı yazılmadı (sahədə: {_value(edit)!r})."
    if mode == "existing":
        low = project.lower()
        row = None
        for c in win.descendants():
            try:
                if c.is_visible() and (c.element_info.name or "").strip().lower() == low:
                    row = c
                    break
            except Exception:
                pass
        if row is None:
            return f"DAYANDI: '{project}' 'Existing project' siyahısında yoxdur (yalnız öz proyektləriniz görünür)."
        _front(win)
        row.click_input()
        time.sleep(0.5)
    _front(win)
    _mark("work context dolduruldu")
    # Start-ı birbaşa basırıq: Existing project-də fokus cədvəldə olur və Enter cədvələ gedə bilər (21.10).
    start = None
    for c in win.descendants():
        try:
            if (c.element_info.name or "").replace("_", "").strip().lower() == "start" and c.is_visible():
                start = c
                break
        except Exception:
            pass
    _front(win)
    if start is not None:
        start.click_input()
        _mark("Start basıldı")
    else:
        win.type_keys("{ENTER}", set_foreground=False)
        _mark("Start (Enter)")
    t0 = time.time()
    while time.time() - t0 < 300:                          # proyektin açılması 3+ dəq çəkə bilər
        time.sleep(2)
        _start_job["now"] = "; ".join(repr(w.window_text()) for w in _windows()) or "pəncərə yoxdur"
        mains = [w for w in _windows() if (w.window_text() or "").startswith("Spatial Services Administrator -")]
        if mains:
            _mark("ana pəncərə")
            title = mains[0].window_text()
            if mode == "main" and " - " in title.split("Administrator - ", 1)[-1]:
                return f"XƏBƏRDARLIQ: main rejim seçildi, amma başlıqda proyekt var: {title!r}"
            if mode != "main" and not title.rstrip().endswith(project):
                return f"XƏBƏRDARLIQ: başlıqda '{project}' yoxdur: {title!r}"
            return f"Work context OK ({mode}{': ' + project if project else ''}). Başlıq: {title!r}"
        others = [w.window_text() for w in _windows() if "select work context" not in (w.window_text() or "").lower()]
        if others and any(t and not t.startswith("Spatial Services Administrator") for t in others):
            return "DAYANDI: Start-dan sonra gözlənilməz pəncərə/mesaj: " + "; ".join(repr(t) for t in others)
    return "DAYANDI: Start-dan sonra 300 saniyəyə ana pəncərə açılmadı."


def _start(login: bool) -> str:
    msg = []
    if not _windows():
        exe = _exe()
        if not os.path.exists(exe):
            raise RuntimeError(f"Fayl tapılmadı: {exe}")
        _state["proc"] = subprocess.Popen([exe], cwd=os.path.dirname(exe))
        msg.append(f"Başladıldı: {os.path.basename(exe)}")
        t0 = time.time()
        while not _windows() and time.time() - t0 < START_TIMEOUT:
            time.sleep(2)
        if not _windows():
            raise RuntimeError(f"{START_TIMEOUT} saniyəyə pəncərə açılmadı.")
        _mark("pəncərə")
        _wait_ready(4)
    else:
        msg.append("Proqram artıq açıqdır.")
    if login:
        msg.append(_login())
    msg.append("Pəncərələr: " + "; ".join(repr(w.window_text()) for w in _windows()))
    return " | ".join(msg)


def _login() -> str:
    win = _window()
    if (win.window_text() or "").lower() == "login" and any(
            "select profile" in (t.element_info.name or "").lower() for t in win.descendants(control_type="Text")):
        return _after_login()          # parol artıq daxil edilib, yalnız profil seçimi qalıb
    pw_boxes = [e for e in win.descendants(control_type="Edit")
                if e.is_visible() and getattr(e.element_info.element, "CurrentIsPassword", False)]
    if not pw_boxes:
        # SunVizion login: PasswordBox UIA ağacında görünmür, düymələr Text kimi gəlir.
        # Pəncərə "Login" adlanırsa və "Username" sahəsi varsa: ad -> Tab -> parol -> Enter.
        user_edit = next((e for e in win.descendants(control_type="Edit")
                          if e.is_visible() and (e.element_info.automation_id or "").lower() in ("username", "login")),
                         None)
        if user_edit is None or "login" not in (win.window_text() or "").lower():
            return "Login forması görünmür (artıq daxil olunub və ya Windows auth)."
        if not (USER and PASS):
            return "Login forması var, amma PORTAL_USER/PORTAL_PASS (secrets.env) verilməyib - əl ilə daxil olun."
        try:
            user_edit.iface_value.SetValue(USER)
        except Exception:
            user_edit.set_focus()
            user_edit.type_keys("^a{BACKSPACE}" + USER, with_spaces=True)
        _front(win)
        user_edit.set_focus()
        user_edit.type_keys("{TAB}")
        time.sleep(0.3)
        from pywinauto.keyboard import send_keys
        send_keys("^a{BACKSPACE}" + _esc(PASS), with_spaces=True, pause=0.02)
        send_keys("{ENTER}")
        _mark("parol")
        return _after_login()
    if not (USER and PASS):
        return "Login forması var, amma SV_DESKTOP_USER/PASS (PORTAL_USER/PASS) verilməyib - əl ilə daxil olun."
    edits = [e for e in win.descendants(control_type="Edit") if e.is_visible()]
    pw_box = pw_boxes[0]
    user_box = next((e for e in edits if e is not pw_box
                     and not getattr(e.element_info.element, "CurrentIsPassword", False)), None)
    if user_box is not None:
        user_box.set_focus()
        try:
            user_box.iface_value.SetValue(USER)
        except Exception:
            user_box.type_keys("^a{BACKSPACE}" + USER, with_spaces=True)
    pw_box.set_focus()
    pw_box.type_keys("^a{BACKSPACE}" + _esc(PASS), with_spaces=True)
    btn = None
    buttons = [b for b in win.descendants(control_type="Button") if b.is_visible()]
    for name in LOGIN_BUTTONS:
        btn = next((b for b in buttons if (b.element_info.name or "").strip().lower() == name.lower()), None)
        if btn:
            break
    if btn:
        _press(btn)
    else:
        pw_box.type_keys("{ENTER}")
    return _after_login()


def _esc(text: str) -> str:
    """pywinauto send_keys üçün xüsusi simvolları qoruyur."""
    return re.sub(r"([{}+^%~()\[\]])", r"{\1}", text)


def _selected_profiles(login) -> list[str]:
    out = []
    for i in login.descendants(control_type="DataItem"):
        try:
            if i.iface_selection_item.CurrentIsSelected:
                out.append(i.element_info.name or "")
        except Exception:
            pass
    return out


def _choose_profile(login) -> tuple[bool, str]:
    """Profil sətrini seçir və həqiqətən seçildiyini yoxlayır: əvvəl real mausla klik, sonra SelectionItem."""
    want = _state.get("profile") or PROFILE
    if not want:
        return True, "Profil seçilmədi (SV_DESKTOP_PROFILE boşdur, default qalır)."
    items = login.descendants(control_type="DataItem")
    target = next((i for i in items if want.lower() in (i.element_info.name or "").lower()), None)
    if target is None:
        return False, f"'{want}' profili siyahıda yoxdur: " + ", ".join(i.element_info.name for i in items)
    name = target.element_info.name
    for attempt in ("click", "select", "click"):
        if attempt == "click":
            _front(login)
            try:
                target.click_input()
            except Exception:
                pass
        else:
            _select_item(target)
        time.sleep(0.6)
        sel = _selected_profiles(login)
        if sel == [name]:
            return True, f"Profil seçildi: {name}"
        if attempt == "click" and not sel:
            # SunVizion siyahısı seçim vəziyyətini UIA-ya vermir (heç bir sətir "selected" deyil):
            # real klik işləyir (20.10 yoxlanılıb), ona görə klikdən sonra qəbul edirik.
            return True, f"Profil seçildi (klik): {name}"
    return False, f"Profil seçilə bilmədi. Seçilmiş: {_selected_profiles(login) or '-'}, lazım olan: {name}."


def _after_login() -> str:
    t0, profile_done, chosen = time.time(), False, ""
    while time.time() - t0 < 90:
        time.sleep(2)
        wins = _windows()
        titles = [(w.window_text() or "") for w in wins]
        login = next((w for w in wins if (w.window_text() or "").lower() == "login"), None)
        if titles and login is None:
            _mark("login bitdi")
            return f"Login olundu ({USER}, profil: {chosen or 'yoxlanılmadı'}). Pəncərələr: " + \
                "; ".join(repr(t) for t in titles)
        if login is not None and not profile_done:
            names = [t.element_info.name or "" for t in login.descendants(control_type="Text")]
            if any("select profile" in n.lower() for n in names):
                profile_done = True
                _mark("profil ekranı")
                ok, msg = _choose_profile(login)
                chosen = msg.split(": ", 1)[-1] if ok else ""
                if not ok:
                    return msg + " Login dayandırıldı (Log In basılmadı) - desk_screenshot ilə baxın."
                _front(login)
                login.type_keys("{ENTER}", set_foreground=False)     # "Log In" default düymədir
                _mark("profil seçildi")
    return "Login göndərildi, amma login pəncərəsi hələ açıqdır - desk_screenshot ilə yoxlayın (parol səhv ola bilər)."


@mcp.tool()
@ui
def desk_login() -> str:
    """Açıq login pəncərəsinə istifadəçi adı və parolu yazıb daxil olur."""
    return _login()


@mcp.tool()
@ui
def desk_windows() -> str:
    """Proqramın açıq pəncərələri (üstdəki birinci). Modal dialoqlar da burada görünür."""
    wins = _windows()
    if not wins:
        return "Proqram açıq deyil."
    return "\n".join(f"{i}. {w.window_text()!r} [{_ctype(w)}] {w.rectangle()}" for i, w in enumerate(wins))


@mcp.tool()
@ui
def desk_tree(window: str = "", max_depth: int = 6, only_interactive: bool = False, raw: bool = False) -> str:
    """Pəncərədəki elementlər ağac kimi, hər biri ref ilə ([e12] Button 'Save' id=btnSave).
    window: başlığın bir hissəsi (boş = ən üstdəki pəncərə). only_interactive=True yalnız
    düymə/sahə/siyahı/cədvəl kimi elementləri göstərir. raw=True: UIA Raw View ilə gəzir və
    'görünməz' (offscreen) işarəli elementləri də göstərir - Telerik ağac/cədvəl sətirləri üçün."""
    interactive = {"Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "List", "ListItem", "DataGrid",
                   "Table", "Tree", "TreeItem", "Tab", "TabItem", "MenuItem", "Menu", "Hyperlink",
                   "DataItem", "SplitButton", "Spinner", "Slider"}
    _refs.clear()
    win = _window(window)
    lines = [f"Pəncərə: {win.window_text()!r}"]

    def kids(c):
        if not raw:
            return c.children()
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        walker = IUIA().iuia.RawViewWalker
        out, el = [], walker.GetFirstChildElement(c.element_info.element)
        while el and len(out) < 500:
            try:
                out.append(UIAWrapper(UIAElementInfo(el)))
            except Exception:
                pass
            el = walker.GetNextSiblingElement(el)
        return out

    def walk(c, depth):
        if len(lines) >= MAX_LINES or depth > max_depth:
            return
        try:
            children = kids(c)
        except Exception:
            return
        for ch in children:
            try:
                if not raw and not ch.is_visible():
                    continue
            except Exception:
                continue
            show = not only_interactive or _ctype(ch) in interactive
            if show:
                lines.append("  " * depth + _describe(ch, _new_ref(ch)))
            walk(ch, depth + 1)
            if len(lines) >= MAX_LINES:
                lines.append(f"... ({MAX_LINES} sətir limiti; window/max_depth/only_interactive ilə daraldın)")
                return

    walk(win, 1)
    return "\n".join(lines)


@mcp.tool()
@ui
def desk_find(text: str = "", control_type: str = "", window: str = "", limit: int = 40) -> str:
    """Mətnə (ad / automation id / dəyər) və ya növə görə element axtarır. control_type nümunələri:
    Button, Edit, ComboBox, CheckBox, DataGrid, DataItem, TabItem, MenuItem, TreeItem, Text."""
    _refs.clear()
    win = _window(window)
    kw = {"control_type": control_type} if control_type else {}
    low = text.lower()
    out = []
    for c in win.descendants(**kw):
        try:
            if not c.is_visible():
                continue
            ei = c.element_info
            hay = " ".join([ei.name or "", ei.automation_id or "", _value(c)]).lower()
            if low and low not in hay:
                continue
            out.append(_describe(c, _new_ref(c)))
        except Exception:
            continue
        if len(out) >= limit:
            out.append(f"... (limit {limit})")
            break
    return "\n".join(out) or "Tapılmadı."


@mcp.tool()
@ui
def desk_click(target: str, control_type: str = "", window: str = "", double: bool = False,
               right: bool = False, confirm: bool = False) -> str:
    """Elementə klikləyir. target: ref (e12), automation id və ya ad. Save/Delete/Finalize/Activate kimi
    vəziyyəti dəyişən düymələr üçün confirm=True lazımdır (real məlumatda istifadəçinin razılığı ilə)."""
    c = _resolve(target, control_type, window)
    name = c.element_info.name or c.element_info.automation_id or target
    if GUARDED.search(name) and not confirm:
        return f"'{name}' vəziyyəti dəyişən düymədir - basmaq üçün confirm=True verin."
    _press(c, double=double, right=right)
    return f"Klik: {_describe(c, target)} | Üstdəki pəncərə: {_window().window_text()!r}"


@mcp.tool()
@ui
def desk_click_xy(x: int, y: int, window: str = "", double: bool = False, right: bool = False,
                  confirm: bool = False) -> str:
    """Real mausla koordinata klik - UIA ağacında görünməyən elementlər üçün (Telerik ağacı/cədvəl sətirləri,
    alt tablar, Properties sahələri, sağ klik menyusu). x, y: desk_screenshot şəklindəki piksel
    (həmin `window`-un sol-üst küncünə nisbətən). Nöqtədəki element Save/Delete kimi adlanırsa confirm=True lazımdır."""
    from pywinauto import mouse
    if window == "screen":                      # desk_screen şəklinin koordinatları
        win, sx, sy = None, x, y
    else:
        win = _window(window)
        r = win.rectangle()
        sx, sy = r.left + x, r.top + y
    hit = ""
    try:
        el = _desktop().from_point(sx, sy)
        hit = _describe(el, "-")
        name = el.element_info.name or ""
        if GUARDED.search(name) and not confirm:
            return f"Nöqtədə '{name}' var - vəziyyəti dəyişən düymədir, basmaq üçün confirm=True verin."
    except Exception:
        pass
    if win is not None:
        _front(win)
    if double:
        mouse.double_click(coords=(sx, sy))
    else:
        mouse.click(button="right" if right else "left", coords=(sx, sy))
    time.sleep(0.8)
    return f"Klik ({x},{y}) -> ekran ({sx},{sy}) | Nöqtədə: {hit or '?'} | Üstdəki pəncərə: {_window().window_text()!r}"


@mcp.tool()
@ui
def desk_screen(name: str = "screen"):
    """Bütün ekranın şəkli - proqram pəncərəsindən kənarda açılan popup/menyu/mesajlar üçün.
    Bu şəkildəki koordinatlarla klik: desk_click_xy(x, y, window='screen')."""
    import io
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(SHOT_DIR, re.sub(r"[^\w\-]+", "_", name) + ".png"))
    img.save(path)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return [path, Image(data=buf.getvalue(), format="png")]


@mcp.tool()
@ui
def desk_set_text(target: str, value: str, window: str = "", by_label: bool = False) -> str:
    """Sahəyə mətn yazır (köhnə dəyəri silir). target: ref, automation id, ad; by_label=True olsa
    target etiket mətnidir və onun yanındakı sahə tapılır."""
    c = _edit_for_label(target, window) if by_label else _resolve(target, "", window)
    if c is None:
        raise RuntimeError(f"'{target}' etiketinin yanında sahə tapılmadı.")
    try:
        if c.iface_value.CurrentIsReadOnly:
            return f"Sahə readonly-dir: {_describe(c, target)}"
    except Exception:
        pass
    c.set_focus()
    try:
        c.iface_value.SetValue(value)
    except Exception:
        c.type_keys("^a{BACKSPACE}" + value.replace("{", "{{}").replace("}", "{}}"), with_spaces=True)
    c.type_keys("{TAB}")
    time.sleep(0.4)
    return f"Yazıldı: {_describe(c, target)}"


@mcp.tool()
@ui
def desk_select(target: str, value: str, window: str = "", by_label: bool = False) -> str:
    """ComboBox/siyahıdan dəyər seçir (dəyərin bir hissəsi də olar)."""
    c = _edit_for_label(target, window) if by_label else _resolve(target, "", window)
    if c is None:
        raise RuntimeError(f"'{target}' tapılmadı.")
    low = value.lower()
    for item in c.descendants(control_type="DataItem") + c.descendants(control_type="ListItem"):
        if low in (item.element_info.name or "").lower() and _select_item(item):
            return f"Seçildi: {item.element_info.name!r}"
    try:
        c.select(value)
        return f"Seçildi: {_describe(c, target)}"
    except Exception:
        pass
    try:
        c.expand()
    except Exception:
        c.click_input()
    time.sleep(0.6)
    low = value.lower()
    items = c.descendants(control_type="ListItem") + c.descendants(control_type="DataItem")
    for w in _desktop().windows()[:3]:          # açılan siyahı ayrıca popup pəncərədə ola bilər
        try:
            items += w.descendants(control_type="ListItem")
        except Exception:
            pass
    for item in items:
        if low in (item.element_info.name or "").lower() or low in " ".join(item.texts()).lower():
            _press(item)
            return f"Seçildi: {item.element_info.name!r} -> {_describe(c, target)}"
    try:
        c.collapse()
    except Exception:
        pass
    raise RuntimeError(f"'{value}' variantı tapılmadı.")


@mcp.tool()
@ui
def desk_check(target: str, checked: bool = True, window: str = "") -> str:
    """CheckBox/RadioButton-u işarələyir və ya işarəni götürür."""
    c = _resolve(target, "", window)
    state = c.get_toggle_state() if hasattr(c, "get_toggle_state") else None
    if state is None or (state == 1) != checked:
        try:
            c.toggle()
        except Exception:
            c.click_input()
    time.sleep(0.4)
    return f"{_describe(c, target)}"


@mcp.tool()
@ui
def desk_read_grid(target: str = "", window: str = "", max_rows: int = 50) -> str:
    """Cədvəli (DataGrid/Table/List) başlıqları ilə oxuyur. target boş olsa pəncərədəki ilk cədvəl."""
    win = _window(window)
    if target:
        g = _resolve(target, "", window)
    else:
        grids = [c for t in ("DataGrid", "Table", "List") for c in win.descendants(control_type=t) if c.is_visible()]
        if not grids:
            return "Pəncərədə cədvəl tapılmadı."
        g = grids[0]
    headers = [h.element_info.name for h in g.descendants(control_type="HeaderItem") if h.element_info.name]
    rows = [r for r in g.children() if _ctype(r) in ("DataItem", "ListItem", "TreeItem", "Custom")]
    out = [" | ".join(headers)] if headers else []
    _refs.clear()
    for r in rows[:max_rows]:
        cells = []
        for cell in r.children():
            t = _value(cell) or cell.element_info.name or " ".join(x for x in cell.texts() if x)
            if not t:
                t = " ".join(x.element_info.name for x in cell.descendants(control_type="Text") if x.element_info.name)
            cells.append(t.strip())
        if not any(cells):
            cells = [r.element_info.name or ""]
        out.append(f"[{_new_ref(r)}] " + " | ".join(cells))
    out.append(f"({min(len(rows), max_rows)} / {len(rows)} sətir görünür)")
    return "\n".join(out)


@mcp.tool()
@ui
def desk_menu(path: str, window: str = "") -> str:
    """Menyu yolunu seçir, məsələn 'File->Open' və ya 'Tools->Options'."""
    win = _window(window)
    try:
        win.menu_select(path)
        return f"Menyu: {path}"
    except Exception:
        pass
    for part in [p.strip() for p in path.split("->")]:
        item = _resolve(part, "MenuItem", window)
        try:
            item.expand()
        except Exception:
            _press(item)
        time.sleep(0.5)
    return f"Menyu: {path}"


@mcp.tool()
@ui
def desk_keys(keys: str, window: str = "") -> str:
    """Pəncərəyə klaviatura göndərir (pywinauto sintaksisi): {ENTER}, {TAB}, {ESC}, ^s (Ctrl+S), %f (Alt+F)."""
    w = _window(window)
    _front(w)
    w.type_keys(keys, with_spaces=True, set_foreground=False)
    time.sleep(0.5)
    return f"Göndərildi: {keys}"


@mcp.tool()
@ui
def desk_read_text(window: str = "", limit: int = 300) -> str:
    """Pəncərədəki bütün görünən mətnlər (etiketlər, sahə dəyərləri, mesajlar), yuxarıdan aşağı."""
    win = _window(window)
    items = []
    for c in win.descendants():
        try:
            if not c.is_visible():
                continue
            t = _value(c) if _ctype(c) in ("Edit", "ComboBox") else c.element_info.name
            if t:
                r = c.rectangle()
                items.append((r.top, r.left, t.strip()))
        except Exception:
            continue
    items.sort()
    seen, out = set(), []
    for _, _, t in items:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return f"Pəncərə: {win.window_text()!r}\n" + "\n".join(out[:limit])


@mcp.tool()
@ui
def desk_wait(text: str, timeout: int = 30, window: str = "") -> str:
    """Mətn (pəncərə başlığı və ya element adı) görünənə qədər gözləyir."""
    low = text.lower()
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in _windows():
            if low in (w.window_text() or "").lower():
                return f"Pəncərə göründü: {w.window_text()!r}"
            if window and window.lower() not in (w.window_text() or "").lower():
                continue
            try:
                for c in w.descendants():
                    if low in (c.element_info.name or "").lower() and c.is_visible():
                        return f"Göründü: {_describe(c, '-')} ({w.window_text()!r})"
            except Exception:
                pass
        time.sleep(1)
    return f"{timeout} saniyə ərzində '{text}' görünmədi."


def _shot(name: str, window: str = "", scale: float = 1.0, region: str = ""):
    """Diskə HƏMİŞƏ tam ölçülü PNG yazır (sübut). Claude-a qaytarılan şəkil: region (x,y,w,h - pəncərə
    pikseli) kəsilir, sonra scale tətbiq olunur; scale<1 olsa JPEG (daha sürətli)."""
    import io
    win = _window(window)
    _front(win)
    img = win.capture_as_image()
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(SHOT_DIR, re.sub(r"[^\w\-]+", "_", name) + ".png"))
    img.save(path)
    view, note = img, ""
    if region:
        x, y, w, h = (int(v) for v in region.replace(" ", "").split(","))
        view = view.crop((x, y, x + w, y + h))
        note = f" (region {region}; koordinat = region başlanğıcı + şəkildəki piksel / scale)"
    if 0 < scale < 1:
        view = view.resize((max(1, int(view.width * scale)), max(1, int(view.height * scale))))
        note += f" (scale {scale}: klik üçün koordinatı {scale}-ə bölün)"
    buf = io.BytesIO()
    if 0 < scale < 1:
        view.convert("RGB").save(buf, format="JPEG", quality=85)
        return [path + note, Image(data=buf.getvalue(), format="jpeg")]
    view.save(buf, format="PNG")
    return [path + note, Image(data=buf.getvalue(), format="png")]


@mcp.tool()
@ui
def desk_screenshot(name: str, window: str = "", scale: float = 1.0, region: str = ""):
    """Pəncərənin şəklini çəkir, SV_DESKTOP_SHOT_DIR-ə tam ölçüdə yazır və Claude-a qaytarır.
    scale=0.5: aralıq yoxlamalar üçün kiçik JPEG (sürətli). region="x,y,w,h": yalnız o sahə (məs. menyu,
    Properties paneli) - nəticəni təyin edən yoxlamalarda tam ölçü (scale=1) və ya region istifadə edin."""
    return _shot(name, window, scale, region)


_BATCH_ACTIONS = ("click", "click_xy", "keys", "wait", "sleep", "menu", "set_text", "expect", "windows")


@mcp.tool()
@ui
def desk_batch(steps: list[dict], screenshot: str = "", shot_window: str = "", scale: float = 0.6,
               max_seconds: int = 40):
    """Bir çağırışda bir neçə addım icra edir (gediş-gəlişi azaldır). İLK XƏTADA DAYANIR və hansı addımda
    dayandığını yazır; sonda (və ya xətada) screenshot verilibsə şəkil qaytarır.
    steps nümunəsi:
      [{"do": "click_xy", "x": 118, "y": 202, "right": true},
       {"do": "keys", "keys": "{F4}"},
       {"do": "wait", "text": "Properties -", "timeout": 10},
       {"do": "expect", "text": "Sədərək"},            # pəncərə başlığı/element adı mövcud olmalıdır
       {"do": "click", "target": "Cancel", "window": "Basic"},
       {"do": "menu", "path": "View->Address Management->Projects"},
       {"do": "set_text", "target": "Newproject", "value": "X"},
       {"do": "sleep", "seconds": 1}, {"do": "windows"}]
    Hər addımda window verilə bilər. Save/Delete kimi düymələr üçün addımda "confirm": true lazımdır.
    Ümumi müddət max_seconds-u keçsə dayanır (MCP limiti ~60 s)."""
    t0 = time.time()
    log, failed = [], False
    for i, st in enumerate(steps, 1):
        do = (st.get("do") or "").lower()
        if time.time() - t0 > max_seconds:
            log.append(f"{i}. DAYANDI: {max_seconds} s limiti keçdi, qalan addımlar icra olunmadı.")
            failed = True
            break
        try:
            if do not in _BATCH_ACTIONS:
                raise RuntimeError(f"naməlum əməliyyat '{do}' ({', '.join(_BATCH_ACTIONS)})")
            w = st.get("window", "")
            if do == "click":
                r = _raw(desk_click)(st["target"], st.get("control_type", ""), w, st.get("double", False),
                                     st.get("right", False), st.get("confirm", False))
                if "confirm=True" in r:
                    raise RuntimeError(r)
            elif do == "click_xy":
                r = _raw(desk_click_xy)(int(st["x"]), int(st["y"]), w, st.get("double", False),
                                        st.get("right", False), st.get("confirm", False))
                if "confirm=True" in r:
                    raise RuntimeError(r)
            elif do == "keys":
                r = _raw(desk_keys)(st["keys"], w)
            elif do == "wait":
                r = _raw(desk_wait)(st["text"], int(st.get("timeout", 10)), w)
                if "görünmədi" in r:
                    raise RuntimeError(r)
            elif do == "expect":
                r = _raw(desk_wait)(st["text"], int(st.get("timeout", 2)), w)
                if "görünmədi" in r:
                    raise RuntimeError(f"gözlənilən '{st['text']}' yoxdur")
            elif do == "sleep":
                time.sleep(float(st.get("seconds", 1)))
                r = "ok"
            elif do == "menu":
                r = _raw(desk_menu)(st["path"], w)
            elif do == "set_text":
                r = _raw(desk_set_text)(st["target"], st["value"], w, st.get("by_label", False))
                if "readonly" in r:
                    raise RuntimeError(r)
            else:
                r = _raw(desk_windows)()
            log.append(f"{i}. {do}: {r}")
        except Exception as e:                               # noqa: BLE001
            log.append(f"{i}. {do}: XƏTA - {e}. Qalan {len(steps) - i} addım icra olunmadı.")
            failed = True
            break
    head = ("DAYANDI" if failed else "OK") + f" ({time.time() - t0:.1f} s)\n" + "\n".join(log)
    if screenshot or failed:
        try:
            return [head] + _shot(screenshot or "batch_error", shot_window or "", scale)
        except Exception as e:                               # noqa: BLE001
            return head + f"\n(screenshot alınmadı: {e})"
    return head


def _raw(tool):
    """@ui-sız funksiya (batch eyni UIA thread-də işləyir, yenidən növbəyə qoymaq deadlock olardı)."""
    return getattr(tool, "__wrapped__", tool)


@mcp.tool()
@ui
def desk_close(force: bool = False) -> str:
    """Proqramı bağlayır. force=True prosesi məcburi dayandırır (yadda saxlanmamış dəyişikliklər itir)."""
    import psutil
    pids = _pids()
    if not pids:
        return "Proqram açıq deyil."
    if force:
        for pid in pids:
            psutil.Process(pid).kill()
        return "Proses dayandırıldı."
    for w in _windows():
        try:
            w.close()
        except Exception:
            pass
    time.sleep(2)
    return "Bağlandı." if not _pids() else "Pəncərə bağlanmadı (dialoq açıq ola bilər) - desk_windows ilə baxın."


# ======================================================================
# TOOL MERGE (token qənaəti): oxşar alətlər `action`/`mode` parametrli tək alətdə birləşdirildi.
# Köhnə desk_* funksiyaları (@ui sarğısı ilə) modul qlobalında OLDUĞU KİMİ qalır (desk_batch onlara birbaşa
# müraciət edir). Yalnız MCP qeydiyyatından çıxarılır. Birləşmiş alətlər @ui ilə SARILMIR: içindəki köhnə
# funksiya özü UIA thread növbəsini idarə edir.
# ======================================================================
def _first_line(fn):
    d = (getattr(fn, "__doc__", "") or "").strip().splitlines()
    return d[0].strip()[:170] if d else ""


def _mk_doc(header, parts):
    return header + "\n" + "\n".join(f"- {k}: {_first_line(f)}" for k, f in parts.items())


def _bad_mode(name, value, allowed):
    return f"XƏTA {name}: naməlum '{value}'. İcazəli: {', '.join(allowed)}"


def _m_desk_session(action: str, login: bool = True, mode: str = "", project: str = "", app: str = "",
                    profile: str = "", wait: int = 40, force: bool = False) -> str:
    a = (action or "").strip().lower()
    if a == "start":
        return desk_start(login, mode, project, app, profile)
    if a == "status":
        return desk_start_status(wait)
    if a == "login":
        return desk_login()
    if a == "close":
        return desk_close(force)
    return _bad_mode("desk_session.action", action, ["start", "status", "login", "close"])


_m_desk_session.__doc__ = (
    _mk_doc("Proqram sessiyası. action: start (login, mode, project, app, profile) | status (wait) | login | close (force).",
            {"status": desk_start_status, "login": desk_login, "close": desk_close})
    + "\n\nstart (desk_start) təfərrüatı:\n" + (desk_start.__doc__ or "").strip())


def _m_desk_look(mode: str = "windows", window: str = "", text: str = "", control_type: str = "",
                 limit: int = 40, max_depth: int = 6, only_interactive: bool = False, raw: bool = False) -> str:
    a = (mode or "").strip().lower()
    if a == "windows":
        return desk_windows()
    if a == "tree":
        return desk_tree(window, max_depth, only_interactive, raw)
    if a == "find":
        return desk_find(text, control_type, window, limit)
    return _bad_mode("desk_look.mode", mode, ["windows", "tree", "find"])


_m_desk_look.__doc__ = _mk_doc(
    "Pəncərə/element axtarışı. mode: windows | tree (window, max_depth, only_interactive, raw) | find (text, control_type, limit).",
    {"windows": desk_windows, "tree": desk_tree, "find": desk_find})


def _m_desk_act(action: str, target: str = "", value: str = "", window: str = "", control_type: str = "",
                double: bool = False, right: bool = False, confirm: bool = False, by_label: bool = False,
                checked: bool = True, x: int = 0, y: int = 0, path: str = "", keys: str = "") -> str:
    a = (action or "").strip().lower()
    if a == "click":
        return desk_click(target, control_type, window, double, right, confirm)
    if a == "click_xy":
        return desk_click_xy(x, y, window, double, right, confirm)
    if a == "set_text":
        return desk_set_text(target, value, window, by_label)
    if a == "select":
        return desk_select(target, value, window, by_label)
    if a == "check":
        return desk_check(target, checked, window)
    if a == "menu":
        return desk_menu(path, window)
    if a == "keys":
        return desk_keys(keys, window)
    return _bad_mode("desk_act.action", action, ["click", "click_xy", "set_text", "select", "check", "menu", "keys"])


_m_desk_act.__doc__ = _mk_doc(
    "UI əməliyyatı. action: click (target, control_type, double, right, confirm) | click_xy (x, y) | "
    "set_text (target, value, by_label) | select (target, value) | check (target, checked) | menu (path) | keys (keys).",
    {"click": desk_click, "click_xy": desk_click_xy, "set_text": desk_set_text, "select": desk_select,
     "check": desk_check, "menu": desk_menu, "keys": desk_keys})


def _m_desk_read(mode: str = "text", target: str = "", window: str = "", max_rows: int = 50,
                 limit: int = 300) -> str:
    a = (mode or "").strip().lower()
    if a == "text":
        return desk_read_text(window, limit)
    if a == "grid":
        return desk_read_grid(target, window, max_rows)
    return _bad_mode("desk_read.mode", mode, ["text", "grid"])


_m_desk_read.__doc__ = _mk_doc(
    "Ekrandan oxuma. mode: text (window, limit) | grid (target, window, max_rows).",
    {"text": desk_read_text, "grid": desk_read_grid})


def _m_desk_shot(name: str = "screen", window: str = "", scale: float = 1.0, region: str = "",
                 full_screen: bool = False):
    if full_screen:
        return desk_screen(name)
    return desk_screenshot(name, window, scale, region)


_m_desk_shot.__doc__ = ("Screenshot. full_screen=True bütün ekran (yalnız name); əks halda pəncərə/region "
                        "(window, scale, region).\n" + _first_line(desk_screenshot))


_MERGED_AWAY = [
    "desk_start", "desk_start_status", "desk_login", "desk_close",
    "desk_windows", "desk_tree", "desk_find",
    "desk_click", "desk_click_xy", "desk_set_text", "desk_select", "desk_check", "desk_menu", "desk_keys",
    "desk_read_grid", "desk_read_text",
    "desk_screen", "desk_screenshot",
]
for _n in _MERGED_AWAY:
    try:
        mcp.remove_tool(_n)
    except Exception:  # noqa: BLE001
        pass

mcp.add_tool(_m_desk_session, name="desk_session")
mcp.add_tool(_m_desk_look, name="desk_look")
mcp.add_tool(_m_desk_act, name="desk_act")
mcp.add_tool(_m_desk_read, name="desk_read")
mcp.add_tool(_m_desk_shot, name="desk_shot")


if __name__ == "__main__":
    mcp.run()
