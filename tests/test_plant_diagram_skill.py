"""engineering-plant-diagram: one plant model -> isometric 3D + plan SVG. The skill must be spec-valid, its entry
and declared check must exist, the example must build, and the model lint must catch reversed or detached pipes."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sinobee.skills import spec

SK = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "engineering-plant-diagram"
PC = SK / "scripts" / "plantcheck.py"
NODE = shutil.which("node")


def pc(*args, cwd):
    return subprocess.run([sys.executable, str(PC), *map(str, args)], capture_output=True, text=True, cwd=cwd,
                          timeout=120, check=False)


def test_skill_is_valid_and_complete():
    p = spec.parse((SK / "SKILL.md").read_text(encoding="utf-8"))
    assert not p.diagnostics and not spec.validate(p.frontmatter, "engineering-plant-diagram")
    assert not spec.quality_warnings(p.frontmatter, p.body)
    meta = p.frontmatter["metadata"]
    assert (SK / meta["sinobee-entry"]).is_file()
    assert (SK / meta["sinobee-checks"].split()[0]).is_file()
    for f in ("assets/plant-kit.js", "assets/template.html", "references/model-example-wwtp.js"):
        assert (SK / f).is_file(), f


def test_bundled_as_builtin(config):
    from sinobee.agent import Runtime
    sk = Runtime(config).skills.skills["engineering-plant-diagram"]
    assert sk.trust == "builtin"


def test_scaffold_and_build(tmp_path):
    assert pc("scaffold", "reports/plant/x", cwd=tmp_path).returncode == 0
    assert pc("scaffold", "reports/plant/x", cwd=tmp_path).returncode != 0          # never overwrites
    r = pc("build", "reports/plant/x/model.js", "reports/plant/x/plant.html", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    html = (tmp_path / "reports/plant/x/plant.html").read_text(encoding="utf-8")
    assert "const PlantKit" in html and "const MODEL" in html and "/*__KIT__*/" not in html
    assert "<title>都市污水處理廠" in html


def _lint(tmp_path, model_js):
    (tmp_path / "m.js").write_text(model_js, encoding="utf-8")
    js = (SK / "assets/plant-kit.js").read_text(encoding="utf-8") + "\n" + model_js + \
        "\nconsole.log(JSON.stringify(PlantKit.lint(MODEL)));"
    r = subprocess.run([NODE, "-"], input=js, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not NODE, reason="needs node")
def test_example_model_lints_clean(tmp_path):
    lint = _lint(tmp_path, (SK / "references/model-example-wwtp.js").read_text(encoding="utf-8"))
    assert lint["errors"] == [] and len(lint["edges"]) == 21
    assert any("[ras] 終沉池" in e and "→ 厭氧槽" in e for e in lint["edges"])


@pytest.mark.skipif(not NODE, reason="needs node")
def test_lint_catches_reversed_pipe_and_unknown_type(tmp_path):
    src = (SK / "references/model-example-wwtp.js").read_text(encoding="utf-8")
    # RAS drawn backwards: points start at the anaerobic tank but from/to still say clarifier -> anaerobic
    bad = src.replace("pts: [[200, 65], [200, 74], [107, 74], [107, 62]]", "pts: [[107, 62], [107, 74], [200, 74], [200, 65]]")
    bad = bad.replace("type: 'chem'", "type: 'chemical'")
    assert bad != src
    errs = _lint(tmp_path, bad)["errors"]
    assert any("p-ras" in e and "未接上設施" in e for e in errs)
    assert any("p-naocl" in e and "未知管線類別" in e for e in errs)


@pytest.mark.skipif(not NODE, reason="needs node")
def test_check_without_playwright_falls_back_to_lint(tmp_path):
    pc("scaffold", "p", cwd=tmp_path)
    pc("build", "p/model.js", "p/plant.html", cwd=tmp_path)
    code = ("import sys, runpy; sys.modules['playwright'] = None; "
            f"sys.argv = ['plantcheck', 'check', 'p/plant.html']; runpy.run_path({str(PC)!r}, run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, timeout=120, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "僅模型 lint" in r.stdout and "playwright" in r.stdout
    assert re.search(r"\[sewage\] ext-sewer → 進流抽水站", r.stdout)
