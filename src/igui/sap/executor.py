"""Runs parsed commands against a SAP GUI session (port of Form1.callSap "Command")."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..commands import Command, Kind
from .model import Result, Status

log = logging.getLogger(__name__)

# Status bar message types that stop the command sequence:
# E = error, A = abort.  S (success), W (warning), I (info) are only reported.
STOP_MESSAGE_TYPES = frozenset({"E", "A"})


def com_error_text(exc: BaseException) -> str:
    """Readable text for pywintypes.com_error and ordinary exceptions."""
    args = getattr(exc, "args", ())
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 3 and args[2][2]:
        return str(args[2][2]).strip()  # EXCEPINFO description
    if len(args) >= 2 and isinstance(args[1], str):
        return args[1]
    return str(exc) or exc.__class__.__name__


def status_bar(session: Any) -> tuple[str, str]:
    """(text, message type) of the main window status bar; ('', '') if unavailable."""
    try:
        sbar = session.findById("wnd[0]/sbar")
        return str(sbar.Text or ""), str(sbar.MessageType or "")
    except Exception:
        return "", ""


def _run_one(session: Any, cmd: Command) -> None:
    if cmd.kind is Kind.TRANSACTION:
        session.StartTransaction(cmd.value)
        return
    if cmd.kind is Kind.OKCODE:
        session.SendCommand(cmd.value)
        return
    window = session.ActiveWindow
    if cmd.kind is Kind.VKEY:
        window.sendVKey(int(cmd.value))
    elif cmd.kind is Kind.SET_TEXT:
        window.findById(cmd.target).text = cmd.value
    elif cmd.kind is Kind.PRESS:
        window.findById(cmd.target).press()
    elif cmd.kind is Kind.SELECT:
        window.findById(cmd.target).select()
    elif cmd.kind is Kind.SET_FOCUS:
        window.findById(cmd.target).setFocus()
    else:
        raise ValueError(f"Unsupported command {cmd.kind}")


def execute(session: Any, commands: list[Command], pause: Callable[[str], None]) -> Result:
    """Execute ``commands`` in order.  Stops at the first failure, an SAP error
    message or a screen mismatch.  The session UI is locked during execution
    (except while a _pause message is shown) and always unlocked afterwards."""
    messages: list[str] = []
    locked = False
    try:
        session.LockSessionUI()
        locked = True
        for cmd in commands:
            if cmd.kind is Kind.SCREEN:
                current = int(session.Info.ScreenNumber)
                if current != int(cmd.value):
                    return Result(Status.SCREEN_MISMATCH, messages,
                                  f"Invalid screen sequence: current={current} expected={cmd.value}")
                continue
            if cmd.kind is Kind.PAUSE:
                session.UnlockSessionUI()
                locked = False
                pause(cmd.value)
                session.LockSessionUI()
                locked = True
                continue
            try:
                _run_one(session, cmd)
            except Exception as exc:
                where = cmd.target or cmd.value
                return Result(Status.SAP_ERROR, messages, f"{cmd.kind.value} {where}: {com_error_text(exc)}")
            text, msg_type = status_bar(session)
            if text:
                messages.append(text)
                if msg_type in STOP_MESSAGE_TYPES:
                    return Result(Status.SAP_ERROR, messages)
        return Result(Status.OK, messages)
    except Exception as exc:
        return Result(Status.SAP_ERROR, messages, com_error_text(exc))
    finally:
        if locked:
            try:
                session.UnlockSessionUI()
            except Exception as exc:
                log.warning("UnlockSessionUI failed: %s", com_error_text(exc))
