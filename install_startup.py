"""Install (or remove) a Windows Startup shortcut for the TokenTray app.

Auto-detects whether we're running from a source checkout or from a frozen
PyInstaller bundle, so end users who download ``TokenTray.exe`` and
double-clicked it can run:

    TokenTray.exe --install-startup       # add shortcut
    TokenTray.exe --uninstall-startup     # remove shortcut

Developers running from source use:

    python install_startup.py             # add
    python install_startup.py --remove    # remove
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import pythoncom  # type: ignore
    from win32com.client import Dispatch  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pywin32 is required. Install with: pip install pywin32"
    ) from exc

SHORTCUT_NAME = "TokenTray.lnk"


def _is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def _shortcut_target() -> tuple[str, str, str]:
    """Return (TargetPath, Arguments, WorkingDirectory) for the .lnk."""
    if _is_frozen():
        exe = Path(sys.executable).resolve()
        return str(exe), "", str(exe.parent)

    # Source / venv: prefer pythonw.exe (no console) from the running interp.
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    here = Path(__file__).resolve().parent
    entry = here / "run.pyw"
    if not entry.exists():
        entry = here / "tray_app.py"
    return str(interpreter), f'"{entry}"', str(here)


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA env var is unset; cannot locate Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def is_installed() -> bool:
    """Return True if the TokenTray Startup shortcut currently exists."""
    try:
        return (_startup_dir() / SHORTCUT_NAME).exists()
    except SystemExit:
        return False


def install() -> int:
    target_path, args, workdir = _shortcut_target()
    if not Path(target_path).exists():
        raise SystemExit(f"Cannot find executable: {target_path}")
    lnk = _startup_dir() / SHORTCUT_NAME
    lnk.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(lnk))
    sc.TargetPath = target_path
    sc.Arguments = args
    sc.WorkingDirectory = workdir
    sc.IconLocation = target_path
    sc.Description = "TokenTray - Copilot CLI token usage tray app"
    sc.WindowStyle = 7  # minimized
    sc.save()
    print(f"Installed startup shortcut: {lnk}")
    print(f"  Target : {target_path}")
    if args:
        print(f"  Args   : {args}")
    print(f"  WorkDir: {workdir}")
    return 0


def remove() -> int:
    lnk = _startup_dir() / SHORTCUT_NAME
    if lnk.exists():
        lnk.unlink()
        print(f"Removed startup shortcut: {lnk}")
    else:
        print("No startup shortcut found.")
    return 0


if __name__ == "__main__":
    if "--remove" in sys.argv or "--uninstall" in sys.argv:
        raise SystemExit(remove())
    raise SystemExit(install())
