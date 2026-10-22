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

    # added for the sandbox plan D2; a backend that cannot do one says so
    def hold(self, combo: str, seconds: float):
        raise NotImplementedError("holding keys is not supported by this desktop backend")

    def drag_path(self, points: list[tuple[int, int]], seconds: float, button: str = "left"):
        raise NotImplementedError("drag paths are not supported by this desktop backend")

    def clipboard(self, text: str | None = None) -> str:
        raise NotImplementedError("the clipboard is not supported by this desktop backend")

    def windows(self, action: str = "list", title: str = "") -> str:
        raise NotImplementedError("window control is not supported by this desktop backend")

    def launch(self, command: str) -> str:
        raise NotImplementedError("launching apps is not supported by this desktop backend")


class XdotoolBackend(Backend):
    name = "xdotool"

    def _run(self, argv: list[str], stdin: str | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
        """Run a command on the desktop's machine (the sandbox backend runs it in the container)."""
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=stdin)

    def _x(self, *args: str, timeout: float = 30) -> str:
        r = self._run(["xdotool", *args], timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or f"xdotool {' '.join(args)} failed")
        return r.stdout.strip()

    @staticmethod
    def _keys(combo: str) -> list[str]:
        return [XDO_KEYS.get(p.strip().lower(), p.strip()) for p in combo.replace(" ", "").split("+") if p.strip()]

    def hold(self, combo, seconds):
        keys = self._keys(combo)            # one xdotool call: down, wait, up (no round trips while holding)
        self._x("keydown", "+".join(keys), "sleep", f"{max(0.05, float(seconds)):.2f}", "keyup", "+".join(keys),
                timeout=float(seconds) + 30)

    def drag_path(self, points, seconds, button="left"):
        pts = [(int(x), int(y)) for x, y in points]
        if len(pts) < 2:
            raise ValueError("a drag path needs at least two points")
        step = max(0.01, float(seconds) / (len(pts) - 1))
        b = str(BUTTONS.get(button, 1))
        args = ["mousemove", "--sync", str(pts[0][0]), str(pts[0][1]), "mousedown", b]
        for x, y in pts[1:]:
            args += ["sleep", f"{step:.3f}", "mousemove", str(x), str(y)]
        args += ["sleep", "0.05", "mouseup", b]
        self._x(*args, timeout=float(seconds) + 30)

    def clipboard(self, text=None):
        if text is None:
            r = self._run(["xclip", "-selection", "clipboard", "-o"])
            return r.stdout if r.returncode == 0 else ""
        r = self._run(["xclip", "-selection", "clipboard", "-i"], stdin=text)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "xclip failed")
        return text

    def windows(self, action="list", title=""):
        if action == "list":
            r = self._run(["wmctrl", "-l"])
            rows = [" ".join(l.split()[3:]) for l in r.stdout.splitlines() if l.strip()]
            return "\n".join(f"- {t}" for t in rows) or "(no windows)"
        if not title:
            raise ValueError("title required")
        argv = {"activate": ["wmctrl", "-a", title], "close": ["wmctrl", "-c", title],
                "maximize": ["wmctrl", "-r", title, "-b", "add,maximized_vert,maximized_horz"]}.get(action)
        if argv is None:
            raise ValueError("action must be list | activate | close | maximize")
        r = self._run(argv)
        if r.returncode != 0:
            raise RuntimeError(f"no window matching {title!r}")
        return f"{action}: {title}"

    def launch(self, command):
        r = self._run(["bash", "-c", f"nohup {command} >/tmp/launch.log 2>&1 & echo $!"])
        return f"started (pid {r.stdout.strip() or '?'}): {command}"

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

    def hold(self, combo, seconds):
        mapping = {"cmd": "command", "control": "ctrl", "escape": "esc", "return": "enter"}
        keys = [mapping.get(p.lower(), p.lower()) for p in combo.replace(" ", "").split("+") if p]
        for k in keys:
            self.g.keyDown(k)
        time.sleep(max(0.05, float(seconds)))
        for k in reversed(keys):
            self.g.keyUp(k)

    def drag_path(self, points, seconds, button="left"):
        pts = [(int(x), int(y)) for x, y in points]
        self.g.moveTo(*pts[0])
        self.g.mouseDown(button=button)
        step = max(0.01, float(seconds) / max(1, len(pts) - 1))
        for x, y in pts[1:]:
            self.g.moveTo(x, y, duration=step)
        self.g.mouseUp(button=button)


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


def available(config=None) -> bool:
    if config is not None:                  # in the sandbox there is always a desktop (Xvfb in the container)
        from ..sandbox import executor_kind
        if executor_kind(config) == "sandbox":
            return True
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
    from ..agent.evidence import capture          # kept by SinoBee as evidence the agent can cite (plan V3)
    ev = capture(ctx, png, f"桌面截圖：{window or '桌面'}", "screenshot")
    if ev:
        info += f". Evidence {ev['id']}"
    if not ctx.config.get("model.vision", True):
        return info + "\n(model has no vision; prefer the browser tool's text snapshot)"
    if ctx.emit:
        ctx.emit("screenshot", {"width": w, "height": h})
    return [TextPart(info), ImagePart.from_bytes(png)]


@tool("mouse", "desktop",
      "Control the mouse. actions: move, click, double_click, right_click, middle_click, drag (x,y -> to_x,to_y), "
      "drag_path (path [[x,y],...] over `seconds`, e.g. pulling a slider or drawing), "
      "scroll (direction up/down/left/right, amount = notches), down, up. Coordinates are in screenshot space.",
      {"properties": {
          "action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "middle_click",
                                                "drag", "drag_path", "scroll", "down", "up"]},
          "path": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                   "description": "drag_path: points [[x,y], ...] in screenshot space"},
          "seconds": {"type": "number", "description": "drag_path: how long the whole drag takes (default 1)"},
          "x": {"type": "number"}, "y": {"type": "number"},
          "to_x": {"type": "number"}, "to_y": {"type": "number"},
          "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
          "amount": {"type": "integer"},
          "screenshot_after": {"type": "boolean", "description": "return a screenshot after acting"},
      }, "required": ["action"]},
      writes=True, check=available)
def mouse(ctx: ToolContext, action: str, x: float | None = None, y: float | None = None,
          to_x: float | None = None, to_y: float | None = None, direction: str = "down", amount: int = 3,
          screenshot_after: bool = False, path: list | None = None, seconds: float = 1.0):
    b = _ex(ctx).input()
    if action == "drag_path":
        if not path or len(path) < 2:
            return "ERROR: drag_path needs path with at least two [x, y] points"
        b.drag_path([_map(ctx, px, py) for px, py in path], min(float(seconds or 1), 60))
        time.sleep(0.2)
        msg = f"dragged along {len(path)} points in {float(seconds or 1):g}s"
        return screenshot(ctx) if screenshot_after else msg
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
      "'enter', 'ctrl+c', 'ctrl+shift+t', 'cmd+space', 'alt+tab'; action=hold keeps keys pressed for `seconds` "
      "(games: hold 'space' 3 s).",
      {"properties": {
          "action": {"type": "string", "enum": ["type", "key", "hold"]},
          "seconds": {"type": "number", "description": "hold: how long (default 1, max 60)"},
          "text": {"type": "string", "description": "text to type (action=type)"},
          "keys": {"type": "string", "description": "key combo (action=key)"},
          "screenshot_after": {"type": "boolean"},
      }, "required": ["action"]},
      writes=True, check=available)
def keyboard(ctx: ToolContext, action: str, text: str = "", keys: str = "", screenshot_after: bool = False,
             seconds: float = 1.0):
    b = _ex(ctx).input()
    if action == "hold":
        if not keys:
            return "ERROR: keys required"
        secs = max(0.05, min(float(seconds or 1), 60))
        b.hold(keys, secs)
        msg = f"held {keys} for {secs:g}s"
    elif action == "type":
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


# ------------------------------------------------------------------ sandbox plan D2: waiting, looking closer, apps
def _region(ctx, region) -> tuple[int, int, int, int] | None:
    """[x, y, w, h] in screenshot space -> the same box in screen pixels."""
    if not region:
        return None
    x, y, w, h = (float(v) for v in region)
    x1, y1 = _map(ctx, x, y)
    x2, y2 = _map(ctx, x + w, y + h)
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def _grab(ctx, box=None):
    """The screen at full resolution as a PIL image (optionally cropped to a screen-pixel box)."""
    from PIL import Image
    png, w, h, raw_w, raw_h = _ex(ctx).screenshot(100000)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if box:
        x, y, bw, bh = box
        img = img.crop((x, y, x + bw, y + bh))
    return img


def _sig(img) -> list[int]:
    small = img.convert("L").resize((64, 40))
    return list(small.tobytes())


@tool("wait_change", "desktop",
      "Wait until the screen (or a region [x,y,w,h] in screenshot space) changes (mode=change) or stops changing "
      "(mode=stable, e.g. an animation or page load finished), then return a screenshot. Better than a fixed wait.",
      {"properties": {
          "mode": {"type": "string", "enum": ["change", "stable"]},
          "region": {"type": "array", "items": {"type": "number"}},
          "timeout": {"type": "number", "description": "seconds (default 10, max 120)"},
      }}, check=available)
def wait_change(ctx: ToolContext, mode: str = "change", region: list | None = None, timeout: float = 10):
    box = _region(ctx, region)
    end = time.time() + max(0.5, min(float(timeout or 10), 120))
    first = prev = _sig(_grab(ctx, box))
    stable_since = time.time()
    met = False
    while time.time() < end:
        time.sleep(0.4)
        cur = _sig(_grab(ctx, box))
        diff = sum(abs(a - b) for a, b in zip(cur, first if mode == "change" else prev)) / len(cur)
        if mode == "change" and diff > 1.5:
            met = True
            break
        if mode == "stable":
            if diff > 0.8:
                stable_since = time.time()
            elif time.time() - stable_since >= 1.2:
                met = True
                break
        prev = cur
    shot = screenshot(ctx)
    note = ("the screen changed" if mode == "change" else "the screen is stable") if met else \
        f"timed out: the screen did not {'change' if mode == 'change' else 'settle'}"
    return [TextPart(note)] + (shot[1:] if isinstance(shot, list) else [TextPart(str(shot))])


@tool("zoom", "desktop",
      "Look closer at a region [x,y,w,h] of the last screenshot (small text, icons, details): returns that part "
      "at full screen resolution, enlarged.",
      {"properties": {"region": {"type": "array", "items": {"type": "number"}},
                      "scale": {"type": "number", "description": "enlarge by (default 2, max 4)"}},
       "required": ["region"]}, check=available)
def zoom(ctx: ToolContext, region: list, scale: float = 2):
    from PIL import Image
    box = _region(ctx, region)
    if not box:
        return "ERROR: region [x, y, w, h] required"
    img = _grab(ctx, box)
    k = max(1.0, min(float(scale or 2), 4.0))
    w, h = int(img.width * k), int(img.height * k)
    if w > 1600:
        h, w = int(h * 1600 / w), 1600
    img = img.resize((max(1, w), max(1, h)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return [TextPart(f"Zoom of region {region} (screenshot space), {img.width}x{img.height}"),
            ImagePart.from_bytes(buf.getvalue())]


@tool("clipboard", "desktop", "Read (action=read) or set (action=write, text) the desktop clipboard.",
      {"properties": {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}},
       "required": ["action"]}, writes=True, check=available)
def clipboard(ctx: ToolContext, action: str, text: str = ""):
    b = _ex(ctx).input()
    if action == "write":
        b.clipboard(text)
        return f"clipboard set ({len(text)} chars)"
    return b.clipboard() or "(clipboard is empty)"


@tool("window", "desktop", "Desktop windows: action=list, or activate / maximize / close a window by (part of) its title.",
      {"properties": {"action": {"type": "string", "enum": ["list", "activate", "maximize", "close"]},
                      "title": {"type": "string"}}, "required": ["action"]}, writes=True, check=available)
def window(ctx: ToolContext, action: str = "list", title: str = ""):
    return _ex(ctx).input().windows(action, title)


@tool("launch", "desktop",
      "Open an app on the desktop and return at once, e.g. 'xterm', 'pcmanfm /workspace', "
      "'libreoffice --calc /workspace/a.xlsx', 'chromium http://localhost:8000'. Then use screenshot / wait_change.",
      {"properties": {"command": {"type": "string"}}, "required": ["command"]}, writes=True, check=available)
def launch(ctx: ToolContext, command: str):
    return _ex(ctx).input().launch(command)
