"""Sandbox plan A: the tools reach the computer through an Executor, and paths map between this computer and the
place the tools run. On this computer (HostExecutor, no mounts) nothing changes."""
import asyncio
import os
from pathlib import Path, PurePosixPath

from sinobee.sandbox import (Executor, HostExecutor, Mount, PathMap, RunResult, executor_kind, executor_of,
                             for_config, sandbox_map)
from sinobee.tools.registry import REGISTRY, ToolContext
from sinobee.types import ToolCall


def run(ctx, name, **args):
    return asyncio.run(REGISTRY.dispatch(ToolCall("1", name, args), ctx))


# ------------------------------------------------------------------ paths
def test_the_host_map_changes_nothing(tmp_path):
    m = PathMap(workspace=tmp_path)
    assert m.identity
    assert m.to_host("a/b.txt") == tmp_path / "a" / "b.txt"
    assert m.to_host("/etc/hosts") == Path("/etc/hosts")
    assert m.to_host("~/x") == Path.home() / "x"
    assert m.to_exec(tmp_path / "a") == str(tmp_path / "a")
    assert m.visible("/anything") and m.describe() == "" and m.to_host_text("/workspace/x") == "/workspace/x"


def test_sandbox_paths_both_ways(tmp_path):
    ws, docs = tmp_path / "ws", tmp_path / "Documents" / "報告"
    ws.mkdir()
    docs.mkdir(parents=True)
    m = sandbox_map(ws, [{"path": str(docs), "mode": "review"}])
    assert m.to_host("/workspace/site/index.html") == ws.resolve() / "site" / "index.html"
    assert m.to_host("/host/報告/q3.docx") == docs.resolve() / "q3.docx"
    assert m.to_host("notes.md") == ws.resolve() / "notes.md"                      # relative: the workspace
    assert m.to_exec(ws / "site" / "index.html") == "/workspace/site/index.html"
    assert m.to_exec(docs) == "/host/報告"
    assert m.visible(docs / "q3.docx") and not m.visible(tmp_path / "secret.txt")
    assert m.to_host("/workspace/../etc/passwd") == Path("/etc/passwd")            # .. does not stay inside
    assert m.mount_of(docs / "a").mode == "review"
    d = m.describe()
    assert "/workspace = " in d and "/host/報告" in d and "review" in d


def test_folder_names_do_not_collide(tmp_path):
    a, b = tmp_path / "x" / "data", tmp_path / "y" / "data"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    m = sandbox_map(tmp_path, [{"path": str(a)}, {"path": str(b), "mode": "ro"}])
    inner = sorted(str(x.inner) for x in m.mounts)
    assert inner == ["/host/data", "/host/data-2", "/workspace"]
    assert m.to_exec(b / "f") == "/host/data-2/f"


def test_symlink_out_of_a_mount_is_not_visible(tmp_path):
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "link").symlink_to(outside)
    m = sandbox_map(ws)
    assert not m.visible(ws / "link" / "f.txt")


def test_tool_output_paths_back_to_this_computer(tmp_path):
    m = sandbox_map(tmp_path / "ws")
    text = "wrote /workspace/out/a.html and /workspace2/b and x/workspace/c (see /workspace)"
    out = m.to_host_text(text)
    ws = str((tmp_path / "ws").resolve())
    assert f"wrote {ws}/out/a.html" in out and "/workspace2/b" in out and "x/workspace/c" in out
    assert out.endswith(f"(see {ws})")


# ------------------------------------------------------------------ executor
def test_host_is_the_default_and_an_unknown_kind_falls_back(config):
    assert executor_kind(config) == "host"
    config.data["sandbox"]["executor"] = "sandbox"          # plan B: not there yet
    assert executor_kind(config) == "host"
    assert for_config(config).identity


def test_host_executor_runs_commands_and_times_out(config, tmp_path):
    ex = HostExecutor(config)
    r = asyncio.run(ex.run("echo $FOO; pwd", tmp_path, 10, {"FOO": "bar"}))
    assert r.code == 0 and r.output.split() == ["bar", str(tmp_path)]
    r = asyncio.run(ex.run("sleep 5", tmp_path, 1))
    assert r.timed_out and r.code == -9


def test_one_executor_per_session(config):
    ctx = ToolContext(config=config)
    assert executor_of(ctx) is executor_of(ctx) and isinstance(executor_of(ctx), HostExecutor)


class Recording(Executor):
    """A stand-in executor: proves every tool goes through it."""
    kind = "test"

    def __init__(self, config, paths):
        super().__init__(config, paths)
        self.commands, self.writes = [], []

    async def run(self, command, cwd, timeout, env=None):
        self.commands.append((command, str(cwd), timeout))
        return RunResult(0, "ran in the stand-in")

    def write_text(self, p, text, append=False):
        self.writes.append(str(p))
        super().write_text(p, text, append)


def test_tools_go_through_the_executor_and_speak_sandbox_paths(config):
    ws = config.workspace
    ws.mkdir(parents=True, exist_ok=True)
    ex = Recording(config, sandbox_map(ws))
    ctx = ToolContext(config=config)
    ctx.state["executor"] = ex
    out = run(ctx, "shell", command="ls", cwd="/workspace").content
    assert out.startswith("[exit 0] cwd=/workspace") and "ran in the stand-in" in out
    assert ex.commands[0][1] == str(ws.resolve())
    out = run(ctx, "write_file", path="/workspace/site/a.html", content="<p>hi</p>").content
    assert "/workspace/site/a.html" in out and (ws / "site" / "a.html").read_text() == "<p>hi</p>"
    assert ex.writes == [str((ws / "site" / "a.html").resolve())]
    out = run(ctx, "read_file", path="/workspace/site/a.html").content
    assert out.startswith("/workspace/site/a.html (1 lines)")
    out = run(ctx, "edit_file", path="site/a.html", old_string="hi", new_string="hello").content
    assert out.startswith("edited /workspace/site/a.html")
    assert run(ctx, "list_dir", path="/workspace").content.startswith("/workspace\n")


def test_on_this_computer_tool_output_is_unchanged(config):
    ws = config.workspace
    ws.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(config=config)
    out = run(ctx, "shell", command="echo hi").content
    assert out == f"[exit 0] cwd={ws}\nhi"
    out = run(ctx, "write_file", path="n.txt", content="x").content
    assert out == f"wrote 1 chars to {(ws / 'n.txt').resolve()}"


def test_the_gui_resolves_sandbox_paths(config):
    from sinobee.fileaccess import resolve_file
    ws = config.workspace
    (ws / "out").mkdir(parents=True, exist_ok=True)
    (ws / "out" / "a.html").write_text("x")
    config.data["sandbox"]["executor"] = "host"
    assert resolve_file(config, "out/a.html") == (ws / "out" / "a.html").resolve()


def test_the_prompt_explains_the_paths_only_in_a_sandbox(config, monkeypatch):
    from sinobee.agent.prompt import environment_block
    from sinobee.sandbox import executor as exmod
    assert "sandbox" not in environment_block(config).lower()
    monkeypatch.setattr(exmod, "IMPLEMENTED", {"host", "sandbox"})
    config.data["sandbox"]["executor"] = "sandbox"
    block = environment_block(config)
    assert "/workspace = " in block and "Your tools run in a sandbox" in block
