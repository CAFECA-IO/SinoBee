"""Keep the computer awake while a task runs (macOS).

A MacBook that goes to idle sleep in the middle of a task drops every open connection: the model call in flight ends
with ReadTimeout('') or ConnectTimeout a few seconds of awake time later, sub-agents fail with "連不到模型服務", and
dark wakes (Power Nap) every 15-30 minutes let a step or two run before the next sleep. A 2-hour 00935 task
(2026-10-29) ran 3 steps in 2 h 35 min this way and was resumed 3 times without getting further.

While at least one task runs, SinoBee holds `caffeinate -i -w <pid>`: idle sleep is prevented, `-w` ends the
assertion by itself if SinoBee exits. Closing the lid still sleeps the Mac (macOS does not allow an app to prevent
that on battery). Setting: system.keep_awake (default true). Other systems: nothing is done.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager

log = logging.getLogger("sinobee.keepawake")
_lock = threading.Lock()
_count = 0
_proc: subprocess.Popen | None = None


def supported() -> bool:
    return sys.platform == "darwin" and shutil.which("caffeinate") is not None


def _start() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        return
    try:
        _proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:  # noqa: PERF203
        log.warning("caffeinate could not start: %s", e)
        _proc = None


def _stop() -> None:
    global _proc
    p, _proc = _proc, None
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass


def acquire(config=None) -> bool:
    """One more running task. Returns True when this call holds a count (release() must follow)."""
    global _count
    if config is not None and not config.get("system.keep_awake", True):
        return False
    if not supported():
        return False
    with _lock:
        _count += 1
        if _count == 1:
            _start()
    return True


def release() -> None:
    global _count
    with _lock:
        _count = max(0, _count - 1)
        if _count == 0:
            _stop()


def holding() -> bool:
    return _proc is not None and _proc.poll() is None


@contextmanager
def hold(config=None):
    held = acquire(config)
    try:
        yield held
    finally:
        if held:
            release()
