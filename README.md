# TokenUsageTray

A lightweight Windows systray app that shows live Copilot CLI token usage
parsed from `~/.copilot/logs/*.log`. Companion to
`AgencyUsageReport/build_report.py` (which produces the static HTML report);
this app gives you the same numbers in real time without having to rebuild.

## What it shows

- **Tray icon**: today's total tokens (`12k`, `1.2M`, etc.)
- **Hover tooltip**: today total + turn / session counts + last refresh time
- **Click**: floating popup with
  - Today's totals broken down (uncached / output / cached / sessions / turns)
  - Stacked bar chart of the last 7 days (cached + uncached + output)
- **Auto-refresh**: every 120 seconds (configurable in `tray_app.py`)

## Data source

`usage_core.py::iter_usage_events()` scans every `*.log` under
`~/.copilot/logs/` for `[Telemetry] cli.telemetry:` blocks where
`kind == "assistant_usage"`, extracting per-event:

- ISO timestamp (from log-line prefix)
- `session_id`
- `metrics.input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens`

The CLI emits `input_tokens` as "new + cache-write tokens billed at base
rate" (cache write is a subset of input), so the displayed
`Total = cached_in + input + output` matches the Microsoft IT usage report
breakdown.

Scope is local CLI (Agency) only — IDE Copilot, Clawpilot, M365 Copilot, and
cloud Coding Agent are not included.

## Install / run

The venv lives at `C:\PythonEnvs\TokenUsageTray\.venv` (per the global
`C:\PythonEnvs\<project>` convention).

```powershell
# One-time
py -m venv C:\PythonEnvs\TokenUsageTray\.venv
C:\PythonEnvs\TokenUsageTray\.venv\Scripts\pip install -r requirements.txt

# Run interactively
C:\PythonEnvs\TokenUsageTray\.venv\Scripts\pythonw.exe run.pyw

# Install Windows Startup shortcut so it launches at login
C:\PythonEnvs\TokenUsageTray\.venv\Scripts\python.exe install_startup.py

# Remove the startup entry
C:\PythonEnvs\TokenUsageTray\.venv\Scripts\python.exe install_startup.py --remove
```

## Smoke test

The data module is runnable directly and prints the last 7 days of buckets:

```powershell
C:\PythonEnvs\TokenUsageTray\.venv\Scripts\python.exe usage_core.py
```

## File layout

```
TokenUsageTray\
├── tray_app.py        # QApplication + QSystemTrayIcon + refresh timer
├── popup_window.py    # frameless QWidget popup with QtCharts stacked bars
├── icon_renderer.py   # renders the badge-style QIcon
├── usage_core.py      # telemetry log parsing + day bucketing
├── install_startup.py # creates/removes the Startup-folder shortcut
├── run.pyw            # entry point for pythonw.exe (no console window)
├── requirements.txt
└── README.md
```
