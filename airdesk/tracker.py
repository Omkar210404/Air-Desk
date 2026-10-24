"""Camera capture, hand tracking and gesture actions, all on a background thread.

Frames stay in memory: nothing from the camera is ever written to disk.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from .control import SystemControl
from .custom import features
from .gestures import GestureEngine, Output, analyse

# Quality levels, best first: (processing width, MediaPipe model complexity, capture size).
LEVELS = [
    (960, 1, (1280, 720)),
    (640, 1, (1280, 720)),
    (640, 0, (640, 480)),
    (424, 0, (640, 480)),
]
TARGET_FPS = 24        # step down a level if we stay below this...
SLOW_PROC_MS = 28      # ...and our own processing is the bottleneck
DARK_LEVEL = 45        # mean brightness (0-255) below which we call it low light

NO_CAMERA = ("No camera found",
             "Plug in a webcam. On Windows, also check Settings › Privacy & security › "
             "Camera and allow desktop apps to use it. AirDesk keeps trying by itself.")
CAMERA_BUSY = ("The camera is busy",
               "Another app (Teams, Zoom, the Camera app…) is probably using it. "
               "Close that app and AirDesk will reconnect by itself.")
CAMERA_LOST = ("Lost the camera", "Trying to reconnect…")
LOW_LIGHT = "It's quite dark — face a window or turn on a light so your hand is easier to see."


@dataclass
class Frame:
    image: QImage | None = None
    landmarks: np.ndarray | None = None     # (21, 2) in 0..1, mirrored like the image
    out: Output | None = None
    fps: float = 0.0
    proc_ms: float = 0.0
    quality: str = ""
    error: tuple | None = None              # (title, body) when there is no video
    warning: str = ""
    features: np.ndarray | None = None      # normalised landmarks, for teaching
    builtin: str = ""                       # what the built-in rules see (before custom)
    match: tuple | None = None              # (name, confidence) of a taught gesture


class Tracker(QThread):
    frame = pyqtSignal(object)
    custom = pyqtSignal(str)                # a taught gesture fired


    def __init__(self, camera_index: int, screen: tuple[int, int]):
        super().__init__()
        self.camera_index = camera_index
        self.engine = GestureEngine(*screen)
        self.control = SystemControl()
        self.control_on = False
        self.teaching = False                # Teach window open: watch, never act
        self.custom_model = None             # replaced as a whole by the UI thread
        self.level = 0
        self._stop = False

    # Called from the UI thread; a plain bool assignment is atomic in CPython.
    def set_control(self, on: bool):
        self.control_on = on

    def stop(self):
        self._stop = True
        self.wait(4000)

    # -- camera -------------------------------------------------------------
    def _open(self):
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
        busy = False
        w, h = LEVELS[self.level][2]
        for backend in backends:
            cap = cv2.VideoCapture(self.camera_index, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_FPS, 30)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(25):                 # some cameras need a moment to warm up
                ok, img = cap.read()
                if ok and img is not None:
                    return cap, None
                time.sleep(0.08)
            cap.release()
            busy = True                         # it opened but gave no frames
        return None, (CAMERA_BUSY if busy else NO_CAMERA)

    def _idle(self, seconds: float):
        end = time.perf_counter() + seconds
        while not self._stop and time.perf_counter() < end:
            time.sleep(0.05)

    def _release_input(self):
        """Hand is gone or camera failed: make sure no mouse button stays held."""
        out = self.engine.update(None, time.perf_counter(), self.control_on)
        self.control.run(out.actions)
        self.control.release()
        return out

    # -- main loop ----------------------------------------------------------
    def run(self):
        cap, hands, complexity = None, None, None
        fps, proc_ms, frame_gap = 0.0, 0.0, 0.0
        last_t = None
        window_start, window_frames, window_tracked, window_proc = time.perf_counter(), 0, 0, 0.0
        frame_no, tracking = 0, False
        dark_frames, note, note_until = 0, "", 0.0

        while not self._stop:
            proc_w, want_complexity, capture = LEVELS[self.level]
            if cap is None:
                cap, error = self._open()
                if cap is None:
                    out = self._release_input()
                    self.frame.emit(Frame(error=error, out=out))
                    self._idle(2.0)
                    continue
                last_t = None
                tracking = False
                window_start, window_frames, window_tracked, window_proc = time.perf_counter(), 0, 0, 0.0
            if hands is None or complexity != want_complexity:
                if hands is not None:
                    hands.close()
                hands = mp.solutions.hands.Hands(
                    static_image_mode=False, max_num_hands=1,
                    model_complexity=want_complexity,
                    min_detection_confidence=0.6, min_tracking_confidence=0.5)
                complexity = want_complexity

            ok, img = cap.read()
            if not ok or img is None:
                cap.release()
                cap = None
                out = self._release_input()
                self.frame.emit(Frame(error=CAMERA_LOST, out=out))
                self._idle(1.0)
                continue

            img = cv2.flip(img, 1)                         # mirror: move right, see right
            if img.shape[1] > proc_w:
                img = cv2.resize(img, (proc_w, round(img.shape[0] * proc_w / img.shape[1])),
                                 interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]

            # With no hand tracked, MediaPipe runs its heavier palm search on every frame.
            # At 30 fps, searching on every other frame keeps the video smooth. When the
            # camera itself is slow (dim light), there is time to search every frame.
            frame_no += 1
            hand, feats, match = None, None, None
            if tracking or frame_no % 2 == 0 or (0 < fps < TARGET_FPS):
                t0 = time.perf_counter()
                rgb.flags.writeable = False
                result = hands.process(rgb)
                spent = (time.perf_counter() - t0) * 1000
                proc_ms = spent if proc_ms == 0 else 0.9 * proc_ms + 0.1 * spent
                if result.multi_hand_landmarks:
                    lm = np.array([[p.x, p.y, p.z]
                                   for p in result.multi_hand_landmarks[0].landmark])
                    handed = result.multi_handedness[0].classification[0]
                    hand = analyse(lm, w / h, handed.score)
                    feats = features(lm, w / h, handed.label)
                    model = self.custom_model
                    if model is not None:
                        match = model.predict(feats)
                    window_tracked += 1
                    window_proc += spent
                tracking = hand is not None

            now = time.perf_counter()
            out = self.engine.update(hand, now, self.control_on and not self.teaching, match)
            builtin = self.engine.builtin if hand is not None else ""
            if out.actions:
                self.control.run(out.actions)
                for action in out.actions:
                    if action[0] == "custom":
                        self.custom.emit(action[1])
            if not self.control_on or self.teaching:
                self.control.release()

            if last_t is not None:                 # average frame gaps, not rates
                gap = now - last_t
                frame_gap = gap if frame_gap == 0 else 0.9 * frame_gap + 0.1 * gap
                fps = 1.0 / max(frame_gap, 1e-3)
            last_t = now

            # light check on a coarse grid of pixels (cheap)
            dark_frames = dark_frames + 1 if rgb[::16, ::16].mean() < DARK_LEVEL else 0

            # Adapt quality every 2 s, judged only on frames where a hand was tracked
            # (that is when smoothness matters).
            window_frames += 1
            if now - window_start >= 2.0:
                avg_fps = window_frames / (now - window_start)
                avg_proc = window_proc / max(window_tracked, 1)
                if (window_tracked > window_frames / 2 and avg_fps < TARGET_FPS
                        and avg_proc > SLOW_PROC_MS and self.level < len(LEVELS) - 1):
                    self.level += 1
                    note, note_until = "Lowered video quality to keep tracking smooth.", now + 4
                    if LEVELS[self.level][2] != capture:
                        cap.release()
                        cap = None
                elif avg_fps < TARGET_FPS - 6 and dark_frames == 0 and now > note_until:
                    note, note_until = (f"The camera is only sending {avg_fps:.0f} frames a second. "
                                        "More light usually helps."), now + 4
                window_start, window_frames, window_tracked, window_proc = now, 0, 0, 0.0

            warning = LOW_LIGHT if dark_frames > 30 else (note if now < note_until else "")
            image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            pts = self.engine.last_pts
            self.frame.emit(Frame(image, pts, out, fps, proc_ms,
                                  f"{w}×{h} · model {complexity}", None, warning,
                                  feats, builtin, match))

        if cap is not None:
            cap.release()
        if hands is not None:
            hands.close()
        self.control.release()
