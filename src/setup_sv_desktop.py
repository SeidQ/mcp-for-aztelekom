"""sv-desktop MCP serverini qurur:
1) requirements-desktop.txt paketlərini portal-ui ilə eyni Python-a quraşdırır;
2) claude_desktop_config.json-a "sv-desktop" girişini əlavə edir (əvvəl backup götürür).
Parol konfiqurasiyaya yazılmır: serverlər onu secrets.env faylından oxuyur.
Nəticə setup_sv_desktop.log faylına da yazılır.
"""
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "setup_sv_desktop.log")
_lines = []


def log(msg):
    print(msg)
    _lines.append(msg)


def config_paths():
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    cands = [os.path.join(appdata, "Claude", "claude_desktop_config.json")]
    cands += glob.glob(os.path.join(local, "Packages", "Claude*", "LocalCache", "Roaming", "Claude",
                                    "claude_desktop_config.json"))
    return [p for p in cands if os.path.exists(p)]


def main():
    paths = config_paths()
    if not paths:
        log("XƏTA: claude_desktop_config.json tapılmadı.")
        return 1
    ref_cmd = None
    for path in paths:
        with open(path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        servers = cfg.setdefault("mcpServers", {})
        portal = servers.get("portal-ui") or next(iter(servers.values()), {})
        cmd = portal.get("command") or sys.executable
        ref_cmd = ref_cmd or (cmd, portal.get("args", []))
        entry = {
            "command": cmd,
            "args": [os.path.join(HERE, "desktop_mcp_server.py")],
            "env": {"SV_DESKTOP_SHOT_DIR": os.path.join(HERE, "..", "artifacts", "shots")},
        }
        if portal.get("cwd"):
            entry["cwd"] = portal["cwd"]
        if servers.get("sv-desktop") == entry:
            log(f"Konfiqurasiya artıq hazırdır: {path}")
            continue
        bak = path + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, bak)
        servers["sv-desktop"] = entry
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        log(f"sv-desktop əlavə olundu: {path}  (backup: {os.path.basename(bak)})")
        log(f"  command: {cmd}")

    # pip: portal-ui hansı Python ilə işləyirsə, onunla
    cmd, args = ref_cmd
    if os.path.basename(cmd).lower().startswith(("python", "py")):
        py = [cmd]
    elif os.path.basename(cmd).lower() == "uv":
        py = None
    else:
        py = [sys.executable]
    if py is None:
        log("portal-ui 'uv' ilə işləyir: paketlər server ilk açılanda uv ilə quraşdırılmalıdır.")
        return 0
    req = os.path.join(HERE, "requirements-desktop.txt")
    log(f"Paketlər quraşdırılır: {' '.join(py)} -m pip install -r {req}")
    r = subprocess.run(py + ["-m", "pip", "install", "--disable-pip-version-check", "-r", req],
                       capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
    for t in tail:
        log("  " + t)
    if r.returncode != 0:
        log(f"XƏTA: pip uğursuz oldu (kod {r.returncode}).")
        return r.returncode
    chk = subprocess.run(py + ["-c", "import pywinauto, comtypes, psutil, PIL, mcp; print('import OK')"],
                         capture_output=True, text=True)
    log("Yoxlama: " + (chk.stdout.strip() or chk.stderr.strip()[-300:]))
    log("HAZIRDIR. Claude desktop-u tam bağlayıb yenidən açın.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # noqa: BLE001
        log(f"XƏTA: {type(e).__name__}: {e}")
        code = 1
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")
    sys.exit(code)
