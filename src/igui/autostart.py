"""Autostart shortcut in the user's Startup folder (port of Autostart.vb)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

SHORTCUT_NAME = "iGUI.lnk"  # same name as iGUI 2.x, so the old shortcut is replaced


def _shell():
    import win32com.client

    return win32com.client.Dispatch("WScript.Shell")


def shortcut_path() -> Path:
    return Path(_shell().SpecialFolders("Startup")) / SHORTCUT_NAME


def _launch_command() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else exe), "-m igui"


def set_autostart(enabled: bool) -> None:
    try:
        link_path = shortcut_path()
        if not enabled:
            link_path.unlink(missing_ok=True)
            return
        target, arguments = _launch_command()
        link = _shell().CreateShortcut(str(link_path))
        link.TargetPath = target
        link.Arguments = arguments
        link.WorkingDirectory = str(paths.install_dir())
        link.WindowStyle = 1
        link.IconLocation = f"{paths.resource('eco.ico')}, 0"
        link.Description = "iGUI"
        link.Save()
    except Exception as exc:
        log.warning("Cannot update autostart shortcut: %s", exc)
