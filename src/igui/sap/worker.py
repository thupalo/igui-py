"""Single SAP worker thread.

All SAP GUI COM calls happen on this one STA thread, one job at a time.  This
replaces iGUI 2.x where every HTTP request started its own thread that shared
COM fields without synchronisation (and, through VB's per-thread default form
instance, never saw the tray application's state).
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from .. import paths, winutil
from ..commands import Command, Kind, describe
from ..macros import MacroSet
from .dump import collect, format_dump
from .executor import com_error_text, execute
from .model import Result, Status
from .recorder import Recorder
from .session import SessionInfo, get_engine, pick_session

log = logging.getLogger(__name__)

QUEUE_SIZE = 8


class Notifier(Protocol):
    def notify(self, text: str, level: str = "info") -> None: ...
    def copy_to_clipboard(self, text: str) -> None: ...
    def sap_window_activated(self) -> None: ...


class SapWorker(threading.Thread):
    def __init__(self, sid: str, record_dir: Path, base_url: str,
                 macros: Callable[[], MacroSet], notifier: Notifier) -> None:
        super().__init__(name="sap-worker", daemon=True)
        self.sid = sid
        self.record_dir = record_dir
        self.base_url = base_url
        self.macros = macros
        self.notifier = notifier
        self.sap_window = 0  # top-level HWND of the SAP window used last (read by the overlay)
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=QUEUE_SIZE)
        self._recorder: Recorder | None = None

    # ---- public, thread-safe API ------------------------------------------------

    def submit(self, job: Callable[[], None]) -> bool:
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            log.warning("SAP worker queue full - request rejected")
            return False

    def activate(self) -> bool:
        return self.submit(lambda: self._report(self._with_session(lambda s: Result(Status.OK))))

    def run_commands(self, commands: list[Command], source: str = "") -> bool:
        if commands and commands[0].kind is Kind.DUMP:
            return self.save_dump()
        return self.submit(lambda: self._run_commands(commands, source))

    def save_dump(self) -> bool:
        return self.submit(self._save_dump)

    def start_recording(self) -> bool:
        return self.submit(self._start_recording)

    def stop_recording(self) -> bool:
        return self.submit(self._stop_recording)

    @property
    def recording(self) -> bool:
        rec = self._recorder
        return rec is not None and rec.active

    def shutdown(self, timeout: float = 5.0) -> None:
        if not self.is_alive():
            return
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            log.warning("SAP worker did not accept shutdown request")
            return
        self.join(timeout)

    # ---- thread ---------------------------------------------------------------

    def run(self) -> None:
        import pythoncom

        pythoncom.CoInitialize()
        try:
            while True:
                try:
                    job = self._queue.get(timeout=0.05)
                except queue.Empty:
                    pythoncom.PumpWaitingMessages()  # delivers recorder events
                    continue
                if job is None:
                    break
                try:
                    job()
                except Exception:
                    log.exception("SAP job failed")
                    self.notifier.notify("Wystąpił nieoczekiwany błąd - szczegóły w logu", "error")
                pythoncom.PumpWaitingMessages()
            if self._recorder is not None:
                self._recorder.stop()
                self._recorder = None
        finally:
            pythoncom.CoUninitialize()

    # ---- jobs (worker thread only) -------------------------------------------------

    def _with_session(self, action: Callable[[Any], Result]) -> Result:
        try:
            engine = get_engine()
        except Exception as exc:
            log.info("SAP GUI not available: %s", com_error_text(exc))
            return Result(Status.SAP_UNAVAILABLE, detail=com_error_text(exc))
        try:
            session, status = pick_session(engine, self.sid, self.sap_window, winutil.top_level)
            if session is None:
                return Result(status)
            hwnd = winutil.top_level(int(session.ActiveWindow.Handle))
            if hwnd:
                self.sap_window = hwnd
                winutil.bring_to_front(hwnd)
                self.notifier.sap_window_activated()
            return action(session)
        except Exception as exc:
            log.exception("SAP call failed")
            return Result(Status.SAP_ERROR, detail=com_error_text(exc))

    def _report(self, result: Result) -> Result:
        sid = self.sid
        if result.status is Status.SAP_UNAVAILABLE:
            self.notifier.notify("Sprawdź status aplikacji SAPGui", "error")
        elif result.status is Status.LOGIN_REQUIRED:
            self.notifier.notify(f"Proszę zalogować się do systemu SAP {sid}", "info")
        elif result.status is Status.ALL_BUSY:
            self.notifier.notify(f"Brak wolnej sesji dla {sid}", "warning")
        elif not result.ok:
            self.notifier.notify("Błąd wykonania:\n" + result.summary(), "error")
        return result

    def _run_commands(self, commands: list[Command], source: str) -> None:
        recorder = self._recorder if self.recording else None
        if recorder is not None:
            recorder.begin_commands(commands, source)
        try:
            result = self._with_session(lambda s: execute(s, commands, self._pause))
        finally:
            if recorder is not None:
                import pythoncom

                pythoncom.PumpWaitingMessages()  # deliver the remaining echo events first
                recorder.end_commands()
        log.info("Commands from %s [%s]: %s %s", source or "?", describe(commands), result.status.value,
                 result.summary().replace("\n", " | "))
        self._report(result)

    @staticmethod
    def _pause(text: str) -> None:
        winutil.message_box(text, "iGUI")

    def _save_dump(self) -> None:
        def action(session: Any) -> Result:
            info = SessionInfo.read(session)
            dump = collect(session)
            link = ""
            macro = self.macros().for_screen(info.transaction, info.screen)
            if macro is not None:
                try:
                    link = macro.link_for_values(lambda el: str(session.ActiveWindow.findById(el).text))
                except Exception as exc:
                    link = f"(unavailable: {com_error_text(exc)})"
            text = format_dump(info.describe(), dump, link)
            target = paths.temp_dir() / f"iGui_{datetime.now():%Y%m%d_%H%M%S}.txt"
            target.write_text(text, encoding="utf-8")
            log.info("Screen dump (tx=%s screen=%s) saved to %s", info.transaction, info.screen, target)
            self.notifier.copy_to_clipboard(str(target))
            self.notifier.notify(f"Kontekst iGUI został zapisany w pliku:\n{target}\n"
                                 "Nazwa pliku została skopiowana do schowka", "info")
            return Result(Status.OK)

        self._report(self._with_session(action))

    def _start_recording(self) -> None:
        if self.recording:
            self.notifier.notify("Nagrywanie już trwa (Ctrl+Alt+H kończy)", "info")
            return
        recorder = Recorder(self.record_dir, self.base_url, on_stopped=self._recording_stopped)

        def action(session: Any) -> Result:
            path = recorder.start(session)
            self._recorder = recorder
            self.notifier.notify(f"Nagrywanie rozpoczęte (Ctrl+Alt+H kończy)\n{path}", "info")
            return Result(Status.OK)

        self._report(self._with_session(action))

    def _stop_recording(self) -> None:
        if not self.recording:
            self.notifier.notify("Nagrywanie nie jest aktywne (Ctrl+Alt+G rozpoczyna)", "info")
            return
        assert self._recorder is not None
        self._recorder.stop()

    def _recording_stopped(self, path: Path) -> None:
        self._recorder = None
        self.notifier.copy_to_clipboard(str(path))
        self.notifier.notify(f"Nagranie zapisane:\n{path}\nNazwa pliku została skopiowana do schowka", "info")
