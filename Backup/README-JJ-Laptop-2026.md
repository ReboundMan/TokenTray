# TokenTray

A lightweight Windows **system tray app** that shows your live **GitHub Copilot CLI** token usage, parsed from local telemetry logs. No network calls, no extra accounts — just a number in your tray and a click-away breakdown.

![TokenTray screenshot](assets/screenshot.png)

## What it does

- **Tray icon** shows today's total tokens at a glance (`6.9M`, `124k`, `0`, …)
- **Hover** for a tooltip with turn / session counts and last refresh time
- **Click** for a popup with:
  - **Today** tab: today's totals broken down into Uncached input / Output / Cached input / Sessions / Turns, plus a stacked **7-day** bar chart
  - **History** tab *(Advanced)*: Today / This week / This month / All-time rollups persisted across CLI log rotation
- **Auto-refresh** every 2 minutes; manual **Refresh** button in the popup
- **Auto-start at login** (optional, one command)

### History (Advanced tier)

The History tab persists per-event token usage to a local SQLite file at
`%LOCALAPPDATA%\TokenTray\history.db` so the Day / Week / Month / All-time
rollups survive the Copilot CLI rotating its raw log files.

- **Free 60-day trial**: recording is on automatically for the first 60 days
  after you first run a TokenTray build that includes this feature.
- **After the trial**: recording pauses unless you tick
  *Settings → Advanced history (record locally)*. Re-enabling it after the
  trial requires a one-time honor-system "coffee" unlock — see
  [Supporting the project](#supporting-the-project) below. Any rows captured
  during the trial remain queryable forever.
- **Privacy**: history never leaves your machine — no network calls, no
  account, no telemetry. Uncheck the toggle at any time to opt out of
  further recording (including during the trial).

Scope is local Copilot **CLI only** (mirrors the "Agency" usage in the Microsoft IT report) plus Agency-wrapped sessions. It does **not** include the IDE Copilot, M365 Copilot, or cloud Coding Agent — those emit telemetry elsewhere.

> **Clawpilot (Electron desktop wrapper):** Earlier builds were thought to write
> no token telemetry locally. As of Clawpilot **5.1.2** (verified May 2026) that
> is no longer true: Clawpilot spawns a Copilot **backend** session whose
> `assistant_usage` telemetry lands in `~/.copilot/logs/process-*.log` alongside
> the standalone CLI's, keyed by the backend session id. Clawpilot's own session
> bodies live encrypted under `~/.copilot/m-sessions/` (unreadable), but the
> plaintext `~/.copilot/m-diagnostics.jsonl` maps each Clawpilot session to its
> backend id, which lets us attribute that telemetry to Clawpilot exactly. See
> [`docs/specs/2026-05-clawpilot-usage-attribution.md`](docs/specs/2026-05-clawpilot-usage-attribution.md)
> for the measurement model.

### Supporting the project

TokenTray is free, open-source, and ad-free. Local recording during the
60-day trial is unconditional. After the trial, continuing to record new
events into the local history database is gated by a one-time
[Buy Me a Coffee](https://buymeacoffee.com/reboundman) tip — there's no
account, no license key, and no telemetry. The honor-system flag is stored
locally in your history database.

If you reinstall or wipe state, *Settings → Restore supporter status* flips
the flag back without re-donating. If you'd rather not be reminded, the
startup nag has a "Don't show again" checkbox and otherwise appears at most
once every 21 days (and never during the free trial).

---

## Install

### Option 1: Run the installer (recommended)

1. Grab the latest `TokenTray-Setup-vX.Y.Z.exe` from the [Releases](https://github.com/jeffjame_microsoft/TokenTray/releases) page.
2. Double-click. Click **Next** through the wizard. On the *Startup* page you can opt in to launching TokenTray automatically at sign-in.
3. The app launches immediately and lives in your system tray. You can manage it later from **Add or Remove Programs** like any normal Windows app.

The installer is a per-user install (no admin required), drops files into `%LOCALAPPDATA%\Programs\TokenTray`, creates a Start Menu shortcut, and registers a proper uninstaller.

### Option 2: Portable zip

Prefer not to install? Download `TokenTray-vX.Y.Z-win64.zip` from the same Releases page, extract it anywhere, and double-click `TokenTray.exe` inside the extracted folder.

> ⚠️ **Windows SmartScreen** may warn the first time you launch the installer or the portable `.exe` because the binary is not code-signed. Click **More info → Run anyway**. The `.sha256.txt` file alongside each asset lets you verify integrity if you want.

> ℹ️ **Why no single onefile `.exe`?** PyInstaller's onefile mode extracts DLLs to `%TEMP%` on launch, where Windows Defender's real-time protection rewrites them and trips Windows' code-integrity check (`STATUS_INVALID_IMAGE_HASH` / "Bad Image"). The installer and the portable zip both unpack to disk once, then run cleanly.

### Option 3: Run from source (developers)

Requirements: Python 3.11+ on Windows.

```powershell
git clone https://github.com/jeffjame_microsoft/TokenTray.git
cd TokenTray
py -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\pythonw run.pyw          # run once
.\.venv\Scripts\python install_startup.py # autostart at login
```

---

## Usage

| Action | Result |
|---|---|
| Left-click the tray icon | Open the details popup (today's totals + 7-day chart) |
| Right-click the tray icon | Menu: Show details / Refresh now / Quit |
| Hover the tray icon | Tooltip with today total + turn/session counts |

CLI flags (work for both the `.exe` and `python tray_app.py`):

```text
--install-startup       Add a Startup-folder shortcut for auto-launch
--uninstall-startup     Remove it
--version               Print version and exit
```

---

## How it works

The unified parser `tokentray.parsers.iter_all_events()` reads token usage
from every known local source and de-duplicates by `session_id`:

- **`~/.copilot/logs/*.log`** — Copilot CLI **≤ 1.0.53** wrote
  `[Telemetry] cli.telemetry:` blocks where `kind == "assistant_usage"`.
- **`~/.copilot/session-state/<id>/events.jsonl`** — Copilot CLI **1.0.54+**
  (shipped with the Claude Opus 4.8 rollout) **stopped writing those process
  logs** and records usage here instead. The `session.shutdown` event carries
  the authoritative per-model rollup
  (`modelMetrics.<model>.usage.{inputTokens, outputTokens, cacheReadTokens,
  cacheWriteTokens}`); active sessions surface per-turn output-only estimates
  until they close. This covers both standalone CLI and Agency-wrapped
  sessions (`producer` = `copilot-agent` / `agency`).
- **`~/.agency/logs/session_*/`** — older Agency builds that kept their own
  event stream / captured process log.

Because the CLI's `session_id` is shared across these sources (a session can
appear in more than one), the process-log / Agency parsers win and their
session_ids are passed to the session-state parser so the same session is
never counted twice. From each event the parser extracts:

- ISO timestamp
- `session_id`
- `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens`

The CLI emits `input_tokens` as "new + cache-write tokens billed at base rate" (cache-write is a subset of input). So the displayed total is:

```
Total = cached_in + input + output
```

…which matches the Microsoft IT usage-report breakdown.

---

## Building from source

> The build venv must use **Python 3.12** (not 3.14). PyInstaller's `--onefile` mode is also unreliable under any Python version on Microsoft-imaged machines because Defender tampers with the temp-extracted DLLs; we build `--onedir` and ship it either as a zip or wrapped in an Inno Setup installer.

```powershell
# One-time: set up a 3.12 venv just for building
py -3.12 -m venv C:\PythonEnvs\TokenUsageTray-build312
C:\PythonEnvs\TokenUsageTray-build312\Scripts\pip install -r requirements.txt "pyinstaller>=6.3"

# One-time: install Inno Setup (only needed if you want to build the installer)
winget install JRSoftware.InnoSetup

# Build
.\build.ps1                     # produces dist\TokenTray\ (onedir folder)
.\build.ps1 -Installer          # also produces dist\TokenTray-Setup-X.Y.Z.exe
.\build.ps1 -Clean -Installer   # nuke build/dist first, then build everything
```

`build.ps1` auto-prefers the 3.12 build venv if it exists; otherwise it falls back to the daily-run venv.

The release process (manual until EMU policy permits hosted Actions runners):

```powershell
# 1. Bump the version in pyproject.toml AND installer\TokenTray.iss (MyAppVersion)
# 2. Build
.\build.ps1 -Clean -Installer

# 3. Package the portable zip + hashes
Compress-Archive dist\TokenTray\* dist\TokenTray-vX.Y.Z-win64.zip -Force
(Get-FileHash dist\TokenTray-Setup-X.Y.Z.exe -Algorithm SHA256).Hash + "  TokenTray-Setup-X.Y.Z.exe" |
    Out-File -Encoding ASCII dist\TokenTray-Setup-X.Y.Z.exe.sha256.txt
(Get-FileHash dist\TokenTray-vX.Y.Z-win64.zip -Algorithm SHA256).Hash + "  TokenTray-vX.Y.Z-win64.zip" |
    Out-File -Encoding ASCII dist\TokenTray-vX.Y.Z-win64.zip.sha256.txt

# 4. Tag and publish
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z `
    dist\TokenTray-Setup-X.Y.Z.exe dist\TokenTray-Setup-X.Y.Z.exe.sha256.txt `
    dist\TokenTray-vX.Y.Z-win64.zip dist\TokenTray-vX.Y.Z-win64.zip.sha256.txt `
    --generate-notes
```

A `.github/workflows/release.yml` is in the repo for the day GitHub-hosted runners are allowed on this EMU tenant; it will automatically build and publish the same artifacts on every `v*` tag push.

---

## File layout

```
TokenTray\
├── tray_app.py           # QApplication + QSystemTrayIcon + refresh timer
├── popup_window.py       # Frameless popup (Today + History tabs, 7-day chart)
├── icon_renderer.py      # Tray badge with today-token-count overlay
├── usage_core.py         # Telemetry log parsing + day/hour bucketing
├── history_store.py      # Local SQLite history + trial/tier gate
├── install_startup.py    # Startup-folder shortcut install/remove
├── run.pyw               # pythonw entry point (no console)
├── build.ps1             # PyInstaller + (optional) Inno Setup build script
├── pyproject.toml        # Package metadata + entry points
├── requirements.txt      # Runtime deps (kept for backward compat)
├── tests\
│   └── test_history_store.py  # Unit tests for the local history store
├── installer\
│   └── TokenTray.iss     # Inno Setup script -> dist\TokenTray-Setup-*.exe
├── tools\
│   ├── make_icon.py      # Regenerate assets\tokentray.ico
│   └── make_screenshot.py# Regenerate assets\screenshot.png
├── assets\
│   ├── tokentray.ico     # App icon (committed; bundled by PyInstaller)
│   └── screenshot.png    # README screenshot
└── .github\workflows\
    └── release.yml       # CI build & release on tag push (needs hosted runners)
```

---

## License

[MIT](LICENSE) © 2026 Jeff James
