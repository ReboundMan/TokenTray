"""Console-less entry point for the systray app (associated with pythonw.exe).

We tee stderr/stdout to a log file so that crashes under ``pythonw.exe``
(which has no attached console) leave a trail we can inspect.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

LOG = Path(__file__).parent / "tray_app.log"

try:
    # Replace stdout/stderr so any prints / tracebacks are captured.
    sys.stdout = sys.stderr = LOG.open("a", encoding="utf-8", buffering=1)
    print("---- tray_app starting ----")
    print("step: about to import tray_app")
    from tray_app import main
    print("step: import OK, calling main()")

    rc = main()
    print(f"---- tray_app exited with code {rc} ----")
    raise SystemExit(rc)
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    raise
