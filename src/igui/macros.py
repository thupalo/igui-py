"""Macro ("context") definitions - port of Context.vb.

A macro is a command template, e.g.::

    {"id": "TABLE", "transaction": "SE16", "screen": 230,
     "link": "_tr=SE16&usr/ctxtDATABROWSE~TABLENAME.text=%TABLE%&_vkey=0",
     "params": ["usr/ctxtDATABROWSE~TABLENAME"]}

``/ctx/TABLE?TABLE=MARA`` substitutes ``%TABLE%`` and runs the commands.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from . import paths
from .config import Config

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
_HEX2_RE = re.compile(r"^[0-9A-Fa-f]{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_DEFINITION_BYTES = 1024 * 1024


class MacroError(ValueError):
    pass


@dataclass
class Macro:
    id: str
    transaction: str = ""
    screen: int = 0
    name: str = ""
    link: str = ""
    params: list[str] = field(default_factory=list)

    def placeholders(self) -> list[str]:
        return [m.group(1) for m in _PLACEHOLDER_RE.finditer(self.link) if not _HEX2_RE.match(m.group(1))]

    def render(self, values: Mapping[str, str]) -> list[tuple[str, str]]:
        """Fill placeholders (URL-encoded, so values may contain & = % etc.) and
        return the ordered command pairs."""

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in values:
                return quote(str(values[name]), safe="")
            if _HEX2_RE.match(name):  # looks like percent-encoding in the template, keep it
                return match.group(0)
            raise MacroError(f"Macro {self.id}: missing parameter {name!r}")

        rendered = _PLACEHOLDER_RE.sub(substitute, self.link)
        return parse_qsl(rendered, keep_blank_values=True)

    def link_for_values(self, read_field: Callable[[str], str]) -> str:
        """Build the /ctx link reproducing the current screen (fixed Context.getLink).

        Each entry of ``params`` is an element id; its placeholder name is the one
        the template assigns to that element (falls back to p1, p2, ...)."""
        assigned: dict[str, str] = {}
        for key, value in parse_qsl(self.link, keep_blank_values=True):
            m = _PLACEHOLDER_RE.fullmatch(value)
            if m:
                element = key[: -len(".text")] if key.lower().endswith(".text") else key
                assigned[element] = m.group(1)
        query = []
        for index, element in enumerate(self.params, start=1):
            name = assigned.get(element, f"p{index}")
            query.append((name, read_field(element.replace("~", "-"))))
        return f"/ctx/{quote(self.id)}?{urlencode(query)}"


class MacroSet:
    def __init__(self, macros: list[Macro] | None = None) -> None:
        self.macros: list[Macro] = list(macros or [])

    def __len__(self) -> int:
        return len(self.macros)

    def get(self, macro_id: str) -> Macro | None:
        return next((m for m in self.macros if m.id == macro_id), None)

    def for_screen(self, transaction: str, screen: int | str) -> Macro | None:
        try:
            screen_no = int(screen)
        except (TypeError, ValueError):
            return None
        return next((m for m in self.macros if m.transaction == transaction and m.screen == screen_no), None)

    def to_json(self) -> str:
        return json.dumps({"l": [asdict(m) for m in self.macros]}, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "MacroSet":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise MacroError(f"Invalid macro definition JSON: {exc}") from exc
        items = data.get("l") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise MacroError("Macro definition must be an object with a list 'l'")
        macros: list[Macro] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise MacroError(f"Macro #{i}: object expected")
            try:
                macro = Macro(
                    id=str(item["id"]),
                    transaction=str(item.get("transaction", "")),
                    screen=int(item.get("screen", 0)),
                    name=str(item.get("name", item["id"])),
                    link=str(item.get("link", "")),
                    params=[str(p) for p in item.get("params", [])],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise MacroError(f"Macro #{i}: invalid definition ({exc})") from exc
            if not _ID_RE.match(macro.id):
                raise MacroError(f"Macro #{i}: invalid id {macro.id!r}")
            if not macro.link:
                raise MacroError(f"Macro {macro.id}: empty link")
            macros.append(macro)
        return cls(macros)


def _fetch(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
        except ValueError:
            loopback = parts.hostname == "localhost"
        if not loopback:
            log.warning("Macro definitions loaded over plain HTTP from %s - use https", parts.hostname)
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - scheme validated in config
        body = response.read(MAX_DEFINITION_BYTES + 1)
    if len(body) > MAX_DEFINITION_BYTES:
        raise MacroError("Macro definition is too large")
    if not body.strip():
        raise MacroError("Response is empty")
    return body.decode("utf-8-sig")


def load_macros(cfg: Config) -> MacroSet:
    """Load macros from the configured URL / file / bundled defaults."""
    if cfg.macros_url:
        log.info("Loading macros from %s", cfg.macros_url)
        try:
            return MacroSet.from_json(_fetch(cfg.macros_url))
        except (OSError, MacroError) as exc:
            raise MacroError(f"Cannot load macros from {cfg.macros_url}: {exc}") from exc
    path: Path = cfg.macros_file or paths.resource("macros.default.json")
    log.info("Loading macros from %s", path)
    try:
        return MacroSet.from_json(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise MacroError(f"Cannot read macros file {path}: {exc}") from exc
