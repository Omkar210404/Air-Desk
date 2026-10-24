"""System control (mouse + keyboard) and the global safety hotkey, via pynput."""
from __future__ import annotations

import ctypes
import logging
import sys

from pynput.keyboard import Controller as Keyboard, GlobalHotKeys, Key
from pynput.mouse import Button, Controller as Mouse

log = logging.getLogger(__name__)

HOTKEY = "<ctrl>+<alt>+g"
HOTKEY_LABEL = "Ctrl+Alt+G"

KEYS = {"left": Key.left, "right": Key.right}


class SystemControl:
    def __init__(self):
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self.held = False

    def run(self, actions):
        for action in actions:
            kind = action[0]
            try:
                if kind == "move":
                    self.mouse.position = (action[1], action[2])
                elif kind == "click":
                    self.mouse.click(Button.left)
                elif kind == "down":
                    self.mouse.press(Button.left)
                    self.held = True
                elif kind == "up":
                    self.release()
                elif kind == "rclick":
                    self.mouse.click(Button.right)
                elif kind == "scroll":
                    self.mouse.scroll(0, action[1])
                elif kind == "key":
                    key = KEYS[action[1]]
                    self.keyboard.press(key)
                    self.keyboard.release(key)
            except Exception:  # never let one failed OS call kill the tracking thread
                log.exception("action %s failed", action)

    def release(self):
        """Let go of the mouse button if a drag is in progress."""
        if self.held:
            self.mouse.release(Button.left)
            self.held = False


def screen_size() -> tuple[int, int]:
    """Primary screen size in the units the mouse controller uses.

    Call after QApplication exists: Qt makes the process DPI-aware on Windows,
    so this returns physical pixels, which is what SetCursorPos expects.
    """
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    from PyQt6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    g = screen.geometry()
    if sys.platform == "darwin":     # macOS moves the mouse in points, not pixels
        return g.width(), g.height()
    r = screen.devicePixelRatio()
    return int(g.width() * r), int(g.height() * r)


class Hotkey:
    """Global hotkey that works even when AirDesk is not the focused window."""

    def __init__(self, callback):
        self.listener = GlobalHotKeys({HOTKEY: callback})

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()
