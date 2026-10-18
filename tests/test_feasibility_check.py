"""engineering-feasibility: the scaffold never hands out a finished answer, and check rejects a folder that is a
worked example, an untouched template, or an analysis that ignores the numbers its own source states.

Reproduces the Roadster Rocket run of 2026-10-18: the agent scaffolded `--example roadster`, flipped
"example": true to false and delivered the example (690 bar, 313 kg) although the transcript says ~250 kg."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "engineering-feasibility"
FIX = Path(__file__).resolve().parent / "fixtures" / "roadster_incident"
sys.path.insert(0, str(SKILL / "scripts"))
import check  # noqa: E402

VID = "qt5p-_tE8d0"
REPORT = "\n".join(["# R", "## 判定", "有條件", "## 主張", "## 工程原理", "## 物理極限", "## 假設",
                    "## 來源", "https://www.youtube.com/watch?v=qt5p-_tE8d0", "https://example.org/a"])


def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    yt = ws / "reports" / "youtube" / VID
    yt.mkdir(parents=True)
    shutil.copy(FIX / "transcript.md", yt / "transcript.md")
    folder = ws / "reports" / "feasibility" / "roadster"
    folder.mkdir(parents=True)
    return folder


def run(*args, cwd=None):
    out = subprocess.run([sys.executable, str(SKILL / "scripts" / "run.py"), *args], capture_output=True, text=True, cwd=cwd)
    return json.loads(out.stdout)


def own_analysis(folder: Path, facts=None, model="export const AIR = 250; // kg, F1\nexport const V = 0.6;\n"):
    f = json.loads((FIX / "feasibility.json").read_text(encoding="utf-8"))
    f.update(summary="可行但只有幾秒：250 kg 空氣、約 450 bar", tiles=[{"value": "250 kg", "label": "600 L 空氣（官方）"}],
             sources=[{"title": "Tesla", "url": "https://www.youtube.com/watch?v=qt5p-_tE8d0"},
                      {"title": "a", "url": "https://example.org/a"}])
    f["source"]["transcript"] = f"reports/youtube/{VID}/transcript.md"
    f["source_facts"] = facts if facts is not None else [
        {"id": "F1", "quote": "There's about 250 kilos of air.", "at": "12:11", "value": 250, "unit": "kg", "use": "AIR"},
        {"id": "F2", "quote": "They are a total of 600 L", "at": "12:11", "value": 600, "unit": "L", "use": "V"}]
    f["figures"] = [{"fig": "Fig 1", "shows": "x", "mode": "mechanism", "claims": ["C1"], "file": "fig1.mjs"}]
    (folder / "feasibility.json").write_text(json.dumps(f, ensure_ascii=False), encoding="utf-8")
    (folder / "model.mjs").write_text(model, encoding="utf-8")
    (folder / "fig1.mjs").write_text("// own figure\n", encoding="utf-8")
    (folder / "audits").mkdir(exist_ok=True)
    (folder / "audits" / "fig1.txt").write_text("clearance: 0 problems\naudit passed\n", encoding="utf-8")
    (folder / "page.html").write_text("<h2>Fig 1</h2>", encoding="utf-8")
    (folder / "report.md").write_text(REPORT, encoding="utf-8")


def test_the_incident_folder_fails(tmp_path):
    folder = workspace(tmp_path)
    for p in (SKILL / "references" / "example-roadster").iterdir():
        shutil.copy(p, folder / p.name)
    shutil.copy(FIX / "feasibility.json", folder / "feasibility.json")
    (folder / "page.html").write_text("<h2>Fig 1</h2>", encoding="utf-8")
    (folder / "report.md").write_text(REPORT, encoding="utf-8")
    res = check.check(folder)
    text = "\n".join(res["problems"])
    assert res["status"] == "fix"
    assert "identical to the worked example references/example-roadster/model.mjs" in text
    assert "repeats the worked example example-roadster" in text
    assert "source_facts: quote every number the source itself states" in text


def test_an_own_analysis_that_uses_the_source_passes(tmp_path):
    folder = workspace(tmp_path)
    own_analysis(folder)
    res = check.check(folder)
    assert res["status"] == "ok", res["problems"]


def test_a_source_number_the_model_ignores_is_caught(tmp_path):
    folder = workspace(tmp_path)
    own_analysis(folder, model="export const AIR = 313; // kg at 690 bar\nexport const V = 0.6;\n")
    text = "\n".join(check.check(folder)["problems"])
    assert "the source says 250 kg but neither model.mjs nor the assumptions use it" in text
    # a deliberate, explained override is allowed
    f = json.loads((folder / "feasibility.json").read_text(encoding="utf-8"))
    f["source_facts"][0]["override"] = "a later spec sheet gives 313 kg (URL)"
    (folder / "feasibility.json").write_text(json.dumps(f, ensure_ascii=False), encoding="utf-8")
    assert check.check(folder)["status"] == "ok"


def test_a_misquoted_fact_is_caught(tmp_path):
    folder = workspace(tmp_path)
    own_analysis(folder, facts=[{"id": "F1", "quote": "about 313 kilos of air at 690 bar", "at": "12:11",
                                 "value": 250, "unit": "kg"}])
    assert any("the quote is not in the transcript" in p for p in check.check(folder)["problems"])


def test_figures_need_an_audit_log(tmp_path):
    folder = workspace(tmp_path)
    own_analysis(folder)
    (folder / "audits" / "fig1.txt").write_text("turn audit failed: 3 problems\n", encoding="utf-8")
    assert any("does not end in 'audit passed'" in p for p in check.check(folder)["problems"])
    shutil.rmtree(folder / "audits")
    assert any("no audit log" in p for p in check.check(folder)["problems"])


def test_scaffold_refuses_examples_and_records_provenance(tmp_path):
    folder = tmp_path / "ws" / "reports" / "feasibility" / "x"
    res = run("scaffold", str(folder), "--example", "roadster")
    assert res["status"] == "error" and "not scaffolded" in res["error"]
    res = run("scaffold", str(folder))
    assert res["status"] == "ok" and "feasibility.json" in res["copied"]
    f = json.loads((folder / "feasibility.json").read_text(encoding="utf-8"))
    assert f["example"] is True and "Roadster" not in json.dumps(f, ensure_ascii=False)
    assert "313" not in json.dumps(f) and "690" not in json.dumps(f)
    prov = json.loads((folder / ".scaffold.json").read_text(encoding="utf-8"))
    assert "model.mjs" in prov["files"]
    problems = "\n".join(check.check(folder)["problems"])
    assert "model.mjs is still the template" in problems and "template placeholders" in problems


def test_skill_text_does_not_carry_an_answer():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for leak in ("313 kg", "151 kg", "4.3 s", "84 kg", "--example roadster"):
        assert leak not in text, leak
