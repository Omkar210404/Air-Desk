"""AirDesk main window: live view, status, controls and your taught gestures."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QMainWindow,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .control import ACTION_TYPES, HOTKEY_LABEL, Hotkey, SystemControl, describe, screen_size
from .custom import CONFIG_FILE, CustomModel, holdout_check, load_config, save_config
from .teach import TeachDialog
from .tracker import Frame, Tracker
from .widgets import (MUTED, STATE_COLORS, STYLE, TEXT, ConfidenceBar, LaserDot,
                      PresenterHints, StatePill, StatusIndicator, VideoView, card, font, label,
                      to_logical)

CHEATSHEET = [
    ("Point with index", "Move cursor"),
    ("Quick pinch", "Left click"),
    ("Pinch and hold", "Drag"),
    ("Thumb + middle finger", "Right click"),
    ("Two fingers up/down", "Scroll"),
    ("Open palm swipe", "Prev / next slide"),
    ("Hold a fist", "Pause / resume"),
]


def subtitle(status: str, control_on: bool, hand: bool, teaching: bool, presenter: bool) -> str:
    if teaching:
        return "Control is paused while the Teach window is open."
    if not control_on:
        return f"Control is off. Click the button or press {HOTKEY_LABEL}."
    if status == "paused":
        return "Paused by fist. Hold a fist again to resume."
    if not hand:
        return "Waiting for your hand… control pauses while none is seen."
    if status == "armed":
        return "Hand found, getting ready…"
    if presenter:
        return "Presenter mode: point for the laser, swipe for slides."
    return "Your hand is driving the mouse."


class Bridge(QObject):
    """Carries the global hotkey (pynput thread) into the Qt thread."""
    toggle = pyqtSignal()


class MainWindow(QMainWindow):
    def __init__(self, camera_index: int = 0):
        super().__init__()
        self.setWindowTitle("AirDesk")
        self.resize(1240, 760)
        self.setMinimumSize(960, 600)
        QApplication.instance().setStyleSheet(STYLE)
        self.control_on = False
        self.status = "off"
        self.teach: TeachDialog | None = None
        self.confirming = False
        self.keys = SystemControl()
        self.config = load_config()

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
        self.laser = LaserDot()
        self.hints = PresenterHints()
        self.bridge = Bridge()
        self.bridge.toggle.connect(self.toggle_control)
        self.hotkey = Hotkey(self.bridge.toggle.emit)
        self.hotkey.start()
        QShortcut(QKeySequence("Esc"), self, activated=lambda: self.set_control(False))

        self.tracker = Tracker(camera_index, screen_size())
        self.tracker.frame.connect(self.on_frame)
        self.tracker.custom.connect(self.run_gesture)
        self._reload_gestures()
        self.tracker.start()

    # -- layout -------------------------------------------------------------
    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(350)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 24, 18, 20)
        v.setSpacing(14)

        v.addWidget(label("AirDesk", 20, TEXT, QFont.Weight.Bold))
        v.addWidget(label("Control your computer with hand gestures", 9.5, MUTED))
        v.addSpacing(4)
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

        row = QHBoxLayout()
        self.presenter = QPushButton("Presenter mode")
        self.presenter.setCheckable(True)
        self.presenter.toggled.connect(self.set_presenter)
        row.addWidget(self.presenter)
        teach = QPushButton("+ Teach gesture")
        teach.clicked.connect(lambda: self.open_teach(None))
        row.addWidget(teach)
        v.addLayout(row)

        mine = card()
        self.mine = QVBoxLayout(mine)
        self.mine.setContentsMargins(18, 14, 12, 14)
        self.mine.setSpacing(6)
        v.addWidget(mine)

        c = card()
        grid = QGridLayout(c)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        grid.addWidget(label("BUILT-IN GESTURES", 8.5, MUTED, QFont.Weight.DemiBold), 0, 0, 1, 2)
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

    def _fill_gesture_list(self, check: dict):
        self._clear(self.mine)
        self.mine.addWidget(label("YOUR GESTURES", 8.5, MUTED, QFont.Weight.DemiBold))
        gestures = self.config["gestures"]
        if not gestures:
            empty = label("None yet. Teach one and map it to a shortcut, media key, "
                          "screenshot, app or website.", 9.5, MUTED)
            empty.setWordWrap(True)
            self.mine.addWidget(empty)
            return
        for g in gestures:
            row = QHBoxLayout()
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(label(g["name"], 10, TEXT, QFont.Weight.DemiBold))
            c, t = check["per"].get(g["name"], (0, 0))
            held = f"  ·  held-out {c}/{t}" if t else ""
            text.addWidget(label(describe(g["action"]) + held, 8.5, MUTED))
            row.addLayout(text, 1)
            for caption, slot in (("Edit", lambda _=False, g=g: self.open_teach(g)),
                                  ("✕", lambda _=False, g=g: self.delete_gesture(g))):
                b = QPushButton(caption)
                b.setObjectName("flat")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(slot)
                row.addWidget(b)
            self.mine.addLayout(row)
        n = sum(len(g["samples"]) for g in gestures)
        note = label(f"Held-out = recognised / kept-aside samples from your own recordings "
                     f"({n} samples, 1 person).", 8, MUTED)
        note.setWordWrap(True)
        self.mine.addWidget(note)

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()                      # off screen now, freed by Qt later
                widget.deleteLater()
            elif item.layout():
                self._clear(item.layout())

    # -- taught gestures ----------------------------------------------------
    def _reload_gestures(self):
        gestures = self.config["gestures"]
        self.tracker.custom_model = CustomModel.from_gestures(gestures)
        check = holdout_check(gestures) if gestures else {"per": {}}
        self._fill_gesture_list(check)

    def open_teach(self, existing: dict | None):
        if self.teach is not None:
            self.teach.raise_()
            return
        self.teach = TeachDialog(self, self.config["gestures"], existing)
        self.teach.saved.connect(self._gesture_saved)
        self.teach.finished.connect(self._teach_closed)
        self.tracker.teaching = True
        self.teach.show()

    def _teach_closed(self):
        self.tracker.teaching = False
        self.teach = None

    def _gesture_saved(self, gesture: dict, old_name: str):
        gestures = [g for g in self.config["gestures"] if g["name"] != old_name]
        gestures.append(gesture)
        self.config["gestures"] = gestures
        self._save()

    def delete_gesture(self, g: dict):
        answer = QMessageBox.question(
            self, "Delete gesture",
            f"Delete “{g['name']}”? Its recorded samples are removed too.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.config["gestures"] = [x for x in self.config["gestures"] if x is not g]
            self._save()

    def _save(self):
        try:
            save_config(self.config)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save",
                                f"AirDesk couldn't write {CONFIG_FILE}.\n\n{e}")
        self._reload_gestures()

    def run_gesture(self, name: str):
        """A taught gesture fired (control is ON and ACTIVE). Run its action."""
        g = next((x for x in self.config["gestures"] if x["name"] == name), None)
        if g is None or self.confirming:
            return
        action = g["action"]
        kind = action["type"]
        try:
            if ACTION_TYPES[kind][2] and not self._confirm(g):
                return
            if kind == "open_url":
                QDesktopServices.openUrl(QUrl(action["value"]))
            elif kind == "open_app":
                open_path(action["value"])
            elif kind == "screenshot":
                path = take_screenshot()
                self.video.event, self.video.event_t = f"Saved {path.name}", time.perf_counter()
                return
            else:
                self.keys.press_keys(action)
            self.video.event = f"{name}  →  {describe(action)}"
            self.video.event_t = time.perf_counter()
        except Exception as e:      # show it instead of crashing the app
            QMessageBox.warning(self, "Couldn't run that",
                                f"“{name}” → {describe(action)} failed:\n\n{e}")

    def _confirm(self, g: dict) -> bool:
        self.confirming = True
        try:
            box = QMessageBox(QMessageBox.Icon.Question, "AirDesk",
                              f"Your “{g['name']}” gesture wants to "
                              f"{describe(g['action']).lower()}.\n\n{g['action']['value']}",
                              QMessageBox.StandardButton.Cancel, self)
            go = box.addButton("Open it", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            box.exec()
            return box.clickedButton() is go
        finally:
            self.confirming = False

    # -- control on/off and presenter mode ----------------------------------
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
            self.laser.hide()

    def set_presenter(self, on: bool):
        self.tracker.engine.presenter = on
        if on:
            self.hints.place()
            self.hints.show()
        else:
            self.hints.hide()
            self.laser.hide()

    # -- frames from the tracker thread -------------------------------------
    def on_frame(self, frame: Frame):
        out = frame.out
        teaching = self.teach is not None
        status = out.status if out else ("armed" if self.control_on else "off")
        if not self.control_on or teaching:
            status = "off"
        self.status = status
        color = STATE_COLORS[status]
        self.video.show_frame(frame, status)
        self.pill.set_state(status)
        self.indicator.pill.set_state(status)
        self.sub.setText(subtitle(status, self.control_on, frame.landmarks is not None,
                                  teaching, self.presenter.isChecked()))
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

        if self.presenter.isChecked():
            laser = out.laser if out and self.control_on else None
            self.laser.point_at(to_logical(*laser) if laser else None)
            self.hints.set_state(status, out.event if out else "")
        if self.teach is not None:
            self.teach.on_frame(frame)

    def closeEvent(self, event):
        self.tracker.set_control(False)
        self.tracker.stop()
        self.hotkey.stop()
        for w in (self.indicator, self.laser, self.hints):
            w.close()
        if self.teach is not None:
            self.teach.close()
        super().closeEvent(event)


def open_path(target: str):
    """Open an app or file the way double-clicking it would. No shell involved."""
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 (target was chosen by the user in Teach)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def take_screenshot() -> Path:
    folder = Path.home() / "Pictures" / "AirDesk"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("screenshot-%Y%m%d-%H%M%S.png")
    QGuiApplication.primaryScreen().grabWindow(0).save(str(path))
    return path
