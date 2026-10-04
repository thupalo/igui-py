"""Screen dump - port of GuiTraverse.vb (gTree / gElement)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

INPUT_TYPES = frozenset({"GuiTextField", "GuiCTextField"})
MAX_DEPTH = 64
MAX_ELEMENTS = 20000


def relative_id(full_id: str) -> str:
    """'/app/con[0]/ses[0]/wnd[0]/usr/ctxtA-B' -> 'usr/ctxtA~B' (iGUI id convention)."""
    rel = full_id
    if "]/wnd[" in rel:
        rel = rel[rel.index("wnd["):]
        if "]/" in rel:
            rel = rel[rel.index("]/") + 2:]
        else:
            rel = ""
    return rel.replace("-", "~")


@dataclass
class Element:
    id: str
    name: str
    type: str
    value: str


@dataclass
class ScreenDump:
    title: str = ""
    inputs: list[Element] = field(default_factory=list)  # text / ctext fields (dumpTxt)
    lines: list[str] = field(default_factory=list)  # every element (dumpAll)
    input_lines: list[str] = field(default_factory=list)
    truncated: bool = False


def _safe(obj: Any, attr: str) -> tuple[bool, str]:
    try:
        value = getattr(obj, attr)
    except Exception:
        return False, ""
    return True, "" if value is None else str(value)


def collect(root: Any) -> ScreenDump:
    dump = ScreenDump()
    count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal count
        if count >= MAX_ELEMENTS or depth > MAX_DEPTH:
            dump.truncated = True
            return
        count += 1
        _, el_type = _safe(node, "Type")
        _, name = _safe(node, "Name")
        _, full_id = _safe(node, "Id")
        has_value, value = _safe(node, "Text")
        el_id = relative_id(full_id)
        indent = "\t" * depth

        if el_type == "GuiTitlebar":
            dump.title = value
        line = f"{indent}{el_id}, Name={name}, Type={el_type}"
        if has_value:
            line += f", Value={value}"
        dump.lines.append(line)
        if el_type in INPUT_TYPES and "%" not in name:
            dump.inputs.append(Element(el_id, name, el_type, value))
            dump.input_lines.append(line)

        known, is_container = _safe(node, "ContainerType")
        if known and is_container in ("False", "0"):
            return
        try:
            children = node.Children
            total = int(children.Count) if children is not None else 0
        except Exception:
            return
        for i in range(total):
            try:
                child = children.ElementAt(i)
            except Exception as exc:
                log.debug("Cannot read child %d of %s: %s", i, el_id, exc)
                continue
            visit(child, depth + 1)

    visit(root, 0)
    return dump


def format_dump(header: str, dump: ScreenDump, macro_link: str = "") -> str:
    parts = [header.rstrip("\n"), ""]
    if dump.title:
        parts.append(f"Title: {dump.title}")
    if macro_link:
        parts.append(f"Macro link: {macro_link}")
    parts += ["", "dumpTXT:", *dump.input_lines, "", "dumpAll:", *dump.lines]
    if dump.truncated:
        parts.append("... (truncated)")
    return "\n".join(parts) + "\n"
