"""Access to the SAP GUI scripting engine and session selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .model import Status

log = logging.getLogger(__name__)


def get_engine() -> Any:
    """Return the GuiApplication object of the running SAP Logon."""
    import win32com.client  # local import keeps the module importable in tests

    sap_gui_auto = win32com.client.GetObject("SAPGUI")
    engine = sap_gui_auto.GetScriptingEngine
    if engine is None:
        raise RuntimeError("SAP GUI scripting engine is not available")
    return engine


def iter_children(collection: Any) -> Iterator[Any]:
    """Iterate a GuiComponentCollection (index based - safe with late binding)."""
    for i in range(int(collection.Count)):
        yield collection.ElementAt(i)


@dataclass
class SessionInfo:
    user: str = ""
    system: str = ""
    client: str = ""
    session_number: int = 0
    transaction: str = ""
    program: str = ""
    screen: int = 0
    app_server: str = ""
    system_number: int = 0

    @classmethod
    def read(cls, session: Any) -> "SessionInfo":
        info = session.Info
        return cls(
            user=str(info.User),
            system=str(info.SystemName),
            client=str(info.Client),
            session_number=int(info.SessionNumber),
            transaction=str(info.Transaction),
            program=str(info.Program),
            screen=int(info.ScreenNumber),
            app_server=str(info.ApplicationServer),
            system_number=int(info.SystemNumber),
        )

    def describe(self) -> str:
        return (
            f"User: {self.user}\nTransaction: {self.transaction}\nProgram: {self.program}\n"
            f"Screen: {self.screen}\nSystemName: {self.system}\nClient: {self.client}\n"
            f"Session: {self.session_number}\nApplicationServer: {self.app_server}\n"
            f"SystemNo: {self.system_number}\n"
        )


def pick_session(
    engine: Any,
    sid: str,
    preferred_window: int,
    top_level: Callable[[int], int],
) -> tuple[Any | None, Status]:
    """Pick a free session of system ``sid``.

    The session whose window is ``preferred_window`` (the SAP window the user
    worked with last) wins; otherwise the first free one.  Unlike iGUI 2.x the
    window comparison is done on top-level windows on both sides, and the
    "all sessions busy" status is actually reported.
    """
    first = None
    busy_seen = False
    for connection in iter_children(engine.Children):
        for session in iter_children(connection.Children):
            try:
                if session.Busy:
                    busy_seen = True
                    continue
                if str(session.Info.SystemName) != sid:
                    continue
                if first is None:
                    first = session
                if preferred_window and top_level(int(session.ActiveWindow.Handle)) == preferred_window:
                    return session, Status.OK
            except Exception as exc:  # session closing while we look at it
                log.debug("Skipping session: %s", exc)
    if first is not None:
        return first, Status.OK
    return None, (Status.ALL_BUSY if busy_seen else Status.LOGIN_REQUIRED)
