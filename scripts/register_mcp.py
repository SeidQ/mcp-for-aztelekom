"""MCP serverlərini run.bat vasitəsilə Kiro (və varsa Claude desktop) konfiqurasiyasına yazır.

- Kiro: %USERPROFILE%\\.kiro\\settings\\mcp.json (yoxdursa yaradılır). Server adları: tfs, portal-ui,
  testrail, sv-desktop.
- Claude desktop: yalnız config faylı artıq mövcuddursa yenilənir (yaradılmır).
- Mövcud başqa serverlərə toxunmur; eyni adlı girişdə yalnız command/args dəyişir, istifadəçinin
  autoApprove/timeout kimi sahələri qalır. Dəyişiklikdən əvvəl backup götürür.
- Parollar konfiqurasiyaya yazılmır: serverlər onları src\\secrets.env-dən oxuyur.
"""
import datetime
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "run.bat")

# run.bat arqumenti -> Kiro server adı / Claude server adı
KIRO_SERVERS = {"tfs": "tfs", "portal": "portal-ui", "testrail": "testrail", "desktop": "sv-desktop"}
CLAUDE_SERVERS = {"tfs": "tfs-defects", "portal": "portal-ui", "testrail": "testrail", "desktop": "sv-desktop"}
# Yeni Kiro girişləri üçün default sahələr (mövcud girişdə dəyişdirilmir)
KIRO_DEFAULTS = {"timeout": 120000}


def kiro_path():
    return os.path.join(os.path.expanduser("~"), ".kiro", "settings", "mcp.json")


def claude_paths():
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    cands = [os.path.join(appdata, "Claude", "claude_desktop_config.json")]
    cands += glob.glob(os.path.join(local, "Packages", "Claude*", "LocalCache", "Roaming", "Claude",
                                    "claude_desktop_config.json"))
    return [p for p in cands if os.path.exists(p)]


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read().strip()
    return json.loads(text) if text else {}


def update(path, names, defaults):
    cfg = load(path)
    servers = cfg.setdefault("mcpServers", {})
    changed = False
    for arg, name in names.items():
        cur = servers.get(name)
        if cur is None:
            servers[name] = {"command": RUN, "args": [arg], **defaults}
            changed = True
        elif cur.get("command") != RUN or cur.get("args") != [arg]:
            cur["command"] = RUN
            cur["args"] = [arg]
            changed = True
    if not changed:
        print(f"Artıq hazırdır: {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        bak = path + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, bak)
        print(f"  backup: {bak}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    print(f"Yeniləndi: {path}")


def main():
    update(kiro_path(), KIRO_SERVERS, KIRO_DEFAULTS)
    for path in claude_paths():
        update(path, CLAUDE_SERVERS, {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
