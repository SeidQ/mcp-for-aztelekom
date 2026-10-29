"""Parolları claude_desktop_config.json-dan secrets.env-ə köçürür.

- portal-ui / tfs-defects / sv-desktop girişlərinin env bölməsindəki gizli dəyərləri
  (PASS, PASSWORD, PAT, TOKEN, SECRET və uyğun USER) secrets.env-ə yazır;
- həmin dəyərləri konfiqurasiyadan silir (əvvəl backup götürür);
- secrets.env-də artıq olan dəyərlərə toxunmur (məs. PORTAL_PASS).
Dəyərlərin özü ekrana və loqa yazılmır, yalnız adları.
"""
import datetime
import glob
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets.env")
LOG = os.path.join(HERE, "migrate_secrets.log")
SERVERS = ("portal-ui", "tfs-defects", "sv-desktop")
SECRET_KEY = re.compile(r"(PASS|PASSWORD|PAT|TOKEN|SECRET)$|^(TFS|PORTAL|SV_DESKTOP)_USER$", re.I)
_lines = []


def log(msg):
    print(msg)
    _lines.append(msg)


def read_secrets():
    data, order = {}, []
    if os.path.exists(SECRETS):
        with open(SECRETS, encoding="utf-8-sig") as fh:
            for line in fh:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    data[k.strip()] = v.strip()
                    order.append(k.strip())
    return data, order


def config_paths():
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    cands = [os.path.join(appdata, "Claude", "claude_desktop_config.json")]
    cands += glob.glob(os.path.join(local, "Packages", "Claude*", "LocalCache", "Roaming", "Claude",
                                    "claude_desktop_config.json"))
    return [p for p in cands if os.path.exists(p)]


def main():
    secrets, _ = read_secrets()
    added = {}
    for path in config_paths():
        with open(path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        changed = False
        for name in SERVERS:
            env = (cfg.get("mcpServers", {}).get(name) or {}).get("env") or {}
            for key in [k for k in env if SECRET_KEY.search(k)]:
                val = env.pop(key)
                changed = True
                if key in secrets or key in added:
                    log(f"{name}: {key} konfiqurasiyadan silindi (secrets.env-dəki dəyər qalır)")
                else:
                    added[key] = val
                    log(f"{name}: {key} secrets.env-ə köçürüldü və konfiqurasiyadan silindi")
        if changed:
            bak = path + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, bak)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2, ensure_ascii=False)
            log(f"Konfiqurasiya yeniləndi: {path} (backup: {os.path.basename(bak)})")
            log("  QEYD: backup faylında köhnə parollar var - yoxladıqdan sonra silin.")
        else:
            log(f"Konfiqurasiyada köçürüləcək parol yoxdur: {path}")
    if added:
        with open(SECRETS, "a", encoding="utf-8") as fh:
            fh.write("\n# TFS (tfs-defects) - Claude konfiqurasiyasından köçürülüb\n")
            for k, v in added.items():
                fh.write(f"{k}={v}\n")
    final, _ = read_secrets()
    log("secrets.env-dəki açarlar: " + ", ".join(final) if final else "secrets.env boşdur")
    log("HAZIRDIR. Claude desktop-u tam bağlayıb (tray -> Quit) yenidən açın.")
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
