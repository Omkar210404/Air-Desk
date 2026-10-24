"""Hand features and the built-in, rule-based gestures.

Nothing here touches the camera, the screen or the mouse: `GestureEngine.update`
takes one analysed hand per frame and returns a list of actions for
`control.SystemControl` to carry out.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# --- tuning ---------------------------------------------------------------
# Part of the camera image that maps to the whole screen, as (x0, y0, x1, y1).
# Smaller than the frame so you can reach screen edges without leaving the view.
CURSOR_BOX = (0.20, 0.12, 0.80, 0.68)
DEAD_ZONE_PX = 4            # ignore cursor moves smaller than this (hand tremor)
SMOOTH_MIN_CUTOFF = 1.0     # One Euro filter: lower = smoother when still
SMOOTH_BETA = 0.004         # One Euro filter: higher = less lag when moving fast

PINCH_ON, PINCH_OFF = 0.26, 0.36   # thumb-index tip distance / hand size
PINCH_NEAR = 0.50           # cursor freezes as the fingers close, so clicks land where you aimed
RPINCH_ON, RPINCH_OFF = 0.32, 0.40
DRAG_HOLD_S = 0.40          # pinch longer than this becomes a drag
SCROLL_STEP = 0.03          # hand travel (fraction of frame height) per wheel notch
SWIPE_DIST = 0.20           # fraction of frame width
SWIPE_WINDOW_S = 0.35
FIST_HOLD_S = 0.6
ARM_DELAY_S = 0.35          # hand must be steady this long before control starts
LOST_GRACE_S = 0.15         # ride out a frame or two where tracking blinks
COOLDOWN = {"click": 0.20, "rclick": 0.6, "swipe": 0.9, "fist": 1.0}
CUSTOM_HOLD_S = 0.5         # hold a taught gesture this long to fire it
CUSTOM_COOLDOWN_S = 1.0

FINGERS = {"index": (6, 8), "middle": (10, 12), "ring": (14, 16), "pinky": (18, 20)}

# Pairs of landmark ids that make up the hand skeleton (MediaPipe layout).
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class Hand:
    pts: np.ndarray            # (21, 2) image coords in 0..1, already mirrored
    ext: dict                  # finger -> 0..1, how straight it is
    pinch: float               # thumb tip to index tip, in hand sizes
    pinch_mid: float           # thumb tip to middle tip
    tips_im: float             # index tip to middle tip
    palm: np.ndarray           # palm centre (x, y) in 0..1
    score: float = 1.0         # MediaPipe's hand confidence


def analyse(lm: np.ndarray, aspect: float, score: float = 1.0) -> Hand:
    """Turn 21 MediaPipe landmarks (x, y, z) into scale-free features."""
    p = np.asarray(lm, dtype=np.float64).copy()
    p[:, 0] *= aspect          # x and z are relative to width, y to height:
    p[:, 2] *= aspect          # bring them to one unit before measuring
    size = float(np.linalg.norm(p[0] - p[9])) + 1e-6   # wrist to middle knuckle

    def d(a: int, b: int) -> float:
        return float(np.linalg.norm(p[a] - p[b]))

    ext = {}
    for name, (pip, tip) in FINGERS.items():
        # A straight finger's tip is clearly farther from the wrist than its middle joint.
        ratio = d(0, tip) / (d(0, pip) + 1e-6)
        ext[name] = _sig((ratio - 1.12) / 0.06)
    lm2 = np.asarray(lm, dtype=np.float64)[:, :2]
    palm = lm2[[0, 5, 9, 13, 17]].mean(axis=0)
    return Hand(lm2, ext, d(4, 8) / size, d(4, 12) / size, d(8, 12) / size, palm, score)


class OneEuro:
    """One Euro filter: smooth when the hand is still, responsive when it moves."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self, x=None, t: float = 0.0):
        self.x = None if x is None else np.asarray(x, dtype=np.float64)
        self.dx = np.zeros(2)
        self.t = t

    def __call__(self, x, t: float):
        x = np.asarray(x, dtype=np.float64)
        if self.x is None:
            self.reset(x, t)
            return x
        dt = max(t - self.t, 1e-3)
        self.t = t
        a_d = self._alpha(dt, self.d_cutoff)
        self.dx = a_d * (x - self.x) / dt + (1 - a_d) * self.dx
        a = self._alpha(dt, self.min_cutoff + self.beta * float(np.linalg.norm(self.dx)))
        self.x = a * x + (1 - a) * self.x
        return self.x


@dataclass
class Output:
    status: str = "off"        # off | armed | paused | active
    label: str = "No hand"
    confidence: float = 0.0
    actions: list = field(default_factory=list)
    event: str = ""            # short message to flash, e.g. "Click"
    hold: float = 0.0          # 0..1 progress of a fist or custom-gesture hold
    laser: tuple | None = None # presenter mode: laser dot position on screen


class GestureEngine:
    def __init__(self, screen_w: int, screen_h: int):
        self.sw, self.sh = screen_w, screen_h
        self.filter = OneEuro(SMOOTH_MIN_CUTOFF, SMOOTH_BETA)
        self.paused = False
        self.presenter = False     # pointing drives a laser dot; no clicks or scrolling
        self.cursor = None
        self._last: dict = {}
        self.dragging = False
        self.last_pts = None       # landmarks to draw (kept through short dropouts)
        self.builtin = ""
        self.last_seen = -1e9
        self._held_out = Output()
        self.reset_hand()

    # -- helpers ------------------------------------------------------------
    def reset_hand(self):
        self.hand_since = None
        self.pinch_on = self.rpinch_on = False
        self.pinch_since = None
        self.dragging = False
        self.drag_offset = np.zeros(2)
        self.block_pinch = False
        self.rclick_fired = False
        self.scroll_ref = None
        self.palm_hist: deque = deque()
        self.last_open = -1e9
        self.fist_since = None
        self.fist_latched = False
        self.custom_name = None
        self.custom_since = 0.0
        self.custom_fired = False
        self.custom_last = None
        self.custom_seen = -1e9
        self.filter.reset()

    def _ready(self, name: str, t: float, cooldown: float | None = None) -> bool:
        """Cooldown gate: True (and starts the cooldown) if `name` may fire now."""
        if t - self._last.get(name, -1e9) < (COOLDOWN[name] if cooldown is None else cooldown):
            return False
        self._last[name] = t
        return True

    def _map(self, xy) -> np.ndarray:
        x0, y0, x1, y1 = CURSOR_BOX
        u = _clip01((xy[0] - x0) / (x1 - x0))
        v = _clip01((xy[1] - y0) / (y1 - y0))
        return np.array([u * (self.sw - 1), v * (self.sh - 1)])

    def _classify(self, h: Hand):
        if self.pinch_on:
            self.pinch_on = h.pinch < PINCH_OFF
        else:
            self.pinch_on = h.pinch < PINCH_ON
        rp = max(h.pinch, h.pinch_mid)
        if self.rpinch_on:
            self.rpinch_on = rp < RPINCH_OFF
        else:   # index and middle must touch each other too, not just a curled middle finger
            self.rpinch_on = rp < RPINCH_ON and h.tips_im < 0.30

        if self.rpinch_on:
            return "Two-finger pinch", _clip01((0.5 - rp) / 0.3)
        if self.pinch_on:
            return "Pinch", _clip01((0.5 - h.pinch) / 0.3)
        i, m, r, p = (h.ext[k] for k in ("index", "middle", "ring", "pinky"))
        poses = {
            "Fist": min(1 - i, 1 - m, 1 - r, 1 - p),
            "Open palm": min(i, m, r, p),
            "Two fingers": min(i, m, 1 - r, 1 - p),
            "Point": min(i, 1 - m),
        }
        label = max(poses, key=poses.get)
        if poses[label] < 0.5:
            return "Hand", poses[label]
        return label, poses[label]

    # -- main entry ---------------------------------------------------------
    def update(self, hand: Hand | None, t: float, enabled: bool, custom=None) -> Output:
        """`custom` is (name, confidence) when a taught gesture matches this frame."""
        out = Output()
        if hand is None and self.hand_since is not None and t - self.last_seen < LOST_GRACE_S:
            held = self._held_out
            return Output(held.status, held.label, held.confidence)
        if hand is None:
            self.last_pts = None
            if self.dragging:
                out.actions.append(("up",))
            self.reset_hand()
            out.status = "off" if not enabled else ("paused" if self.paused else "armed")
            return out

        self.last_seen = t
        self.last_pts = hand.pts
        if self.hand_since is None:
            self.hand_since = t
        label, conf = self._classify(hand)
        out.label, out.confidence = label, conf * hand.score
        self.builtin = label        # what the built-in rules see, before any custom match

        # A taught gesture outranks the built-in ones while it is shown.
        if custom is not None:
            self.custom_last, self.custom_seen = custom, t
        elif self.custom_last is not None and t - self.custom_seen < LOST_GRACE_S:
            custom = self.custom_last
        else:
            self.custom_last = None
        if custom is not None:
            label = out.label = custom[0]
            out.confidence = custom[1]

        # Fist = pause / resume. Works while paused, so it can always undo itself.
        if label == "Fist":
            if self.fist_since is None:
                self.fist_since = t
            held = t - self.fist_since
            out.hold = _clip01(held / FIST_HOLD_S) if not self.fist_latched else 0.0
            if enabled and not self.fist_latched and held >= FIST_HOLD_S and self._ready("fist", t):
                self.paused = not self.paused
                self.fist_latched = True
                out.event = "Paused" if self.paused else "Resumed"
        else:
            self.fist_since = None
            self.fist_latched = False

        if not enabled or self.paused:
            if self.dragging:
                out.actions.append(("up",))
                self.dragging = False
            self.pinch_since = None
            self.scroll_ref = None
            out.status = "paused" if enabled else "off"
            self._held_out = out
            return out
        if t - self.hand_since < ARM_DELAY_S:
            out.status = "armed"
            self.filter.reset()
            self._held_out = out
            return out

        out.status = "active"
        self._act(hand, label, t, out, custom is not None)
        self._held_out = out
        return out

    def _release_drag(self, out: Output):
        if self.dragging:
            out.actions.append(("up",))
            self.dragging = False
        self.pinch_since = None

    def _custom(self, name: str, t: float, out: Output):
        if self.custom_name != name:
            self.custom_name, self.custom_since, self.custom_fired = name, t, False
        if self.custom_fired:
            return
        held = t - self.custom_since
        out.hold = _clip01(held / CUSTOM_HOLD_S)
        if held >= CUSTOM_HOLD_S and self._ready("custom:" + name, t, CUSTOM_COOLDOWN_S):
            out.actions.append(("custom", name))
            out.event = name
            self.custom_fired = True

    def _act(self, h: Hand, label: str, t: float, out: Output, is_custom: bool):
        target = self._map(h.pts[8])
        move = False

        if is_custom:
            self._release_drag(out)
            self.scroll_ref = None
            self.palm_hist.clear()
            self._custom(label, t, out)
            if self.cursor is not None:
                self.filter.reset(self.cursor, t)
            return
        self.custom_name = None

        if self.presenter:          # no clicks, drags or scrolling during a talk
            self._release_drag(out)
            self.scroll_ref = None
        else:
            move = self._mouse_gestures(h, label, t, out)
            if move:
                target = target + self.drag_offset

        self._swipe(h, label, t, out)

        # Cursor (or laser) follows the index tip while pointing, not while closing a pinch.
        if (not move and h.ext["index"] > 0.5 and label in ("Point", "Hand")
                and h.pinch > PINCH_NEAR):
            move = True

        if move and self.presenter:
            f = self.filter(target, t)
            self.cursor = f
            out.laser = (float(f[0]), float(f[1]))
        elif move:
            f = self.filter(target, t)
            if self.cursor is None or np.linalg.norm(f - self.cursor) >= DEAD_ZONE_PX:
                self.cursor = f
                out.actions.append(("move", int(round(f[0])), int(round(f[1]))))
        elif self.cursor is not None:
            # Hold the filter at the frozen cursor so resuming glides instead of jumping.
            self.filter.reset(self.cursor, t)

    def _mouse_gestures(self, h: Hand, label: str, t: float, out: Output) -> bool:
        """Clicks, right click, drag and scroll. Returns True while dragging."""
        target = self._map(h.pts[8])
        # Right click: thumb touches index and middle together (fires once per pinch).
        if self.rpinch_on:
            if self.dragging:
                out.actions.append(("up",))
                self.dragging = False
            if not self.rclick_fired and self._ready("rclick", t):
                out.actions.append(("rclick",))
                out.event = "Right click"
                self.rclick_fired = True
            self.block_pinch = True        # don't turn the release into a left click
            self.pinch_since = None
        else:
            self.rclick_fired = False

        # Left pinch: short = click, held = drag.
        if self.pinch_on and not self.rpinch_on and not self.block_pinch:
            if self.pinch_since is None:
                self.pinch_since = t
            if not self.dragging and t - self.pinch_since >= DRAG_HOLD_S:
                out.actions.append(("down",))
                out.event = "Drag"
                self.dragging = True
                anchor = self.cursor if self.cursor is not None else target
                self.drag_offset = anchor - target
            if self.dragging:
                out.label = "Drag"
        elif self.pinch_since is not None and not self.pinch_on:
            if self.dragging:
                out.actions.append(("up",))
                out.event = "Drop"
                self.dragging = False
            elif self._ready("click", t):
                out.actions.append(("click",))
                out.event = "Click"
            self.pinch_since = None
        if not self.pinch_on and not self.rpinch_on:
            self.block_pinch = False

        # Scroll: two fingers up, move the hand up or down.
        if label == "Two fingers":
            y = (h.pts[8][1] + h.pts[12][1]) / 2
            if self.scroll_ref is None:
                self.scroll_ref = y
            steps = int((self.scroll_ref - y) / SCROLL_STEP)
            if steps:
                out.actions.append(("scroll", steps))
                out.event = "Scroll up" if steps > 0 else "Scroll down"
                self.scroll_ref -= steps * SCROLL_STEP
        else:
            self.scroll_ref = None
        return self.dragging

    def _swipe(self, h: Hand, label: str, t: float, out: Output):
        """Open palm moved quickly sideways = previous / next slide."""
        if label == "Open palm":
            self.last_open = t
            self.palm_hist.append((t, float(h.palm[0]), float(h.palm[1])))
            while self.palm_hist and t - self.palm_hist[0][0] > SWIPE_WINDOW_S:
                self.palm_hist.popleft()
            t0, x0, y0 = self.palm_hist[0]
            dx, dy = h.palm[0] - x0, h.palm[1] - y0
            if abs(dx) > SWIPE_DIST and abs(dx) > 2 * abs(dy) and self._ready("swipe", t):
                right = dx > 0
                out.actions.append(("key", "right" if right else "left"))
                out.event = "Next slide  →" if right else "←  Previous slide"
                self.palm_hist.clear()
        elif t - self.last_open > 0.15:     # tolerate a frame or two of motion blur
            self.palm_hist.clear()
