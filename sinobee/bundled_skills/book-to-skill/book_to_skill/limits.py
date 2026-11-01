"""Size limits for untrusted input (SinoBee patch, not in upstream book-to-skill).

A document is attacker-controlled: a small DOCX/EPUB can declare members that inflate
to gigabytes (a zip bomb), and a multi-gigabyte text file is read whole. These checks
run before any parser touches the file. Override with environment variables (in MB):
  BOOK_SKILL_MAX_INPUT_MB         one source file on disk            (default 512)
  BOOK_SKILL_MAX_ZIP_MEMBER_MB    one member of a DOCX/EPUB, unpacked (default 128)
  BOOK_SKILL_MAX_ZIP_TOTAL_MB     all members together, unpacked     (default 1024)
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from book_to_skill.exceptions import ExtractionError

MB = 1024 * 1024
MAX_ZIP_MEMBERS = 20000


def _limit(name: str, default_mb: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default_mb)
    except ValueError:
        value = default_mb
    return max(1, value) * MB


def max_input_bytes() -> int:
    return _limit("BOOK_SKILL_MAX_INPUT_MB", 512)


def check_file_size(path: str | Path) -> None:
    """Refuse a source file larger than the input limit."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return  # the caller reports missing/unreadable files itself
    if size > max_input_bytes():
        raise ExtractionError(
            f"{p.name} is {size // MB} MB, over the {max_input_bytes() // MB} MB input limit "
            "(raise BOOK_SKILL_MAX_INPUT_MB to allow it)"
        )


def check_zip(path: str | Path) -> None:
    """Refuse a ZIP container (DOCX/EPUB) whose members would inflate past the limits.

    The sizes come from the central directory. A member that lies about its size is
    still stopped by zipfile itself, which raises BadZipFile when the decompressed
    data does not match the declared size.
    """
    p = Path(path)
    member_cap = _limit("BOOK_SKILL_MAX_ZIP_MEMBER_MB", 128)
    total_cap = _limit("BOOK_SKILL_MAX_ZIP_TOTAL_MB", 1024)
    try:
        with zipfile.ZipFile(p) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        return  # not a readable ZIP: the format-specific parser reports it
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ExtractionError(f"{p.name} has {len(infos)} archive members (limit {MAX_ZIP_MEMBERS})")
    total = 0
    for info in infos:
        if info.file_size > member_cap:
            raise ExtractionError(
                f"{p.name}: member {info.filename!r} unpacks to {info.file_size // MB} MB "
                f"(limit {member_cap // MB} MB; raise BOOK_SKILL_MAX_ZIP_MEMBER_MB to allow it)"
            )
        total += info.file_size
        if total > total_cap:
            raise ExtractionError(
                f"{p.name} unpacks to more than {total_cap // MB} MB "
                "(raise BOOK_SKILL_MAX_ZIP_TOTAL_MB to allow it)"
            )
