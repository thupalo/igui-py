"""iGUI entry point:  python -m igui   (or the packaged igui.exe)."""

from __future__ import annotations

import logging
import sys

from . import APP_NAME, __version__, paths, runtime, winutil
from .config import ConfigError, UserState, load_config
from .macros import MacroError, MacroSet, load_macros

log = logging.getLogger("igui")


def main() -> int:
    runtime.setup_logging()
    runtime.install_excepthooks()
    log.info("%s v%s starting", APP_NAME, __version__)

    if not runtime.acquire_single_instance():
        winutil.message_box("iGUI już zostało uruchomione", APP_NAME, winutil.MB_ICONINFORMATION)
        return 0

    try:
        cfg = load_config()
    except ConfigError as exc:
        winutil.message_box(f"Błąd konfiguracji:\n{exc}", APP_NAME, winutil.MB_ICONERROR)
        return 2
    log.info("Configuration: %s", ", ".join(str(p) for p in cfg.sources))

    macro_warning = ""
    try:
        macros = load_macros(cfg)
    except MacroError as exc:
        log.error("%s", exc)
        macro_warning = f"Nie udało się wczytać makr:\n{exc}"
        macros = MacroSet()
    log.info("%d macro(s) loaded", len(macros))

    # Qt and the COM / HTTP parts are imported only after the cheap checks above.
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from .autostart import set_autostart
    from .sap.worker import SapWorker
    from .server import ApiServer, create_app
    from .ui.hotkeys import MOD_ALT, MOD_CONTROL, HotkeyListener
    from .ui.overlay import Badge, Splash
    from .ui.tray import (HOTKEY_ACTIVATE, HOTKEY_RECORD_START, HOTKEY_RECORD_STOP, TrayApp, UiBridge,
                          app_icon)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    icon = app_icon(str(paths.resource("eco.ico")))
    help_text = paths.resource("help.txt").read_text(encoding="utf-8")

    state = UserState()
    bridge = UiBridge()
    base_url = f"http://localhost:{cfg.port}"
    worker = SapWorker(cfg.sap_sid, cfg.record_dir, base_url, lambda: macros, bridge)
    worker.start()

    server = ApiServer(create_app(cfg, worker, lambda: macros, help_text), cfg.port)
    try:
        server.start()
    except OSError as exc:
        log.error("Cannot start API server: %s", exc)
        winutil.message_box(f"Nie można uruchomić serwera na porcie {cfg.port}:\n{exc}",
                            APP_NAME, winutil.MB_ICONERROR)
        worker.shutdown()
        return 3

    hotkeys = HotkeyListener({
        HOTKEY_ACTIVATE: (MOD_CONTROL | MOD_ALT, "I"),
        HOTKEY_RECORD_START: (MOD_CONTROL | MOD_ALT, "G"),
        HOTKEY_RECORD_STOP: (MOD_CONTROL | MOD_ALT, "H"),
    })

    def shutdown() -> None:
        log.info("Shutting down")
        tray.hide()
        badge.stop()
        hotkeys.stop()
        server.stop()
        worker.shutdown()
        app.quit()

    tray = TrayApp(icon, state, worker, bridge, help_text, on_exit=shutdown)
    hotkeys.triggered.connect(tray.on_hotkey)
    hotkeys.start()

    badge = Badge(QPixmap(str(paths.resource("eco.png"))), lambda: worker.sap_window)
    bridge.sap_activated.connect(badge.retrigger)
    badge.start()

    splash = Splash(QPixmap(str(paths.resource("iGUI_big1.png"))))
    splash.play()
    tray.show()

    if cfg.suppress_attach_warning:
        winutil.suppress_sap_attach_warning()
    if getattr(sys, "frozen", False):
        set_autostart(state.autostart)
    else:  # a shortcut to a development venv is not wanted; the tray toggle still works
        log.info("Running from source - autostart shortcut left unchanged")

    if hotkeys.failed:
        tray.notify("Niektóre skróty klawiszowe są zajęte przez inną aplikację", "warning")
    if macro_warning:
        tray.notify(macro_warning, "warning")

    app.aboutToQuit.connect(lambda: log.info("Stopped"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
