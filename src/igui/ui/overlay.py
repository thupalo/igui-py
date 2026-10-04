"""Translucent overlays (port of TransparentForm.vb and the Timer_GUI logic).

* Splash  - logo that fades in/out at start-up (click to dismiss).
* Badge   - small "eco" mark flashed over the SAP window's top-left corner
            whenever the SAP window used by iGUI becomes the foreground window.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QElapsedTimer, QPropertyAnimation, QSequentialAnimationGroup, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .. import winutil


class _PixmapWindow(QWidget):
    def __init__(self, pixmap: QPixmap, click_through: bool) -> None:
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        if click_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("iGUI")
        self.pixmap = pixmap
        self.setFixedSize(pixmap.size() / max(pixmap.devicePixelRatio(), 1.0))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.pixmap)


class Splash(_PixmapWindow):
    FADE_MS = 300
    HOLD_MS = 500

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__(pixmap, click_through=False)
        self._group = QSequentialAnimationGroup(self)
        fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        fade_in.setDuration(self.FADE_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(0.95)
        self._group.addAnimation(fade_in)
        self._group.addPause(self.HOLD_MS)
        fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        fade_out.setDuration(self.FADE_MS)
        fade_out.setStartValue(0.95)
        fade_out.setEndValue(0.0)
        self._group.addAnimation(fade_out)
        self._group.finished.connect(self.close)

    def play(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        self.setWindowOpacity(0.0)
        self.show()
        self._group.start()

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self._group.stop()
        self.close()


class Badge(_PixmapWindow):
    """Flashes over the SAP window (fade in, hold, fade out) when it gets focus."""

    IDLE_INTERVAL_MS = 100
    ANIM_INTERVAL_MS = 15
    FADE_IN_MS = 200
    HOLD_MS = 400
    FADE_OUT_MS = 200
    OFFSET_Y = 3

    def __init__(self, pixmap: QPixmap, sap_window: Callable[[], int]) -> None:
        super().__init__(pixmap, click_through=True)
        self._sap_window = sap_window
        self._was_active = False
        self._clock = QElapsedTimer()
        self._animating = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start(self.IDLE_INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def retrigger(self) -> None:
        """Flash again the next time the SAP window is in front (after Ctrl+Alt+I / a request)."""
        self._was_active = False

    def _finish(self) -> None:
        self._animating = False
        self.hide()
        self._timer.setInterval(self.IDLE_INTERVAL_MS)

    def _tick(self) -> None:
        sap = self._sap_window()
        if not sap or not winutil.is_window(sap):
            if self._animating:
                self._finish()
            self._was_active = False
            return
        active = winutil.foreground_top_level() == sap
        if active and not self._was_active:
            self._animating = True
            self._clock.start()
            self.setWindowOpacity(0.0)
            self.show()
            self._timer.setInterval(self.ANIM_INTERVAL_MS)
        elif not active and self._animating:
            self._finish()
        self._was_active = active
        if not self._animating:
            return

        origin = winutil.client_origin(sap)
        if origin:
            winutil.move_topmost(int(self.winId()), origin[0], origin[1] + self.OFFSET_Y)
        t = self._clock.elapsed()
        if t < self.FADE_IN_MS:
            self.setWindowOpacity(t / self.FADE_IN_MS)
        elif t < self.FADE_IN_MS + self.HOLD_MS:
            self.setWindowOpacity(1.0)
        elif t < self.FADE_IN_MS + self.HOLD_MS + self.FADE_OUT_MS:
            self.setWindowOpacity(1.0 - (t - self.FADE_IN_MS - self.HOLD_MS) / self.FADE_OUT_MS)
        else:
            self._finish()
