"""Process-level concerns: logging, crash handling, single instance."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from types import TracebackType

from . import paths, winutil

log = logging.getLogger("igui")

ERROR_ALREADY_EXISTS = 183
_mutex_handle = None


def setup_logging() -> None:
    log_dir = paths.user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "igui.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)


def _report_crash(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    log.critical("Unhandled exception\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        winutil.message_box(f"Exception: {exc}\n\nSzczegóły w logu: {paths.user_data_dir() / 'logs'}",
                            "iGUI", winutil.MB_ICONERROR)
    except Exception:
        pass


def install_excepthooks() -> None:
    sys.excepthook = _report_crash
    threading.excepthook = lambda args: _report_crash(args.exc_type, args.exc_value, args.exc_traceback)


def acquire_single_instance(name: str = "Local\\iGUI-single-instance") -> bool:
    """True if this is the only running instance (named mutex held for process life)."""
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # cannot tell - do not block start-up
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _mutex_handle = handle
    return True
