"""Teach window: record a gesture, check it on held-out samples, map it to an action."""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

from .control import ACTION_TYPES, parse_combo
from .custom import SAMPLES_PER_GESTURE, CustomModel, holdout_check, round_sample
from .tracker import Frame
from .widgets import AMBER, MUTED, TEXT, VideoView, label

SAMPLE_GAP_S = 0.12
COUNTDOWN_S = 3.0
BUILTIN_SHAPES = ("Point", "Pinch", "Two-finger pinch", "Two fingers", "Open palm", "Fist")
PLACEHOLDERS = {
    "keys": "e.g. ctrl+shift+m  or  win+shift+s",
    "open_app": r"e.g. notepad  or  C:\Program Files\App\app.exe",
    "open_url": "e.g. https://github.com",
}


class TeachDialog(QDialog):
    saved = pyqtSignal(dict, str)          # gesture, the name it had before (for edits)

    def __init__(self, parent, gestures: list, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Teach a gesture" if existing is None else "Edit gesture")
        self.setMinimumSize(980, 660)
        self.others = [g for g in gestures if existing is None or g["name"] != existing["name"]]
        self.original = existing["name"] if existing else ""
        self.samples = list(existing["samples"]) if existing else []
        self.builtins: list = []
        self.state = "idle"                # idle | countdown | recording | done
        self.t_state = 0.0
        self.last_sample = 0.0
        self.preview = None

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(20)
        self.video = VideoView()
        self.video.setMinimumSize(440, 300)
        root.addWidget(self.video, 1)

        side = QWidget()
        side.setFixedWidth(380)
        form = QVBoxLayout(side)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        root.addWidget(side)
        form.addWidget(label("Teach a gesture" if existing is None else "Edit gesture",
                             16, TEXT, QFont.Weight.Bold))
        form.addWidget(label("Name", 9, MUTED))
        self.name = QLineEdit(existing["name"] if existing else "")
        self.name.setPlaceholderText("e.g. Peace, Thumbs up, Rock on")
        self.name.setMaxLength(40)
        form.addWidget(self.name)

        form.addWidget(label("When I show it, do this", 9, MUTED))
        self.kind = QComboBox()
        for key, (text, _, _) in ACTION_TYPES.items():
            self.kind.addItem(text, key)
        form.addWidget(self.kind)
        row = QHBoxLayout()
        self.value = QLineEdit()
        row.addWidget(self.value, 1)
        self.browse = QPushButton("Browse\u2026")
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.browse)
        form.addLayout(row)
        self.value_note = label("", 8.5, MUTED)
        self.value_note.setWordWrap(True)
        form.addWidget(self.value_note)
        if existing:
            self.kind.setCurrentIndex(max(0, self.kind.findData(existing["action"]["type"])))
            self.value.setText(existing["action"].get("value", ""))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

        form.addSpacing(6)
        self.status = label("", 11, TEXT, QFont.Weight.DemiBold)
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, SAMPLES_PER_GESTURE)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        form.addWidget(self.progress)
        self.record = QPushButton(f"Record {SAMPLES_PER_GESTURE} samples")
        self.record.clicked.connect(self._start)
        form.addWidget(self.record)
        tip = label("Keep the shape, but move your hand a little while recording (nearer, "
                    "further, slightly turned) so AirDesk learns the gesture, not one frame.",
                    8.5, MUTED)
        tip.setWordWrap(True)
        form.addWidget(tip)
        self.result = label("", 9.5, TEXT)
        self.result.setWordWrap(True)
        form.addWidget(self.result)
        self.warning = label("", 9.5, AMBER)
        self.warning.setWordWrap(True)
        form.addWidget(self.warning)
        self.live = label("", 9.5, MUTED)
        form.addWidget(self.live)
        form.addStretch(1)
        self.error = label("", 9.5, "#ff6b7a")
        self.error.setWordWrap(True)
        form.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.save = QPushButton("Save gesture")
        self.save.setObjectName("primary")
        self.save.clicked.connect(self._save)
        buttons.addWidget(self.save)
        form.addLayout(buttons)

        if self.samples:
            self.progress.setValue(len(self.samples))
            self._evaluate()
        self._set_status()

    # -- form ---------------------------------------------------------------
    def _kind_changed(self):
        kind = self.kind.currentData()
        needs_value = ACTION_TYPES[kind][1]
        self.value.setVisible(needs_value)
        self.value.setPlaceholderText(PLACEHOLDERS.get(kind, ""))
        self.browse.setVisible(kind == "open_app")
        notes = {
            "open_app": "AirDesk asks you to confirm before opening it.",
            "open_url": "AirDesk asks you to confirm before opening it.",
            "screenshot": "Saved as a PNG in Pictures\\AirDesk.",
            "keys": "Pressed like you typed it. Modifiers: ctrl, shift, alt, win.",
        }
        self.value_note.setText(notes.get(kind, ""))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app or file")
        if path:
            self.value.setText(os.path.normpath(path))

    def _action(self) -> dict:
        kind = self.kind.currentData()
        value = self.value.text().strip()
        if kind == "keys":
            parse_combo(value)
        elif kind == "open_app":
            if not value:
                raise ValueError("Choose the app or file to open.")
            looks_like_path = os.sep in value or "/" in value
            if looks_like_path and not os.path.exists(value):
                raise ValueError("I can't find that file. Use Browse\u2026 to pick it.")
            if not looks_like_path and shutil.which(value) is None:
                raise ValueError(f"I can't find an app called \u201c{value}\u201d. "
                                 "Use Browse\u2026 to pick it.")
        elif kind == "open_url":
            if "://" not in value:
                value = "https://" + value
            if not value.startswith(("http://", "https://")) or "." not in value:
                raise ValueError("Type a web address like https://github.com.")
        return {"type": kind, "value": value if ACTION_TYPES[kind][1] else ""}

    # -- recording ----------------------------------------------------------
    def _start(self):
        self.samples, self.builtins = [], []
        self.preview = None
        self.result.setText("")
        self.warning.setText("")
        self.live.setText("")
        self.progress.setValue(0)
        self.state, self.t_state = "countdown", time.perf_counter()
        self.record.setEnabled(False)

    def _set_status(self, hand: bool = True):
        if self.state == "countdown":
            left = COUNTDOWN_S - (time.perf_counter() - self.t_state)
            self.status.setText(f"Get ready\u2026 {max(1, int(left) + 1)}")
        elif self.state == "recording":
            self.status.setText(f"Recording {len(self.samples)}/{SAMPLES_PER_GESTURE}"
                                + ("" if hand else " \u2014 show your hand"))
        elif self.samples:
            self.status.setText(f"{len(self.samples)} samples recorded.")
        else:
            self.status.setText("Show the gesture to the camera, then press Record.")

    def on_frame(self, frame: Frame):
        self.video.show_frame(frame, "armed" if self.state == "recording" else "off")
        now = time.perf_counter()
        if self.state == "countdown" and now - self.t_state >= COUNTDOWN_S:
            self.state, self.last_sample = "recording", 0.0
        if self.state == "recording" and frame.features is not None \
                and now - self.last_sample >= SAMPLE_GAP_S:
            self.samples.append(round_sample(frame.features))
            self.builtins.append(frame.builtin)
            self.last_sample = now
            self.progress.setValue(len(self.samples))
            if len(self.samples) >= SAMPLES_PER_GESTURE:
                self.state = "done"
                self.record.setEnabled(True)
                self.record.setText("Record again")
                self._evaluate()
                self._set_status()
        if self.state in ("countdown", "recording"):
            self._set_status(frame.features is not None)
        elif self.preview is not None:
            hit = self.preview.predict(frame.features) if frame.features is not None else None
            name = self.name.text().strip() or "New gesture"
            if frame.features is None:
                self.live.setText("Live test: no hand in view")
            elif hit is None:
                self.live.setText("Live test: no match (good for other hand shapes)")
            else:
                shown = name if hit[0] == "\0new" else hit[0]
                self.live.setText(f"Live test: matches \u201c{shown}\u201d ({hit[1] * 100:.0f}%)")

    def _evaluate(self):
        """Measure on held-out samples and look for clashes with built-in gestures."""
        candidate = {"name": "\0new", "samples": self.samples}
        everything = self.others + [candidate]
        check = holdout_check(everything)
        c, t = check["per"]["\0new"]
        text = (f"Held-out check: {c} of {t} held-out samples of this gesture recognised"
                f" ({len(self.samples) - t} used for training; all samples from 1 person, you).")
        if self.others:
            text += (f" Across all {len(everything)} gestures: "
                     f"{check['correct']}/{check['total']} held-out samples correct.")
        self.result.setText(text)
        self.preview = CustomModel.from_gestures(everything)

        warn = []
        if t and c / t < 0.8:
            warn.append("Several held-out samples were missed. Try recording again with a "
                        "steadier shape.")
        if self.builtins:
            shape, n = Counter(self.builtins).most_common(1)[0]
            if shape in BUILTIN_SHAPES and n / len(self.builtins) >= 0.6:
                warn.append(f"This looks like the built-in \u201c{shape}\u201d gesture "
                            f"({n} of {len(self.builtins)} samples). Doing {shape} may trigger "
                            "this gesture instead. A more distinct shape works better.")
        self.warning.setText("\n".join(warn))

    # -- save ---------------------------------------------------------------
    def _save(self):
        name = self.name.text().strip()
        try:
            if not name:
                raise ValueError("Give the gesture a name.")
            if any(g["name"].lower() == name.lower() for g in self.others):
                raise ValueError(f"You already have a gesture called \u201c{name}\u201d.")
            if len(self.samples) < SAMPLES_PER_GESTURE:
                raise ValueError("Record the gesture first.")
            action = self._action()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.saved.emit({"name": name, "action": action, "samples": self.samples},
                        self.original)
        self.accept()
