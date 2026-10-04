"""Configuration (replaces My.Settings / App.config) and persisted user state."""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityConfig:
    allowed_origins: frozenset[str] = frozenset()
    api_token: str = ""
    allow_get_actions: bool = False
    max_commands: int = 50
    max_value_length: int = 1024
    max_body_bytes: int = 16384


@dataclass(frozen=True)
class Config:
    sap_sid: str = "DEV"
    suppress_attach_warning: bool = True
    port: int = 50501
    macros_url: str = ""
    macros_file: Path | None = None
    record_dir: Path = field(default_factory=lambda: Path.home() / "Documents" / "iGui" / "recordings")
    security: SecurityConfig = field(default_factory=SecurityConfig)
    sources: tuple[Path, ...] = ()


class ConfigError(ValueError):
    pass


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _normalize_origin(origin: str) -> str:
    parts = urlsplit(origin.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"Invalid origin in security.allowed_origins: {origin!r}")
    host = parts.hostname
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else 80
    port = f":{parts.port}" if parts.port and parts.port != default_port else ""
    return f"{parts.scheme}://{host}{port}".lower()


def config_files() -> list[Path]:
    return [
        paths.resource("igui.default.toml"),
        paths.install_dir() / "igui.toml",
        paths.user_config_dir() / "igui.toml",
    ]


def load_config(files: list[Path] | None = None) -> Config:
    data: dict[str, Any] = {}
    used: list[Path] = []
    for path in files if files is not None else config_files():
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                loaded = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
        # a relative macros.file is relative to the config file that sets it
        macros_file = str(loaded.get("macros", {}).get("file", "")).strip()
        if macros_file and not Path(macros_file).expanduser().is_absolute():
            loaded["macros"]["file"] = str(path.parent / macros_file)
        data = _merge(data, loaded)
        used.append(path)
    return config_from_dict(data, tuple(used))


def config_from_dict(data: dict[str, Any], sources: tuple[Path, ...] = ()) -> Config:
    sap = data.get("sap", {})
    server = data.get("server", {})
    macros = data.get("macros", {})
    recorder = data.get("recorder", {})
    sec = data.get("security", {})

    port = int(server.get("port", 50501))
    if not 1 <= port <= 65535:
        raise ConfigError(f"Invalid server.port: {port}")

    macros_url = str(macros.get("url", "")).strip()
    if macros_url and urlsplit(macros_url).scheme not in ("http", "https"):
        raise ConfigError("macros.url must be an http(s) URL")

    # load_config() already made file paths absolute; this covers plain dicts.
    macros_file = None
    if str(macros.get("file", "")).strip():
        macros_file = Path(macros["file"]).expanduser()
        if not macros_file.is_absolute() and sources:
            macros_file = sources[-1].parent / macros_file

    record_dir = Config().record_dir
    if str(recorder.get("directory", "")).strip():
        record_dir = Path(recorder["directory"]).expanduser()

    security = SecurityConfig(
        allowed_origins=frozenset(_normalize_origin(o) for o in sec.get("allowed_origins", [])),
        api_token=str(sec.get("api_token", "")),
        allow_get_actions=bool(sec.get("allow_get_actions", False)),
        max_commands=int(sec.get("max_commands", 50)),
        max_value_length=int(sec.get("max_value_length", 1024)),
        max_body_bytes=int(sec.get("max_body_bytes", 16384)),
    )
    if security.api_token and len(security.api_token) < 16:
        raise ConfigError("security.api_token must be at least 16 characters long")

    return Config(
        sap_sid=str(sap.get("sid", "DEV")),
        suppress_attach_warning=bool(sap.get("suppress_attach_warning", True)),
        port=port,
        macros_url=macros_url,
        macros_file=macros_file,
        record_dir=record_dir,
        security=security,
        sources=sources,
    )


class UserState:
    """Small per-user state that the UI changes (replaces user-scoped My.Settings)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.user_config_dir() / "state.json"
        self.autostart = True
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.autostart = bool(data.get("autostart", True))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Cannot read %s: %s", self.path, exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"autostart": self.autostart}, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Cannot save %s: %s", self.path, exc)
