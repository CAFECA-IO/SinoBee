"""SinoBee's patches to the bundled book-to-skill extractor: symlinks out of a folder or glob are skipped,
heading/ToC regexes stay linear on hostile lines, and zip bombs / oversized sources are refused before parsing."""
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "book-to-skill"
sys.path.insert(0, str(SKILL))
sys.dont_write_bytecode = True

from book_to_skill import utils  # noqa: E402
from book_to_skill.exceptions import ExtractionError  # noqa: E402
from book_to_skill.limits import check_zip  # noqa: E402


@pytest.fixture(autouse=True)
def _no_pycache():
    yield
    for d in SKILL.rglob("__pycache__"):
        for f in d.iterdir():
            f.unlink()
        d.rmdir()


def test_folder_and_glob_skip_symlinks_that_leave_them(tmp_path):
    secret = tmp_path / "private" / "notes.txt"
    secret.parent.mkdir()
    secret.write_text("secret")
    book = tmp_path / "book"
    book.mkdir()
    (book / "ch1.md").write_text("# One")
    (book / "ch2.md").symlink_to(secret)
    (book / "linked").symlink_to(secret.parent, target_is_directory=True)
    for arg in (str(book), str(book / "*.md"), str(book / "**" / "*.txt")):
        found = utils.resolve_input_files([arg])
        assert secret.resolve() not in found, arg
    assert utils.resolve_input_files([str(book)]) == [(book / "ch1.md").resolve()]
    # a file the user names explicitly is taken as given
    assert utils.resolve_input_files([str(book / "ch2.md")]) == [secret.resolve()]


def test_heading_and_toc_regexes_stay_fast_on_hostile_text():
    t = time.perf_counter()
    utils._structural_chapter_headings("# a" + " " * 200_000 + "b\n# One\n# Two\n")
    utils._TOC_PATTERN.search("\n" * 30_000 + "x")
    assert time.perf_counter() - t < 1.0
    assert utils._TOC_PATTERN.search("intro\n  Contents  \nch1")
    assert utils._TOC_PATTERN.search("## 目錄\n")


def test_zip_bomb_and_oversized_source_are_refused(tmp_path, monkeypatch):
    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\0" * (3 * 1024 * 1024))
    monkeypatch.setenv("BOOK_SKILL_MAX_ZIP_MEMBER_MB", "2")
    with pytest.raises(ExtractionError, match="unpacks to"):
        check_zip(bomb)
    with pytest.raises(ExtractionError, match="unpacks to"):
        utils.extract_single_file(bomb, "text", "no")
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (2 * 1024 * 1024 + 1))
    monkeypatch.setenv("BOOK_SKILL_MAX_INPUT_MB", "2")
    with pytest.raises(ExtractionError, match="input limit"):
        utils.extract_single_file(big, "text", "no")
