"""Sandbox plan stage 3 in Docker: a page whose button opens the file picker (D acceptance), and the plan C overlay
probe on this machine's Docker."""
import asyncio
import re
import uuid

import pytest

from sinobee.sandbox.manager import manager_for
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.types import ToolCall

PAGE = """<!doctype html><meta charset=utf-8><title>上傳</title>
<input type=file id=f hidden><button id=b onclick="document.getElementById('f').click()">選擇檔案</button>
<div id=out>尚未選擇</div>
<script>document.getElementById('f').onchange = e => { const x = e.target.files[0];
  x.text().then(t => document.getElementById('out').textContent = '已選擇 ' + x.name + ' ' + x.size + ' bytes: ' + t.trim()); };</script>"""


def run(ctx, tool_name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", tool_name, args), ctx))


@pytest.fixture
def box(config):
    config.data["permissions"]["approval"] = "sandbox"
    config.data["permissions"]["toolsets"] = ["shell", "files", "browser", "desktop"]
    config.data["sandbox"]["memory"] = "1g"
    config.workspace.chmod(0o777)
    conv = "s3-" + uuid.uuid4().hex[:8]
    from sinobee.sandbox.workspaces import own_dir
    own_dir(config, conv).chmod(0o777)

    async def approve(tool, args, reason):
        return True
    yield config, conv, ToolContext(config=config, session_id=conv, approve=approve)
    from sinobee.tools import browser
    asyncio.run(browser.forget_sandbox(conv))
    asyncio.run(manager_for(config).remove(conv))


@pytest.mark.docker
def test_a_button_that_opens_the_file_picker(box):
    config, conv, ctx = box
    run(ctx, "write_file", path="/workspace/site/index.html", content=PAGE)
    run(ctx, "write_file", path="/workspace/報價單.txt", content="總價 120 萬\n")
    run(ctx, "shell", command="cd /workspace/site && (nohup python3 -m http.server 8777 >/tmp/h.log 2>&1 &) && sleep 1")
    snap = run(ctx, "browser", action="navigate", url="http://localhost:8777/").content
    ref = re.search(r"\[(\d+)\] button[^\n]*選擇檔案", snap).group(1)
    out = run(ctx, "browser", action="upload", ref=ref, path="/workspace/報價單.txt").content
    assert "已選擇 報價單.txt" in out and "總價 120 萬" in out


@pytest.mark.docker
def test_the_overlay_probe_on_this_docker(tmp_path):
    from sinobee.sandbox import overlay
    d = tmp_path / "huge"
    d.mkdir()
    for i in range(200):
        (d / f"f{i}.txt").write_text(str(i))
    res = overlay.probe(d)
    assert res["ok"], overlay.report(res)
    assert res["files_seen"] == 200 and (d / "f0.txt").read_text() == "0" and not (d / "__sinobee_probe__.txt").exists()
