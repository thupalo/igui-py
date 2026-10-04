"""Well-known file locations (works both from source and from a PyInstaller build)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    """Directory with bundled read-only resources (images, default config, help)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "igui" / "resources"
    return Path(__file__).resolve().parent / "resources"


def resource(name: str) -> Path:
    return resource_dir() / name


def install_dir() -> Path:
    """Directory of the executable - place for a machine-wide igui.toml."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def user_config_dir() -> Path:
    """%APPDATA%\\iGui - user configuration and state."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "iGui"


def user_data_dir() -> Path:
    """%LOCALAPPDATA%\\iGui - logs and other machine-local data."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "iGui"


def temp_dir() -> Path:
    return Path(os.environ.get("TEMP") or os.environ.get("TMP") or Path.home())
