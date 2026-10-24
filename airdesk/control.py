"""System control (mouse + keyboard) and the global safety hotkey, via pynput."""
from __future__ import annotations

import ctypes
import logging
import os
import sys

from pynput.keyboard import Controller as Keyboard, GlobalHotKeys, Key, KeyCode
from pynput.mouse import Button, Controller as Mouse

log = logging.getLogger(__name__)

HOTKEY = "<ctrl>+<alt>+g"
HOTKEY_LABEL = "Ctrl+Alt+G"

KEYS = {"left": Key.left, "right": Key.right}

# Actions a taught gesture can run: type -> (menu label, needs a value, asks first).
ACTION_TYPES = {
    "keys": ("Key or shortcut", True, False),
    "mute": ("Mute / unmute", False, False),
    "volume_up": ("Volume up", False, False),
    "volume_down": ("Volume down", False, False),
    "play_pause": ("Play / pause media", False, False),
    "next_track": ("Next track", False, False),
    "prev_track": ("Previous track", False, False),
    "screenshot": ("Screenshot", False, False),
    "open_app": ("Open an app or file", True, True),
    "open_url": ("Open a website", True, True),
}
MEDIA = {
    "mute": Key.media_volume_mute, "volume_up": Key.media_volume_up,
    "volume_down": Key.media_volume_down, "play_pause": Key.media_play_pause,
    "next_track": Key.media_next, "prev_track": Key.media_previous,
}
MODIFIERS = {
    "ctrl": Key.ctrl, "control": Key.ctrl, "shift": Key.shift, "alt": Key.alt,
    "win": Key.cmd, "windows": Key.cmd, "cmd": Key.cmd, "super": Key.cmd, "meta": Key.cmd,
}
NAMED = {
    "enter": Key.enter, "return": Key.enter, "esc": Key.esc, "escape": Key.esc,
    "space": Key.space, "tab": Key.tab, "backspace": Key.backspace, "delete": Key.delete,
    "del": Key.delete, "insert": Key.insert, "home": Key.home, "end": Key.end,
    "pageup": Key.page_up, "pgup": Key.page_up, "pagedown": Key.page_down,
    "pgdn": Key.page_down, "up": Key.up, "down": Key.down, "left": Key.left,
    "right": Key.right, "printscreen": Key.print_screen, "prtsc": Key.print_screen,
    **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
}


def parse_combo(text: str) -> list:
    """'Ctrl+Shift+M' -> pynput keys. Raises ValueError with a friendly message."""
    parts = [p for p in text.lower().replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("Type a shortcut, for example ctrl+shift+m or win+shift+s.")
    keys = []
    for part in parts:
        if part in MODIFIERS:
            keys.append(MODIFIERS[part])
        elif part in NAMED:
            keys.append(NAMED[part])
        elif len(part) == 1:
            keys.append(KeyCode.from_char(part))
        else:
            raise ValueError(f"I don't know the key “{part}”. Use names like ctrl, shift, "
                             "alt, win, enter, space, f5, left, or single letters.")
    if all(k in MODIFIERS.values() for k in keys):
        raise ValueError("Add a normal key after the modifiers, like ctrl+shift+m.")
    return keys


def describe(action: dict) -> str:
    kind, value = action.get("type", ""), action.get("value", "")
    if kind == "keys":
        return "+".join(p.capitalize() if len(p) > 1 else p.upper()
                        for p in value.replace(" ", "").split("+") if p)
    if kind == "open_app":
        return "Open " + os.path.basename(value.rstrip("\\/")) if value else "Open app"
    if kind == "open_url":
        return "Open " + value.split("://")[-1].split("/")[0] if value else "Open website"
    return ACTION_TYPES.get(kind, ("Nothing",))[0]


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

    def press_keys(self, action: dict):
        """Run a keyboard-only action: a shortcut or a media key."""
        if action["type"] == "keys":
            keys = parse_combo(action["value"])
        else:
            keys = [MEDIA[action["type"]]]
        for k in keys:
            self.keyboard.press(k)
        for k in reversed(keys):
            self.keyboard.release(k)

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
