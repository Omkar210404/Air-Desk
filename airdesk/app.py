"""AirDesk window: live view with a glowing hand skeleton, status and controls."""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QKeySequence, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient, QShortcut)
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                             QPushButton, QVBoxLayout, QWidget)

from .control import HOTKEY_LABEL, Hotkey, screen_size
from .gestures import CONNECTIONS
from .tracker import Frame, Tracker

BG = "#0b0d12"
PANEL = "#11141b"
CARD = "#161a23"
BORDER = "#232838"
TEXT = "#e8ebf2"
MUTED = "#8b93a7"
STATE_COLORS = {"off": "#7aa2ff", "armed": "#ffb547", "paused": "#ffb547", "active": "#3ee6b0"}
STATE_NAMES = {"off": "OFF", "armed": "ARMED", "paused": "ARMED", "active": "ACTIVE"}
FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Inter", "SF Pro Display",
                 "Helvetica Neue", "Ubuntu", "Arial"]

CHEATSHEET = [
    ("Point with index", "Move cursor"),
    ("Quick pinch", "Left click"),
    ("Pinch and hold", "Drag"),
    ("Thumb + two fingers", "Right click"),
    ("Two fingers up/down", "Scroll"),
    ("Open palm swipe", "Prev / next slide"),
    ("Hold a fist", "Pause / resume"),
]


def font(size: float, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def subtitle(status: str, control_on: bool, hand: bool) -> str:
    if not control_on:
        return f"Control is off. Click the button or press {HOTKEY_LABEL}."
    if status == "paused":
        return "Paused by fist. Hold a fist again to resume."
    if not hand:
        return "Waiting for your hand… control pauses while none is seen."
    if status == "armed":
        return "Hand found, getting ready…"
    return "Your hand is driving the mouse."


class VideoView(QWidget):
    """Mirrored camera image with the hand skeleton painted on top."""

    def __init__(self):
        super().__init__()
        self.frame = Frame()
        self.status = "off"
        self.event, self.event_t = "", 0.0
        self.setMinimumSize(480, 300)

    def show_frame(self, frame: Frame, status: str):
        self.frame, self.status = frame, status
        if frame.out and frame.out.event:
            self.event, self.event_t = frame.out.event, time.perf_counter()
        self.update()

    def _video_rect(self) -> QRectF:
        img = self.frame.image
        area = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        if img is None:
            return area
        s = min(area.width() / img.width(), area.height() / img.height())
        w, h = img.width() * s, img.height() * s
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(BG))
        r = self._video_rect()
        clip = QPainterPath()
        clip.addRoundedRect(r, 16, 16)

        if self.frame.image is None:
            p.fillPath(clip, QColor(CARD))
            title, body = self.frame.error or ("Starting camera…", "This takes a few seconds.")
            self._message(p, r, title, body)
            return

        p.save()
        p.setClipPath(clip)
        p.drawImage(r, self.frame.image)
        shade = QLinearGradient(r.topLeft(), r.bottomLeft())
        shade.setColorAt(0.0, QColor(0, 0, 0, 70))
        shade.setColorAt(0.5, QColor(0, 0, 0, 20))
        shade.setColorAt(1.0, QColor(0, 0, 0, 110))
        p.fillRect(r, shade)
        if self.frame.landmarks is not None:
            self._skeleton(p, r)
        p.restore()
        p.setPen(QPen(QColor(STATE_COLORS[self.status]).darker(160), 1.5))
        p.drawPath(clip)

        self._flash(p, r)
        if self.frame.warning:
            self._banner(p, r, self.frame.warning)

    def _skeleton(self, p: QPainter, r: QRectF):
        pts = [QPointF(r.left() + x * r.width(), r.top() + y * r.height())
               for x, y in self.frame.landmarks]
        color = QColor(STATE_COLORS[self.status])
        scale = r.width() / 900
        # glow: wide faint strokes under a thin bright one
        for width, alpha in ((22, 18), (13, 40), (7, 90)):
            c = QColor(color)
            c.setAlpha(alpha)
            p.setPen(QPen(c, width * scale, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for a, b in CONNECTIONS:
                p.drawLine(pts[a], pts[b])
        p.setPen(QPen(color.lighter(150), 2.4 * scale, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        for a, b in CONNECTIONS:
            p.drawLine(pts[a], pts[b])
        p.setPen(Qt.PenStyle.NoPen)
        for i, pt in enumerate(pts):
            tip = i in (4, 8, 12, 16, 20)
            rad = (9 if tip else 6) * scale
            g = QRadialGradient(pt, rad * 2.2)
            g.setColorAt(0, QColor(255, 255, 255, 230))
            g.setColorAt(0.35, color)
            c = QColor(color)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.setBrush(QBrush(g))
            p.drawEllipse(pt, rad * 2.2, rad * 2.2)

        out = self.frame.out
        if out and out.label in ("Pinch", "Drag", "Two-finger pinch"):
            mid = (pts[4] + pts[8]) / 2
            ring = QColor("#ffffff")
            ring.setAlpha(200)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 2.5 * scale))
            p.drawEllipse(mid, 22 * scale, 22 * scale)
        if out and out.hold > 0:
            palm = pts[9]
            p.setPen(QPen(QColor(STATE_COLORS["armed"]), 5 * scale, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            rad = 60 * scale
            p.drawArc(QRectF(palm.x() - rad, palm.y() - rad, 2 * rad, 2 * rad),
                      90 * 16, int(-360 * 16 * out.hold))

    def _flash(self, p: QPainter, r: QRectF):
        age = time.perf_counter() - self.event_t
        if not self.event or age > 0.9:
            return
        alpha = 1.0 if age < 0.5 else 1 - (age - 0.5) / 0.4
        p.setFont(font(15, QFont.Weight.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(self.event) + 40
        box = QRectF(r.center().x() - tw / 2, r.top() + 20, tw, 40)
        bg = QColor(STATE_COLORS[self.status])
        bg.setAlphaF(0.92 * alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(box, 20, 20)
        fg = QColor(BG)
        fg.setAlphaF(alpha)
        p.setPen(fg)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.event)

    def _banner(self, p: QPainter, r: QRectF, text: str):
        box = QRectF(r.left() + 16, r.bottom() - 60, r.width() - 32, 44)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 16, 8, 225))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor(STATE_COLORS["armed"]))
        p.setFont(font(10.5))
        p.drawText(box.adjusted(16, 0, -16, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, text)

    def _message(self, p: QPainter, r: QRectF, title: str, body: str):
        p.setPen(QColor(TEXT))
        p.setFont(font(17, QFont.Weight.DemiBold))
        p.drawText(QRectF(r.left() + 40, r.center().y() - 50, r.width() - 80, 40),
                   Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(QColor(MUTED))
        p.setFont(font(11))
        p.drawText(QRectF(r.left() + 60, r.center().y() - 4, r.width() - 120, 80),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap, body)


class StatePill(QWidget):
    def __init__(self, big=True):
        super().__init__()
        self.state = "off"
        self.big = big
        self.setFixedHeight(40 if big else 34)

    def set_state(self, state: str):
        if state != self.state:
            self.state = state
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(STATE_COLORS[self.state])
        text = STATE_NAMES[self.state] if self.big else f"AirDesk  ·  {STATE_NAMES[self.state]}"
        p.setFont(font(12 if self.big else 10.5, QFont.Weight.Bold))
        w = p.fontMetrics().horizontalAdvance(text) + 58
        h = self.height() - 4
        box = QRectF(2, 2, min(w, self.width() - 4), h)
        bg = QColor(color)
        bg.setAlpha(38)
        p.setBrush(QColor(12, 14, 19, 235) if not self.big else bg)
        edge = QColor(color)
        edge.setAlpha(160)
        p.setPen(QPen(edge, 1.4))
        p.drawRoundedRect(box, h / 2, h / 2)
        dot = QPointF(box.left() + 22, box.center().y())
        glow = QRadialGradient(dot, 12)
        glow.setColorAt(0, color)
        c = QColor(color)
        c.setAlpha(0)
        glow.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(dot, 12, 12)
        p.setBrush(color)
        p.drawEllipse(dot, 4.5, 4.5)
        p.setPen(color.lighter(115))
        p.drawText(box.adjusted(38, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)


class ConfidenceBar(QWidget):
    def __init__(self):
        super().__init__()
        self.value, self.color = 0.0, QColor(STATE_COLORS["off"])
        self.setFixedHeight(10)

    def set_value(self, value: float, color: str):
        self.value = 0.7 * self.value + 0.3 * value    # ease so it doesn't flicker
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BORDER))
        p.drawRoundedRect(r, 4, 4)
        if self.value > 0.01:
            fill = QRectF(r.left(), r.top(), r.width() * self.value, r.height())
            g = QLinearGradient(fill.topLeft(), fill.topRight())
            g.setColorAt(0, self.color.darker(140))
            g.setColorAt(1, self.color)
            p.setBrush(g)
            p.drawRoundedRect(fill, 4, 4)


class StatusIndicator(QWidget):
    """Small always-on-top pill so you can see control is ON from any app.

    Click-through: the mouse passes straight through it.
    """

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.pill = StatePill(big=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.pill)
        self.resize(200, 34)

    def place(self):
        from PyQt6.QtGui import QGuiApplication
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.top() + 10)


class Bridge(QObject):
    """Carries the global hotkey (pynput thread) into the Qt thread."""
    toggle = pyqtSignal()


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def label(text: str, size: float, color: str = TEXT, weight=QFont.Weight.Normal) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font(size, weight))
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    return lab


class MainWindow(QMainWindow):
    def __init__(self, camera_index: int = 0):
        super().__init__()
        self.setWindowTitle("AirDesk")
        self.resize(1200, 700)
        self.setMinimumSize(940, 580)
        self.control_on = False
        self.status = "off"
        self.setStyleSheet(f"""
            QMainWindow, QWidget#root {{ background: {BG}; }}
            QFrame#panel {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
            QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
            QPushButton#toggle {{
                border: none; border-radius: 12px; padding: 14px; font-weight: 600;
                background: {STATE_COLORS['active']}; color: {BG}; }}
            QPushButton#toggle:hover {{ background: #6ff0c6; }}
            QPushButton#toggle[on="true"] {{ background: #ff6b7a; color: {BG}; }}
            QPushButton#toggle[on="true"]:hover {{ background: #ff8e99; }}
        """)

        self.video = VideoView()
        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.video, 1)
        layout.addWidget(self._panel())
        self.setCentralWidget(root)

        self.indicator = StatusIndicator()
        self.bridge = Bridge()
        self.bridge.toggle.connect(self.toggle_control)
        self.hotkey = Hotkey(self.bridge.toggle.emit)
        self.hotkey.start()
        QShortcut(QKeySequence("Esc"), self, activated=lambda: self.set_control(False))

        self.tracker = Tracker(camera_index, screen_size())
        self.tracker.frame.connect(self.on_frame)
        self.tracker.start()

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(330)
        v = QVBoxLayout(panel)
        v.setContentsMargins(22, 24, 22, 20)
        v.setSpacing(14)

        v.addWidget(label("AirDesk", 20, TEXT, QFont.Weight.Bold))
        v.addWidget(label("Control your computer with hand gestures", 9.5, MUTED))
        v.addSpacing(6)

        self.pill = StatePill()
        v.addWidget(self.pill)
        self.sub = label("", 10, MUTED)
        self.sub.setWordWrap(True)
        self.sub.setMinimumHeight(38)
        v.addWidget(self.sub)

        g = card()
        gl = QVBoxLayout(g)
        gl.setContentsMargins(18, 14, 18, 16)
        gl.setSpacing(6)
        gl.addWidget(label("GESTURE", 8.5, MUTED, QFont.Weight.DemiBold))
        self.gesture = label("No hand", 19, TEXT, QFont.Weight.DemiBold)
        gl.addWidget(self.gesture)
        row = QHBoxLayout()
        self.bar = ConfidenceBar()
        row.addWidget(self.bar, 1)
        self.conf = label("0%", 9, MUTED)
        self.conf.setFixedWidth(36)
        self.conf.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.conf)
        gl.addLayout(row)
        v.addWidget(g)

        self.button = QPushButton("Turn control ON")
        self.button.setObjectName("toggle")
        self.button.setFont(font(11, QFont.Weight.DemiBold))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.toggle_control)
        v.addWidget(self.button)
        hint = label(f"{HOTKEY_LABEL} turns control on/off from any app.\n"
                     "Esc turns it off while this window is focused.", 8.5, MUTED)
        hint.setWordWrap(True)
        v.addWidget(hint)

        c = card()
        grid = QGridLayout(c)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        grid.addWidget(label("GESTURES", 8.5, MUTED, QFont.Weight.DemiBold), 0, 0, 1, 2)
        for i, (gesture, action) in enumerate(CHEATSHEET, start=1):
            grid.addWidget(label(gesture, 9.5, TEXT), i, 0)
            a = label(action, 9.5, MUTED)
            a.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(a, i, 1)
        v.addWidget(c)
        v.addStretch(1)

        self.stats = label("", 8.5, MUTED)
        v.addWidget(self.stats)
        return panel

    # -- control on/off -----------------------------------------------------
    def toggle_control(self):
        self.set_control(not self.control_on)

    def set_control(self, on: bool):
        self.control_on = on
        self.tracker.set_control(on)
        self.button.setText("Turn control OFF" if on else "Turn control ON")
        self.button.setProperty("on", "true" if on else "false")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        if on:
            self.tracker.engine.paused = False
            self.indicator.place()
            self.indicator.show()
        else:
            self.indicator.hide()

    # -- frames from the tracker thread ----------------------------------------
    def on_frame(self, frame: Frame):
        out = frame.out
        status = out.status if out else ("armed" if self.control_on else "off")
        if not self.control_on:
            status = "off"
        self.status = status
        color = STATE_COLORS[status]
        self.video.show_frame(frame, status)
        self.pill.set_state(status)
        self.indicator.pill.set_state(status)
        self.sub.setText(subtitle(status, self.control_on, frame.landmarks is not None))
        name = out.label if out else "No hand"
        conf = out.confidence if out and frame.landmarks is not None else 0.0
        self.gesture.setText(name)
        self.bar.set_value(conf, color)
        self.conf.setText(f"{conf * 100:.0f}%")
        if frame.image is not None:
            self.stats.setText(f"{frame.fps:4.1f} fps · {frame.proc_ms:.0f} ms per frame"
                               f" · {frame.quality}")
        else:
            self.stats.setText("")

    def closeEvent(self, event):
        self.tracker.set_control(False)
        self.tracker.stop()
        self.hotkey.stop()
        self.indicator.close()
        super().closeEvent(event)
