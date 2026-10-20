"""The tool an evaluation skill checks its suite with (toolset evalauthor: only on while a suite is being written)."""
from __future__ import annotations

from .registry import ToolContext, tool


@tool("eval_suite_check", "evalauthor",
      "Check the evaluation suite package in out/ (suite.json, inputs/, anchors.json) against SinoBee's rules: "
      "question mix, categories, the 安全性 category, an injected input, anchors, privacy, no quoting the original "
      "result. Returns {ok, problems}; fix every problem and check again.",
      {"properties": {"dir": {"type": "string", "description": "the package folder (default out)"}}})
def eval_suite_check(ctx: ToolContext, dir: str = "out"):
    from ..evals import authoring
    from .files import resolve
    folder = resolve(ctx, dir or "out")
    problems = authoring.check_package(folder, authoring.original_of(ctx.config.workspace))
    out = {"ok": not problems, "problems": problems}
    if not problems:
        try:
            suite, texts = authoring.read_package(folder)
            suite = authoring._fixed(suite)
            out["inputs"] = len(suite.get("inputs") or [])
            out["questions"] = len(suite.get("graders") or [])
            out["hash"] = authoring.package_hash(suite, texts)[:12]
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "problems": [str(e)]}
    return out
