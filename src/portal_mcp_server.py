"""portal-ui MCP server (Playwright).

Əvvəlki alətlər: open_portal, login, close_portal, screenshot, current_state, read_text,
inspect_page, click, click_text, select_option, choose_profile, click_search,
search_customer, fill, fill_by_row, fill_nth, type_text, press_key

YENİ (vizual və xəta yoxlaması üçün):
  - screenshot      : indi şəkli Claude-a IMAGE kimi qaytarır (həm də diskə yazır)
  - get_events      : brauzer dialoqları (alert/confirm), console error, JS exception,
                      uğursuz/4xx/5xx şəbəkə sorğuları, yeni açılan pəncərələr
  - check_layout    : boş/1 variantlı select, kəsilmiş mətn, üst-üstə düşən və ekrandan
                      çıxan elementlər, sınıq şəkillər, üfüqi scroll
  - switch_page     : yeni açılan pəncərəyə keç (məs. 'Show process details')
                      və ya əvvəlki pəncərəyə qayıt
  - make_test_files : test.pdf, test.jpg, large_<N>mb.pdf, test.exe yaradır
  - list_file_inputs: səhifədəki input[type=file] elementləri
  - upload_file     : faylı OS fayl pəncərəsi açılmadan yükləyir (set_input_files / file_chooser)

YENİ (SunVizion Portal, eyni brauzer və eyni login ilə):
  - open_sunvizion  : Portal-ı açır (lazım olsa login olur). page_name ilə hazır səhifələr
                      (tasks, processes, service_catalog ...) və ya istənilən path
  - sv_grid_read    : Portal cədvəlini (grid/treegrid) başlıqları ilə oxuyur, istəsə bütün səhifələri
  - sv_open_row     : cədvəldə mətnə görə sətri açır (sağdakı preview / tapşırıq forması)
  - sv_select_row   : sətrin checkbox-unu seçir (Accept/Finalize kimi əməliyyatlardan əvvəl)
  - sv_toolbar      : toolbar düyməsi; vəziyyəti dəyişən düymələr yalnız confirm=True ilə
  - sv_fields       : açıq formadakı sahələr: label | dəyər | editable/readonly/disabled | məcburi
  - sv_choose       : Angular/Kendo dropdown-dan seçim (native <select> olmayan)
  - sv_process_tasks: proses identifikatoruna (70000_870927) görə tapşırıqları açıb oxuyur

YENİ (API testing, YALNIZ PREPROD - PROD host-una sorğu getmir):
  - api_capture     : Portal-ın XHR/fetch sorğularını qeydə alır (start/stop/status)
  - api_log         : qeydə alınan sorğular; index ilə tam request/response
  - api_endpoints   : endpoint kataloqu (+ başlanğıc test case-ləri JSON faylına)
  - api_call        : login olunmuş sessiya ilə birbaşa sorğu (risky endpoint-lər confirm=True ilə)
  - api_run_tests   : JSON test faylı -> Pass/Fail, nəticə CSV (API_DIR)

YENİ (27.10.2026, daha etibarlı kliklər + recipe):
  - click / click_text / select_option / sv_choose: yalnız GÖRÜNƏN elementlər sayılır (Kendo-da gizli
    tablar DOM-da qalır); label basılmırsa avtomatik əsl sahə (label[for] / Kendo combobox), sonra force klik.
    select_option native <select> deyilsə Kendo dropdown kimi açır. İşləyən fallback cavabda göstərilir.
  - Hər alət çağırışı artifacts/recipes/_last_session.json-a yazılır.
  - recipe_start / recipe_save / recipe_list / recipe_show / recipe_run: test case addımlarını
    saxlayıb gələn dəfə göstərmək və ya təkrar işlətmək (confirm=True addımları default atlanır).

YENİ (29.10.2026, öyrənən element yaddaşı, LLM token-i sərf etmir):
  - Element alətlərinin vaxtı, xətaları, işləyən klik üsulu və frame-i src/state/element_memory.json-a yazılır.
  - click/click_text/fill/fill_by_row/type_text/select_option: element verilən frame-də yoxdursa öyrənilmiş
    frame, sonra digər frame-lər yoxlanır; recipe-yə işləyən frame yazılır.
  - _smart_click keçən dəfə işləyən üsulu (force / əsl idarə elementi) birbaşa sınayır.
  - learn_report: yavaş / alınmayan / fallback-ə düşən elementlər (forget=True ilə səhv qeydi silmək).

DƏYİŞİKLİKLƏR:
  - PORTAL_USER/PORTAL_PASS artıq koda yazılmayıb, yalnız mühit dəyişəni ilə verilir.
  - Brauzer dialoqları (alert/confirm) indi qeydə alınır və PORTAL_DIALOG mühit
    dəyişəninə görə qəbul edilir ("accept", default) və ya rədd edilir ("dismiss").
    Əvvəl handler yox idi, Playwright dialoqları avtomatik rədd edirdi (confirm = Cancel).
"""
import functools
import inspect
import os
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Union

from mcp.server.fastmcp import FastMCP, Image
from playwright.sync_api import sync_playwright


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

PORTAL_URL = os.getenv(
    "PORTAL_URL",
    "https://ppwebportal.aztelekom.az/20_0/BSS.ContactCenter/Default.aspx",
)
SHOT_DIR = os.getenv("PORTAL_SCREENSHOT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "shots")
TIMEOUT = 15000

# Avtomatik login: secrets.env faylından və ya mühit dəyişənindən (koda yazmayın):
PORTAL_USER = os.getenv("PORTAL_USER", "")
PORTAL_PASS = os.getenv("PORTAL_PASS", "")

# Brauzer dialoqlarına (alert/confirm/prompt) cavab: "accept" və ya "dismiss"
DIALOG_ACTION = os.getenv("PORTAL_DIALOG", "accept").lower()

# Mühitlər: open_portal(env=...) / open_sunvizion(env=...) ilə seçilir. Default PREPROD (PORTAL_URL/SUNVIZION_URL).
ENVS = {
    "PREPROD": {"crm": os.getenv("PORTAL_URL", "https://ppwebportal.aztelekom.az/20_0/BSS.ContactCenter/Default.aspx"),
                "sv": os.getenv("SUNVIZION_URL", "https://ppwebportal.aztelekom.az/20_0/Portal")},
    "PROD": {"crm": os.getenv("PROD_PORTAL_URL", "https://azt-bss.aztelekom.az/20_0/BSS.ContactCenter/LoginForm.aspx"),
             "sv": os.getenv("PROD_SUNVIZION_URL", "https://azt-bss.aztelekom.az/20_0/Portal")},
}
PROD_HOST = "azt-bss.aztelekom.az"
# prod_mode: "readonly" (yalnız baxış/naviqasiya), "verify" (baxış + Accept + forma oxuma,
# amma data dəyişən düymələr bloklu). set_prod_mode ilə dəyişilir.
_env = {"name": "PREPROD", "prod_mode": "full"}

# PROD-da YALNIZ OXUMA: bu adlara klik etmək olar (sorğu/proses nömrəsi, menyu, axtarış, baxış düymələri).
PROD_CLICK_OK = re.compile(
    r"^\s*(TR\d{5,}|\d{4,}|\d+_\d+|Find Customer|Find Subscriber|Customers|Subscribers|Customer home page|"
    r"Subscriber starting page|Customer data|Customer list|Active processes|Processes finished/ ?cancelled|"
    r"Customer support processes|Search|Show.*|show|Close|Accept|Edit|"
    r"Customer service accounts|Show accounts|Customer contracts|Show contracts|My Tasks|Tasks|Processes|"
    r"Refresh|Filter Row|Load All|Expand All|Details|Additional Info|General|Technical Issue|"
    r"Go to the (next|previous|first|last) page|previous page|next page|first page|last page)\s*$")
# PROD-da verify rejimində belə BLOKLANAN (data dəyişən) düymə/əməliyyat adları.
PROD_WRITE_WORDS = re.compile(
    r"(Finalize|Finish|Save|Create|Cancel|Delete|Remove|Submit|Activate|Reject|Approve|Start Process|"
    r"Accept and Finalize|Change Status)", re.IGNORECASE)
# PROD-da yalnız AXTARIŞ sahələrinə yazmaq olar (məlumatı dəyişmir): SunVizion proses/task axtarışı, CRM 'Enter identifier'.
PROD_SEARCH_LABELS = {"Name", "Process ID", "Starting Context", "Matching Context", "Collaboration ID",
                      "Reference Process", "Enter identifier:", "Enter identifier"}


def _prod_search_ok(label: str) -> bool:
    url = (_s["page"].url if _s["page"] else "") or ""
    if label in ("Enter identifier:", "Enter identifier"):
        return True
    return label in PROD_SEARCH_LABELS and "/Portal/app/wfl/" in url
PROD_KEYS_OK = {"Escape", "Tab", "Shift+Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight",
                "PageDown", "PageUp", "Home", "End"}


def _is_prod() -> bool:
    if _env["name"] == "PROD":
        return True
    try:
        return any(PROD_HOST in (p.url or "") for p in (_s["ctx"].pages if _s["ctx"] else []))
    except Exception:  # noqa: BLE001
        return False


def _prod_refuse(what: str):
    raise RuntimeError(f"PROD yalnız oxuma rejimindədir: '{what}' icazəli deyil (heç nə dəyişdirilmədi).")


def prod_readonly(kind: str):
    """PROD-da yazma əməliyyatlarını bloklayır. kind: 'write' (həmişə blok), 'click' (yalnız PROD_CLICK_OK),
    'key' (yalnız PROD_KEYS_OK)."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if _is_prod():
                b = sig.bind(*args, **kwargs)
                b.apply_defaults()
                a = b.arguments
                mode = _env.get("prod_mode", "verify")
                if mode == "full":
                    return fn(*args, **kwargs)   # PROD tam icazə (orkestrator təsdiqi ilə)
                verify = mode == "verify"
                if kind == "search":   # fill: axtarış sahələri (verify rejimində hər fill icazəli)
                    if not (verify or _prod_search_ok(a.get("label") or "")):
                        _prod_refuse(f"{fn.__name__}('{a.get('label')}')")
                if kind == "write":
                    # verify rejimi: yalnız data dəyişən adlar bloklanır; baxış/forma əməliyyatları keçir.
                    name = str(a.get("name") or a.get("text") or a.get("button") or a.get("value") or "")
                    if not verify or PROD_WRITE_WORDS.search(name):
                        _prod_refuse(f"{fn.__name__}('{name}')" if name else fn.__name__)
                if kind == "click":
                    name = a.get("name") or a.get("text") or ""
                    if PROD_WRITE_WORDS.search(name):
                        _prod_refuse(f"{fn.__name__}('{name}') [data dəyişən əməliyyat]")
                    if not (verify or PROD_CLICK_OK.match(name)):
                        _prod_refuse(f"{fn.__name__}('{name}')")
                if kind == "key" and a.get("key") not in PROD_KEYS_OK:
                    url = (_s["page"].url if _s["page"] else "") or ""
                    if not (verify or (a.get("key") == "Enter" and "/Portal/app/wfl/" in url)):
                        _prod_refuse(f"press_key('{a.get('key')}')")
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _creds():
    if _env["name"] == "PROD":
        return (os.getenv("PROD_PORTAL_USER") or PORTAL_USER, os.getenv("PROD_PORTAL_PASS") or PORTAL_PASS)
    return PORTAL_USER, PORTAL_PASS


def _set_env(env: str) -> str:
    """Mühiti dəyişir. Brauzer başqa mühitdə açıqdırsa bağlanır (sessiyalar qarışmasın)."""
    global PORTAL_URL, SV_URL
    env = (env or "").strip().upper() or _env["name"]
    if env not in ENVS:
        raise RuntimeError(f"env: {', '.join(ENVS)}")
    msg = ""
    if env != _env["name"] and _s["browser"]:
        try:
            _s["browser"].close()
            _s["pw"].stop()
        finally:
            for k in _s:
                _s[k] = None
        msg = f"{_env['name']} brauzeri bağlandı. "
    _env["name"] = env
    PORTAL_URL, SV_URL = ENVS[env]["crm"], ENVS[env]["sv"].rstrip("/")
    return msg + (f"Mühit: {env}" + (" (YALNIZ OXUMA)" if env == "PROD" else ""))

mcp = FastMCP("portal-ui")

# Playwright sync API asyncio dövrəsində işləmir və yalnız yarandığı thread-dən
# istifadə oluna bilər. Ona görə bütün alətlər TƏK ayrıca thread-də icra olunur.
_pool = ThreadPoolExecutor(max_workers=1)


def pw(fn):
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        def run():
            _s["used"] = ""
            t0 = time.time()
            try:
                res = fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                _rec_step(fn.__name__, sig, args, kwargs, False, str(e), t0)
                raise
            _rec_step(fn.__name__, sig, args, kwargs, True, res if isinstance(res, str) else "", t0)
            if _s.get("used") and isinstance(res, str):
                res += f" [fallback: {_s['used']}]"
            return res
        return _pool.submit(run).result()
    return wrapper


_s = {"pw": None, "browser": None, "ctx": None, "page": None, "used": ""}

# ---------- addım jurnalı (recipe) ----------
# Hər alət çağırışı yaddaşa və artifacts/recipes/_last_session.json-a yazılır (bir neçə KB).
# recipe_save(name) uğurlu addımları <name>.json kimi saxlayır; recipe_show / recipe_run gələn dəfə istifadə edir.
RECIPE_DIR = os.getenv("PORTAL_RECIPE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "recipes")
_NO_REC = {"recipe_start", "recipe_save", "recipe_show", "recipe_list", "recipe_run",
           "get_events", "current_state", "inspect_page", "api_log", "api_endpoints"}
_steps = []


def _rec_step(name, sig, args, kwargs, ok, result, t0):
    if name in _NO_REC:
        return
    try:
        b = sig.bind(*args, **kwargs)
        b.apply_defaults()
        params = {k: v for k, v in b.arguments.items() if v not in ("", None) and not (
            isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)}
    except Exception:  # noqa: BLE001
        params = dict(kwargs)
    fu = _s.pop("frame_used", None)
    if fu is not None and "frame" in sig.parameters:
        params["frame"] = fu          # recipe gələn dəfə birbaşa işləyən frame-dən başlasın
        if fu == 0:
            params.pop("frame", None)
    ms = int((time.time() - t0) * 1000)
    _mem_record(name, params, ok, ms, result)
    _steps.append({"tool": name, "args": params, "ok": ok, "used": _s.get("used", ""),
                   "result": (result or "")[:200], "ms": ms,
                   "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    del _steps[:-300]
    try:
        import json as _j
        os.makedirs(RECIPE_DIR, exist_ok=True)
        with open(os.path.join(RECIPE_DIR, "_last_session.json"), "w", encoding="utf-8") as fh:
            _j.dump(_steps, fh, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


# ---------- öyrənən element yaddaşı (29.10.2026) ----------
# Hər element alətinin (click, fill, sv_choose ...) nəticəsi src/state/element_memory.json-a yazılır:
# neçə dəfə çağırılıb, neçə dəfə alınmayıb, orta/maks ms, hansı klik üsulu işləyib, hansı frame-də tapılıb.
# Gələn dəfə: (1) element verilən frame-də yoxdursa əvvəl öyrənilmiş frame, sonra qalan frame-lər yoxlanır;
# (2) _smart_click keçən dəfə işləyən üsulu (force / əsl idarə elementi) BİRBAŞA sınayır, 3 s-lik
# uğursuz cəhdləri gözləmir. LLM token-i sərf olunmur. Hesabat: learn_report.
MEM_PATH = os.getenv("PORTAL_MEMORY_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "state", "element_memory.json")
SLOW_MS = int(os.getenv("PORTAL_SLOW_MS", "3000"))
_LEARN_TOOLS = {"click", "click_text", "fill", "fill_by_row", "type_text", "select_option", "sv_choose",
                "sv_toolbar", "sv_open_row", "sv_select_row", "click_search", "search_customer", "upload_file"}
_mem = {"data": None}


def _mem_data() -> dict:
    if _mem["data"] is None:
        try:
            import json as _j
            with open(MEM_PATH, encoding="utf-8") as fh:
                _mem["data"] = _j.load(fh)
        except Exception:  # noqa: BLE001
            _mem["data"] = {}
    return _mem["data"]


def _mem_save():
    try:
        import json as _j
        d = _mem_data()
        if len(d) > 600:   # ən köhnələri at
            for k in sorted(d, key=lambda k: d[k].get("last", ""))[:len(d) - 600]:
                d.pop(k, None)
        os.makedirs(os.path.dirname(MEM_PATH), exist_ok=True)
        tmp = MEM_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _j.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, MEM_PATH)
    except Exception:  # noqa: BLE001
        pass


def _mem_key(tool: str, a: dict) -> str:
    target = next((str(a[k]) for k in ("name", "text", "label", "row_text", "button") if a.get(k)), "")
    role = a.get("role") or ("button" if tool == "click" else "")
    nth = a.get("nth") or a.get("index") or 0
    return f"{tool}|{target}|{role}|{nth}"


def _url_key() -> str:
    try:
        from urllib.parse import urlparse as _up
        u = _up(_s["page"].url if _s["page"] else "")
        return re.sub(r"\d{3,}", "#", (u.path or "") + (("#" + u.fragment.split("?")[0]) if u.fragment else ""))
    except Exception:  # noqa: BLE001
        return ""


def _mem_get(key: str) -> dict:
    return _mem_data().get(key or "", {}) if key else {}


def _mem_record(tool, params, ok, ms, result):
    if tool not in _LEARN_TOOLS:
        return
    try:
        d = _mem_data()
        key = _mem_key(tool, params)
        e = d.setdefault(key, {"n": 0, "fail": 0, "avg_ms": 0, "max_ms": 0})
        e["n"] += 1
        e["avg_ms"] = int((e["avg_ms"] * (e["n"] - 1) + ms) / e["n"])
        e["max_ms"] = max(e["max_ms"], ms)
        e["last_ms"] = ms
        e["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if ok:
            if _s.get("method"):
                e["method"] = _s["method"]
            if _s.get("used"):
                e["used"] = _s["used"][:120]
        else:
            e["fail"] += 1
            e["last_err"] = (str(result).splitlines() or [""])[0][:200]
        _s["method"] = ""
        _mem_save()
    except Exception:  # noqa: BLE001
        pass


def _not_found(e: Exception) -> bool:
    s = str(e)
    return "tapılmadı" in s or "yoxdur (cəmi" in s


def learned(fn):
    """frame parametri olan alətlər üçün: element verilən frame-də TAPILMAYANDA öyrənilmiş frame, sonra
    digər frame-lər yoxlanır. 'Tapılmadı' xətaları sürətlidir (count()==0), ona görə əvvəlcə verilən frame
    sınanır (köhnə davranış dəyişmir). Timeout və digər xətalarda təkrar cəhd YOXDUR."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        b = sig.bind(*args, **kwargs)
        b.apply_defaults()
        a = dict(b.arguments)
        key = _mem_key(fn.__name__, a)
        url = _url_key()
        given = a.get("frame", 0)
        hint = (_mem_get(key).get("frames") or {}).get(url)
        _s["learn_key"] = key
        _s["frame_used"] = None
        try:
            try:
                n = len(_page().frames)
            except Exception:  # noqa: BLE001
                n = 1
            order = [given] + ([hint] if isinstance(hint, int) else []) + list(range(n))
            tried, last = set(), None
            for fr in order:
                if fr in tried or fr < 0 or fr >= max(n, given + 1):
                    continue
                tried.add(fr)
                a["frame"] = fr
                if fr != given:
                    _s["used"] = ""
                try:
                    res = fn(**a)
                except Exception as e:  # noqa: BLE001
                    if not _not_found(e):
                        raise
                    last = e
                    continue
                if fr != given:
                    _s["frame_used"] = fr
                    _s["used"] = ((_s["used"] + "; ") if _s.get("used") else "") + \
                        f"frame {given}-də yox idi -> frame={fr}" + (" (öyrənilmiş)" if fr == hint else "")
                    e = _mem_data().setdefault(key, {"n": 0, "fail": 0, "avg_ms": 0, "max_ms": 0})
                    e.setdefault("frames", {})[url] = fr
                return res
            raise last if last else RuntimeError(f"{fn.__name__}: element tapılmadı")
        finally:
            _s["learn_key"] = ""
    return wrapper

# Hadisə jurnalı: (vaxt, növ, mətn)
_events = deque(maxlen=300)


# ---------- hadisələrin tutulması ----------
def _ev(kind: str, text: str):
    _events.append((time.strftime("%H:%M:%S"), kind, text[:500]))


def _on_dialog(dialog):
    _ev("dialog", f"{dialog.type}: {dialog.message}")
    try:
        if DIALOG_ACTION == "dismiss":
            dialog.dismiss()
        else:
            dialog.accept()
    except Exception as e:  # noqa: BLE001
        _ev("dialog-error", str(e))


def _on_console(msg):
    if msg.type in ("error", "warning"):
        _ev(f"console.{msg.type}", msg.text)


def _on_response(resp):
    if resp.status >= 400:
        _ev(f"http {resp.status}", f"{resp.request.method} {resp.url}")
    _api_record(resp)


def _on_request_failed(req):
    failure = req.failure or ""
    # ASP.NET postback-lərində naviqasiya zamanı ERR_ABORTED normaldır, səs-küy salmasın
    if "ERR_ABORTED" in failure:
        return
    _ev("requestfailed", f"{req.method} {req.url} {failure}")


def _attach(page):
    page.set_default_timeout(TIMEOUT)
    page.on("dialog", _on_dialog)
    page.on("console", _on_console)
    page.on("pageerror", lambda e: _ev("pageerror", str(e)))
    page.on("response", _on_response)
    page.on("requestfailed", _on_request_failed)


def _on_new_page(page):
    _attach(page)
    _ev("new_page", f"yeni pəncərə açıldı (indeks {len(_s['ctx'].pages) - 1}): {page.url}")


# ---------- köməkçi funksiyalar ----------
def _page():
    if not _s["page"]:
        raise RuntimeError("Brauzer açıq deyil. Əvvəl open_portal çağırın.")
    return _s["page"]


def _frame(i: int = 0):
    frames = _page().frames
    if i < 0 or i >= len(frames):
        raise RuntimeError(f"Frame {i} yoxdur (cəmi {len(frames)} frame var).")
    return frames[i]


def _body(f):
    # bəzi frame-lərdə 2 body var -> strict mode xətası olmasın
    return f.locator("body").first


QUICK = 3000  # fallback cəhdləri üçün qısa timeout (15 s gözləməsin)


def _visible(loc):
    """Locator-un yalnız görünən elementləri (siyahı). Kendo-da gizli tablar DOM-da qalır."""
    out = []
    try:
        for i in range(min(loc.count(), 50)):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    out.append(el)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def _control_for(el):
    """Label/mətn elementi üçün əsl idarə elementi: label[for] -> input, Kendo wrapper
    (multiselect/combobox/dropdownlist) -> görünən role=combobox və ya wrapper-in özü."""
    try:
        fid = el.get_attribute("for")
    except Exception:  # noqa: BLE001
        fid = None
    page_or_frame = el.page
    if fid:
        tgt = page_or_frame.locator(f"[id='{fid}']")
        if tgt.count():
            wrap = tgt.first.locator(
                "xpath=ancestor-or-self::*[self::kendo-multiselect or self::kendo-combobox or "
                "self::kendo-dropdownlist or self::kendo-datepicker or self::kendo-datetimepicker][1]")
            if wrap.count() and wrap.first.is_visible():
                return wrap.first, "label[for] -> kendo wrapper"
            if tgt.first.is_visible():
                return tgt.first, "label[for] -> input"
    wrap = el.locator("xpath=ancestor::*[self::kendo-multiselect or self::kendo-combobox or "
                      "self::kendo-dropdownlist or self::kendo-floatinglabel or contains(@class,'k-floating-label-container')][1]")
    if wrap.count():
        inner = _visible(wrap.first.locator("[role=combobox], input, .k-input-inner"))
        if inner:
            return inner[0], "ancestor kendo -> combobox"
        if wrap.first.is_visible():
            return wrap.first, "ancestor kendo wrapper"
    return None, ""


def _smart_click(loc, nth: int, what: str):
    """Görünən elementlər arasında nth-i basır. Alınmasa: əsl idarə elementi (label->input/combobox),
    sonra force klik. İşləyən yol _s['used']-ə yazılır (recipe-də görünür)."""
    vis = _visible(loc)
    total = loc.count()
    if total == 0:
        raise RuntimeError(f"{what} tapılmadı.")
    cands = vis if vis else [loc.nth(i) for i in range(min(total, 50))]
    if not vis:
        _s["used"] = "görünən element yox idi -> gizli elementlər yoxlandı"
    el = cands[nth] if nth < len(cands) else cands[-1]
    # öyrənilmiş üsul: keçən dəfə adi klik alınmayıbsa (3 s itirilib), işləyən üsulu birbaşa sına
    learned_m = _mem_get(_s.get("learn_key", "")).get("method", "")
    if learned_m == "control":
        ctl, how = _control_for(el)
        if ctl is not None:
            try:
                ctl.click(timeout=QUICK)
                _s["used"], _s["method"] = f"{how} (öyrənilmiş, birbaşa)", "control"
                return len(cands)
            except Exception:  # noqa: BLE001
                pass
    elif learned_m == "force":
        try:
            el.click(timeout=QUICK, force=True)
            _s["used"], _s["method"] = "force click (öyrənilmiş, birbaşa)", "force"
            return len(cands)
        except Exception:  # noqa: BLE001
            pass
    try:
        el.click(timeout=QUICK)
        _s["method"] = "normal"
        if vis and len(vis) != total:
            _s["used"] = f"yalnız görünən elementlər ({len(vis)}/{total})"
        return len(cands)
    except Exception as e1:  # noqa: BLE001
        for c in ([el] + [x for x in cands if x is not el]):
            ctl, how = _control_for(c)
            if ctl is not None:
                try:
                    ctl.click(timeout=QUICK)
                    _s["used"], _s["method"] = how, ("control" if c is el else "")
                    return len(cands)
                except Exception:  # noqa: BLE001
                    continue
        try:
            el.click(timeout=QUICK, force=True)
            _s["used"], _s["method"] = "force click", "force"
            return len(cands)
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"{what}: klik alınmadı ({str(e1).splitlines()[0]})") from e1


def _row_textbox(f, row_text: str, index: int = 0):
    """row_text olan, içində textbox olan ən dərin <tr>-in index-ci textbox-u."""
    rows = (
        f.locator("tr")
        .filter(has_text=row_text)
        .filter(has=f.get_by_role("textbox"))
    )
    if rows.count() == 0:
        raise RuntimeError(f"'{row_text}' mətnli, textbox-u olan sətir tapılmadı.")
    return rows.last.get_by_role("textbox").nth(index)


def _relogin_if_logged_out(page) -> str:
    """Sessiya bitibsə ('Logout from service' səhifəsi) 'Log in again' düyməsinə basır."""
    btn = page.get_by_role("button", name=re.compile(r"log ?in again", re.I))
    if btn.count() > 0:
        btn.first.click()
        page.wait_for_load_state("load")
        return "Sessiya bitmişdi, 'Log in again' basıldı."
    return ""


def _auto_login(page) -> str:
    """Login səhifəsində istifadəçi/parolu doldurub daxil olur."""
    user, pwd = _creds()
    if not (user and pwd):
        return "PORTAL_USER/PORTAL_PASS verilməyib, login əl ilə edilməlidir."
    pw_box = page.locator("input[type=password]:visible").first
    try:
        pw_box.wait_for(timeout=5000)
    except Exception:  # noqa: BLE001
        return "Parol sahəsi yoxdur (artıq login olunub ola bilər)."
    user_box = page.locator(
        "input[type=text]:visible, input[type=email]:visible, input:not([type]):visible"
    ).first
    user_box.fill(user)
    pw_box.fill(pwd)
    # Əvvəl dəqiq ad: SunVizion-da "Forgot Login?" düyməsi "Login"-dən əvvəl gəlir
    btn = None
    for nm in ("Login", "Log in", "Log In", "Sign in", "Daxil ol", "Giriş"):
        b = page.get_by_role("button", name=nm, exact=True)
        if b.count() > 0:
            btn = b.first
            break
    if btn is None:
        b = page.get_by_role("button", name=re.compile(r"log ?in|sign ?in|giriş|daxil", re.I)).filter(
            has_not_text=re.compile(r"forgot|unut", re.I)
        )
        btn = b.first if b.count() > 0 else None
    if btn is not None:
        btn.click()
    else:
        pw_box.press("Enter")
    page.wait_for_load_state("load")
    return "Login edildi."


def _auto_profile(page, profile: str = "") -> str:
    """'Choose profile' səhifəsində `profile` adını (böyük/kiçik hərfə həssas deyil,
    hissə-hissə uyğunluq) seçir. profile boşdursa seçmir, mövcud profilləri qaytarır."""
    btn = page.get_by_role("button", name=re.compile("Choose", re.I))
    try:
        btn.first.wait_for(timeout=5000)
    except Exception:  # noqa: BLE001
        return "Profil seçimi səhifəsi çıxmadı."
    if not profile:
        text = page.locator("body").first.inner_text()
        return "Profil göstərilməyib, seçilmədi. Səhifədəki mətn:\n" + text[:1500]
    target = page.get_by_text(re.compile(re.escape(profile), re.I))
    if target.count() == 0:
        text = page.locator("body").first.inner_text()
        return f"'{profile}' profili tapılmadı. Səhifədəki mətn:\n" + text[:1500]
    target.first.click()
    btn.first.click()
    page.wait_for_load_state("load")
    return f"Profil seçildi: {profile}"


def _start_browser():
    """Brauzer və kontekst yoxdursa yaradır (ContactCenter və SunVizion eyni kontekstdə,
    yəni eyni cookie/sessiya ilə işləyir)."""
    if _s["browser"]:
        return
    _events.clear()
    _s["pw"] = sync_playwright().start()
    _s["browser"] = _s["pw"].chromium.launch(headless=False)
    _s["ctx"] = _s["browser"].new_context(ignore_https_errors=True)
    _s["ctx"].on("page", _on_new_page)


def _commit(box):
    """ASP.NET/Angular üçün change hadisəsini işə sal."""
    box.dispatch_event("input")
    box.dispatch_event("change")
    box.press("Tab")


# ---------- əsas alətlər ----------
@mcp.tool()
@pw
def open_portal(profile: str = "", env: str = "") -> str:
    """Brauzeri açır, login olur və `profile` adlı profili seçir (məs. 'CRM').
    Profil case-ə görə dəyişir. PORTAL_USER/PORTAL_PASS verilməyibsə login əl ilə edilir.
    env: PREPROD (default) və ya PROD. PROD-da alətlər YALNIZ OXUYUR: fill/select/upload/Save/Finish və s.
    bloklanır, klik yalnız sorğu nömrəsi, menyu və baxış düymələrinə icazəlidir."""
    envmsg = _set_env(env)
    if _s["browser"] and any(p.url.startswith(PORTAL_URL.rsplit("/", 1)[0]) for p in _s["ctx"].pages):
        return "Brauzer artıq açıqdır."
    _start_browser()
    # SunVizion əvvəl açılıbsa ContactCenter üçün ayrıca tab
    _s["page"] = _s["ctx"].pages[0] if _s["ctx"].pages and _s["ctx"].pages[0].url == "about:blank" \
        else _s["ctx"].new_page()
    # context.on("page") new_page()-in özü üçün də işləyir və _attach çağırır
    _s["page"].goto(PORTAL_URL, wait_until="load")
    msg = [envmsg, f"Açıldı: {PORTAL_URL}"]
    relog = _relogin_if_logged_out(_s["page"])
    if relog:
        msg.append(relog)
    if all(_creds()):
        msg.append(_auto_login(_s["page"]))
        msg.append(_auto_profile(_s["page"], profile))
    else:
        msg.append("Login olun, sonra choose_profile çağırın.")
    return " | ".join(msg)


@mcp.tool()
@pw
def login(profile: str = "") -> str:
    """Avtomatik login + profil seçimi (PORTAL_USER/PORTAL_PASS ilə). profile: case-ə uyğun profil adı."""
    page = _page()
    relog = _relogin_if_logged_out(page)
    parts = [relog] if relog else []
    parts += [_auto_login(page), _auto_profile(page, profile)]
    return " | ".join(parts)


@mcp.tool()
@pw
def close_portal() -> str:
    """Brauzeri bağlayır (sessiya itir)."""
    try:
        if _s["browser"]:
            _s["browser"].close()
        if _s["pw"]:
            _s["pw"].stop()
    finally:
        for k in _s:
            _s[k] = None
    return "Bağlandı."


@mcp.tool()
@pw
def screenshot(name: str, full_page: bool = False):
    """Screenshot çəkir, PORTAL_SCREENSHOT_DIR qovluğuna yazır və şəkli birbaşa qaytarır
    (Claude şəkli görə bilir). full_page=True bütün səhifəni çəkir, amma iframe-in öz
    daxili scroll-u daxil olmur. Default: görünən hissə."""
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(SHOT_DIR, f"{name}.png"))
    data = _page().screenshot(path=path, full_page=full_page)
    return [path, Image(data=data, format="png")]


@mcp.tool()
@pw
def current_state() -> str:
    """Açıq pəncərələrin URL-lərini və cari səhifənin frame sayını qaytarır.
    Aktiv pəncərə * ilə işarələnir."""
    pages = _s["ctx"].pages if _s["ctx"] else []
    active = _s["page"]
    lines = [f"pencere {i}{' *' if p is active else ''}: {p.url}" for i, p in enumerate(pages)]
    lines.append(f"cari sehifede frame sayi: {len(_page().frames)}")
    return "\n".join(lines)


@mcp.tool()
@pw
def switch_page(index: int = 0) -> str:
    """Aktiv pəncərəni dəyişir (current_state-dəki indeks). Yeni açılan pəncərəni
    (məs. 'Show process details') oxumaq üçün onun indeksini verin, sonra 0-a qayıdın."""
    pages = _s["ctx"].pages if _s["ctx"] else []
    if index < 0 or index >= len(pages):
        raise RuntimeError(f"Pəncərə {index} yoxdur (cəmi {len(pages)} pəncərə var).")
    _s["page"] = pages[index]
    _s["page"].bring_to_front()
    return f"Aktiv pəncərə {index}: {_s['page'].url} (frame sayı: {len(_s['page'].frames)})"


@mcp.tool()
@pw
def read_text(frame: int = 0) -> str:
    """Frame-in görünən mətnini oxuyur (dəyərləri yoxlamaq üçün)."""
    return _body(_frame(frame)).inner_text()


@mcp.tool()
@pw
def inspect_page(frame: int = -1) -> str:
    """Cari səhifənin strukturunu (accessibility snapshot) qaytarır. frame=-1: bütün frame-lər."""
    page = _page()
    out = [f"URL: {page.url}"]
    idxs = range(len(page.frames)) if frame == -1 else [frame]
    for i in idxs:
        f = _frame(i)
        out.append(f"\n--- FRAME {i}: {f.url}")
        try:
            out.append(_body(f).aria_snapshot())
        except Exception as e:  # noqa: BLE001
            out.append(f"(oxunmadi: {e})")
    return "\n".join(out)


@mcp.tool()
@pw
@prod_readonly("click")
@learned
def click(name: str, exact: bool = False, frame: int = 0, role: str = "button", nth: int = 0) -> str:
    """Rol və görünən ada görə klik: role=button|link|menuitem|tab|checkbox|radio|combobox.
    nth yalnız GÖRÜNƏN elementlər arasında sayılır. Tapılmasa/basılmasa avtomatik fallback:
    eyni adlı mətn/label -> əsl idarə elementi (Kendo combobox) -> force klik."""
    f = _frame(frame)
    loc = f.get_by_role(role, name=name, exact=exact)
    if loc.count() == 0:
        loc = f.get_by_text(name, exact=exact)
        if loc.count():
            _s["used"] = f"role={role} tapılmadı -> mətn"
    n = _smart_click(loc, nth, f"{role} '{name}'")
    return f"Klik: {role} '{name}' ({min(nth + 1, n)}/{n}). URL: {_page().url}"


@mcp.tool()
@pw
@prod_readonly("click")
@learned
def click_text(text: str, exact: bool = False, frame: int = 0, nth: int = 0) -> str:
    """Görünən mətnə görə klik. Eyni mətn bir neçə yerdədirsə nth ilə seçin (0-dan, yalnız GÖRÜNƏN-lər).
    Mətn label-dirsə və basılmırsa (Kendo floating label) avtomatik əsl sahəni (combobox/input) basır."""
    f = _frame(frame)
    loc = f.get_by_text(text, exact=exact)
    if loc.count() == 0:
        loc = f.get_by_role("combobox", name=text, exact=exact)
        if loc.count():
            _s["used"] = "mətn yox -> role=combobox"
    n = _smart_click(loc, nth, f"'{text}'")
    return f"Klik: '{text}' ({min(nth + 1, n)}/{n}). URL: {_page().url}"


@mcp.tool()
@pw
@prod_readonly("click")
def right_click(text: str, exact: bool = False, frame: int = 0, nth: int = 0) -> str:
    """Görünən mətnə SAĞ klik (kontekst menyu açır, məs. OSS Events sətri -> Processes -> New).
    nth yalnız GÖRÜNƏN elementlər arasında. Sonra menyu bəndini click_text ilə seçin."""
    f = _frame(frame)
    vis = _visible(f.get_by_text(text, exact=exact))
    if not vis:
        raise RuntimeError(f"'{text}' tapılmadı (görünən).")
    if nth >= len(vis):
        raise RuntimeError(f"'{text}': nth={nth}, görünən say {len(vis)}.")
    vis[nth].click(button="right", timeout=QUICK)
    time.sleep(0.6)
    items = [t.strip() for t in f.locator(
        "[role=menuitem]:visible, .k-menu-item:visible, .k-context-menu li:visible, .p-contextmenu li:visible"
    ).all_inner_texts() if t.strip()][:30]
    return (f"Sağ klik: '{text}' ({nth + 1}/{len(vis)}). Menyu: "
            + (" | ".join(items) if items else "(menyu bəndi tapılmadı - screenshot ilə yoxlayın)"))


@mcp.tool()
@pw
@prod_readonly("write")
@learned
def select_option(label: str, value: str, frame: int = 0) -> str:
    """Label-ə görə dropdown/select-dən seçim edir. Native <select> və Kendo/Angular dropdown
    (multiselect, combobox) ikisi də işləyir: native deyilsə avtomatik sahəni açıb variantı basır.
    Label tapılmasa sətir mətninə görə axtarır."""
    f = _frame(frame)
    vis = _visible(f.get_by_label(label))
    native = [x for x in vis if (x.evaluate("e => e.tagName") or "").upper() == "SELECT"]
    if native:
        native[0].select_option(label=value)
        return f"Seçildi: {label} = {value}"
    if not vis:
        rows = f.locator("tr").filter(has_text=label).filter(has=f.locator("select"))
        rv = _visible(rows.locator("select"))
        if rv:
            rv[-1].select_option(label=value)
            return f"Seçildi (sətir): {label} = {value}"
    _s["used"] = "kendo dropdown"
    return _kendo_pick(f, label, value, False)


def _kendo_pick(f, label: str, value: str, exact: bool) -> str:
    """Görünən Kendo/Angular dropdown-u (label/ad üzrə) açıb `value` variantını basır."""
    boxes = _visible(f.get_by_role("combobox", name=label))
    if not boxes:
        lab = _visible(f.get_by_text(label, exact=True))
        for l in lab:
            ctl, _how = _control_for(l)
            if ctl is not None:
                boxes = [ctl]
                break
    if not boxes:
        boxes = _visible(f.get_by_label(label))
    if not boxes:
        raise RuntimeError(f"'{label}' dropdown tapılmadı (görünən).")
    boxes[0].click(timeout=QUICK)
    time.sleep(0.4)
    opt = _visible(f.get_by_role("option", name=value, exact=exact))
    if not opt:
        opt = _visible(f.locator("[role=listbox] li, .k-list-item, .k-item").filter(has_text=value))
    if not opt:
        shown = [t.strip() for t in f.locator("[role=listbox] li:visible, .k-list-item:visible").all_inner_texts()][:30]
        none = _visible(f.get_by_text("No data available"))
        _page().keyboard.press("Escape")
        raise RuntimeError(f"'{label}' siyahısında '{value}' yoxdur. Görünən variantlar: "
                           + (", ".join(shown) if shown else ("No data available" if none else "(boş)")))
    opt[0].click(timeout=QUICK)
    return f"Seçildi: {label} = {value}"


@mcp.tool()
@pw
def choose_profile(profile: str = "") -> str:
    """Login-dən sonrakı 'Choose profile' səhifəsində case-ə uyğun profili (məs. 'CRM') seçib Choose basır."""
    return _auto_profile(_page(), profile)


# ---------- düzgün Search düyməsi ----------
def _form_frame():
    """'Clear Fields' düyməsi olan (axtarış formu) frame-i tapır."""
    for f in _page().frames:
        try:
            if f.get_by_role("button", name="Clear Fields").count() > 0:
                return f
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Axtarış formu ('Clear Fields' düyməsi) tapılmadı.")


@mcp.tool()
@pw
def click_search() -> str:
    """Axtarış formundakı (Clear Fields ilə eyni sətirdəki) Search düyməsini basır.
    Yuxarıdakı 'Enter identifier' (sorğu identifikatoru) Search düyməsinə toxunmur."""
    f = _form_frame()
    row = f.locator("tr").filter(has=f.get_by_role("button", name="Clear Fields"))
    row.last.get_by_role("button", name="Search", exact=True).first.click()
    return "Form axtarışı (Search) basıldı."


@mcp.tool()
@pw
def search_customer(name: str = "", customer_id: str = "") -> str:
    """Find Customer səhifəsində Name və ya Customer ID ilə axtarır (düzgün Search düyməsi ilə).
    Məs: search_customer(name='%Dürdanə%')."""
    if not (name or customer_id):
        raise RuntimeError("name və ya customer_id verilməlidir.")
    f = _form_frame()

    def group(text):
        return f.get_by_role("group").filter(has_text=text).last

    if name:
        g = group("Name")
        g.get_by_role("checkbox").first.check()
        box = g.get_by_role("textbox").first
        box.fill(name)
        _commit(box)
    else:
        group("Name").get_by_role("checkbox").first.uncheck()
    if customer_id:
        g = group("Customer ID")
        g.get_by_role("checkbox").first.check()
        box = g.get_by_role("textbox").first
        box.fill(customer_id)
        _commit(box)
    click_search.__wrapped__()
    return "Axtarış icra olundu."


# ---------- mətn sahələri ----------
@mcp.tool()
@pw
@prod_readonly("search")
@learned
def fill(label: str, value: str, frame: int = 0) -> str:
    """Label-ə görə input doldurur. Label tapılmasa avtomatik sətir mətninə görə axtarır."""
    f = _frame(frame)
    loc = f.get_by_label(label)
    if loc.count() > 0:
        box = loc.first
        box.fill(value)
        _commit(box)
        return f"Dolduruldu (label): {label}"
    box = _row_textbox(f, label)
    box.fill(value)
    _commit(box)
    return f"Dolduruldu (sətir mətni): {label}"


@mcp.tool()
@pw
@prod_readonly("write")
@learned
def fill_by_row(row_text: str, value: str, frame: int = 0, index: int = 0) -> str:
    """Sətirdəki mətnə (məs. 'Contact Name', 'Name', 'Customer ID') görə həmin sətrin textbox-unu doldurur."""
    box = _row_textbox(_frame(frame), row_text, index)
    box.fill(value)
    _commit(box)
    return f"Dolduruldu: {row_text} = {value}"


@mcp.tool()
@pw
@prod_readonly("write")
def fill_nth(index: int, value: str, frame: int = 0) -> str:
    """Frame-dəki index-ci (0-dan) textbox-u doldurur. Kövrəkdir: səhifə dəyişsə sıra dəyişir."""
    box = _frame(frame).get_by_role("textbox").nth(index)
    box.fill(value)
    _commit(box)
    return f"{index}-ci textbox dolduruldu"


@mcp.tool()
@pw
@prod_readonly("write")
@learned
def type_text(row_text: str, value: str, frame: int = 0, index: int = 0, delay_ms: int = 40) -> str:
    """Sətir mətninə görə sahəyə hərf-hərf yazır (fill işləməyən sahələr üçün)."""
    box = _row_textbox(_frame(frame), row_text, index)
    box.click()
    box.press_sequentially(value, delay=delay_ms)
    box.press("Tab")
    return f"Yazıldı: {row_text} = {value}"


@mcp.tool()
@pw
@prod_readonly("key")
def press_key(key: str, frame: int = 0) -> str:
    """Klaviatura düyməsi basır (Enter, Tab, Escape, ArrowDown ...)."""
    _frame(frame)  # frame-in mövcudluğunu yoxla
    _page().keyboard.press(key)
    return f"Basıldı: {key}"


# ---------- YENİ: xəta və vizual yoxlama ----------
@mcp.tool()
@pw
def get_events(clear: bool = True, kind: str = "") -> str:
    """Son addımlardan bəri toplanmış hadisələri qaytarır: brauzer dialoqları (alert/confirm),
    console error/warning, JS exception, uğursuz sorğular, HTTP 4xx/5xx, yeni pəncərələr.
    clear=True qaytardıqdan sonra jurnalı təmizləyir. kind: süzgəc (məs. 'dialog', 'http', 'console').
    Hər vacib addımdan sonra çağırın: boş nəticə = heç nə baş verməyib."""
    items = [e for e in _events if not kind or kind.lower() in e[1].lower()]
    if clear:
        if kind:
            keep = [e for e in _events if kind.lower() not in e[1].lower()]
            _events.clear()
            _events.extend(keep)
        else:
            _events.clear()
    if not items:
        return "Hadisə yoxdur."
    return "\n".join(f"[{t}] {k}: {m}" for t, k, m in items)


_LAYOUT_JS = r"""
() => {
  const out = [];
  const add = (type, el, extra) => out.push({type, el: desc(el), extra: extra || ''});
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const desc = el => {
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id.slice(-50);
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    if (t) d += ' "' + t + '"';
    return d;
  };

  // 1) səhifə səviyyəsində üfüqi scroll
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) {
    out.push({type: 'h-scroll', el: 'document', extra: de.scrollWidth + ' > ' + de.clientWidth});
  }

  // 2) select-lər: boş və ya yalnız 1 variantlı
  document.querySelectorAll('select').forEach(s => {
    if (!visible(s)) return;
    const n = s.options.length;
    if (n === 0) add('empty-select', s, 'variant yoxdur');
    else if (n === 1) add('single-option-select', s, 'yalnız: "' + s.options[0].text + '"');
  });

  // 3) sınıq şəkillər
  document.querySelectorAll('img').forEach(i => {
    if (visible(i) && i.complete && i.naturalWidth === 0) add('broken-image', i, i.src.slice(-80));
  });

  // 4) kəsilmiş mətn (ellipsis / overflow hidden)
  document.querySelectorAll('body *').forEach(el => {
    if (el.childElementCount > 0 || !visible(el)) return;
    if (!(el.textContent || '').trim()) return;
    const s = getComputedStyle(el);
    if ((s.textOverflow === 'ellipsis' || s.overflow === 'hidden' || s.overflowX === 'hidden')
        && el.scrollWidth > el.clientWidth + 1) {
      add('truncated-text', el, el.scrollWidth + ' > ' + el.clientWidth);
    }
  });

  // 5) interaktiv elementlər: ekrandan çıxanlar və üst-üstə düşənlər
  const sel = 'input:not([type=hidden]), select, textarea, button, a[href]';
  const items = [...document.querySelectorAll(sel)].filter(visible);
  items.forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.right > window.innerWidth + 1 || r.left < -1) add('offscreen', el, Math.round(r.left) + '..' + Math.round(r.right));
  });
  for (let i = 0; i < items.length && i < 150; i++) {
    const a = items[i], ra = a.getBoundingClientRect();
    for (let j = i + 1; j < items.length && j < 150; j++) {
      const b = items[j], rb = b.getBoundingClientRect();
      if (a.contains(b) || b.contains(a)) continue;
      const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (w <= 0 || h <= 0) continue;
      const small = Math.min(ra.width * ra.height, rb.width * rb.height);
      if (small > 0 && (w * h) / small > 0.3) add('overlap', a, 'ilə: ' + desc(b));
    }
  }
  return out.slice(0, 40);
}
"""


@mcp.tool()
@pw
def check_layout(frame: int = -1) -> str:
    """Səhifədəki (frame=-1: bütün frame-lərdəki) avtomatik tapıla bilən UI problemlərini yoxlayır:
    boş/1 variantlı select, kəsilmiş mətn, üst-üstə düşən və ekrandan çıxan elementlər,
    sınıq şəkillər, üfüqi scroll. Boş nəticə problem olmadığını sübut etmir (rəng,
    hizalanma kimi şeyləri screenshot ilə baxmaq lazımdır)."""
    page = _page()
    idxs = range(len(page.frames)) if frame == -1 else [frame]
    lines = []
    for i in idxs:
        f = _frame(i)
        try:
            res = f.evaluate(_LAYOUT_JS)
        except Exception as e:  # noqa: BLE001
            lines.append(f"FRAME {i}: oxunmadı ({e})")
            continue
        for r in res:
            extra = f" ({r['extra']})" if r["extra"] else ""
            lines.append(f"FRAME {i}: {r['type']}: {r['el']}{extra}")
    return "\n".join(lines) if lines else "Avtomatik yoxlamada problem tapılmadı."


# ---------- YENİ: fayl yükləmə ----------
TEST_FILES_DIR = os.getenv("PORTAL_TEST_FILES_DIR", "test_files")

_MIN_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 18 Tf 20 70 Td (Test document) Tj ET\nendstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _resolve_file(path: str) -> str:
    """Nisbi yol verilibsə TEST_FILES_DIR-ə görə həll edir."""
    p = path if os.path.isabs(path) else os.path.join(TEST_FILES_DIR, path)
    p = os.path.abspath(p)
    if not os.path.isfile(p):
        raise RuntimeError(f"Fayl tapılmadı: {p}")
    return p


def _size(p: str) -> str:
    n = os.path.getsize(p)
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.1f} KB"


@mcp.tool()
@pw
def make_test_files(large_mb: int = 30) -> str:
    """Yükləmə testləri üçün PORTAL_TEST_FILES_DIR (default 'test_files') qovluğunda fayllar yaradır:
    test.pdf (kiçik, düzgün PDF), test.jpg (real JPEG, brauzer screenshot-u),
    large_<N>mb.pdf (PDF başlıqlı, ölçüsü large_mb MB), test.exe (MZ başlıqlı saxta exe,
    işə salına bilməz - yalnız uzantı/tip yoxlaması üçün). Mövcud faylların üstünə yazır."""
    os.makedirs(TEST_FILES_DIR, exist_ok=True)
    out = []

    def w(name, data):
        p = os.path.abspath(os.path.join(TEST_FILES_DIR, name))
        with open(p, "wb") as fh:
            fh.write(data)
        out.append(f"{name}: {_size(p)}")

    w("test.pdf", _MIN_PDF)
    if _s["page"]:
        jpg = _s["page"].screenshot(type="jpeg", quality=70)
    else:
        b = sync_playwright().start()
        try:
            br = b.chromium.launch(headless=True)
            pg = br.new_page()
            pg.set_content("<h1 style='font-family:sans-serif'>Test image</h1>")
            jpg = pg.screenshot(type="jpeg", quality=70)
            br.close()
        finally:
            b.stop()
    w("test.jpg", jpg)
    # Böyük fayl: PDF başlığı + doldurucu, yalnız ölçü limiti yoxlansın deyə
    big = os.path.abspath(os.path.join(TEST_FILES_DIR, f"large_{large_mb}mb.pdf"))
    with open(big, "wb") as fh:
        fh.write(_MIN_PDF)
        chunk = b"%" + b"0" * (1024 * 1024 - 2) + b"\n"
        for _ in range(large_mb):
            fh.write(chunk)
    out.append(f"large_{large_mb}mb.pdf: {_size(big)}")
    w("test.exe", b"MZ" + b"\x00" * 1022)
    return f"Qovluq: {os.path.abspath(TEST_FILES_DIR)}\n" + "\n".join(out)


@mcp.tool()
@pw
def list_file_inputs() -> str:
    """Bütün frame-lərdəki <input type=file> elementlərini göstərir (gizli olanlar da):
    frame, sıra, id/name, accept, multiple, görünürlük."""
    lines = []
    for i, f in enumerate(_page().frames):
        try:
            loc = f.locator("input[type=file]")
            for j in range(loc.count()):
                el = loc.nth(j)
                info = el.evaluate(
                    "e => ({id: e.id, name: e.name, accept: e.accept, multiple: e.multiple})"
                )
                lines.append(
                    f"frame {i} #{j}: id={info['id'] or '-'} name={info['name'] or '-'} "
                    f"accept='{info['accept']}' multiple={info['multiple']} "
                    f"visible={el.is_visible()}"
                )
        except Exception as e:  # noqa: BLE001
            lines.append(f"frame {i}: oxunmadı ({e})")
    return "\n".join(lines) if lines else "Səhifədə <input type=file> yoxdur."


@mcp.tool()
@pw
@prod_readonly("write")
def upload_file(
    file_path: str,
    trigger_text: str = "",
    frame: int = -1,
    nth: int = 0,
    exact: bool = False,
) -> str:
    """Faylı əməliyyat sisteminin fayl pəncərəsi AÇILMADAN yükləyir.
    file_path: tam yol və ya PORTAL_TEST_FILES_DIR-ə nisbi ad (məs. 'test.pdf'). Bir neçə fayl üçün ';' ilə ayırın.
    Rejim 1 (trigger_text boş): səhifədəki input[type=file] elementinə birbaşa fayl verilir
      (frame=-1: bütün frame-lərdə axtarır, nth: neçənci input). list_file_inputs ilə baxın.
    Rejim 2 (trigger_text verilib): həmin mətnli düymə/link basılır, açılan fayl seçicisi
      tutulur və fayl ora verilir (input JS ilə sonradan yaradılırsa bu rejimi işlədin).
    Sonra portalın reaksiyasını screenshot və get_events ilə yoxlayın."""
    files = [_resolve_file(x.strip()) for x in file_path.split(";") if x.strip()]
    page = _page()
    frames = page.frames if frame == -1 else [_frame(frame)]
    names = ", ".join(f"{os.path.basename(p)} ({_size(p)})" for p in files)

    if trigger_text:
        for f in frames:
            loc = f.get_by_text(trigger_text, exact=exact)
            if loc.count() == 0:
                loc = f.get_by_role("button", name=trigger_text, exact=exact)
            if loc.count() > nth:
                with page.expect_file_chooser(timeout=10000) as fc:
                    loc.nth(nth).click()
                fc.value.set_files(files if len(files) > 1 else files[0])
                return f"Fayl seçicisi ilə verildi: {names}"
        raise RuntimeError(f"'{trigger_text}' tapılmadı.")

    for f in frames:
        loc = f.locator("input[type=file]")
        if loc.count() > nth:
            loc.nth(nth).set_input_files(files if len(files) > 1 else files[0])
            return f"input[type=file]-ə verildi (frame {page.frames.index(f)}, #{nth}): {names}"
    raise RuntimeError(
        "input[type=file] tapılmadı. Yükləmə pəncərəsini açın və ya trigger_text istifadə edin."
    )


# ======================================================================
# SunVizion Portal (Angular). ContactCenter ilə EYNİ brauzer kontekstində işləyir:
# eyni login, eyni pəncərə siyahısı (current_state / switch_page ilə görünür).
# ======================================================================
SV_URL = os.getenv("SUNVIZION_URL", "https://ppwebportal.aztelekom.az/20_0/Portal").rstrip("/")
SV_PAGES = {
    "start": "/app",
    "tasks": "/app/wfl/tasks/worker-tasks",
    "processes": "/app/wfl/processes/process-instances",
    "service_catalog": "/app/si/service-catalog/customer-service-specifications",
    "resource_specs": "/app/si/service-catalog/resource-service-specifications",
    "services": "/app/si",
    "service_templates": "/app/si/templates",
    "resources": "/app/ri-aggregation",
    "numbers": "/app/num",
    "work_orders": "/app/wfr/work",
    "workforce_map": "/app/wfr/workforce-map",
    "configuration": "/app/configuration",
}
# Workflow vəziyyətini dəyişən düymələr: yalnız confirm=True ilə basılır
SV_RISKY = re.compile(
    r"accept|finali[sz]e|activate|cancel|delete|remove|approve|reject|save|submit|new|start|"
    r"close task|complete|assign|reassign|release|terminate|abort",
    re.I,
)
_SV_SPINNER = ".k-loading-mask, .tch-loader, .spinner, [aria-busy=true]"


def _is_sv(page) -> bool:
    return page.url.startswith(SV_URL)


def _sv_page():
    page = _page()
    if not _is_sv(page):
        raise RuntimeError(
            "Aktiv pəncərə SunVizion Portal deyil. open_sunvizion çağırın və ya switch_page ilə keçin."
        )
    return page


def _sv_wait(page, timeout: int = 5000):
    """Angular səhifəsi yüklənib cədvəl/forma çəkilənə qədər gözləyir."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.locator(_SV_SPINNER).first.wait_for(state="hidden", timeout=5000)
    except Exception:  # noqa: BLE001
        pass


def _sv_needs_login(page) -> bool:
    if "/login" in page.url.lower():
        return True
    try:
        return page.locator("input[type=password]:visible").count() > 0
    except Exception:  # noqa: BLE001
        return False


SV_PROFILE = os.getenv("SUNVIZION_PROFILE", "")  # boş: Portal-ın seçili saxladığı profil


def _sv_profile(page, profile: str = "") -> str:
    """Login-dən sonrakı /profile səhifəsi: profili seçib 'Select' basır.
    Portal bundan sonra returnUrl-ə özü keçir."""
    btn = page.get_by_role("button", name=re.compile(r"^\s*(Select|Choose)\s*$", re.I))
    try:
        btn.first.wait_for(timeout=8000)
    except Exception:  # noqa: BLE001
        return ""
    profile = profile or SV_PROFILE
    if profile:
        opt = page.get_by_text(re.compile(re.escape(profile), re.I))
        if opt.count() == 0:
            return f"'{profile}' profili tapılmadı. Səhifədəki mətn:\n" + page.locator("body").first.inner_text()[:800]
        opt.first.click()
    btn.first.click()
    try:
        page.wait_for_url(lambda u: "/profile" not in u.lower(), timeout=20000)
    except Exception:  # noqa: BLE001
        return f"Profil seçildi, amma səhifə dəyişmədi (URL: {page.url})."
    return f"Profil seçildi: {profile or 'default'}"


def _sv_login(page, profile: str = "") -> str:
    msg = [_auto_login(page)]
    try:
        page.wait_for_url(lambda u: "/login" not in u.lower(), timeout=20000)
    except Exception:  # noqa: BLE001
        return " | ".join(msg + [f"Login səhifəsindən çıxmadı (URL: {page.url}). Xəta mesajına screenshot ilə baxın."])
    if "/profile" in page.url.lower() or page.get_by_role(
        "button", name=re.compile(r"^\s*(Select|Choose)\s*$", re.I)
    ).count() > 0:
        msg.append(_sv_profile(page, profile))
    _sv_wait(page)
    return " | ".join(m for m in msg if m)


def _sv_settled(page, timeout: int = 10000):
    """Cari görünüş çəkilənə qədər gözləyir (başlıq h1 boş deyil)."""
    try:
        page.wait_for_function(
            "() => { const h = document.querySelector('h1'); return !!h && h.textContent.trim().length > 0; }",
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        pass


def _sv_route(page, url: str) -> bool:
    """Daxil olmuş tabda səhifəni YENİDƏN YÜKLƏMƏDƏN keçir (Angular router, menyu klikinin eynisi).
    page.goto Portal sessiyasını itirir və login-ə atır, ona görə istifadə olunmur."""
    target = url.split("?")[0].rstrip("/")
    if page.url.split("?")[0].rstrip("/") == target:
        return True
    _sv_settled(page, 8000)   # əvvəlki keçid hələ çəkilirsə, onu "köhnə səhifə" kimi götürməyək
    try:
        # köhnə səhifəni işarələ: keçiddən sonra yeni görünüş çəkilənə qədər gözləmək üçün
        page.evaluate(
            "() => { const h = document.querySelector('h1');"
            " window.__svOldTitle = h ? h.textContent.trim() : null;"
            " const gs = document.querySelectorAll('[role=grid],[role=treegrid]');"
            " window.__svHadGrid = gs.length > 0; gs.forEach(g => g.dataset.svOld = '1'); }"
        )
        page.evaluate(
            "u => { history.pushState(null, '', u); window.dispatchEvent(new PopStateEvent('popstate', {state: null})); }",
            url,
        )
        page.wait_for_url(lambda u: u.split("?")[0].rstrip("/") == target or "/login" in u.lower(), timeout=10000)
    except Exception:  # noqa: BLE001
        return False
    try:
        # Angular köhnə komponenti silib yenisini çəkəndə işarəli h1/grid yox olur
        # yeni görünüş: başlıq dəyişib və ya köhnə cədvəl DOM-dan çıxıb (eyni başlıqlı səhifələr üçün)
        page.wait_for_function(
            "() => { const h = document.querySelector('h1');"
            " const titleChanged = window.__svOldTitle !== null && h && h.textContent.trim() !== window.__svOldTitle;"
            " const gridGone = window.__svHadGrid && !document.querySelector('[data-sv-old]');"
            " return titleChanged || gridGone || (window.__svOldTitle === null && !window.__svHadGrid); }",
            timeout=15000,
        )
    except Exception:  # noqa: BLE001
        pass
    _sv_wait(page)
    return not _sv_needs_login(page)


def _sv_grid(page, grid_index: int = 0):
    grids = page.locator("[role=grid]:visible, [role=treegrid]:visible")
    n = grids.count()
    if n == 0:
        raise RuntimeError("Səhifədə görünən cədvəl (grid) yoxdur.")
    if grid_index >= n:
        raise RuntimeError(f"Cədvəl {grid_index} yoxdur (cəmi {n} cədvəl var).")
    return grids.nth(grid_index)


_GRID_JS = r"""
g => {
  const t = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const hs = [...g.querySelectorAll('[role=columnheader]')].map(t);
  const rows = [...g.querySelectorAll('[role=row]')]
    .map(r => [...r.querySelectorAll('[role=gridcell]')].map(t))
    .filter(r => r.length && r.some(c => c));
  return {hs, rows};
}
"""


def _sv_grid_ready(page, grid_index: int = 0, timeout: int = 10000):
    """Cədvəlin məlumatı serverdən gələnə qədər gözləyir: ən azı bir dolu sətir.
    Portal yüklənmə zamanı müvəqqəti 'No records' göstərir, ona görə 'No records' yalnız
    3 saniyə dəyişməz qalarsa (həqiqətən boş cədvəl) qəbul edilir."""
    try:
        grid = _sv_grid(page, grid_index).element_handle(timeout=timeout)
        page.wait_for_function(
            "g => { const hasRows = [...g.querySelectorAll('[role=row]')].some(r =>"
            " [...r.querySelectorAll('[role=gridcell]')].some(c => c.innerText.trim()));"
            " if (hasRows) return true;"
            " if (/no records/i.test(g.innerText)) {"
            "   window.__svEmptySince = window.__svEmptySince || Date.now();"
            "   return Date.now() - window.__svEmptySince > 3000; }"
            " window.__svEmptySince = 0; return false; }",
            arg=grid,
            timeout=timeout,
            polling=250,
        )
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            page.evaluate("() => { window.__svEmptySince = 0; }")
        except Exception:  # noqa: BLE001
            pass


def _sv_pager_text(page) -> str:
    try:
        txt = page.locator("body").first.inner_text()
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"\d+\s*-\s*\d+\s+of\s+\d+(\s+Objects)?", txt, re.I)
    return m.group(0) if m else ""


def _sv_row(page, text: str, grid_index: int = 0, exact: bool = False, nth: int = 0):
    grid = _sv_grid(page, grid_index)
    rows = grid.get_by_role("row").filter(has=page.get_by_role("gridcell"))
    if exact:
        rows = rows.filter(has=page.get_by_role("gridcell", name=text, exact=True))
    else:
        rows = rows.filter(has_text=text)
    n = rows.count()
    if n == 0:
        raise RuntimeError(f"'{text}' olan sətir tapılmadı (cədvəl {grid_index}).")
    if nth >= n:
        raise RuntimeError(f"'{text}' olan {n} sətir var, nth={nth} yoxdur.")
    return rows.nth(nth), n


@mcp.tool()
@pw
def open_sunvizion(page_name: str = "start", path: str = "", new_tab: bool = False, profile: str = "",
                   env: str = "") -> str:
    """SunVizion Portal-ı ContactCenter ilə EYNİ brauzerdə açır və aktiv pəncərə edir.
    Login səhifəsi çıxsa PORTAL_USER/PORTAL_PASS ilə daxil olur.
    page_name: start, tasks, processes, service_catalog, resource_specs, services,
      service_templates, resources, numbers, work_orders, workforce_map, configuration.
    path: page_name əvəzinə istənilən yol, məs. '/app/wfl/processes/process-tasks/70000/870927'.
    new_tab=False: açıq SunVizion tabı varsa onu istifadə edir.
    env: PREPROD (default: cari mühit) və ya PROD (yalnız oxuma)."""
    if env:
        _set_env(env)
    _start_browser()
    rel = path or SV_PAGES.get(page_name.lower())
    if rel is None:
        raise RuntimeError(f"Naməlum page_name '{page_name}'. Mümkün: {', '.join(SV_PAGES)}")
    url = SV_URL + (rel if rel.startswith("/") else "/" + rel)
    page = None
    if not new_tab:
        # üstünlük: aktiv SunVizion tabı, sonra login olunmuş tab, sonra istənilən SunVizion tabı
        sv_tabs = [p for p in _s["ctx"].pages if _is_sv(p)]
        active = _s["page"] if _s["page"] in sv_tabs and not _sv_needs_login(_s["page"]) else None
        logged = next((p for p in sv_tabs if not _sv_needs_login(p)), None)
        page = active or logged or (sv_tabs[0] if sv_tabs else None)
    msg = []
    routed = False
    if page is not None and not _sv_needs_login(page) and "/profile" not in page.url.lower():
        routed = _sv_route(page, url)   # daxil olunub: yenidən yükləmədən keç
    if not routed:
        if page is None:
            page = _s["ctx"].new_page()
        if not _sv_needs_login(page) or "returnUrl" not in page.url:
            page.goto(url, wait_until="domcontentloaded")   # sessiya yoxdursa login?returnUrl=... açılır
            _sv_wait(page)
        if _sv_needs_login(page):
            msg.append(_sv_login(page, profile))   # login + profil -> Portal returnUrl-ə özü keçir
        elif "/profile" in page.url.lower():
            msg.append(_sv_profile(page, profile))
        target = url.split("?")[0]
        if not page.url.startswith(target) and not _sv_needs_login(page):
            if not _sv_route(page, url):
                msg.append(f"Hədəf səhifə açılmadı (URL: {page.url}).")
    _s["page"] = page
    page.bring_to_front()
    if not _sv_needs_login(page):
        _sv_settled(page)
    idx = _s["ctx"].pages.index(page)
    h1 = page.locator("h1").first.inner_text() if page.locator("h1").count() else ""
    msg.append(f"Aktiv pəncərə {idx}: {page.url} | Başlıq: {h1}")
    return " | ".join(m for m in msg if m)


@mcp.tool()
@pw
def sv_grid_read(grid_index: int = 0, max_rows: int = 200, all_pages: bool = False) -> str:
    """SunVizion cədvəlini (role=grid/treegrid) TSV kimi oxuyur: birinci sətir başlıqlar.
    all_pages=True: 'Go to next page' ilə bütün səhifələri gəzir (max_rows-a qədər).
    Sonda səhifələmə məlumatı ('1 - 16 of 21 Objects') göstərilir."""
    page = _sv_page()
    _sv_wait(page)
    _sv_grid_ready(page, grid_index)
    headers, rows, pages = [], [], 0
    while True:
        data = _sv_grid(page, grid_index).evaluate(_GRID_JS)
        headers = headers or data["hs"]
        rows.extend(data["rows"])
        pages += 1
        if not all_pages or len(rows) >= max_rows or pages >= 50:
            break
        nxt = page.get_by_role("button", name="Go to next page")
        if nxt.count() == 0 or not nxt.first.is_enabled():
            break
        nxt.first.click()
        _sv_wait(page)
        _sv_grid_ready(page, grid_index)
    rows = rows[:max_rows]
    out = ["\t".join(headers)] + ["\t".join(r) for r in rows]
    width = max((len(r) for r in rows), default=0)
    note = []
    if headers and width and width != len(headers):
        note.append(f"(diqqət: başlıq sayı {len(headers)}, xana sayı {width}; checkbox sütunu ola bilər)")
    pager = _sv_pager_text(page)
    note.append(f"Oxunan sətir: {len(rows)}, səhifə: {pages}" + (f" | Portal: {pager}" if pager else ""))
    return "\n".join(out + note) if rows else "Cədvəl boşdur. " + " ".join(note)


@mcp.tool()
@pw
def sv_open_row(text: str, grid_index: int = 0, exact: bool = False, nth: int = 0, double: bool = False) -> str:
    """Cədvəldə `text` olan sətri açır (klik; double=True: iki klik). Adətən sağda preview və ya
    tapşırıq forması açılır; sonra sv_fields və ya screenshot ilə baxın."""
    page = _sv_page()
    row, n = _sv_row(page, text, grid_index, exact, nth)
    cell = row.get_by_role("gridcell").filter(has_text=text).first
    target = cell if cell.count() else row
    (target.dblclick if double else target.click)()
    _sv_wait(page)
    heads = [h.strip() for h in page.get_by_role("heading").all_inner_texts() if h.strip()]
    return f"Açıldı: '{text}' ({nth + 1}/{n}). Görünən başlıqlar: {' | '.join(heads[:6])}"


@mcp.tool()
@pw
def sv_select_row(text: str, grid_index: int = 0, exact: bool = False, nth: int = 0, select: bool = True) -> str:
    """`text` olan sətrin checkbox-unu seçir (select=False: seçimi götürür). Checkbox yoxdursa
    sətrə klik edir. Toolbar əməliyyatından (sv_toolbar) əvvəl istifadə edin."""
    page = _sv_page()
    row, n = _sv_row(page, text, grid_index, exact, nth)
    box = row.get_by_role("checkbox")
    if box.count() == 0:
        box = row.locator("input[type=checkbox]")
    if box.count():
        box.first.set_checked(select, force=True)
    else:
        row.get_by_role("gridcell").first.click()
    _sv_wait(page)
    m = re.search(r"selected:\s*\d+", page.locator("body").first.inner_text(), re.I)
    return f"{'Seçildi' if select else 'Seçim götürüldü'}: '{text}' ({nth + 1}/{n}). {m.group(0) if m else ''}"


@mcp.tool()
@pw
@prod_readonly("write")
def sv_toolbar(button: str, confirm: bool = False, nth: int = 0) -> str:
    """Toolbar və ya panel düyməsini basır (Accept, Accept and Finalize, Finalize, Activate,
    Load All, Refresh, Show Tasks, Show All Tasks, Expand All ...).
    Workflow vəziyyətini dəyişən düymələr (Accept, Finalize, Activate, Save, Cancel, Approve ...)
    YALNIZ confirm=True ilə basılır; əks halda heç nə basılmır və nə seçildiyi göstərilir."""
    page = _sv_page()
    loc = page.get_by_role("button", name=button, exact=True)
    if loc.count() == 0:
        loc = page.get_by_role("switch", name=button, exact=True)
    if loc.count() == 0:
        loc = page.get_by_role("button", name=button)
    if loc.count() <= nth:
        raise RuntimeError(f"'{button}' düyməsi tapılmadı.")
    btn = loc.nth(nth)
    if not btn.is_enabled():
        return f"'{button}' düyməsi deaktivdir (sətir seçilməyib və ya icazə yoxdur)."
    if SV_RISKY.search(button) and not confirm:
        m = re.search(r"selected:\s*\d+", page.locator("body").first.inner_text(), re.I)
        return (f"BASILMADI: '{button}' workflow vəziyyətini dəyişir. "
                f"{m.group(0) if m else ''} Təsdiq üçün confirm=True ilə yenidən çağırın.")
    btn.click()
    _sv_wait(page)
    return f"Basıldı: '{button}'. URL: {page.url}"


_FIELDS_JS = r"""
root => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = s => (s || '').replace(/\s+/g, ' ').replace(/[:*]\s*$/, '').trim();
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(id => document.getElementById(id)).filter(Boolean).map(x => x.innerText).join(' '); if (t.trim()) return t; }
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.innerText; }
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, .k-label, .tch-label, [class*=label]');
      if (l && !l.contains(el) && clean(l.innerText)) return l.innerText;
    }
    return el.getAttribute('placeholder') || el.name || el.id || '';
  };
  const sel = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, select, [role=combobox], [role=checkbox], [role=switch], input[type=checkbox]';
  const seen = new Set(); const out = [];
  root.querySelectorAll(sel).forEach(el => {
    if (!vis(el)) return;
    if (el.closest('[role=grid],[role=treegrid],[role=toolbar],[role=navigation],[role=application],.k-pager,.k-grid-pager')) return;
    const combo = el.closest('[role=combobox]');
    const node = combo && combo !== el ? combo : el;
    if (seen.has(node)) return; seen.add(node);
    let val;
    if (node.matches('input[type=checkbox],[role=checkbox],[role=switch]'))
      val = (node.checked || node.getAttribute('aria-checked') === 'true') ? '[x]' : '[ ]';
    else if (node.tagName === 'SELECT') val = node.selectedOptions[0] ? node.selectedOptions[0].text : '';
    else val = node.value !== undefined && node.tagName !== 'DIV' && node.tagName !== 'SPAN' ? node.value : node.innerText;
    const inner = node.querySelector && node.querySelector('input');
    const ro = node.readOnly || node.getAttribute('aria-readonly') === 'true' || (inner && inner.readOnly);
    const dis = node.disabled || node.getAttribute('aria-disabled') === 'true' || !!node.closest('[aria-disabled=true], .k-disabled, fieldset[disabled]') || (inner && inner.disabled);
    const req = node.required || node.getAttribute('aria-required') === 'true' || (inner && inner.required);
    out.push([clean(labelOf(node)), clean(val), dis ? 'disabled' : (ro ? 'readonly' : 'editable'), req ? 'məcburi' : '']);
  });
  return out;
}
"""


@mcp.tool()
@pw
def sv_fields(container_text: str = "") -> str:
    """Açıq formadakı (tapşırıq/preview) sahələri sadalayır: label | dəyər | editable/readonly/disabled | məcburi.
    container_text: yalnız bu mətni (məs. paneldəki başlıq) olan bölməni oxu; boşdursa bütün səhifə.
    Read-only/editable yoxlamaları üçündür. 'məcburi' yalnız HTML/ARIA-da işarələnibsə görünür;
    qırmızı label və ya saxlama xətası ilə də yoxlayın."""
    page = _sv_page()
    _sv_wait(page)
    root = page.locator("body").first
    if container_text:
        cand = (
            page.locator("section, form, aside, [role=dialog], [role=tabpanel], div")
            .filter(has=page.get_by_text(container_text, exact=False))
            .filter(has=page.locator("input:visible, textarea:visible, select:visible, [role=combobox]:visible"))
        )
        if cand.count() == 0:
            raise RuntimeError(f"'{container_text}' olan bölmə tapılmadı.")
        root = cand.last
    rows = root.evaluate(_FIELDS_JS)
    if not rows:
        return "Sahə tapılmadı."
    return "\n".join(" | ".join(r).rstrip(" |") for r in rows)


@mcp.tool()
@pw
@prod_readonly("write")
def sv_choose(label: str, value: str, exact: bool = False) -> str:
    """Angular/Kendo dropdown-da (native <select> deyil) `label` sahəsini açıb `value` seçir.
    Native <select> üçün mövcud select_option(frame=0) alətini istifadə edin."""
    page = _sv_page()
    res = _kendo_pick(page.main_frame, label, value, exact)
    _sv_wait(page)
    return res


@mcp.tool()
@pw
def sv_process_tasks(process_id: str, show_all: bool = True, max_rows: int = 100) -> str:
    """Workflow prosesinin tapşırıqlarını açıb oxuyur. process_id: '70000_870927' və ya '70000/870927'
    (CRM sorğusunda 'Show process details' açılan URL-dəki processInstance).
    show_all=True: 'Show All Tasks' açıqdırsa bütün tapşırıqlar (avtomatiklər də) görünür."""
    pid = process_id.replace("_", "/").strip("/")
    if not re.fullmatch(r"\d+/\d+", pid):
        raise RuntimeError("process_id formatı: 70000_870927")
    open_sunvizion.__wrapped__(path=f"/app/wfl/processes/process-tasks/{pid}")
    page = _sv_page()
    sw = page.get_by_role("switch", name="Show All Tasks")
    if sw.count():
        on = sw.first.get_attribute("aria-checked") == "true"
        if on != show_all:
            sw.first.click()
            _sv_wait(page)
    return sv_grid_read.__wrapped__(max_rows=max_rows, all_pages=True)


# ======================================================================
# API TESTING (yalnız PREPROD). Portal-ın öz XHR/fetch sorğuları qeydə alınır, eyni login olunmuş
# sessiya (cookie + Authorization/XSRF başlıqları) ilə birbaşa çağırılır və JSON test faylı işlədilir.
# PROD host-una (azt-bss.aztelekom.az) heç bir sorğu getmir: hər alət əvvəl _api_guard yoxlayır.
# ======================================================================
import csv
import json
from urllib.parse import urljoin, urlparse

API_DIR = os.getenv("PORTAL_API_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_tests")
API_ALLOWED_HOSTS = {h.strip().lower() for h in os.getenv(
    "API_ALLOWED_HOSTS", "ppwebportal.aztelekom.az,ppappwfl.aztelekom.az").split(",") if h.strip()}
# Nisbi path-lər (/20_0/...) Portal host-una gedir; workflow API-ləri üçün tam URL (https://ppappwfl...) verin
API_BASE = "https://" + os.getenv("API_BASE_HOST", "ppwebportal.aztelekom.az")
# Vəziyyəti dəyişən metod/endpoint adları: yalnız confirm=True ilə göndərilir (PREPROD-da da)
_RISKY_VERB = re.compile(
    r"^(?i:(save|delete|remove|finali[sz]e|activate|deactivate|accept|approve|reject|cancel|create|insert|"
    r"update|modify|submit|assign|reassign|release|terminate|abort|complete|close|start|execute|run|"
    r"import|upload|send|set|add|post|put|change|edit|lock|unlock|register|write|log|logout|logoff|signout|signin|login|logon))(?=[A-Z_\-.]|$)")


def _risky(path: str) -> bool:
    """Path seqmentlərindən biri vəziyyəti dəyişən felllə başlayırsa (SaveTask, DeleteX, start-process ...)."""
    return any(_RISKY_VERB.match(seg) for seg in urlparse(path).path.split("/") if seg)


_AUTH_HDR = re.compile(r"^(authorization|x-xsrf-token|x-csrf-token|requestverificationtoken|"
                       r"__requestverificationtoken|x-requested-with|x-.*token.*|x-.*session.*)$", re.I)
_SECRET_KEY = re.compile(r"pass(word)?|pwd|secret|token", re.I)

_api = {"rec": False, "filter": "", "log": deque(maxlen=1000), "auth": {}, "token": ""}
_TOKEN_KEYS = {"usertokenkey", "usertoken"}
TOKEN_PH = "{{userToken}}"


def _find_token(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _TOKEN_KEYS and isinstance(v, str) and v:
                return v
            t = _find_token(v)
            if t:
                return t
    if isinstance(obj, list):
        for v in obj:
            t = _find_token(v)
            if t:
                return t
    return ""


def _tokenize(raw: str) -> str:
    """Captured body -> test body: token dəyəri {{userToken}} yer tutucusu ilə əvəz olunur."""
    if not raw:
        return raw
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return _mask(raw)

    def walk(o):
        if isinstance(o, dict):
            return {k: (TOKEN_PH if str(k).lower() in _TOKEN_KEYS else
                        "***" if _SECRET_KEY.search(str(k)) else walk(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return json.dumps(walk(obj), ensure_ascii=False)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _api_guard(url: str = "") -> str:
    """Yalnız PREPROD: mühit PROD-dursa və ya URL icazəli host-da deyilsə dayandırır."""
    if _env["name"] != "PREPROD" or _is_prod():
        raise RuntimeError("API alətləri hələlik YALNIZ PREPROD üçündür (PROD-a sorğu göndərilmir). "
                           "open_portal(env='PREPROD') / open_sunvizion(env='PREPROD') ilə açın.")
    if not url:
        return url
    full = url if re.match(r"^https?://", url, re.I) else urljoin(API_BASE + "/", url.lstrip("/"))
    h = _host(full)
    if PROD_HOST in h or h not in API_ALLOWED_HOSTS:
        raise RuntimeError(f"Host icazəli deyil: {h}. İcazəli: {', '.join(sorted(API_ALLOWED_HOSTS))} "
                           "(API_ALLOWED_HOSTS).")
    return full


def _mask(text):
    """Body-də parol/token dəyərlərini gizlədir (JSON və form-data)."""
    if not text:
        return text
    try:
        obj = json.loads(text)

        def walk(o):
            if isinstance(o, dict):
                return {k: ("***" if _SECRET_KEY.search(str(k)) else walk(v)) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return o
        return json.dumps(walk(obj), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return re.sub(r"((?:pass(?:word)?|pwd|token|secret)[^=&]*=)[^&]*", r"\1***", text, flags=re.I)


def _api_record(resp):
    """_on_response-dan çağırılır: icazəli host-a gedən XHR/fetch sorğuları."""
    try:
        req = resp.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        h = _host(resp.url)
        if h not in API_ALLOWED_HOSTS:
            return
        # Son görünən auth/XSRF başlıqları: api_call bunları avtomatik əlavə edir
        for k, v in (req.headers or {}).items():
            if _AUTH_HDR.match(k):
                _api["auth"][k] = v
        try:
            tok = _find_token(json.loads(req.post_data or "null"))
            if tok:
                _api["token"] = tok
        except Exception:  # noqa: BLE001
            pass
        if not _api["rec"]:
            return
        if _api["filter"] and _api["filter"].lower() not in resp.url.lower():
            return
        _api["log"].append({"t": time.strftime("%H:%M:%S"), "method": req.method, "url": resp.url,
                            "status": resp.status, "req": req, "resp": resp,
                            "post": _mask(req.post_data or ""), "raw": req.post_data or ""})
    except Exception as e:  # noqa: BLE001
        _ev("api-record-error", str(e))


def _ms(req) -> int:
    try:
        t = req.timing or {}
        end = t.get("responseEnd", -1)
        return int(end) if end and end > 0 else -1
    except Exception:  # noqa: BLE001
        return -1


def _short(text: str, n: int = 3000) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + f"... [+{len(text) - n} simvol]"


def _pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        return text or ""


def _norm_path(url: str) -> str:
    """Endpoint kataloqu üçün: query atılır, rəqəmli/GUID seqmentlər {id} olur."""
    p = urlparse(url).path
    p = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/{id}", p, flags=re.I)
    return re.sub(r"/\d+(?=/|$)", "/{id}", p)


def _api_dir() -> str:
    os.makedirs(API_DIR, exist_ok=True)
    return os.path.abspath(API_DIR)


@mcp.tool()
@pw
def api_capture(action: str = "start", url_filter: str = "") -> str:
    """Portal-ın XHR/fetch sorğularını qeydə alır (YALNIZ PREPROD).
    action: 'start' (jurnalı təmizləyib yazmağa başlayır), 'stop', 'status'.
    url_filter: yalnız URL-ində bu mətn olan sorğular (məs. 'webapiservices').
    İstifadə: api_capture('start') -> UI-da axını keçin (open_sunvizion, sv_process_tasks ...) ->
    api_capture('stop') -> api_log / api_endpoints."""
    _api_guard()
    action = action.lower()
    if action == "start":
        _api["log"].clear()
        _api["rec"], _api["filter"] = True, url_filter
        return f"Qeyd başladı (host: {', '.join(sorted(API_ALLOWED_HOSTS))}; filtr: '{url_filter or '-'}')."
    if action == "stop":
        _api["rec"] = False
        return f"Qeyd dayandı. {len(_api['log'])} sorğu yazılıb. api_log / api_endpoints ilə baxın."
    return (f"Qeyd: {'açıq' if _api['rec'] else 'bağlı'}, sorğu: {len(_api['log'])}, filtr: "
            f"'{_api['filter'] or '-'}', yadda saxlanan auth başlıqları: {', '.join(_api['auth']) or 'yoxdur'}")


@mcp.tool()
@pw
def api_log(index: int = -1, text_filter: str = "", max_items: int = 60, body_chars: int = 3000) -> str:
    """Qeydə alınmış sorğular. index=-1: siyahı (№ | saat | metod | status | ms | URL).
    index=N: N-ci sorğunun tam detalı - request başlıqları (cookie-siz), request body, response body.
    text_filter: URL-də axtarış."""
    _api_guard()
    items = list(_api["log"])
    if index >= 0:
        if index >= len(items):
            raise RuntimeError(f"Sorğu #{index} yoxdur (cəmi {len(items)}).")
        it = items[index]
        req, resp = it["req"], it["resp"]
        hdrs = {k: ("***" if _AUTH_HDR.match(k) and k.lower() != "x-requested-with" else v)
                for k, v in (req.headers or {}).items() if k.lower() != "cookie"}
        try:
            body = resp.text()
        except Exception as e:  # noqa: BLE001
            body = f"(body oxunmadı: {e})"
        return "\n".join([
            f"#{index} {it['method']} {it['url']}",
            f"Status: {it['status']} | {_ms(req)} ms | content-type: {resp.headers.get('content-type', '')}",
            "Request headers: " + json.dumps(hdrs, ensure_ascii=False),
            "Request body:\n" + _short(_pretty(it["post"]), body_chars),
            "Response body:\n" + _short(_pretty(_mask(body)), body_chars),
        ])
    rows = [(i, it) for i, it in enumerate(items) if not text_filter or text_filter.lower() in it["url"].lower()]
    if not rows:
        return "Qeydə alınmış sorğu yoxdur (api_capture('start') edib UI-da axını keçin)."
    out = [f"{i:>3} | {it['t']} | {it['method']:<6} | {it['status']} | {_ms(it['req']):>5} ms | "
           f"{urlparse(it['url']).path}{('?' + urlparse(it['url']).query) if urlparse(it['url']).query else ''}"
           for i, it in rows[-max_items:]]
    return f"{len(rows)} sorğu (son {min(len(rows), max_items)}):\n" + "\n".join(out)


@mcp.tool()
@pw
def api_endpoints(save_cases: str = "") -> str:
    """Qeydə alınmış sorğulardan endpoint kataloqu: metod + path ({id} normallaşdırılıb), say, statuslar,
    orta/maks ms. Kataloq API_DIR/api_catalog.json-a yazılır.
    save_cases='fayl.json': hər endpoint üçün başlanğıc test case-ləri yaradır (müşahidə olunan status,
    max_ms = 3 x müşahidə olunan, min 3000) - sonra api_run_tests ilə işlədin."""
    _api_guard()
    cat = {}
    for it in _api["log"]:
        key = (it["method"], _norm_path(it["url"]))
        e = cat.setdefault(key, {"method": it["method"], "path": key[1], "count": 0, "statuses": set(),
                                 "ms": [], "example_url": it["url"], "example_body": _tokenize(it["raw"]),
                                 "error_null": None})
        e["count"] += 1
        e["statuses"].add(it["status"])
        if e["error_null"] is None and it["status"] < 400:
            try:
                j = json.loads(it["resp"].text())
                e["error_null"] = isinstance(j, dict) and "error" in j and j["error"] is None
            except Exception:  # noqa: BLE001
                e["error_null"] = False
        m = _ms(it["req"])
        if m > 0:
            e["ms"].append(m)
    if not cat:
        return "Kataloq boşdur: əvvəl api_capture('start') edib UI-da axını keçin."
    rows, data = [], []
    for e in sorted(cat.values(), key=lambda x: (x["path"], x["method"])):
        avg = int(sum(e["ms"]) / len(e["ms"])) if e["ms"] else -1
        mx = max(e["ms"]) if e["ms"] else -1
        risky = "⚠" if _risky(e["path"]) else ""
        rows.append(f"{e['method']:<6} {e['path']} | x{e['count']} | status {sorted(e['statuses'])} | "
                    f"avg {avg} ms, max {mx} ms {risky}")
        data.append({**{k: e[k] for k in ("method", "path", "count", "example_url", "example_body", "error_null")},
                     "statuses": sorted(e["statuses"]), "avg_ms": avg, "max_ms": mx, "risky": bool(risky)})
    d = _api_dir()
    with open(os.path.join(d, "api_catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    msg = [f"{len(data)} endpoint (⚠ = vəziyyəti dəyişə bilər, testdə confirm lazımdır):", *rows,
           f"Kataloq: {os.path.join(d, 'api_catalog.json')}"]
    if save_cases:
        cases = []
        for n, e in enumerate(data, 1):
            st = [s for s in e["statuses"] if s < 400] or e["statuses"]
            cases.append({"id": f"API-{n:03d}", "name": f"{e['method']} {e['path']}", "method": e["method"],
                          "url": e["example_url"], "body": e["example_body"] or "",
                          "expect_status": st[0], "expect_contains": [],
                          "expect_json": {"error": None} if e.get("error_null") else {},
                          "max_ms": max(3000, 3 * e["max_ms"]) if e["max_ms"] > 0 else 5000,
                          "no_session": False, "confirm": False, "skip": e["risky"]})
        p = save_cases if os.path.isabs(save_cases) else os.path.join(d, save_cases)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cases, fh, ensure_ascii=False, indent=1)
        msg.append(f"{len(cases)} başlanğıc case yazıldı: {p} (⚠ olanlar skip=true).")
    return "\n".join(msg)


def _parse_headers(headers) -> dict:
    if not headers:
        return {}
    if isinstance(headers, dict):
        return headers
    return json.loads(headers)


def _do_call(method: str, url: str, body="", headers=None, no_session: bool = False,
             timeout_ms: int = 30000, confirm: bool = False):
    """Bir API sorğusu. Qaytarır: (status, ms, content_type, text)."""
    full = _api_guard(url)
    method = (method or "GET").upper()
    path = urlparse(full).path
    if _risky(path) and not confirm:
        raise RuntimeError(f"'{path}' vəziyyəti dəyişə bilər - göndərilmədi. Göndərmək üçün confirm=True "
                           "(istifadəçinin açıq razılığı ilə).")
    hdrs = {} if no_session else dict(_api["auth"])
    hdrs.update(_parse_headers(headers))
    data = None
    if body not in (None, ""):
        data = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        if TOKEN_PH in data:
            if not no_session and not _api["token"]:
                raise RuntimeError("userToken yoxdur: əvvəl api_capture('start') edib Portal-da bir səhifə açın.")
            data = data.replace(TOKEN_PH, "INVALID-TOKEN" if no_session else _api["token"])
        if not any(k.lower() == "content-type" for k in hdrs):
            hdrs["Content-Type"] = "application/json" if data.lstrip()[:1] in "{[" else \
                "application/x-www-form-urlencoded"
    if no_session:
        rc = _s["pw"].request.new_context(ignore_https_errors=True)
    else:
        if not _s["ctx"]:
            raise RuntimeError("Brauzer açıq deyil: əvvəl open_sunvizion(env='PREPROD') ilə login olun.")
        rc = _s["ctx"].request
    t0 = time.perf_counter()
    try:
        r = rc.fetch(full, method=method, headers=hdrs, data=data, timeout=timeout_ms,
                     fail_on_status_code=False)
        ms = int((time.perf_counter() - t0) * 1000)
        try:
            text = r.text()
        except Exception:  # noqa: BLE001
            text = ""
        return r.status, ms, r.headers.get("content-type", ""), text, r.headers.get("location", "")
    finally:
        if no_session:
            rc.dispose()


@mcp.tool()
@pw
def api_call(method: str, url: str, body: str = "", headers: str = "", no_session: bool = False,
             confirm: bool = False, timeout_ms: int = 30000, body_chars: int = 4000) -> str:
    """API sorğusu göndərir (YALNIZ PREPROD, icazəli host). url: tam URL və ya path
    (məs. '/20_0/Portal/...' - PREPROD host-u əlavə olunur). body: JSON və ya form mətni.
    headers: JSON obyekt. Login olunmuş brauzer sessiyası (cookie) və son görünən Authorization/XSRF
    başlıqları avtomatik əlavə olunur. Body-dəki {{userToken}} cari sessiyanın baseContext.userData.userTokenKey
    dəyəri ilə əvəz olunur (no_session=True-da 'INVALID-TOKEN'). no_session=True: cookie/başlıqsız (avtorizasiya testi).
    Save/Delete/Finalize/Create... kimi endpoint-lər yalnız confirm=True ilə göndərilir."""
    status, ms, ctype, text, loc = _do_call(method, url, body, headers, no_session, timeout_ms, confirm)
    head = f"{method.upper()} {_api_guard(url)}\nStatus: {status} | {ms} ms | content-type: {ctype}"
    if loc:
        head += f" | Location: {loc}"
    return head + "\n" + _short(_pretty(_mask(text)), body_chars)


def _json_get(obj, path: str):
    """Sadə yol: 'data.items[0].name'."""
    cur = obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            cur = cur[part]
    return cur


def _check(case: dict, status: int, ms: int, text: str) -> list:
    errs = []
    exp = case.get("expect_status")
    if exp is not None:
        ok = status in exp if isinstance(exp, list) else status == int(exp)
        if not ok:
            errs.append(f"status {status} != {exp}")
    mx = case.get("max_ms")
    if mx and ms > int(mx):
        errs.append(f"{ms} ms > {mx} ms")
    for s in case.get("expect_contains") or []:
        if s not in text:
            errs.append(f"cavabda yoxdur: '{s}'")
    for s in case.get("expect_not_contains") or []:
        if s in text:
            errs.append(f"cavabda olmamalı idi: '{s}'")
    ej = case.get("expect_json") or {}
    if ej:
        try:
            obj = json.loads(text)
        except Exception:  # noqa: BLE001
            return errs + ["cavab JSON deyil"]
        for p, v in ej.items():
            try:
                got = _json_get(obj, p)
            except Exception:  # noqa: BLE001
                errs.append(f"JSON yolu yoxdur: {p}")
                continue
            if v == "*exists*":
                continue
            if got != v:
                errs.append(f"{p} = {got!r}, gözlənilən {v!r}")
    return errs


@mcp.tool()
@pw
def api_run_tests(file: str, only: str = "", out_name: str = "") -> str:
    """JSON test faylını işlədir (YALNIZ PREPROD). file: API_DIR-ə nisbi və ya tam yol.
    Case sahələri: id, name, method, url, body, headers, expect_status (int və ya siyahı),
    expect_contains [..], expect_not_contains [..], expect_json {"a.b[0].c": dəyər | "*exists*"},
    max_ms, no_session (avtorizasiyasız negativ test), confirm (risky endpoint), skip.
    only: vergüllə id-lər. Nəticə: API_DIR/<out_name|fayl>_results_<vaxt>.csv (Excel açır) + qısa cədvəl."""
    _api_guard()
    d = _api_dir()
    p = file if os.path.isabs(file) else os.path.join(d, file)
    with open(p, encoding="utf-8-sig") as fh:
        cases = json.load(fh)
    want = {x.strip() for x in only.split(",") if x.strip()}
    res, lines = [], []
    for c in cases:
        cid = c.get("id", "?")
        if want and cid not in want:
            continue
        if c.get("skip"):
            res.append({"id": cid, "name": c.get("name", ""), "result": "Skipped", "status": "", "ms": "",
                        "details": "skip=true"})
            continue
        try:
            status, ms, _ct, text, _loc = _do_call(c.get("method", "GET"), c["url"], c.get("body", ""),
                                                   c.get("headers"), bool(c.get("no_session")),
                                                   int(c.get("timeout_ms", 30000)), bool(c.get("confirm")))
            errs = _check(c, status, ms, text)
            r = {"id": cid, "name": c.get("name", ""), "result": "Fail" if errs else "Pass", "status": status,
                 "ms": ms, "details": "; ".join(errs), "response": _short(_mask(text), 1000)}
        except Exception as e:  # noqa: BLE001
            r = {"id": cid, "name": c.get("name", ""), "result": "Error", "status": "", "ms": "",
                 "details": str(e)[:300], "response": ""}
        res.append(r)
    if not res:
        return "Heç bir case işlənmədi (only filtri?)."
    base = out_name or os.path.splitext(os.path.basename(p))[0]
    outp = os.path.join(d, f"{base}_results_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with open(outp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "name", "result", "status", "ms", "details", "response"],
                           delimiter=";")
        w.writeheader()
        for r in res:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    cnt = {k: sum(1 for r in res if r["result"] == k) for k in ("Pass", "Fail", "Error", "Skipped")}
    for r in res:
        lines.append(f"{r['id']} | {r['result']} | {r['status']} | {r['ms']} ms | {r['name']}"
                     + (f" | {r['details']}" if r["details"] else ""))
    return (f"Pass {cnt['Pass']}, Fail {cnt['Fail']}, Error {cnt['Error']}, Skipped {cnt['Skipped']}\n"
            + "\n".join(lines) + f"\nNəticə: {outp}")


# ======================================================================
# RECIPE: icra olunmuş addımları saxlamaq və təkrar işlətmək (artifacts/recipes/<ad>.json, bir neçə KB)
# ======================================================================
_recipe_mark = {"i": 0, "name": ""}


def _recipe_path(name: str) -> str:
    safe = re.sub(r"[^\w\-. ]", "_", name).strip() or "recipe"
    return os.path.abspath(os.path.join(RECIPE_DIR, safe + ".json"))


def _fmt_step(i, st):
    args = ", ".join(f"{k}={v!r}" for k, v in st.get("args", {}).items())
    used = f"   # fallback: {st['used']}" if st.get("used") else ""
    return f"{i:>2}. {st['tool']}({args}){used}"


@mcp.tool()
@pw
def recipe_start(name: str = "") -> str:
    """Yeni test icrasının başlanğıcını qeyd edir: bundan sonrakı addımlar recipe_save ilə saxlanacaq.
    Hər test case-in əvvəlində çağırın (məs. name='WOM_TC-05')."""
    _recipe_mark["i"] = len(_steps)
    _recipe_mark["name"] = name
    return f"Qeyd başladı{(': ' + name) if name else ''}. Addımlar avtomatik yazılır."


@mcp.tool()
@pw
def recipe_save(name: str = "", note: str = "", include_failed: bool = False) -> str:
    """recipe_start-dan (yoxdursa sessiyanın əvvəlindən) bəri UĞURLU addımları artifacts/recipes/<name>.json
    faylına yazır. Uğursuz cəhdlər atılır (include_failed=True: 'failed_attempts' kimi ayrıca saxlanır,
    hansı yolun işləmədiyini xatırlamaq üçün). note: insan üçün qeyd (nəticə, diqqət ediləcək yer)."""
    name = name or _recipe_mark["name"] or time.strftime("run_%Y%m%d_%H%M%S")
    seg = _steps[_recipe_mark["i"]:]
    ok = [{"tool": s["tool"], "args": s["args"], **({"used": s["used"]} if s.get("used") else {})}
          for s in seg if s["ok"]]
    if not ok:
        return "Saxlanacaq uğurlu addım yoxdur."
    data = {"name": name, "saved": time.strftime("%Y-%m-%d %H:%M"), "env": _env.get("name", ""),
            "note": note, "steps": ok}
    if include_failed:
        data["failed_attempts"] = [{"tool": s["tool"], "args": s["args"], "error": s["result"][:150]}
                                   for s in seg if not s["ok"]]
    os.makedirs(RECIPE_DIR, exist_ok=True)
    p = _recipe_path(name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return (f"Saxlandı: {p} ({len(ok)} addım, {os.path.getsize(p)} bayt)\n"
            + "\n".join(_fmt_step(i + 1, s) for i, s in enumerate(ok)))


@mcp.tool()
@pw
def recipe_list() -> str:
    """Saxlanmış recipe-lərin siyahısı (ad | tarix | addım sayı | qeyd)."""
    if not os.path.isdir(RECIPE_DIR):
        return "Recipe yoxdur."
    out = []
    for fn_ in sorted(os.listdir(RECIPE_DIR)):
        if not fn_.endswith(".json") or fn_.startswith("_"):
            continue
        try:
            with open(os.path.join(RECIPE_DIR, fn_), encoding="utf-8") as fh:
                d = json.load(fh)
            out.append(f"{d.get('name', fn_[:-5])} | {d.get('saved', '')} | {len(d.get('steps', []))} addım | "
                       f"{(d.get('note') or '')[:80]}")
        except Exception:  # noqa: BLE001
            out.append(f"{fn_} | (oxunmadı)")
    return "\n".join(out) or "Recipe yoxdur."


@mcp.tool()
@pw
def recipe_show(name: str) -> str:
    """Recipe-nin addımlarını göstərir (test case-i icra etməzdən ƏVVƏL baxın: işləyən alət və parametrlər)."""
    p = _recipe_path(name)
    if not os.path.exists(p):
        return f"'{name}' recipe-si yoxdur. recipe_list ilə baxın."
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    lines = [f"{d.get('name')} ({d.get('saved')}) — {d.get('note') or ''}"]
    lines += [_fmt_step(i + 1, s) for i, s in enumerate(d.get("steps", []))]
    if d.get("failed_attempts"):
        lines.append("İşləməyən cəhdlər (təkrarlamayın):")
        lines += [f"  - {s['tool']}({s['args']}) -> {s['error']}" for s in d["failed_attempts"]]
    return "\n".join(lines)


@mcp.tool()
@pw
def recipe_run(name: str, stop_on_error: bool = True, allow_confirm: bool = False, shot_suffix: str = "") -> str:
    """Recipe addımlarını ardıcıl təkrar işlədir və hər addımın nəticəsini qaytarır.
    confirm=True olan (workflow vəziyyətini dəyişən) addımlar allow_confirm=True verilməsə ATLANIR.
    screenshot addımları shot_suffix ilə yeni adla çəkilir (default: _rerun_<vaxt>).
    Nəticələri (say, status, qrafik) Claude özü yoxlamalıdır: recipe yalnız naviqasiyanı təkrarlayır."""
    p = _recipe_path(name)
    if not os.path.exists(p):
        return f"'{name}' recipe-si yoxdur."
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    suffix = shot_suffix or time.strftime("_rerun_%H%M%S")
    out = []
    for i, st in enumerate(d.get("steps", []), 1):
        tool, args = st["tool"], dict(st.get("args", {}))
        fn_ = globals().get(tool)
        if fn_ is None or tool.startswith("recipe_"):
            out.append(f"{i}. {tool}: ATLANDI (alət yoxdur)")
            continue
        if args.get("confirm") and not allow_confirm:
            out.append(f"{i}. {tool}({args}): ATLANDI (confirm=True, allow_confirm verilməyib)")
            continue
        if tool == "screenshot":
            args["name"] = f"{args.get('name', 'shot')}{suffix}"
        inner = getattr(fn_, "__wrapped__", fn_)   # pw thread-indəyik: birbaşa daxili funksiya
        _s["used"] = ""
        t0 = time.time()
        try:
            r = inner(**args)
            txt = r[0] if isinstance(r, list) else str(r)
            _rec_step(tool, inspect.signature(inner), (), args, True, txt, t0)
            out.append(f"{i}. {tool}: OK {txt[:160]}" + (f" [fallback: {_s['used']}]" if _s.get("used") else ""))
        except Exception as e:  # noqa: BLE001
            _rec_step(tool, inspect.signature(inner), (), args, False, str(e), t0)
            out.append(f"{i}. {tool}: XƏTA {str(e).splitlines()[0][:200]}")
            if stop_on_error:
                out.append("Dayandırıldı (stop_on_error=True).")
                break
    return "\n".join(out)


@mcp.tool()
@pw
def learn_report(min_ms: int = 0, only_problems: bool = True, target: str = "", forget: bool = False) -> str:
    """Öyrənən element yaddaşı (src/state/element_memory.json): hər element üçün çağırış sayı, uğursuz sayı,
    orta/maks ms, işləyən klik üsulu, öyrənilmiş frame, son xəta.
    only_problems=True: yalnız YAVAŞ (orta >= min_ms, default PORTAL_SLOW_MS=3000) və ya ALINMAYAN elementlər.
    target: yalnız adında bu mətn olanlar. forget=True: target-ə uyğun qeydləri SİLİR (səhv öyrənilibsə).
    Kodda daimi düzəliş lazım olanda (məs. həmişə fallback-ə düşən element) bu hesabata baxın."""
    d = _mem_data()
    keys = [k for k in d if not target or target.lower() in k.lower()]
    if forget:
        if not target:
            return "forget=True üçün target verin (hamısını silmək üçün faylı əl ilə silin)."
        for k in keys:
            d.pop(k, None)
        _mem_save()
        return f"Silindi: {len(keys)} qeyd."
    lim = min_ms or SLOW_MS
    rows = []
    for k in keys:
        e = d[k]
        slow, bad = e.get("avg_ms", 0) >= lim, e.get("fail", 0) > 0
        fb = e.get("method") in ("force", "control")
        if only_problems and not (slow or bad or fb or e.get("frames")):
            continue
        flags = ",".join(x for x, c in (("YAVAŞ", slow), ("XƏTA", bad), ("FALLBACK", fb),
                                        ("FRAME", bool(e.get("frames")))) if c)
        rows.append((e.get("avg_ms", 0), f"{k} | {e.get('n')}x, {e.get('fail')} xəta | orta {e.get('avg_ms')} ms, "
                     f"maks {e.get('max_ms')} ms | {flags} | üsul={e.get('method', '')} "
                     f"{('frames=' + str(e['frames'])) if e.get('frames') else ''} "
                     f"{('| son xəta: ' + e['last_err']) if e.get('last_err') else ''}".rstrip()))
    if not rows:
        return f"Problem yoxdur ({len(d)} element yaddaşda)." if only_problems else "Yaddaş boşdur."
    rows.sort(key=lambda r: -r[0])
    return f"{len(rows)}/{len(d)} element (alət|hədəf|rol|nth):\n" + "\n".join(r[1] for r in rows[:80])


_NO_REC.add("learn_report")


# ======================================================================
# BATCH (28.10.2026): bir neçə addım BİR çağırışda. Hər addım adi alətin ÖZ daxili funksiyası ilə
# icra olunur, ona görə fallback-lər, PROD qoruması, confirm qaydası və jurnal eynən qalır.
# ======================================================================
_BATCH_DENY = {"portal_batch", "close_portal", "make_test_files", "api_run_tests", "api_capture",
               "recipe_start", "recipe_save", "recipe_list", "recipe_show", "recipe_run"}
_BATCH_EVENT_KINDS = ("dialog", "pageerror", "http", "requestfailed", "new_page", "console.error")
_NO_REC.add("portal_batch")   # batch-in özü yox, içindəki addımlar jurnala yazılır (recipe ikiqat olmasın)


def _batch_parse(steps):
    """steps: JSON siyahı (str və ya list). Addım: {"do": "<alət>", ...arqumentlər} və ya
    {"tool": "<alət>", "args": {...}}. Qaytarır: [(alət, inner_fn, args_dict)]. Heç nə icra etmir."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"steps JSON deyil: {e}") from e
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("steps boş olmayan siyahı olmalıdır.")
    plan, errors = [], []
    for i, st in enumerate(steps, 1):
        if not isinstance(st, dict):
            errors.append(f"{i}: obyekt deyil")
            continue
        st = dict(st)
        tool = st.pop("do", None) or st.pop("tool", None)
        args = st.pop("args", None)
        args = dict(args) if isinstance(args, dict) else {}
        args.update(st)
        if tool in ("wait", "expect_text"):
            plan.append((tool, None, args))
            continue
        if not tool or tool in _BATCH_DENY or tool.startswith("_"):
            errors.append(f"{i}: '{tool}' batch-də icazəli deyil")
            continue
        fn_ = globals().get(tool)
        inner = getattr(fn_, "__wrapped__", None)   # pw-nin içi: prod_readonly qoruması daxildir
        if inner is None:
            errors.append(f"{i}: '{tool}' alət deyil")
            continue
        try:
            inspect.signature(inner).bind(**args)
        except TypeError as e:
            errors.append(f"{i}: {tool} arqumentləri səhvdir ({e})")
            continue
        plan.append((tool, inner, args))
    if errors:
        raise RuntimeError("Batch İCRA OLUNMADI (heç bir addım), plan səhvləri:\n" + "\n".join(errors))
    return plan


def _batch_expect(text: str, timeout_ms: int = 5000, absent: bool = False) -> str:
    """Mətn (bütün frame-lərdə) görünənə və ya absent=True olanda yox olana qədər gözləyir."""
    page = _page()
    deadline = time.time() + max(0, int(timeout_ms)) / 1000
    while True:
        found = False
        for f in page.frames:
            try:
                if _visible(f.get_by_text(text)):
                    found = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if found != absent:
            return f"'{text}' {'yoxdur' if absent else 'görünür'}"
        if time.time() >= deadline:
            raise RuntimeError(f"Yoxlama alınmadı: '{text}' {'hələ görünür' if absent else 'görünmür'} "
                               f"({timeout_ms} ms)")
        time.sleep(0.25)


expect_text = _batch_expect   # recipe_run batch-dən saxlanmış expect_text addımlarını da işlədə bilsin


def _batch_new_events(mark):
    new = []
    for e in reversed(_events):
        if mark is not None and e is mark:
            break
        new.append(e)
    new.reverse()
    keep = [e for e in new if e[1].startswith(_BATCH_EVENT_KINDS)
            and not (e[1].startswith("http") and e[1][5:8].isdigit() and int(e[1][5:8]) < 400)]
    return keep


@mcp.tool()
@pw
def portal_batch(steps: Union[str, list], stop_on_error: bool = True, final_screenshot: str = "",
                 max_seconds: int = 40):
    """Bir neçə portal addımını BİR çağırışda ardıcıl icra edir (UAT addımları üçün: 5-10 çağırış -> 1).
    steps: siyahı, hər addım {"do": "<alət adı>", ...həmin alətin arqumentləri}. Məs.:
      [{"do":"click_text","text":"Work Orders"},
       {"do":"sv_choose","label":"Status","value":"New"},
       {"do":"expect_text","text":"1 - 20 of"},
       {"do":"sv_grid_read","max_rows":20},
       {"do":"screenshot","name":"tc04_05_status_new"}]
    Əlavə addımlar: {"do":"wait","ms":1000} (maks 10000), {"do":"expect_text","text":"...","timeout_ms":5000,
      "absent":false} - mətn görünmürsə XƏTA (nəticəni yoxlamaq üçün).
    Qaydalar adi alətlərlə EYNİDİR: hər addım həmin alətin öz funksiyası ilə gedir (fallback, PROD yalnız oxuma,
    sv_toolbar-da confirm=True olmadan workflow düyməsi basılmır), hər addım _last_session.json-a yazılır.
    Əvvəlcə BÜTÜN addımlar yoxlanır (alət adı, arqumentlər); səhv varsa heç biri icra olunmur.
    stop_on_error=True: ilk xətada dayanır və avtomatik screenshot qaytarır (batch_error_<vaxt>).
    final_screenshot: ad verilsə sonda screenshot çəkilir və şəkil qaytarılır.
    max_seconds (default 40, maks 50): bu vaxt keçəndən sonra yeni addıma başlanmır (bridge ~60 s-də kəsir).
    Cavabda: hər addımın nəticəsi + batch zamanı yaranan dialoq / JS xətası / HTTP 4xx-5xx hadisələri."""
    plan = _batch_parse(steps)
    limit = max(5, min(int(max_seconds or 40), 50))
    t_start = time.time()
    mark = _events[-1] if _events else None
    out, failed, done = [], False, 0
    for i, (tool, inner, args) in enumerate(plan, 1):
        if time.time() - t_start > limit:
            out.append(f"VAXT LİMİTİ ({limit} s): {i}-{len(plan)} addımlar İCRA OLUNMADI, növbəti batch-də davam edin.")
            break
        _s["used"] = ""
        t0 = time.time()
        try:
            if tool == "wait":
                ms = max(0, min(int(args.get("ms", 1000)), 10000))
                time.sleep(ms / 1000)
                txt = f"{ms} ms gözlənildi"
            elif tool == "expect_text":
                txt = _batch_expect(str(args.get("text", "")), int(args.get("timeout_ms", 5000)),
                                    bool(args.get("absent", False)))
                _rec_step("expect_text", inspect.signature(_batch_expect), (), {
                    k: v for k, v in args.items() if k in ("text", "timeout_ms", "absent")}, True, txt, t0)
            else:
                r = inner(**args)
                if isinstance(r, list):          # screenshot: [path, Image] -> yalnız yol
                    r = r[0]
                txt = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, default=str)
                _rec_step(tool, inspect.signature(inner), (), args, True, txt, t0)
            ms_ = int((time.time() - t0) * 1000)
            fb = f" [fallback: {_s['used']}]" if _s.get("used") else ""
            # oxuma alətlərinin (sv_grid_read, read_text, sv_fields ...) nəticəsi tam lazımdır
            cut = 6000 if tool in ("sv_grid_read", "read_text", "sv_fields", "sv_process_tasks",
                                   "inspect_page", "check_layout", "api_call") else 400
            out.append(f"{i}. {tool}: OK ({ms_} ms) {txt[:cut]}{fb}")
            done += 1
        except Exception as e:  # noqa: BLE001
            if inner is not None:
                _rec_step(tool, inspect.signature(inner), (), args, False, str(e), t0)
            elif tool == "expect_text":
                _rec_step("expect_text", inspect.signature(_batch_expect), (), {
                    k: v for k, v in args.items() if k in ("text", "timeout_ms", "absent")}, False, str(e), t0)
            out.append(f"{i}. {tool}({args}): XƏTA {str(e).splitlines()[0][:300]}")
            failed = True
            if stop_on_error:
                rest = len(plan) - i
                if rest:
                    out.append(f"Dayandırıldı: qalan {rest} addım icra olunmadı.")
                break
    try:
        _page().evaluate("1")   # gecikmiş brauzer hadisələrini (JS xətası, dialoq) bu batch-ə daxil et
    except Exception:  # noqa: BLE001
        pass
    ev = _batch_new_events(mark)
    head = (f"Batch: {done}/{len(plan)} addım OK, {round(time.time() - t_start, 1)} s"
            + (" — XƏTA VAR" if failed else ""))
    lines = [head] + out
    if ev:
        lines.append("Hadisələr (batch zamanı):")
        lines += [f"  [{t}] {k}: {m}" for t, k, m in ev[-15:]]
    text = "\n".join(lines)
    shot = final_screenshot or (time.strftime("batch_error_%H%M%S") if failed else "")
    if shot and _s.get("page"):
        try:
            os.makedirs(SHOT_DIR, exist_ok=True)
            path = os.path.abspath(os.path.join(SHOT_DIR, f"{shot}.png"))
            data = _page().screenshot(path=path)
            return [text + f"\nScreenshot: {path}", Image(data=data, format="png")]
        except Exception as e:  # noqa: BLE001
            text += f"\n(screenshot alınmadı: {e})"
    return text


if __name__ == "__main__":
    mcp.run()