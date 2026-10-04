"""Session recorder (Ctrl+Alt+G / Ctrl+Alt+H) - port of the RecSess event handlers.

A recording produces, in the recorder directory:

* ``igui_<timestamp>.txt`` - event log, written as it happens, followed by the
  recorded steps as iGUI commands (POST body for /go and a /go?... link);
* ``igui_<timestamp>.vbs`` - the same steps as a standard SAP GUI script.  It is
  generated from the events because SAP GUI rejects ``RecordFile`` here
  ("The method got an invalid argument"); ``Record = True`` is still needed,
  SAP raises Change events only while it is set.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO
from urllib.parse import urlencode

from ..commands import Command, Kind, to_pairs
from .com_events import EventConnection
from .dump import relative_id
from .executor import com_error_text
from .session import SessionInfo

log = logging.getLogger(__name__)


VBS_HEADER = """If Not IsObject(application) Then
   Set SapGuiAuto  = GetObject("SAPGUI")
   Set application = SapGuiAuto.GetScriptingEngine
End If
If Not IsObject(connection) Then
   Set connection = application.Children(0)
End If
If Not IsObject(session) Then
   Set session    = connection.Children(0)
End If
If IsObject(WScript) Then
   WScript.ConnectObject session,     "on"
   WScript.ConnectObject application, "on"
End If
"""


def session_path(full_id: str) -> str:
    """'/app/con[1]/ses[0]/wnd[0]/usr/ctxtA-B' -> 'wnd[0]/usr/ctxtA-B' (session.findById)."""
    idx = full_id.find("/wnd[")
    return full_id[idx + 1:] if idx >= 0 else ""


def vbs_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + ("" if value is None else str(value)).replace('"', '""') + '"'


def command_vbs(c: Command) -> str:
    """SAP GUI script line for an iGUI command."""
    lit = vbs_literal(c.value)
    element = f'session.ActiveWindow.findById("{c.target}")'
    return {
        Kind.TRANSACTION: f"session.StartTransaction {lit}",
        Kind.OKCODE: f"session.SendCommand {lit}",
        Kind.VKEY: f"session.ActiveWindow.sendVKey {c.value}",
        Kind.SCREEN: f"If session.Info.ScreenNumber <> {c.value} Then WScript.Quit",
        Kind.PAUSE: f"MsgBox {lit}",
        Kind.SET_TEXT: f"{element}.text = {lit}",
        Kind.PRESS: f"{element}.press",
        Kind.SELECT: f"{element}.select",
        Kind.SET_FOCUS: f"{element}.setFocus",
    }.get(c.kind, f"' {c.kind.value}")


class RecordingConverter:
    """Turns SAP 'Change' events into iGUI command pairs and SAP GUI script
    lines (pure, testable)."""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []
        self.vbs_lines: list[str] = []
        self._screen: int | None = None
        self._screen_changed = False
        self.suspended = False  # True while iGUI itself executes commands

    def add_commands(self, commands: list[Command]) -> list[str]:
        """Record commands executed by iGUI (API / macro / hotkey) while recording.
        SAP's own Change events for them are incomplete (StartTransaction is
        reported as a bare Enter), so they are taken as sent."""
        notes = []
        for key, value in to_pairs(c for c in commands if c.kind is not Kind.DUMP):
            self.pairs.append((key, value))
            notes.append(f"  {key}={value}" if value else f"  {key}")
        self.vbs_lines += [command_vbs(c) for c in commands if c.kind is not Kind.DUMP]
        self._screen_changed = False
        return notes

    def on_end_request(self, screen: int) -> None:
        if self._screen is not None and screen != self._screen:
            self._screen_changed = True
        self._screen = screen

    def on_change(self, component_id: str, command_array: Any) -> list[str]:
        """Convert one Change event; returns human readable lines for the log."""
        if self.suspended:
            return ["  (caused by the iGUI command above - not recorded again)"]
        element = relative_id(component_id)
        notes: list[str] = []
        for entry in command_array or ():
            if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence) or len(entry) < 2:
                continue
            op, name, args = str(entry[0]).upper(), str(entry[1]).lower(), list(entry[2:])
            self.vbs_lines.append(self._vbs(component_id, op, str(entry[1]), args))
            pair: tuple[str, str] | None = None
            if op == "SP" and name == "text" and element:
                pair = (f"{element}.text", "" if not args or args[0] is None else str(args[0]))
            elif op == "M" and name == "sendvkey" and args:
                pair = ("_vkey", str(int(args[0])))
            elif op == "M" and name in ("press", "select", "setfocus") and element:
                pair = (f"{element}.{name}", "")
            elif op == "SP" and name == "selected" and element and args and bool(args[0]):
                pair = (f"{element}.select", "")
            if pair is None:
                notes.append(f"  (not convertible: {element or component_id} {op} {name} {args})")
                continue
            if self._screen_changed and self._screen is not None:
                self.pairs.append(("_screen", str(self._screen)))
                notes.append(f"  _screen={self._screen}")
                self._screen_changed = False
            self.pairs.append(pair)
            notes.append(f"  {pair[0]}={pair[1]}" if pair[1] else f"  {pair[0]}")
        return notes

    @staticmethod
    def _vbs(component_id: str, op: str, member: str, args: list[Any]) -> str:
        path = session_path(component_id)
        target = f'session.findById("{path}")' if path else "session"
        if op == "SP":
            return f"{target}.{member} = {vbs_literal(args[0] if args else '')}"
        return f"{target}.{member} {', '.join(vbs_literal(a) for a in args)}".rstrip()

    def body(self) -> str:
        return urlencode(self.pairs)

    def vbs(self) -> str:
        return VBS_HEADER + "".join(line + "\n" for line in self.vbs_lines)


class SessionEvents:
    """Event sink for GuiSession (methods named On<EventName>, see com_events)."""

    recorder: "Recorder"

    def OnStartRequest(self, Session):  # noqa: N802,N803 - COM naming
        self.recorder.event("StartRequest")

    def OnEndRequest(self, Session):  # noqa: N802,N803
        try:
            info = Session.Info
            screen = int(info.ScreenNumber)
            self.recorder.converter.on_end_request(screen)
            self.recorder.event(f"EndRequest tx={info.Transaction} program={info.Program} screen={screen}"
                                f" title={Session.ActiveWindow.Text!r}")
        except Exception as exc:
            self.recorder.event(f"EndRequest ({com_error_text(exc)})")

    def OnChange(self, Session, Component, CommandArray):  # noqa: N802,N803
        try:
            comp_id = str(Component.Id)
        except Exception:
            comp_id = "?"
        notes = self.recorder.converter.on_change(comp_id, CommandArray)
        self.recorder.event("Change " + relative_id(comp_id), notes)

    def OnFocusChanged(self, Session, NewFocusedControl):  # noqa: N802,N803
        self.recorder.event("FocusChanged " + describe_control(NewFocusedControl))

    def OnError(self, Session, ErrorId, Desc1, Desc2, Desc3, Desc4):  # noqa: N802,N803
        self.recorder.event(f"Error {ErrorId} {Desc1} {Desc2} {Desc3} {Desc4}")

    def OnAutomationFCode(self, Session, FunctionCode):  # noqa: N802,N803
        self.recorder.event(f"AutomationFCode {FunctionCode}")

    def OnContextMenu(self, Session, Component):  # noqa: N802,N803
        self.recorder.event("ContextMenu " + describe_control(Component))

    def OnHit(self, Session, Component, InnerObject):  # noqa: N802,N803
        self.recorder.event(f"Hit {describe_control(Component)} {InnerObject}")

    def OnDestroy(self, Session):  # noqa: N802,N803
        self.recorder.event("Destroy - session closed")
        self.recorder.on_destroyed()


def describe_control(control: Any) -> str:
    if control is None:
        return ""
    parts = []
    for attr in ("Type", "Id", "Name", "Text"):
        try:
            value = getattr(control, attr)
        except Exception:
            continue
        parts.append(relative_id(str(value)) if attr == "Id" else str(value))
    return " ".join(parts)


class Recorder:
    """Owns one recording.  All methods run on the SAP worker thread."""

    def __init__(self, directory: Path, base_url: str, on_stopped: Callable[[Path], None] | None = None) -> None:
        self.directory = directory
        self.base_url = base_url
        self.on_stopped = on_stopped
        self.converter = RecordingConverter()
        self.session: Any = None
        self._events: Any = None
        self._log: TextIO | None = None
        self.log_path: Path | None = None
        self.vbs_path: Path | None = None

    @property
    def active(self) -> bool:
        return self._log is not None

    def start(self, session: Any) -> Path:
        """Start recording; on failure nothing is left half-open and the error is raised."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.directory / f"igui_{stamp}.txt"
        self.vbs_path = self.directory / f"igui_{stamp}.vbs"
        self.converter = RecordingConverter()
        info = SessionInfo.read(session)
        self.converter.on_end_request(info.screen)

        handler = SessionEvents()
        handler.recorder = self
        try:
            session.Record = True  # SAP raises Change events only while recording
            self._events = EventConnection(session, handler, "GuiSession")
        except Exception:
            try:
                session.Record = False
            except Exception:
                pass
            raise
        self.session = session
        self._log = self.log_path.open("w", encoding="utf-8", buffering=1)
        self._write(f"# iGUI recording started {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        self._write(f"# System {info.system} client {info.client} user {info.user} session {info.session_number}\n")
        self._write(f"# Start screen: tx={info.transaction} program={info.program} screen={info.screen}\n\n")
        log.info("Recording to %s", self.log_path)
        return self.log_path

    def event(self, text: str, notes: list[str] | None = None) -> None:
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._write(f"[{stamp}] {text}\n")
        for line in notes or ():
            self._write(line + "\n")

    def _write(self, text: str) -> None:
        if self._log is not None:
            self._log.write(text)

    def begin_commands(self, commands: list[Command], source: str) -> None:
        self.event(f"iGUI commands from {source or '?'}", self.converter.add_commands(commands))
        self.converter.suspended = True

    def end_commands(self) -> None:
        self.converter.suspended = False

    def on_destroyed(self) -> None:
        self.stop()

    def stop(self) -> Path | None:
        if self._log is None:
            return None
        try:
            if self.session is not None:
                self.session.Record = False
        except Exception as exc:
            log.debug("Record=False failed: %s", com_error_text(exc))
        body = self.converter.body()
        self._write(f"\n# Recording stopped {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        self._write("\n# Replay - POST body for /go (application/x-www-form-urlencoded):\n")
        self._write(body + "\n")
        self._write("\n# Replay - link (needs security.allow_get_actions = true):\n")
        self._write(f"{self.base_url}/go?{body}\n")
        if self.vbs_path is not None:
            try:
                self.vbs_path.write_text(self.converter.vbs(), encoding="utf-16")  # wscript: UTF-16 with BOM
                self._write(f"\n# SAP GUI script: {self.vbs_path}\n")
            except OSError as exc:
                self._write(f"\n# SAP GUI script not saved: {exc}\n")
        self._log.close()
        self._log = None
        if self._events is not None:
            try:
                self._events.close()
            except Exception:
                pass
        self._events = None
        self.session = None
        path = self.log_path
        log.info("Recording saved to %s", path)
        if self.on_stopped and path:
            self.on_stopped(path)
        return path
