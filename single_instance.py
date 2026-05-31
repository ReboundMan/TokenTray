"""Cross-process single-instance guard for the TokenTray app.

Uses a named Windows mutex so that a second copy launched from another
autostart entry (or a manual double-click) detects the first running
instance and bows out instead of adding a duplicate tray icon.

The acquired mutex handle is intentionally kept alive for the lifetime of
the process; Windows releases the name automatically when the process
exits, so there is nothing to clean up on a crash.
"""
from __future__ import annotations

# Default Windows error code returned by GetLastError when CreateMutexW is
# asked for a name that already exists.
_ERROR_ALREADY_EXISTS = 183

# Module-global so the handle is not garbage-collected (which would close it
# and release the name) while the app is running.
_mutex_handle = None

MUTEX_NAME = "TokenTray-SingleInstance-Mutex"


def acquire_single_instance(name: str = MUTEX_NAME) -> bool:
    """Try to become the single running instance.

    Returns ``True`` if this process acquired the instance lock (i.e. it is
    the first/only instance), and ``False`` if another instance already holds
    it. On non-Windows platforms or if the OS call is unavailable, returns
    ``True`` (fail open — never block startup because of the guard).
    """
    global _mutex_handle

    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:  # pragma: no cover - non-Windows
        return True

    kernel32 = windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    last_error = kernel32.GetLastError()

    if not handle:
        # Could not create the mutex for some reason; don't block startup.
        return True

    if last_error == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    _mutex_handle = handle
    return True
