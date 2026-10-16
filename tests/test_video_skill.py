"""ai-video-production skill: spec validity, planning scripts, prompt building, graph building and assembly."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "ai-video-production"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import comfy_h3  # noqa: E402
import vpcommon  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(script, *args, ok=True):
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True)
    out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r.returncode, out


def test_skill_is_valid_and_clean():
    from sinobee.skills import spec
    from sinobee.tools.approval import scan_content, scan_script
    text = (SKILL / "SKILL.md").read_text()
    p = spec.parse(text)
    assert spec.validate(p.frontmatter, SKILL.name, strict_ascii=True) == []
    assert scan_content(text) == []
    for f in SCRIPTS.glob("*.py"):
        assert scan_script(f.read_text()) == [], f.name
    for ref in ("script-formats", "profiling-matrix", "shot-grammar", "h3-prompting", "checklist", "project-schema"):
        assert f"references/{ref}.md" in text or ref == "project-schema" and "project-schema.md" in text


def test_h3_geometry():
    assert vpcommon.align_frames(124) == 124 and vpcommon.align_frames(125) == 141
    assert vpcommon.frames_for_seconds(2) == 124            # short shots render at the minimum and get trimmed
    assert vpcommon.frames_for_seconds(6.5) == 158
    assert vpcommon.frames_for_seconds(40) == 362
    assert vpcommon.canvas("16:9") == (1344, 768)
    assert vpcommon.canvas("9:16") == (768, 1344)
    assert vpcommon.canvas("16:9", 0.75) == (1024, 576)
    assert vpcommon.output_size("9:16", "1080p") == (1080, 1920)


def project(tmp_path):
    root = tmp_path / "tea"
    run("project.py", "init", root, "--genre", "documentary", "--title", "茶")
    data = json.loads((SKILL / "assets" / "example-documentary.json").read_text())
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    return root


def test_validate_and_prompts(tmp_path):
    root = project(tmp_path)
    code, out = run("project.py", "validate", root)
    assert out["ok"] and not out["errors"]
    data = json.loads((root / "project.json").read_text())
    data["subjects"][0]["negative_boundary"] = ""
    data["shots"][0]["duration"] = 12
    data["shots"][1]["duration"] = 6                        # SC01 now 18 s > H3's 15 s
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    code, out = run("project.py", "validate", root, ok=False)
    assert code == 1
    assert any("profiling matrix incomplete" in e for e in out["errors"])
    assert any("exceeds H3" in e for e in out["errors"])

    data["subjects"][0]["negative_boundary"] = "never rushes"
    data["shots"][0]["duration"] = 4
    data["shots"][1]["duration"] = 2.5
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    run("project.py", "prompts", root)
    p = (root / "prompts" / "SC01.txt").read_text()
    assert "[0.0s-4.0s] Shot 1" in p and "[4.0s-6.5s] Shot 2" in p
    assert "slightly stooped" in p and "boundaries: never rushes" in p
    assert '阿明 (Ming) says in Mandarin Chinese (Taiwan accent), lips in sync: "今年的茶很香。"' in p
    assert "Nobody speaks on camera in this shot" in p
    assert "subtle, natural" in p                           # documentary motion strength 0.3
    assert "only 阿明 (Ming) (1 person), no other people" in p
    code, plan = run("project.py", "plan", root)
    assert [u["unit"] for u in plan["units"]] == ["SC01", "SC02"] and plan["units"][0]["frames"] == 158


def test_graph_modes(tmp_path):
    m = {"fl2va": "fl.safetensors", "ref2va": "ref.safetensors", "clip": "c", "video_vae": "v", "audio_vae": "a",
         "lora_fl2va": "fl2v_turbo_8step.safetensors", "lora_ref2va": "ref2v_turbo_4step.safetensors"}
    g = comfy_h3.build(m, mode="fl2va", prompt="p", width=1024, height=576, frames=158, seed=1, turbo=True,
                       prefix="x", first="kf.png")
    assert g["cond"]["class_type"] == "MiniMaxH3ImageToVideo" and g["cond"]["inputs"]["first_frame"] == ["ff", 0]
    assert g["sched"]["inputs"]["steps"] == 8 and g["save"]["inputs"]["format.codec"] == "auto"
    g = comfy_h3.build(m, mode="ref2va", prompt="p", width=1344, height=768, frames=124, seed=1, turbo=False,
                       prefix="x", refs=["a.png", "b.png"])
    assert g["cond"]["inputs"]["ref_images.ref_image_1"] == ["ref1", 0]
    assert g["unet"]["inputs"]["unet_name"] == "ref.safetensors" and g["sched"]["inputs"]["steps"] == 20
    g = comfy_h3.build(m, mode="fl2va", prompt="p", width=1344, height=768, frames=5, seed=1, turbo=True,
                       prefix="x", still=True)
    assert g["save"]["class_type"] == "SaveImage" and "adec" not in g

    root = project(tmp_path)
    data = json.loads((root / "project.json").read_text())
    (root / "refs" / "S1_front.png").write_bytes(b"x")
    data["subjects"][0]["sheet"] = ["refs/S1_front.png"]
    files, pre = comfy_h3.ref_plan(data, root, {"id": "SC01", "shots": ["SH01", "SH02"]})
    assert files == [root / "refs" / "S1_front.png"] and "<Picture 1> show 阿明" in pre
    files, pre = comfy_h3.ref_plan(data, root, {"id": "SC02", "shots": ["SH03"]})   # extreme wide: no face ref
    assert files == [] and pre == ""


def test_model_picking():
    unets = ["minimax_h3_fl2va_bf16.safetensors", "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
             "minimax_h3_fl2va_int8_convrot.safetensors"]
    order = ["_int8_convrot", "pruned_int8", "fp8", "bf16"]
    nonpruned_first = [u for u in unets if "pruned" not in u] + [u for u in unets if "pruned" in u]
    assert comfy_h3.pick(nonpruned_first, "fl2va", order) == "minimax_h3_fl2va_int8_convrot.safetensors"
    assert comfy_h3.pick(unets, "ref2va", order) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_timing_assemble_qc(tmp_path):
    root = project(tmp_path)
    data = json.loads((root / "project.json").read_text())
    for sid, secs in (("SH01", 3.8), ("SH03", 2.7)):
        f = root / "audio" / f"{sid}_vo.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=300:d={secs}", str(f)], check=True)
        shot = next(s for s in data["shots"] if s["id"] == sid)
        shot.update(audio_file=f"audio/{f.name}", audio_role="voiceover")
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    code, out = run("project.py", "timing", root)
    durs = {s["id"]: s["duration"] for s in json.loads((root / "project.json").read_text())["shots"]}
    assert durs["SH01"] == 4.0 and durs["SH03"] == 3.0      # 3.8 s speech -> 4 s shot
    # animatic with grey slates for SC02, a fake render for SC01
    clip = root / "clips" / "SC01_t1.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=7", "-f", "lavfi",
                    "-i", "sine=f=200:d=7", "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
    data = json.loads((root / "project.json").read_text())
    data["renders"] = {"SC01": {"status": "done", "clip": "clips/SC01_t1.mp4"}}
    (root / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    code, _ = run("assemble.py", root, ok=False)
    assert code != 0                                        # SC02 missing
    code, out = run("assemble.py", root, "--allow-missing", "--delivery", "720p")
    assert out["placeholders"] == ["SC02"] and out["size"] == [1280, 720]
    assert abs(out["duration"] - 9.0) < 0.2 and out["voiceover_clips"] == 2
    srt = (root / "out" / "final.srt").read_text()
    assert "清晨五點" in srt and "00:00:06,100" in srt
    code, q = run("qc.py", "project", root)
    assert q["final"]["has_audio"] and (root / q["final"]["sheet"]).exists()


def test_forgiving_cli_and_editing(tmp_path):
    root = tmp_path / "duel"
    run("project.py", "init", root, "--genre", "film")
    # folder before command, repeated script name, invented command, project.json as command
    code, out = run("project.py", root, "plan")
    assert out["ok"]
    code, out = run("project.py", "scripts/project.py", "plan", root)
    assert out["ok"]
    code, out = run("project.py", "profiling", root, ok=False)
    assert code != 0 and "not a command" in out["error"] and "subjects" in out["error"]
    code, out = run("project.py", root / "project.json", ok=False)
    assert "first argument must be a command" in out["error"]
    # set creates items by id, merge maps common aliases, remove drops placeholders
    run("project.py", "set", root, "subjects.S2.name", "血煞", "shots.SH02.duration", "2.5", "style.palette",
        '["#111", "#222"]')
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"characters": [{"id": "S2", "appearance": "scarred man"}],
                                "setting": {"E1": {"description": "ruins", "light": "sunset left"}},
                                "script_body": "EXT. RUINS - DUSK"}, ensure_ascii=False))
    code, out = run("project.py", "merge", root, plan)
    assert "characters -> subjects" in out["notes"]
    run("project.py", "remove", root, "subjects.S1")
    data = json.loads((root / "project.json").read_text())
    assert [s["id"] for s in data["subjects"]] == ["S2"] and data["subjects"][0]["appearance"] == "scarred man"
    assert data["subjects"][0]["kinematic_signature"] == ""            # created from the template
    assert next(s for s in data["shots"] if s["id"] == "SH02")["duration"] == 2.5
    assert data["style"]["palette"] == ["#111", "#222"] and data["script"]["body"] == "EXT. RUINS - DUSK"
    assert data["environments"][0]["light"] == "sunset left"
    (root / "characters.txt").write_text("x")
    code, out = run("project.py", "validate", root, ok=False)
    assert any("characters.txt" in w for w in out["warnings"])
    assert any("subjects.S2: profiling matrix incomplete" in e for e in out["errors"])


def test_next_walks_the_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:9")
    root = tmp_path / "duel"
    code, out = run("next.py", root)
    assert out["phase"] == "0 brief" and out["call"]["args"].startswith(f"init {root}")
    run("project.py", "init", root, "--genre", "film")
    code, out = run("next.py", root)
    assert out["phase"].startswith("1-4") and out["call"]["args"] == f"merge {root} plan.json"
    assert any("script.body" in m for m in out["missing"])
    run("project.py", "merge", root, SKILL / "assets" / "example-film-duel.json")
    code, out = run("next.py", root)
    assert out["phase"] == "1 audio-first" and out["call"]["script"] == "scripts/tts.py"
    run("project.py", "set", root, "skip_tts", "true")
    code, out = run("next.py", root)
    assert out["phase"] == "5 prompts"
    run("project.py", "prompts", root)
    code, out = run("next.py", root)          # nothing listens on the test URL
    assert out["phase"].startswith("blocked") and out["call"] is None
    p = (root / "prompts" / "SC03.txt").read_text()
    assert "凌風 (Ling Feng): low centered stance" in p and "only 凌風 (Ling Feng) and 血煞 (Xue Sha) (2 people)" in p


def test_skill_run_names_the_scripts():
    import asyncio
    from types import SimpleNamespace
    from sinobee.tools import skill_tools
    skill = SimpleNamespace(policy=SimpleNamespace(scripts="allow"), disabled=False, path=SKILL, trust="builtin",
                            reason="", entry=None, files=lambda: ["SKILL.md", "scripts/next.py", "scripts/project.py"])
    ctx = SimpleNamespace(skills=SimpleNamespace(get=lambda n: skill))
    fn = getattr(skill_tools.skill_run, "fn", None) or getattr(skill_tools.skill_run, "func", None) or skill_tools.skill_run
    res = asyncio.run(fn(ctx, "ai-video-production", "videos/x/project.json"))
    assert res.is_error and "scripts/next.py" in res.content and "args" in res.content
