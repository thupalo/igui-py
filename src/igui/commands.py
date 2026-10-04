"""iGUI command language: ordered (key, value) pairs -> validated Command list.

    _tr=<TCODE>                 start transaction
    _cmd=<OKCODE>               send OK code
    _vkey=<0..99>               send virtual key to the active window
    _screen=<n>                 abort unless the current screen number is n
    _pause[=<message>]          show a message box and wait for OK
    <field>[.text]=<value>      set field text
    <element>.press             press button / menu item
    <element>.select            select element (tab, radio button, menu)
    <element>.setfocus          set focus
    dump                        (alone) save a dump of the current screen

Control keys are matched by prefix (``_vkey1``, ``_vkey2`` ... are allowed, so
the same command can appear several times in one URL).  In element ids ``~``
stands for ``-`` (iGUI 2.x convention).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .config import SecurityConfig


class Kind(Enum):
    TRANSACTION = "_tr"
    OKCODE = "_cmd"
    VKEY = "_vkey"
    SCREEN = "_screen"
    PAUSE = "_pause"
    SET_TEXT = "text"
    PRESS = "press"
    SELECT = "select"
    SET_FOCUS = "setfocus"
    DUMP = "dump"


@dataclass(frozen=True)
class Command:
    kind: Kind
    target: str = ""  # SAP element id relative to the active window
    value: str = ""


class CommandError(ValueError):
    pass


# Prefix order matters: no prefix is a prefix of another one.
_CONTROL_PREFIXES = (
    ("_tr", Kind.TRANSACTION),
    ("_cmd", Kind.OKCODE),
    ("_vkey", Kind.VKEY),
    ("_screen", Kind.SCREEN),
    ("_pause", Kind.PAUSE),
)
_ELEMENT_ACTIONS = {
    ".text": Kind.SET_TEXT,
    ".press": Kind.PRESS,
    ".select": Kind.SELECT,
    ".setfocus": Kind.SET_FOCUS,
}

_TCODE_RE = re.compile(r"^[A-Za-z0-9_/\-]{1,40}$")
# SAP element ids, e.g. usr/subSUB:SAPLXYZ:0100/ctxtRF02D-KUNNR or usr/tblX/txtF[1,3]
_ELEMENT_RE = re.compile(r"^[A-Za-z0-9_/\[\],:\-~#$*+]{1,256}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DEFAULT_PAUSE_TEXT = "Press OK to continue"

RESERVED_KEYS = frozenset({"_token"})


def _check_text(name: str, value: str, max_len: int, allow_newlines: bool = False) -> str:
    if len(value) > max_len:
        raise CommandError(f"{name}: value longer than {max_len} characters")
    if _CONTROL_CHARS_RE.search(value) or (not allow_newlines and ("\n" in value or "\r" in value)):
        raise CommandError(f"{name}: value contains control characters")
    return value


def _parse_control(kind: Kind, key: str, value: str, limits: SecurityConfig) -> Command:
    if kind is Kind.TRANSACTION:
        tcode = value.strip().replace("~", "-")
        if not _TCODE_RE.match(tcode):
            raise CommandError(f"{key}: invalid transaction code {value!r}")
        return Command(kind, value=tcode)
    if kind is Kind.OKCODE:
        okcode = _check_text(key, value.strip(), 70)
        if not okcode:
            raise CommandError(f"{key}: empty OK code")
        return Command(kind, value=okcode)
    if kind in (Kind.VKEY, Kind.SCREEN):
        text = value.strip()
        if not text.isdigit() or len(text) > 5:
            raise CommandError(f"{key}: number expected, got {value!r}")
        if kind is Kind.VKEY and int(text) > 99:
            raise CommandError(f"{key}: virtual key must be 0..99")
        return Command(kind, value=str(int(text)))
    # PAUSE
    text = value.strip()
    if not text or text == key:
        text = _DEFAULT_PAUSE_TEXT
    return Command(kind, value=_check_text(key, text, 500, allow_newlines=True))


def _parse_element(key: str, value: str, limits: SecurityConfig) -> Command:
    element, kind = key, Kind.SET_TEXT
    lowered = key.lower()
    for suffix, action in _ELEMENT_ACTIONS.items():
        if lowered.endswith(suffix):
            element, kind = key[: -len(suffix)], action
            break
    else:
        if "." in key:
            raise CommandError(f"{key}: unknown element action")
    if not _ELEMENT_RE.match(element):
        raise CommandError(f"{key}: invalid element id")
    target = element.replace("~", "-")
    if kind is Kind.SET_TEXT:
        return Command(kind, target, _check_text(key, value, limits.max_value_length))
    return Command(kind, target)


def parse(pairs: Iterable[tuple[str, str]], limits: SecurityConfig | None = None) -> list[Command]:
    """Validate and convert request parameters (in order) into commands."""
    limits = limits or SecurityConfig()
    commands: list[Command] = []
    for raw_key, raw_value in pairs:
        key, value = str(raw_key).strip(), str(raw_value)
        if not key or key in RESERVED_KEYS:
            continue
        lowered = key.lower()
        if lowered == "dump":
            commands.append(Command(Kind.DUMP))
            continue
        for prefix, kind in _CONTROL_PREFIXES:
            if lowered.startswith(prefix):
                commands.append(_parse_control(kind, key, value, limits))
                break
        else:
            commands.append(_parse_element(key, value, limits))
        if len(commands) > limits.max_commands:
            raise CommandError(f"Too many commands (max {limits.max_commands})")

    if not commands:
        raise CommandError("No commands given")
    if any(c.kind is Kind.DUMP for c in commands) and len(commands) > 1:
        raise CommandError("'dump' cannot be combined with other commands")
    return commands


def to_pairs(commands: Iterable[Command]) -> list[tuple[str, str]]:
    """Inverse of parse() - used by the recorder to produce replayable links."""
    out: list[tuple[str, str]] = []
    for c in commands:
        if c.kind is Kind.DUMP:
            out.append(("dump", ""))
        elif c.kind in (Kind.TRANSACTION, Kind.OKCODE, Kind.VKEY, Kind.SCREEN, Kind.PAUSE):
            out.append((c.kind.value, c.value))
        else:
            out.append((f"{c.target.replace('-', '~')}.{c.kind.value}", c.value))
    return out


def describe(commands: Iterable[Command]) -> str:
    """One-line summary for the log; field values are masked (business data)."""
    parts = []
    for c in commands:
        if c.kind in (Kind.TRANSACTION, Kind.OKCODE, Kind.VKEY, Kind.SCREEN):
            parts.append(f"{c.kind.value}={c.value}")
        elif c.kind is Kind.SET_TEXT:
            parts.append(f"{c.target}=<{len(c.value)} chars>")
        elif c.kind in (Kind.PAUSE, Kind.DUMP):
            parts.append(c.kind.value)
        else:
            parts.append(f"{c.target}.{c.kind.value}")
    return ", ".join(parts)
