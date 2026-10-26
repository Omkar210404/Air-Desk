"""Shared look: colours, fonts, the live video view, pills and on-screen overlays."""
from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from .gestures import CONNECTIONS
from .tracker import Frame

BG = "#0b0d12"
PANEL = "#11141b"
CARD = "#161a23"
BORDER = "#232838"
TEXT = "#e8ebf2"
MUTED = "#8b93a7"
STATE_COLORS = {"off": "#7aa2ff", "armed": "#ffb547", "paused": "#ffb547", "active": "#3ee6b0"}
STATE_NAMES = {"off": "OFF", "armed": "ARMED", "paused": "ARMED", "active": "ACTIVE"}
AMBER = STATE_COLORS["armed"]
ACCENT = STATE_COLORS["active"]

# One dark stylesheet for the whole app, so dialogs and message boxes match.
STYLE = f"""
    QMainWindow, QDialog, QMessageBox, QWidget#root {{ background: {BG}; }}
    QLabel {{ color: {TEXT}; background: transparent; }}
    QFrame#panel {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
    QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QLineEdit, QComboBox {{
        background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 9px;
        padding: 8px 10px; selection-background-color: {ACCENT}; selection-color: {BG}; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
    QComboBox QAbstractItemView {{
        background: {CARD}; color: {TEXT}; border: 1px solid {BORDER};
        selection-background-color: {BORDER}; outline: none; }}
    QPushButton {{
        background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px;
        padding: 9px 14px; }}
    QPushButton:hover {{ border-color: {MUTED}; }}
    QPushButton:disabled {{ color: {MUTED}; }}
    QPushButton:checked {{ background: #2a2140; border-color: #b18cff; color: #e0d2ff; }}
    QPushButton#primary {{ background: {ACCENT}; color: {BG}; border: none; font-weight: 600; }}
    QPushButton#primary:hover {{ background: #6ff0c6; }}
    QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; }}
    QPushButton#flat:hover {{ color: {TEXT}; }}
    QPushButton#toggle {{
        border: none; border-radius: 12px; padding: 14px; font-weight: 600;
        background: {ACCENT}; color: {BG}; }}
    QPushButton#toggle:hover {{ background: #6ff0c6; }}
    QPushButton#toggle[on="true"] {{ background: #ff6b7a; color: {BG}; }}
    QPushButton#toggle[on="true"]:hover {{ background: #ff8e99; }}
    QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; }}
    QProgressBar::chunk {{ background: {AMBER}; border-radius: 4px; }}
    QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""
FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Inter", "SF Pro Display",
                 "Helvetica Neue", "Ubuntu", "Arial"]

def font(size: float, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def label(text: str, size: float, color: str = TEXT, weight=QFont.Weight.Normal) -> QLabel:
    lab = QLabel(text)
    lab.setFont(font(size, weight))
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    return lab


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
        if out and out.label in ("Pinch", "Drag", "Right pinch"):
            mid = (pts[4] + (pts[12] if out.label == "Right pinch" else pts[8])) / 2
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
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.top() + 10)


def to_logical(x: float, y: float) -> QPointF:
    """Mouse-controller pixels -> Qt logical pixels on the primary screen."""
    import sys
    screen = QGuiApplication.primaryScreen()
    r = 1.0 if sys.platform == "darwin" else screen.devicePixelRatio()
    g = screen.geometry()
    return QPointF(g.left() + x / r, g.top() + y / r)


class Overlay(QWidget):
    """Transparent, always-on-top window that mouse clicks pass straight through."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


class LaserDot(Overlay):
    SIZE = 72

    def __init__(self):
        super().__init__()
        self.resize(self.SIZE, self.SIZE)

    def point_at(self, pos: QPointF | None):
        if pos is None:
            self.hide()
            return
        self.move(int(pos.x() - self.SIZE / 2), int(pos.y() - self.SIZE / 2))
        if not self.isVisible():
            self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.SIZE / 2, self.SIZE / 2)
        g = QRadialGradient(c, self.SIZE / 2)
        g.setColorAt(0.0, QColor(255, 255, 255, 255))
        g.setColorAt(0.12, QColor(255, 80, 96, 255))
        g.setColorAt(0.3, QColor(255, 40, 70, 150))
        g.setColorAt(1.0, QColor(255, 40, 70, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, self.SIZE / 2, self.SIZE / 2)


PRESENTER_HINTS = [("Swipe right", "Next slide"), ("Swipe left", "Previous slide"),
                   ("Point", "Laser pointer"), ("Hold a fist", "Pause")]


class PresenterHints(Overlay):
    """Big gesture hints along the bottom of the screen, plus a large action flash."""

    def __init__(self):
        super().__init__()
        self.state = "off"
        self.event, self.event_t = "", 0.0
        self.items = list(PRESENTER_HINTS)
        self.resize(980, 190)

    def place(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 24)

    def set_hints(self, items: list):
        """[(gesture, what it does), ...] — follows the user's customisations."""
        self.items = items
        self.update()

    def set_state(self, state: str, event: str = ""):
        changed = state != self.state
        self.state = state
        if event:
            self.event, self.event_t = event, time.perf_counter()
        if changed or event or time.perf_counter() - self.event_t < 1.2:
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(STATE_COLORS[self.state])
        bar = QRectF(0, self.height() - 96, self.width(), 96)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(QColor(10, 12, 17, 215))
        p.drawRoundedRect(bar.adjusted(1, 1, -1, -1), 22, 22)

        # state chip on the left
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(bar.left() + 34, bar.center().y()), 7, 7)
        p.setPen(color)
        p.setFont(font(13, QFont.Weight.Bold))
        p.drawText(QRectF(bar.left() + 50, bar.top(), 110, bar.height()),
                   Qt.AlignmentFlag.AlignVCenter, STATE_NAMES[self.state])

        cell_w = (bar.width() - 170) / len(self.items)
        for i, (gesture, action) in enumerate(self.items):
            r = QRectF(bar.left() + 160 + i * cell_w, bar.top() + 14, cell_w, bar.height() - 28)
            p.setPen(QColor(TEXT))
            p.setFont(font(15, QFont.Weight.DemiBold))
            p.drawText(r.adjusted(0, 0, 0, -r.height() / 2), Qt.AlignmentFlag.AlignCenter, gesture)
            p.setPen(QColor(MUTED))
            p.setFont(font(12))
            p.drawText(r.adjusted(0, r.height() / 2, 0, 0), Qt.AlignmentFlag.AlignCenter, action)

        age = time.perf_counter() - self.event_t
        if self.event and age < 1.2:
            alpha = 1.0 if age < 0.8 else 1 - (age - 0.8) / 0.4
            p.setFont(font(24, QFont.Weight.Bold))
            tw = p.fontMetrics().horizontalAdvance(self.event) + 64
            box = QRectF(self.width() / 2 - tw / 2, 0, tw, 70)
            bg = QColor(color)
            bg.setAlphaF(0.95 * alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(box, 35, 35)
            fg = QColor(BG)
            fg.setAlphaF(alpha)
            p.setPen(fg)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.event)
