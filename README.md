# mcp-for-aztelekom

A set of [Model Context Protocol (MCP)](https://modelcontextprotocol.io) servers and supporting
tooling used to automate TFS/Azure DevOps defect management, the Aztelekom web portal, and the
SunVizion desktop applications. The servers are consumed by MCP clients such as Claude Desktop and
Kiro; each server exposes tools the client can call to read data, drive the UI, and capture evidence
screenshots.

## MCP servers

| Server (Claude / Kiro key) | Purpose | Entry point |
| --- | --- | --- |
| `tfs-defects` / `tfs` | TFS / Azure DevOps defect management: query, create, update and validate work items. | `src\tfs_mcp_server.py` |
| `portal-ui` | Web portal UI automation (Playwright) against the Aztelekom BSS ContactCenter portal, including API test suites. | `src\portal_mcp_server.py` |
| `sv-desktop` | SunVizion desktop (WPF) automation via Windows UI Automation. | `src\desktop_mcp_server.py` |
| `testrail` | TestRail test cases: list, read, create and update (write tools are dry-run by default). | `src\testrail_mcp_server.py` |

## Supporting tooling

- `src\defect_monitor.py` — scheduled defect validation that compares new defects against the bug
  template and notifies authors over Outlook mail / Teams. Driven by the scripts in `scripts\`.
- `src\defect_checks.py` — validation rules used by the TFS server and the monitor (reads
  `src\config\*.json`).
- `src\migrate_secrets.py` — moves secret values out of the Claude config into `src\secrets.env`.
- `src\setup_sv_desktop.py` — installs desktop requirements and registers the `sv-desktop` entry in
  the Claude config.

## Folder layout

```
mcp-for-aztelekom\
├── README.md
├── .gitignore
├── requirements.txt
├── requirements-desktop.txt
├── setup.bat                  first-time install (venv, packages, config)
├── run.bat                    launcher used by Claude: git pull + start server
├── src\                       MCP servers + tooling (run location)
│   ├── tfs_mcp_server.py
│   ├── portal_mcp_server.py
│   ├── desktop_mcp_server.py
│   ├── defect_monitor.py
│   ├── defect_checks.py
│   ├── migrate_secrets.py
│   ├── setup_sv_desktop.py
│   ├── secrets.env            secrets — never commit
│   ├── secrets.env.example    template (no real values)
│   ├── config\                bug template, contacts, environments, severity, fields, rules
│   ├── api_tests\             portal API test suites
│   └── state\                 monitor/validation state
├── scripts\                   .bat launchers (Task Scheduler + manual runs)
└── artifacts\
    └── shots\                 screenshots captured by the servers
```

All runtime paths (`config\`, `state\`, `api_tests\`, `secrets.env`) resolve relative to the server
files in `src\`, so the servers work regardless of the current working directory. Screenshot folders
default to `artifacts\shots\` and can be overridden with the `*_SCREENSHOT_DIR` / `*_SHOT_DIR`
environment variables.

## Quick install (team members)

Requirements: Windows, [Git](https://git-scm.com), Python 3.11+ (on PATH), Kiro, access to the
Aztelekom network/VPN. For `sv-desktop` the SunVizion desktop client must also be installed.

```powershell
git clone https://github.com/SeidQ/mcp-for-aztelekom.git
cd mcp-for-aztelekom
setup.bat
```

1. `setup.bat` (run once) creates `.venv`, installs the packages and Playwright Chromium, creates
   `src\secrets.env` from `src\secrets.env.example`, and registers the four servers (`tfs`,
   `portal-ui`, `testrail`, `sv-desktop`) in `%USERPROFILE%\.kiro\settings\mcp.json`.
2. Fill in your credentials in `src\secrets.env` (`PORTAL_*`, `TFS_*`, `TESTRAIL_*`) and save.
3. In Kiro open this folder (File > Open Folder), so the rules in `.kiro\steering\` load, then restart
   Kiro or reconnect the servers in the MCP panel.

Existing entries in `mcp.json` keep their own settings (`autoApprove`, `timeout`); only `command` and
`args` are pointed at `run.bat`. New entries get `timeout` 120000 and no `autoApprove`, so Kiro asks
before each tool call until you add `autoApprove` yourself. If Claude desktop is installed, its config
is updated too.

### Automatic updates

The Kiro config does not start Python directly; it starts `run.bat portal|tfs|desktop|testrail`. Every
time Kiro starts the servers, `run.bat` runs `git pull --ff-only`, reinstalls packages only if
`requirements*.txt` changed, and then starts the server. So after a push to `main`, each user gets the
new code the next time Kiro is restarted - nothing to run by hand.

- If the pull fails (no VPN, local changes) the server still starts with the current version.
- Details are logged to `.update.log` (git-ignored).
- To pin a machine to its current version, create an empty file named `.no-autoupdate` in the repo folder.
- Work on a `dev` branch and merge to `main` only after checking; users follow `main`.

## Manual install

```powershell
pip install -r requirements.txt
# for the sv-desktop server only:
pip install -r requirements-desktop.txt
```

## Running

The MCP servers are normally launched by the MCP client (see Configuration). To run a server by hand:

```powershell
python src\tfs_mcp_server.py
python src\portal_mcp_server.py
python src\desktop_mcp_server.py
```

The defect monitor runs through the scripts in `scripts\`:

- `scripts\run_monitor_dryrun.bat` — dry run: writes `src\defect_monitor_preview.html`, sends nothing.
- `scripts\run_monitor_test_mail.bat` — sends all mail only to the `MONITOR_ME` address.
- `scripts\run_monitor.bat` — the normal run invoked by Task Scheduler.
- `scripts\install_monitor_task.bat` — registers the weekday 16:30 scheduled task.
- `scripts\migrate_secrets.bat`, `scripts\setup_sv_desktop.bat` — tooling helpers.

## Configuration

MCP client configs point at the server entry points in `src\`:

- Claude Desktop: `C:\Users\<user>\AppData\Roaming\Claude\claude_desktop_config.json`
- Kiro: `C:\Users\<user>\.kiro\settings\mcp.json`

## Secrets

Secret values live in `src\secrets.env` and are read by the servers at runtime. This file is
git-ignored and must never be committed. It holds keys such as `TFS_USER`, `TFS_PASSWORD`,
`PORTAL_USER`, `PORTAL_PASS`, and `TEAMS_WEBHOOK_URL` (key names only — never store or share the
values in the repo). A template is in `src\secrets.env.example`.
