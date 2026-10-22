"""Desktop control: screenshot, mouse, keyboard.

Backends
  xdotool    Linux / X11 (default inside Docker with Xvfb)
  pyautogui  macOS / Windows / Linux native (needs Accessibility + Screen Recording permission on macOS)

Coordinates given by the model are in *screenshot image space*; they are mapped back to the real
input space (handles down-scaling and HiDPI/Retina).
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import time

from ..types import ImagePart, TextPart
from .registry import ToolContext, tool

XDO_KEYS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "space",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next", "insert": "Insert",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "cmd": "super", "command": "super",
    "win": "super", "super": "super", "meta": "super", "capslock": "Caps_Lock", "printscreen": "Print",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
BUTTONS = {"left": 1, "middle": 2, "right": 3}


class Backend:
    name = "base"

    def size(self) -> tuple[int, int]: ...
    def move(self, x: int, y: int): ...
    def click(self, x: int, y: int, button: str = "left", count: int = 1): ...
    def down(self, button: str = "left"): ...
    def up(self, button: str = "left"): ...
    def drag(self, x1: int, y1: int, x2: int, y2: int): ...
    def scroll(self, x: int, y: int, direction: str, amount: int): ...
    def type(self, text: str): ...
    def key(self, combo: str): ...
    def cursor(self) -> tuple[int, int]: ...
    def active_window(self) -> str: return ""


class XdotoolBackend(Backend):
    name = "xdotool"

    def _x(self, *args: str) -> str:
        r = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or f"xdotool {' '.join(args)} failed")
        return r.stdout.strip()

    def size(self):
        w, h = self._x("getdisplaygeometry").split()
        return int(w), int(h)

    def move(self, x, y):
        self._x("mousemove", "--sync", str(x), str(y))

    def click(self, x, y, button="left", count=1):
        self.move(x, y)
        self._x("click", "--repeat", str(count), "--delay", "90", str(BUTTONS.get(button, 1)))

    def down(self, button="left"):
        self._x("mousedown", str(BUTTONS.get(button, 1)))

    def up(self, button="left"):
        self._x("mouseup", str(BUTTONS.get(button, 1)))

    def drag(self, x1, y1, x2, y2):
        self.move(x1, y1)
        self._x("mousedown", "1")
        steps = 12
        for i in range(1, steps + 1):
            self._x("mousemove", str(int(x1 + (x2 - x1) * i / steps)), str(int(y1 + (y2 - y1) * i / steps)))
            time.sleep(0.02)
        self._x("mouseup", "1")

    def scroll(self, x, y, direction, amount):
        self.move(x, y)
        btn = {"up": "4", "down": "5", "left": "6", "right": "7"}[direction]
        self._x("click", "--repeat", str(amount), "--delay", "40", btn)

    def type(self, text):
        for chunk in [text[i:i + 200] for i in range(0, len(text), 200)]:
            self._x("type", "--delay", "12", "--", chunk)

    def key(self, combo):
        parts = [XDO_KEYS.get(p.strip().lower(), p.strip()) for p in combo.replace(" ", "").split("+") if p.strip()]
        self._x("key", "--clearmodifiers", "+".join(parts))

    def cursor(self):
        out = dict(kv.split(":") for kv in self._x("getmouselocation").split()[:2])
        return int(out["x"]), int(out["y"])

    def active_window(self):
        try:
            return self._x("getactivewindow", "getwindowname")
        except RuntimeError:
            return ""


class PyAutoGUIBackend(Backend):
    name = "pyautogui"

    def __init__(self):
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        self.g = pyautogui

    def size(self):
        s = self.g.size()
        return int(s[0]), int(s[1])

    def move(self, x, y):
        self.g.moveTo(x, y, duration=0.1)

    def click(self, x, y, button="left", count=1):
        self.g.click(x, y, clicks=count, interval=0.09, button=button)

    def down(self, button="left"):
        self.g.mouseDown(button=button)

    def up(self, button="left"):
        self.g.mouseUp(button=button)

    def drag(self, x1, y1, x2, y2):
        self.g.moveTo(x1, y1)
        self.g.dragTo(x2, y2, duration=0.4, button="left")

    def scroll(self, x, y, direction, amount):
        self.g.moveTo(x, y)
        if direction in ("up", "down"):
            self.g.scroll(amount * (1 if direction == "up" else -1) * 3)
        else:
            self.g.hscroll(amount * (1 if direction == "right" else -1) * 3)

    def type(self, text):
        if text.isascii():
            self.g.write(text, interval=0.01)
        else:  # non-ASCII (e.g. Chinese): paste through the clipboard
            import subprocess as sp
            sysname = platform.system()
            if sysname == "Darwin":
                sp.run(["pbcopy"], input=text.encode(), check=True)
                self.g.hotkey("command", "v")
            elif sysname == "Windows":
                sp.run(["clip"], input=text.encode("utf-16"), check=True)
                self.g.hotkey("ctrl", "v")
            else:
                sp.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
                self.g.hotkey("ctrl", "v")

    def key(self, combo):
        mapping = {"cmd": "command", "super": "win", "meta": "command", "control": "ctrl", "escape": "esc",
                   "return": "enter", "del": "delete"}
        parts = [mapping.get(p.lower(), p.lower()) for p in combo.replace(" ", "").split("+") if p]
        self.g.hotkey(*parts) if len(parts) > 1 else self.g.press(parts[0])

    def cursor(self):
        p = self.g.position()
        return int(p[0]), int(p[1])


_backend: Backend | None = None


def get_backend(config) -> Backend:
    global _backend
    if _backend is not None:
        return _backend
    choice = config.get("desktop.backend", "auto")
    if choice == "xdotool" or (choice == "auto" and platform.system() == "Linux" and os.environ.get("DISPLAY")
                               and shutil.which("xdotool")):
        _backend = XdotoolBackend()
    else:
        _backend = PyAutoGUIBackend()
    return _backend


def available() -> bool:
    if platform.system() == "Linux":
        return bool(os.environ.get("DISPLAY")) and (shutil.which("xdotool") is not None or _has("pyautogui"))
    return _has("pyautogui")


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def grab_screen(max_width: int = 1280) -> tuple[bytes, int, int, int, int]:
    """Return (png_bytes, img_w, img_h, raw_w, raw_h)."""
    from PIL import Image
    try:
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        if shutil.which("import"):  # ImageMagick fallback
            raw = subprocess.run(["import", "-window", "root", "png:-"], capture_output=True, check=True).stdout
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        else:
            raise
    raw_w, raw_h = img.size
    if raw_w > max_width:
        img = img.resize((max_width, int(raw_h * max_width / raw_w)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.size[0], img.size[1], raw_w, raw_h


def _ex(ctx: ToolContext):
    """The session's executor: the screen and the input backend are reached through it (sandbox plan A1)."""
    from ..sandbox import executor_of
    return executor_of(ctx)


def _scale(ctx: ToolContext) -> tuple[float, float]:
    """image-space -> input-space factors (set by the last screenshot)."""
    s = ctx.state.get("desktop_scale")
    if s:
        return s
    backend = _ex(ctx).input()
    in_w, in_h = backend.size()
    max_w = int(ctx.config.get("desktop.screenshot_max_width", 1280))
    img_w = min(in_w, max_w)
    return in_w / img_w, in_w / img_w


def _map(ctx, x, y) -> tuple[int, int]:
    sx, sy = _scale(ctx)
    return int(round(float(x) * sx)), int(round(float(y) * sy))


@tool("screenshot", "desktop",
      "Take a screenshot of the whole desktop. Coordinates for mouse actions refer to this image. "
      "Always look before clicking and verify after acting.",
      {"properties": {}}, check=available)
def screenshot(ctx: ToolContext):
    ex = _ex(ctx)
    backend = ex.input()
    png, w, h, raw_w, raw_h = ex.screenshot(int(ctx.config.get("desktop.screenshot_max_width", 1280)))
    in_w, in_h = backend.size()
    ctx.state["desktop_scale"] = (in_w / w, in_h / h)
    window = backend.active_window()
    info = f"Screenshot {w}x{h} (screen {in_w}x{in_h}). Active window: {window or 'unknown'}"
    if not ctx.config.get("model.vision", True):
        return info + "\n(model has no vision; prefer the browser tool's text snapshot)"
    if ctx.emit:
        ctx.emit("screenshot", {"width": w, "height": h})
    return [TextPart(info), ImagePart.from_bytes(png)]


@tool("mouse", "desktop",
      "Control the mouse. actions: move, click, double_click, right_click, middle_click, drag (x,y -> to_x,to_y), "
      "scroll (direction up/down/left/right, amount = notches), down, up. Coordinates are in screenshot space.",
      {"properties": {
          "action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "middle_click",
                                                "drag", "scroll", "down", "up"]},
          "x": {"type": "number"}, "y": {"type": "number"},
          "to_x": {"type": "number"}, "to_y": {"type": "number"},
          "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
          "amount": {"type": "integer"},
          "screenshot_after": {"type": "boolean", "description": "return a screenshot after acting"},
      }, "required": ["action"]},
      writes=True, check=available)
def mouse(ctx: ToolContext, action: str, x: float | None = None, y: float | None = None,
          to_x: float | None = None, to_y: float | None = None, direction: str = "down", amount: int = 3,
          screenshot_after: bool = False):
    b = _ex(ctx).input()
    if x is None or y is None:
        cx, cy = b.cursor()
        sx, sy = _scale(ctx)
        x, y = cx / sx, cy / sy
    rx, ry = _map(ctx, x, y)
    if action == "move":
        b.move(rx, ry)
    elif action in ("click", "double_click", "right_click", "middle_click"):
        button = {"right_click": "right", "middle_click": "middle"}.get(action, "left")
        b.click(rx, ry, button, 2 if action == "double_click" else 1)
    elif action == "drag":
        if to_x is None or to_y is None:
            return "ERROR: drag needs to_x and to_y"
        tx, ty = _map(ctx, to_x, to_y)
        b.drag(rx, ry, tx, ty)
    elif action == "scroll":
        b.scroll(rx, ry, direction, max(1, min(int(amount or 3), 30)))
    elif action == "down":
        b.move(rx, ry); b.down()
    elif action == "up":
        b.move(rx, ry); b.up()
    else:
        return f"ERROR: unknown action {action}"
    time.sleep(0.3)
    msg = f"{action} at ({int(x)}, {int(y)})"
    return screenshot(ctx) if screenshot_after else msg


@tool("keyboard", "desktop",
      "Keyboard input. action=type types text (unicode OK); action=key presses a key or combo such as "
      "'enter', 'ctrl+c', 'ctrl+shift+t', 'cmd+space', 'alt+tab'.",
      {"properties": {
          "action": {"type": "string", "enum": ["type", "key"]},
          "text": {"type": "string", "description": "text to type (action=type)"},
          "keys": {"type": "string", "description": "key combo (action=key)"},
          "screenshot_after": {"type": "boolean"},
      }, "required": ["action"]},
      writes=True, check=available)
def keyboard(ctx: ToolContext, action: str, text: str = "", keys: str = "", screenshot_after: bool = False):
    b = _ex(ctx).input()
    if action == "type":
        if not text:
            return "ERROR: text required"
        b.type(text)
        msg = f"typed {len(text)} chars"
    elif action == "key":
        if not keys:
            return "ERROR: keys required"
        b.key(keys)
        msg = f"pressed {keys}"
    else:
        return f"ERROR: unknown action {action}"
    time.sleep(0.3)
    return screenshot(ctx) if screenshot_after else msg


@tool("screen_info", "desktop", "Get screen size, cursor position and the active window title.",
      {"properties": {}}, check=available)
def screen_info(ctx: ToolContext):
    b = _ex(ctx).input()
    w, h = b.size()
    cx, cy = b.cursor()
    sx, sy = _scale(ctx)
    return {"backend": b.name, "screen": [w, h], "cursor_screenshot_space": [round(cx / sx), round(cy / sy)],
            "active_window": b.active_window()}
