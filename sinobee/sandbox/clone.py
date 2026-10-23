"""Copying a folder as cheaply as the file system allows, leaving out what the sandbox makes for itself.

- macOS (APFS): clonefile(2) per file - copy-on-write, no data is copied and no space used until a file changes; a
  `.git` folder is cloned in one call. Falls back to `cp -c`, then to a plain copy.
- Linux: the FICLONE ioctl (a reflink on btrfs / xfs), else a plain copy.

DEPS are never copied: node_modules, virtualenvs and build caches hold programs built for this computer (macOS)
that do not run in the Linux sandbox anyway - the sandbox installs its own. The walk returns, at no extra cost, the
manifest {relative path: [size, mtime_ns, None]} a review compares against (no hashing: contents are compared only
for files whose size or time changed). Each copy keeps the original's modification time.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import shutil
import stat as st_
import subprocess
from pathlib import Path

DEPS = {"node_modules", "__pycache__", ".venv", "venv", ".next", ".turbo", ".cache", ".pytest_cache", ".mypy_cache",
        ".parcel-cache", ".svelte-kit", ".nuxt", ".gradle", ".tox"}
NOISE = {".DS_Store"}

_DARWIN = platform.system() == "Darwin"
_clonefile = None
if _DARWIN:
    try:
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        _clonefile = _libc.clonefile
        _clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        _clonefile.restype = ctypes.c_int
    except (OSError, AttributeError):
        _clonefile = None
FICLONE = 0x40049409


def _reflink(src: str, dst: str) -> bool:
    try:
        import fcntl
        with open(src, "rb") as a, open(dst, "wb") as b:
            fcntl.ioctl(b.fileno(), FICLONE, a.fileno())
        shutil.copystat(src, dst)
        return True
    except (OSError, ImportError):
        try:
            os.unlink(dst)
        except OSError:
            pass
        return False


def clone_file(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """One file (or symlink), as a clone when possible; the copy keeps src's modification time."""
    src, dst = os.fspath(src), os.fspath(dst)
    if os.path.lexists(dst):
        os.unlink(dst)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
        return
    if _clonefile is not None and _clonefile(src.encode(), dst.encode(), 1) == 0:       # CLONE_NOFOLLOW
        return
    if _DARWIN and subprocess.run(["cp", "-c", src, dst], capture_output=True, timeout=600).returncode == 0:
        return
    if not _DARWIN and _reflink(src, dst):
        return
    shutil.copy2(src, dst)


def _clone_dir_whole(src: str, dst: str) -> bool:
    if _clonefile is not None and _clonefile(src.encode(), dst.encode(), 1) == 0:
        return True
    if _DARWIN:
        return subprocess.run(["cp", "-c", "-R", src, dst], capture_output=True, timeout=3600).returncode == 0
    return subprocess.run(["cp", "-a", "--reflink=auto", src, dst], capture_output=True, timeout=3600).returncode == 0


def clone_tree(src: Path, dst: Path, skip: set[str] = DEPS, whole: set[str] = frozenset({".git"})) -> dict:
    """Copy src to dst (replacing dst) without the `skip` folders; returns the manifest of what a review compares
    (everything copied except `whole` folders, which are cloned as one piece, and NOISE)."""
    src, dst = Path(src), Path(dst)
    if os.path.lexists(dst):
        shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, list] = {}
    stack = [""]
    while stack:
        rel = stack.pop()
        s_dir, d_dir = src / rel if rel else src, dst / rel if rel else dst
        d_dir.mkdir(exist_ok=True)
        try:
            entries = list(os.scandir(s_dir))
        except OSError:
            continue
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name in skip:
                        continue
                    if e.name in whole:
                        if not _clone_dir_whole(e.path, str(d_dir / e.name)):
                            shutil.copytree(e.path, d_dir / e.name, symlinks=True)
                        continue
                    stack.append(r)
                    continue
                s = e.stat(follow_symlinks=False)
                clone_file(e.path, d_dir / e.name)
                if st_.S_ISREG(s.st_mode):
                    os.utime(d_dir / e.name, ns=(s.st_atime_ns, s.st_mtime_ns), follow_symlinks=False)
                    if e.name not in NOISE:
                        manifest[r] = [s.st_size, s.st_mtime_ns, None]
            except OSError:
                continue
        try:
            sd = s_dir.stat()
            os.utime(d_dir, ns=(sd.st_atime_ns, sd.st_mtime_ns))
        except OSError:
            pass
    return manifest


def walk(root: Path, skip: set[str]) -> dict[str, os.stat_result]:
    """Regular files under root (not in `skip` folders, not NOISE) with their stat."""
    out: dict[str, os.stat_result] = {}
    root = Path(root)
    if not root.is_dir():
        return out
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            entries = list(os.scandir(root / rel if rel else root))
        except OSError:
            continue
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in skip:
                        stack.append(r)
                elif e.is_file(follow_symlinks=False) and e.name not in NOISE:
                    out[r] = e.stat(follow_symlinks=False)
            except OSError:
                continue
    return out
