"""Global hotkeys via RegisterHotKey on a dedicated message-loop thread."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class HotkeyListener(QObject):
    """Emits ``triggered(id)`` (in the Qt main thread) for each registered hotkey."""

    triggered = Signal(int)

    def __init__(self, bindings: dict[int, tuple[int, str]]) -> None:
        """bindings: id -> (MOD_* flags, key letter), e.g. {100: (MOD_CONTROL | MOD_ALT, "I")}"""
        super().__init__()
        self.bindings = bindings
        self.failed: list[int] = []
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = []
        for hk_id, (mods, key) in self.bindings.items():
            if user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, ord(key.upper())):
                registered.append(hk_id)
            else:
                self.failed.append(hk_id)
                log.warning("Hotkey %d (%s) is already used by another application", hk_id, key)
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.triggered.emit(int(msg.wParam))
        finally:
            for hk_id in registered:
                user32.UnregisterHotKey(None, hk_id)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
