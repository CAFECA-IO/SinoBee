"""Sandbox plan 3b: a folder that is not a git project and has too many files to copy is reviewed by overlay - your
folder below, the sandbox's changes in a Docker volume above, the review is the upper layer."""
import asyncio
import os
import uuid
from pathlib import Path

import pytest

from sinobee.sandbox import folders as fd
from sinobee.sandbox import overlay, staging
from sinobee.sandbox.manager import manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.tools.sandbox_tools import plan_folder
from sinobee.types import ToolCall


def run(ctx, tool_name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", tool_name, args), ctx))


def big_folder(root: Path, n: int = 40) -> Path:
    for i in range(n):
        d = root / f"part{i % 4}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"f{i}.txt").write_text(f"line {i}\n")
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("# A\n")
    (root / "docs" / "b.md").write_text("# B\n")
    (root / "readme.txt").write_text("hello\n")
    for p in [root, *root.rglob("*")]:            # Linux: the sandbox user is uid 1000 (Docker Desktop maps owners)
        p.chmod(0o777 if p.is_dir() else 0o666)
    return root


def test_too_many_files_to_copy_means_overlay_when_docker_can(config, tmp_path, monkeypatch):
    f = big_folder(tmp_path / "data")
    config.data["sandbox"]["overlay_min_files"] = 10
    monkeypatch.setattr(overlay, "available", lambda c: True)
    assert plan_folder(config, f, "review", "c1") == ("review", "overlay", None)
    monkeypatch.setattr(overlay, "available", lambda c: False)
    assert plan_folder(config, f, "review", "c1") == ("review", "copy", None)        # small enough to copy anyway
    config.data["sandbox"]["review_max_files"] = 20
    assert plan_folder(config, f, "review", "c1")[2]                                  # too big, and no overlay
    config.data["sandbox"]["overlay_min_files"] = 1000
    monkeypatch.setattr(overlay, "available", lambda c: True)
    assert plan_folder(config, f, "review", "c1")[1] == "copy"                        # below the overlay threshold


@pytest.mark.docker
def test_review_by_overlay_end_to_end(config, tmp_path, monkeypatch):
    monkeypatch.setattr(overlay, "capability", lambda c, refresh=False: {"ok": True, "prefix": ""})
    config.data["permissions"]["approval"] = "sandbox"
    config.data["sandbox"]["memory"] = "1g"
    src = big_folder(tmp_path / "data")
    before = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}
    conv = "ov-" + uuid.uuid4().hex[:8]
    from sinobee.sandbox.workspaces import own_dir
    own_dir(config, conv).chmod(0o777)
    fd.add_grant(config, conv, fd.entry(src, "review", via="overlay"))
    folder = fd.folders_for(config, conv)[0]
    m = manager_for(config)

    async def approve(tool, args, reason):
        return True
    ctx = ToolContext(config=config, session_id=conv, approve=approve)
    try:
        out = run(ctx, "shell", command="cd /host/data && cat readme.txt && echo changed >> part0/f0.txt && "
                                        "echo new > part1/new.txt && rm part2/f2.txt && rm -rf docs && "
                                        "mkdir -p out && echo r > out/report.txt && ls part0 | wc -l").content
        assert "hello" in out and out.startswith("[exit 0]"), out
        # the file tools reach the view in the sandbox
        assert "changed" in run(ctx, "read_file", path="/host/data/part0/f0.txt").content
        assert not run(ctx, "write_file", path="/host/data/readme.txt", content="hello again\n").is_error
        assert "f0.txt" in run(ctx, "list_dir", path="/host/data/part0").content
        # your folder: untouched
        assert {p: p.read_bytes() for p in src.rglob("*") if p.is_file()} == before
        got = {c["path"]: c for c in staging.changes(config, conv, folder)}
        assert {p: c["kind"] for p, c in got.items()} == {
            "part0/f0.txt": "modified", "part1/new.txt": "added", "part2/f2.txt": "deleted",
            "docs/a.md": "deleted", "docs/b.md": "deleted", "out/report.txt": "added", "readme.txt": "modified"}
        assert "+changed" in got["part0/f0.txt"]["diff"] and not any(c["conflict"] for c in got.values())
        # you edit one of them meanwhile: a conflict
        os.utime(src / "readme.txt", ns=(1, 2 * 10 ** 19))
        assert {p for p, c in {c["path"]: c for c in staging.changes(config, conv, folder)}.items() if c["conflict"]} \
            == {"readme.txt"}
        res = staging.apply(config, conv, folder, ["part0/f0.txt", "part1/new.txt", "part2/f2.txt", "readme.txt"])
        assert sorted(res["applied"]) == ["part0/f0.txt", "part1/new.txt", "part2/f2.txt"]
        assert res["conflicts"] == ["readme.txt"]
        assert (src / "part0" / "f0.txt").read_text() == "line 0\nchanged\n" and (src / "part1" / "new.txt").exists()
        assert not (src / "part2" / "f2.txt").exists()
        left = {c["path"] for c in staging.changes(config, conv, folder)}
        assert left == {"docs/a.md", "docs/b.md", "out/report.txt", "readme.txt"}
        # discard: your version comes back into the view
        assert sorted(staging.discard(config, conv, folder, ["docs/a.md", "out/report.txt"])) == \
            ["docs/a.md", "out/report.txt"]
        out = run(ctx, "shell", command="cat /host/data/docs/a.md; ls /host/data/out 2>&1 | head -1").content
        assert "# A" in out
        assert {c["path"] for c in staging.changes(config, conv, folder)} == {"docs/b.md", "readme.txt"}
        # the sandbox changes the applied file again: a change, not a conflict
        run(ctx, "shell", command="echo again >> /host/data/part0/f0.txt")
        got = {c["path"]: c for c in staging.changes(config, conv, folder)}
        assert got["part0/f0.txt"]["kind"] == "modified" and not got["part0/f0.txt"]["conflict"]
    finally:
        asyncio.run(m.remove(conv))
    rc, out, _ = overlay._run("volume", "ls", "-q")
    assert len([v for v in out.split() if conv in v]) == 2         # 清除環境 keeps changes waiting for review
    assert {c["path"] for c in staging.changes(config, conv, folder)} >= {"docs/b.md", "part0/f0.txt"}
    fd.remove_grant(config, conv, str(src))                          # the folder taken away: its changes go
    rc, out, _ = overlay._run("volume", "ls", "-q")
    assert not [v for v in out.split() if conv in v]
