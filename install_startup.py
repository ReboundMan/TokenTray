"""Install (or remove) a Windows Startup shortcut for the systray app.

Usage:
    python install_startup.py          # install
    python install_startup.py --remove # uninstall
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import pythoncom
    from win32com.client import Dispatch  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pywin32 is required. Install with: pip install pywin32"
    ) from exc

HERE = Path(__file__).resolve().parent
VENV_PYTHONW = Path(r"C:\PythonEnvs\TokenUsageTray\.venv\Scripts\pythonw.exe")
SCRIPT = HERE / "run.pyw"
SHORTCUT_NAME = "TokenUsageTray.lnk"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA env var is unset; cannot locate Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install() -> None:
    if not VENV_PYTHONW.exists():
        raise SystemExit(f"pythonw.exe not found at {VENV_PYTHONW}")
    if not SCRIPT.exists():
        raise SystemExit(f"run.pyw not found at {SCRIPT}")
    target = _startup_dir() / SHORTCUT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(target))
    sc.TargetPath = str(VENV_PYTHONW)
    sc.Arguments = f'"{SCRIPT}"'
    sc.WorkingDirectory = str(HERE)
    sc.IconLocation = str(VENV_PYTHONW)
    sc.Description = "Copilot CLI token-usage tray app"
    sc.WindowStyle = 7  # minimized
    sc.save()
    print(f"Installed startup shortcut: {target}")


def remove() -> None:
    target = _startup_dir() / SHORTCUT_NAME
    if target.exists():
        target.unlink()
        print(f"Removed startup shortcut: {target}")
    else:
        print("No startup shortcut found.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        install()
