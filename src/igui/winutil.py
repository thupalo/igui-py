"""Win32 helpers (port of win32.vb / parts of Form1.vb) using ctypes with explicit,
64-bit safe signatures."""

from __future__ import annotations

import ctypes
import logging
import winreg
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GA_ROOTOWNER = 3
SW_RESTORE = 9
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_SYSTEMMODAL = 0x1000
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int


def _h(hwnd: int | None) -> int:
    return int(hwnd or 0)


def top_level(hwnd: int | None) -> int:
    """Root owner of a window (SAP popups are owned by the main SAP window)."""
    if not hwnd:
        return 0
    return _h(user32.GetAncestor(hwnd, GA_ROOTOWNER)) or int(hwnd)


def foreground_top_level() -> int:
    return top_level(user32.GetForegroundWindow())


def is_window(hwnd: int | None) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def bring_to_front(hwnd: int | None) -> None:
    if not is_window(hwnd):
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if not user32.SetForegroundWindow(hwnd):
        log.debug("SetForegroundWindow refused for %#x", hwnd)


def client_origin(hwnd: int) -> tuple[int, int] | None:
    """Screen coordinates (physical pixels) of the window's client area origin."""
    pt = wintypes.POINT(0, 0)
    if not is_window(hwnd) or not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return pt.x, pt.y


def move_topmost(hwnd: int, x: int, y: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)


def message_box(text: str, title: str = "iGUI", icon: int = MB_ICONINFORMATION) -> None:
    """Blocking, system-modal message box. Safe to call from any thread."""
    user32.MessageBoxW(None, text, title, MB_OK | icon | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST)


SAP_SECURITY_KEY = r"Software\SAP\SAPGUI Front\SAP Frontend Server\Security"


def suppress_sap_attach_warning() -> None:
    """Set HKCU\\...\\Security\\WarnOnAttach = 0 (only writes when it differs)."""
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SAP_SECURITY_KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                current, _ = winreg.QueryValueEx(key, "WarnOnAttach")
            except FileNotFoundError:
                current = None
            if current != 0:
                winreg.SetValueEx(key, "WarnOnAttach", 0, winreg.REG_DWORD, 0)
                log.info("SAP GUI WarnOnAttach set to 0")
    except OSError as exc:
        log.warning("Cannot update SAP GUI security settings: %s", exc)
