---
name: desktop-automation
description: Operates native desktop applications reliably with screenshots, mouse and keyboard. Use when a task involves a GUI app, window, dialog or anything the browser tool cannot reach (桌面、視窗、點擊、應用程式).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: desktop, gui, mouse, keyboard
  sinobee-triggers: 桌面, 滑鼠, 鍵盤, 點擊, 視窗, 應用程式, desktop, click, window, app
  sinobee-requires-tools: screenshot, mouse, keyboard
  author: sinobee
---

# When to Use
Tasks in native GUI applications, or when a web page cannot be handled by the `browser` tool.

# Procedure
1. `screenshot` first. Identify the target element and its center coordinates in the screenshot.
2. Prefer keyboard: launch apps via shell (e.g. `nohup gedit file.txt &`) or launchers, use shortcuts
   (ctrl+s, ctrl+l, alt+tab, ctrl+w) instead of hunting for menu items.
3. Click the CENTER of the element. For text fields: click, then `keyboard` ctrl+a, then type.
4. After every state-changing action, verify with `screenshot` (or screenshot_after=true).
5. If an element is not visible, scroll (mouse action=scroll) or maximise the window (double-click title bar).
6. When stuck after 3 attempts, describe what you see and ask the user.

# Pitfalls
- Coordinates refer to the latest screenshot; take a new one after windows move or the screen changes.
- Typing goes to the focused window - confirm focus before typing.
- Animations: wait ~1s (shell `sleep 1`) after opening apps or menus before the next screenshot.
- Non-ASCII text is typed via xdotool/clipboard; verify it rendered correctly.

# Verification
A final screenshot shows the intended end state (file saved, dialog closed, value entered).
