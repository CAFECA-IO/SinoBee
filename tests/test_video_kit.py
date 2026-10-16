"""Production kit of the video skill: export the creative assets as a zip, import them for a consistent next film."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "ai-video-production"
SCRIPTS = SKILL / "scripts"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def run(script, *args, ok=True):
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r.returncode, out


def film(tmp_path) -> Path:
    """A finished project: example plan + reference sheets, a master, narration audio, music, a LUT and seeds."""
    root = tmp_path / "duel"
    run("project.py", "init", root, "--genre", "film", "--title", "繡春刀")
    data = json.loads((SKILL / "assets" / "example-film-duel.json").read_text())
    for d in ("refs", "audio", "music", "keyframes"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for s in data["subjects"]:
        for v in ("front", "three-quarter"):
            (root / "refs" / f"{s['id']}_{v}.png").write_bytes(PNG)
        s["sheet"] = [f"refs/{s['id']}_front.png", f"refs/{s['id']}_three-quarter.png"]
        s["sheet_seed"] = 4242
    e = data["environments"][0]
    (root / "refs" / f"{e['id']}_master.png").write_bytes(PNG)
    e["master"], e["master_seed"] = f"refs/{e['id']}_master.png", 77
    sh = data["shots"][0]
    (root / "audio" / f"{sh['id']}.wav").write_bytes(b"RIFF0000WAVEfmt ")
    sh["audio_file"], sh["audio_role"] = f"audio/{sh['id']}.wav", "voiceover"
    (root / "keyframes" / "k1.png").write_bytes(PNG)
    sh["first_frame"] = "keyframes/k1.png"
    (root / "music" / "theme.mp3").write_bytes(b"ID3" + b"\0" * 64)
    (root / "grade.cube").write_text("LUT_3D_SIZE 2\n")
    data["style"].update(bgm_file="music/theme.mp3", lut="grade.cube", voice="zh-TW-HsiaoChenNeural")
    data["renders"] = {"SC01": {"seed": 123, "status": "done"}}
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    return root


def test_export_writes_every_material_and_readable_documents(tmp_path):
    root = film(tmp_path)
    _, out = run("kit.py", "export", root)
    kit = Path(out["kit"])
    assert kit.name.endswith("-kit.zip") and kit.parent == root / "out"
    assert out["counts"] == {"subjects": 2, "environments": 1, "shots": len(json.loads((root / "project.json")
                             .read_text())["shots"]), "refs": 5, "voice": 1, "music": 1, "keyframes": 1}
    with zipfile.ZipFile(kit) as z:
        names = set(z.namelist())
        for doc in ("README.md", "script.md", "characters.md", "locations.md", "style.md", "shotlist.md",
                    "bible.json", "kit.json"):
            assert doc in names
        bible = json.loads(z.read("bible.json"))
        manifest = json.loads(z.read("kit.json"))
        chars = z.read("characters.md").decode()
        shots = z.read("shotlist.md").decode()
        style = z.read("style.md").decode()
    s1 = bible["subjects"][0]
    assert s1["sheet"] == ["refs/subjects/S1/S1_front.png", "refs/subjects/S1/S1_three-quarter.png"]
    assert all(p in names for p in s1["sheet"]) and bible["environments"][0]["master"] in names
    assert bible["style"]["bgm_file"] == "audio/music/theme.mp3" and bible["style"]["lut"] == "style/grade.cube"
    assert bible["shots"][0]["audio_file"].startswith("audio/voice/") and bible["seeds"] == {"SC01": 123}
    assert manifest["format"] == "sinobee-video-kit" and all(len(f["sha256"]) == 64 for f in manifest["files"])
    assert "凌風" in chars and "能力上限" in chars and "動作特徵" in chars
    assert "景別" in shots and "運鏡" in shots and "zh-TW-HsiaoChenNeural" in style and "taiko" in style


def test_series_import_keeps_identity_and_style_through_the_planners_changes(tmp_path):
    root = film(tmp_path)
    _, out = run("kit.py", "export", root)
    new = tmp_path / "sequel"
    _, imp = run("kit.py", "import", out["kit"], new)
    assert imp["mode"] == "series" and set(imp["locked"]) == {"subjects", "environments", "style"}
    plan = json.loads((new / "plan.json").read_text())
    assert plan["shots"] == [] and plan["script"]["body"] == "" and plan["title"] == ""
    assert (new / plan["subjects"][0]["sheet"][0]).exists() and (new / plan["style"]["bgm_file"]).exists()
    # the planner writes the sequel: it renames Ling Feng's id, "improves" his look and the style, adds a new person
    plan.update(title="繡春刀・雪夜", intent="續集：雪夜追兇", script={"format": "screenplay", "body": "雪夜。"})
    lf = plan["subjects"][0]
    lf.update(id="S9", appearance="a blond man in a suit")
    plan["subjects"] = [lf, plan["subjects"][1], {"id": "S3", "name": "小蝶", "appearance": "young woman",
                                                    "wardrobe": "plain blue robe"}]
    plan["style"]["look"] = "anime cel shading"
    plan["shots"] = [{"id": "SH01", "group": "SC01", "env": "E1", "subjects": ["S9", "S3"], "duration": 4,
                      "action": "Ling Feng walks through snow", "dialogue": [{"subject": "S9", "line": "走。"}]}]
    (new / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    run("run.py", new, "--budget", "1", ok=False)
    proj = json.loads((new / "project.json").read_text())
    subj = {s["id"]: s for s in proj["subjects"]}
    assert "S9" not in subj and subj["S1"]["name"] == "凌風"
    assert subj["S1"]["appearance"].startswith("Chinese man around 30") and subj["S1"]["sheet_seed"] == 4242
    assert subj["S3"]["name"] == "小蝶"                                       # new characters are allowed
    assert proj["shots"][0]["subjects"] == ["S1", "S3"] and proj["shots"][0]["dialogue"][0]["subject"] == "S1"
    assert proj["style"]["look"].startswith("Cinematic Chinese wuxia") and proj["style"]["bgm_file"] == "kit/audio/music/theme.mp3"
    assert proj["title"] == "繡春刀・雪夜"


def test_remake_copies_story_audio_and_seeds_and_unlock_allows_changes(tmp_path):
    root = film(tmp_path)
    _, out = run("kit.py", "export", root)
    new = tmp_path / "remake"
    run("kit.py", "import", out["kit"], new, "--mode", "remake", "--unlock", "style")
    plan = json.loads((new / "plan.json").read_text())
    assert plan["shots"] and plan["shots"][0]["audio_file"].startswith("kit/audio/voice/")
    plan["style"]["look"] = "black and white noir"
    (new / "plan.json").write_text(json.dumps(plan, ensure_ascii=False))
    run("run.py", new, "--budget", "1", ok=False)
    proj = json.loads((new / "project.json").read_text())
    assert proj["style"]["look"] == "black and white noir"                    # style was unlocked
    assert proj["renders"]["SC01"]["seed"] == 123


def test_import_rejects_unsafe_or_damaged_kits(tmp_path):
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("kit.json", json.dumps({"format": "sinobee-video-kit", "version": 1}))
        z.writestr("bible.json", "{}")
        z.writestr("../escape.png", "x")
    code, out = run("kit.py", "import", bad, tmp_path / "x", ok=False)
    assert code and "unsafe path" in out["error"]
    root = film(tmp_path)
    _, out = run("kit.py", "export", root)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(out["kit"]) as src, zipfile.ZipFile(broken, "w") as dst:
        for i in src.infolist():
            data = src.read(i)
            dst.writestr(i, b"tampered" if i.filename.endswith("S1_front.png") else data)
    code, out = run("kit.py", "import", broken, tmp_path / "y", ok=False)
    assert code and "damaged" in out["error"]
    code, out = run("kit.py", "import", tmp_path / "nope.zip", tmp_path / "z", ok=False)
    assert code


def test_show_and_autopilot_exports_the_kit_when_done(tmp_path):
    root = film(tmp_path)
    sys.path.insert(0, str(SCRIPTS))
    import run as autopilot
    log = []
    kit = autopilot.export_kit(root, log)
    assert kit and Path(kit).exists() and log == [f"production kit {Path(kit).name}"]
    assert autopilot.export_kit(root, []) == kit                                # not rebuilt when unchanged
    _, info = run("kit.py", "show", kit)
    assert info["title"] == "繡春刀・黃昏對決" and info["counts"]["subjects"] == 2


@pytest.fixture
def cfg(config):
    config.data["permissions"]["toolsets"] = ["files", "skills", "work"]
    config.data.setdefault("learning", {})["enabled"] = False
    return config


async def test_workflow_plan_node_imports_the_kit_and_plans_from_it(cfg, tmp_path, monkeypatch):
    from sinobee.agent import Runtime
    from sinobee.work import actions, agents
    from sinobee.work.hub import get_hub
    root = film(tmp_path)
    _, out = run("kit.py", "export", root)
    real = actions._run_entry
    tasks = []

    async def entry(ctx, skill, args, timeout=3600, script=""):
        if script:
            return await real(ctx, skill, args, timeout, script)
        return {"status": "working", "folder": args[0]}

    async def planner(rt, *, task, **kw):
        tasks.append(task)
        base = json.loads(task[task.index("```json") + 7: task.rindex("```")])
        base.update(title="雪夜", shots=[{"id": "SH01", "group": "SC01", "env": "E1", "subjects": ["S1"],
                                         "duration": 4, "action": "Ling Feng waits"}])
        return {"session_id": "p1", "result": json.dumps(base, ensure_ascii=False)}
    monkeypatch.setattr(actions, "_run_entry", entry)
    monkeypatch.setattr(agents, "run_subagent", planner)

    class Fake:
        provider_name, model, label, recorder = "openai", "fake", "test/fake", None

        async def chat(self, *a, **k):
            raise AssertionError("no model call expected")
    rt = Runtime(cfg, provider=Fake())
    hub = get_hub(rt)
    run_ = hub.store.create_run(job_id=None, name="續集", kind="workflow", trigger="manual", state={})
    ctx = actions.ActionContext(hub=hub, run=run_, job=None, board_scope=f"run:{run_['id']}", node_state={})
    res = await actions.skill_plan(ctx, skill="ai-video-production", intent="續集：雪夜", folder="videos/sequel",
                                   kit=out["kit"], kit_mode="series", example="assets/example-film-duel.json")
    assert res["folder"] == "videos/sequel" and res["shots"] == 1
    assert "continues an earlier production" in tasks[0] and "凌風" in tasks[0]
    ws = Path(cfg.workspace) / "videos" / "sequel"
    assert (ws / "kit.lock.json").exists() and (ws / "kit" / "refs" / "subjects" / "S1").is_dir()
    board = hub.store.board_read(f"run:{run_['id']}", kinds=["decision"])
    assert any("沿用素材包" in str(b.get("content")) for b in board)
