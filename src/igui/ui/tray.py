"""Tray icon, context menu and notifications (port of Form1's UI parts)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .. import APP_NAME, AUTHOR, __version__
from ..autostart import set_autostart
from ..config import UserState
from ..sap.worker import SapWorker

HOTKEY_ACTIVATE = 100
HOTKEY_RECORD_START = 102
HOTKEY_RECORD_STOP = 103


class UiBridge(QObject):
    """Notifier for the SAP worker; signals are delivered in the Qt main thread."""

    notified = Signal(str, str)
    clipboard_requested = Signal(str)
    sap_activated = Signal()

    def notify(self, text: str, level: str = "info") -> None:
        self.notified.emit(text, level)

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_requested.emit(text)

    def sap_window_activated(self) -> None:
        self.sap_activated.emit()


_ICONS = {
    "info": QSystemTrayIcon.MessageIcon.Information,
    "warning": QSystemTrayIcon.MessageIcon.Warning,
    "error": QSystemTrayIcon.MessageIcon.Critical,
}


class TrayApp(QObject):
    def __init__(self, icon: QIcon, state: UserState, worker: SapWorker, bridge: UiBridge,
                 help_text: str, on_exit: Callable[[], None]) -> None:
        super().__init__()
        self.state = state
        self.worker = worker
        self.help_text = help_text
        self.on_exit = on_exit

        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(f"{APP_NAME} v{__version__}\nwywołanie przez Ctrl+Alt+I")
        menu = QMenu()
        menu.addAction("Informacja", self.show_about)
        menu.addSeparator()
        self.autostart_action = QAction("Autostart", menu, checkable=True)
        self.autostart_action.triggered.connect(self.toggle_autostart)
        menu.addAction(self.autostart_action)
        menu.addAction("Zapisz kontekst", self.worker.save_dump)
        self.rec_start_action = menu.addAction("Rozpocznij nagrywanie\tCtrl+Alt+G", self.worker.start_recording)
        self.rec_stop_action = menu.addAction("Zakończ nagrywanie\tCtrl+Alt+H", self.worker.stop_recording)
        menu.addAction("Pomoc dla skrótów", self.show_help)
        menu.addSeparator()
        menu.addAction("Wyjście", self.on_exit)
        menu.aboutToShow.connect(self._refresh_menu)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

        bridge.notified.connect(self.notify)
        bridge.clipboard_requested.connect(self._copy)

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    @Slot(str, str)
    def notify(self, text: str, level: str = "info") -> None:
        self.tray.showMessage(APP_NAME, text, _ICONS.get(level, _ICONS["info"]), 5000)

    @Slot(str)
    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _refresh_menu(self) -> None:
        self.autostart_action.setChecked(self.state.autostart)
        recording = self.worker.recording
        self.rec_start_action.setEnabled(not recording)
        self.rec_stop_action.setEnabled(recording)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.worker.activate()

    def on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_ACTIVATE:
            self.worker.activate()
        elif hotkey_id == HOTKEY_RECORD_START:
            self.worker.start_recording()
        elif hotkey_id == HOTKEY_RECORD_STOP:
            self.worker.stop_recording()

    def toggle_autostart(self, checked: bool) -> None:
        self.state.autostart = checked
        self.state.save()
        set_autostart(checked)

    def show_about(self) -> None:
        QMessageBox.information(None, APP_NAME,
                                f"iGUI FrontEnd Integration v{__version__}\nConcept & Code by {AUTHOR}")

    def show_help(self) -> None:
        box = QMessageBox(QMessageBox.Icon.NoIcon, f"{APP_NAME} - pomoc", self.help_text)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()


def app_icon(path: str) -> QIcon:
    icon = QIcon(path)
    QApplication.setWindowIcon(icon)
    return icon
