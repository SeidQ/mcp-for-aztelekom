"""Claude desktop konfiqurasiyasına 3 serveri run.bat vasitəsilə yazır.

- Mövcud başqa serverlərə toxunmur; dəyişiklikdən əvvəl backup götürür.
- Eyni adlı köhnə girişi (məs. birbaşa python ilə) run.bat girişi ilə əvəz edir.
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
SERVERS = {"portal-ui": "portal", "tfs-defects": "tfs", "sv-desktop": "desktop"}


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
        # config hələ yoxdursa (Claude heç açılmayıb) - standart yerdə yarat
        default = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
        os.makedirs(os.path.dirname(default), exist_ok=True)
        with open(default, "w", encoding="utf-8") as fh:
            fh.write("{}")
        paths = [default]
    for path in paths:
        with open(path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        servers = cfg.setdefault("mcpServers", {})
        changed = False
        for name, arg in SERVERS.items():
            entry = {"command": RUN, "args": [arg]}
            if servers.get(name) != entry:
                servers[name] = entry
                changed = True
        if changed:
            bak = path + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, bak)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2, ensure_ascii=False)
            print(f"Yeniləndi: {path}\n  backup: {os.path.basename(bak)} (köhnə parollar ola bilər - yoxlayıb silin)")
        else:
            print(f"Artıq hazırdır: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
