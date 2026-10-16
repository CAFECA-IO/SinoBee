#!/usr/bin/env python3
"""Render with MiniMax H3 through a ComfyUI server (API at COMFYUI_URL, default http://127.0.0.1:8188).

  comfy_h3.py check                                      # server, H3 nodes and model files
  comfy_h3.py sheet   <folder> --subject S1 [--views front,three-quarter,back,hard-light]   # Phase 3.1
  comfy_h3.py master  <folder> --env E1                  # Phase 3.2 empty establishing still
  comfy_h3.py keyframe <folder> --unit SC01 [--set]      # Phase 4.3 first-frame still for a unit
  comfy_h3.py render  <folder> (--unit SC01 | --all) [--quality draft|final|best] [--seed N] [--force] [--no-wait]
  comfy_h3.py collect <folder>                           # download units queued with --no-wait
  comfy_h3.py generate --prompt "…" --seconds 5 --out clip.mp4 [--aspect 16:9] [--first-frame img.png]

Modes are picked per unit: a first_frame/last_frame keyframe -> fl2va (image-to-video); otherwise subject sheets /
environment masters -> ref2va (reference-to-video, if that model is installed); otherwise text-to-video.
Quality: draft = ~0.75 canvas + turbo LoRA (fast previews), final = full canvas + turbo, best = full canvas, 20 steps.
Output JSON. Long renders: pass a large timeout to the tool, or --no-wait then `collect`.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from prompting import (ANGLES_SHEET, environment_master_prompt, keyframe_prompt, subject_sheet_prompt, unit_prompt)
from vpcommon import (normalize_argv, canvas, die, emit, frames_for_seconds, load_project, resolve, save_project, shots_by_id,
                      unit_seconds, unit_state, units)

WIDE = {"wide", "extreme wide", "medium wide", "bird's-eye", "overhead"}
QUALITY = {"draft": (0.75, True), "final": (1.0, True), "best": (1.0, False)}
TIMEOUT = 3 * 3600


class Comfy:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def get(self, path: str, raw: bool = False):
        try:
            with urllib.request.urlopen(self.url + path, timeout=60) as r:
                data = r.read()
        except urllib.error.URLError as e:
            die(f"cannot reach ComfyUI at {self.url} ({e}). Start it, or open the SSH tunnel "
                f"(ssh -N -L 8188:127.0.0.1:8188 <user>@<gpu-server>), or set COMFYUI_URL")
        return data if raw else json.loads(data or b"{}")

    def post(self, path: str, body: bytes, ctype: str = "application/json"):
        req = urllib.request.Request(self.url + path, data=body, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            die(f"ComfyUI rejected the request ({e.code}): {detail[:1500]}")
        except urllib.error.URLError as e:
            die(f"cannot reach ComfyUI at {self.url} ({e})")

    def options(self, node: str, field: str) -> list[str]:
        info = self.get(f"/object_info/{node}").get(node, {}).get("input", {})
        spec = {**info.get("required", {}), **info.get("optional", {})}.get(field)
        if not spec:
            return []
        if isinstance(spec[0], list):
            return spec[0]
        return list((spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}).get("options", []))

    def upload(self, path: Path) -> str:
        boundary = uuid.uuid4().hex
        name = f"vp_{uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex[:10]}_{path.name}"
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode() + path.read_bytes() + b"\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode(),
            f"--{boundary}--\r\n".encode(),
        ]
        r = self.post("/upload/image", b"".join(parts), f"multipart/form-data; boundary={boundary}")
        return (r.get("subfolder") + "/" if r.get("subfolder") else "") + r["name"]

    def queue(self, graph: dict) -> str:
        return self.post("/prompt", json.dumps({"prompt": graph, "client_id": "sinobee-video"}).encode())["prompt_id"]

    def wait(self, pid: str, timeout: float) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self.get(f"/history/{pid}")
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "error" or (st.get("completed") is False and st.get("messages") and
                                                        any(m[0] == "execution_error" for m in st["messages"])):
                    err = next((m[1] for m in st.get("messages", []) if m[0] == "execution_error"), {})
                    die(f"render failed in node {err.get('node_type')}: {err.get('exception_message', '')[:800]}")
                if st.get("completed") or h[pid].get("outputs"):
                    return h[pid]
            time.sleep(5)
        die(f"timed out after {timeout}s; the job keeps running in ComfyUI (prompt_id {pid}) - run `collect` later")
        raise AssertionError

    def done(self, pid: str) -> dict | None:
        h = self.get(f"/history/{pid}")
        return h.get(pid) if pid in h and (h[pid].get("outputs") or h[pid].get("status", {}).get("completed")) else None

    def download(self, item: dict, dst: Path) -> Path:
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(self.get(f"/view?{q}", raw=True))
        return dst


def url_for(proj: dict | None, arg: str | None) -> str:
    return arg or os.environ.get("COMFYUI_URL") or (proj or {}).get("comfyui_url") or "http://127.0.0.1:8188"


def pick(options: list[str], must: str, prefer: list[str], override: str | None = None) -> str | None:
    if override:
        if override not in options:
            die(f"model {override} is not on the ComfyUI server (have: {', '.join(options)[:600]})")
        return override
    cands = [o for o in options if must in o.lower()]
    for p in prefer:
        for o in cands:
            if p in o.lower():
                return o
    return cands[0] if cands else None


def models(c: Comfy, proj: dict | None = None) -> dict:
    ov = (proj or {}).get("models") or {}
    unets = c.options("UNETLoader", "unet_name")
    clips = c.options("CLIPLoader", "clip_name")
    vaes = c.options("VAELoader", "vae_name")
    loras = c.options("LoraLoaderModelOnly", "lora_name")
    order = ["_int8_convrot", "pruned_int8", "fp8", "bf16", "w6a8"]
    m = {
        "fl2va": pick([u for u in unets if "pruned" not in u] + [u for u in unets if "pruned" in u], "fl2va", order,
                      ov.get("fl2va")),
        "ref2va": pick([u for u in unets if "pruned" not in u] + [u for u in unets if "pruned" in u], "ref2va", order,
                       ov.get("ref2va")),
        "clip": pick(clips, "qwen3vl", ["int8", "bf16", "fp8", "nvfp4"], ov.get("clip")),
        "video_vae": pick(vaes, "h3_video_vae", ["fp16", "bf16", "int8"], ov.get("video_vae")),
        "audio_vae": pick(vaes, "h3_audio_vae", ["fp32", "fp16"], ov.get("audio_vae")),
        "lora_fl2va": pick(loras, "fl2v_turbo", ["8step", "4step"], ov.get("lora_fl2va")),
        "lora_ref2va": pick(loras, "ref2v_turbo", ["4step", "8step"], ov.get("lora_ref2va")),
    }
    return m


def turbo_steps(lora: str | None) -> int:
    if lora and "4step" in lora:
        return 4
    return 8


def build(m: dict, *, mode: str, prompt: str, width: int, height: int, frames: int, seed: int, turbo: bool,
          prefix: str, first: str | None = None, last: str | None = None, refs: list[str] | None = None,
          still: bool = False) -> dict:
    unet = m["ref2va"] if mode == "ref2va" else m["fl2va"]
    lora = m["lora_ref2va"] if mode == "ref2va" else m["lora_fl2va"]
    if not unet or not m["clip"] or not m["video_vae"] or (not still and not m["audio_vae"]):
        die(f"missing H3 model files on the server: {json.dumps(m)}")
    g: dict = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "minimax", "device": "default"}},
        "vvae": {"class_type": "VAELoader", "inputs": {"vae_name": m["video_vae"]}},
    }
    model = ["unet", 0]
    steps = 20
    if turbo and lora:
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": model, "lora_name": lora, "strength_model": 1.0}}
        model, steps = ["lora", 0], turbo_steps(lora)
    if mode == "ref2va":
        cond = {"clip": ["clip", 0], "vae": ["vvae", 0], "prompt": prompt, "width": width, "height": height,
                "length": frames, "ref_image_size": "match"}
        if not still:
            g["avae"] = {"class_type": "VAELoader", "inputs": {"vae_name": m["audio_vae"]}}
            cond["audio_vae"] = ["avae", 0]
        for i, name in enumerate(refs or []):
            g[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            cond[f"ref_images.ref_image_{i}"] = [f"ref{i}", 0]
        g["cond"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cond}
    else:
        cond = {"clip": ["clip", 0], "vae": ["vvae", 0], "prompt": prompt, "width": width, "height": height,
                "length": frames}
        if first:
            g["ff"] = {"class_type": "LoadImage", "inputs": {"image": first}}
            cond["first_frame"] = ["ff", 0]
        if last:
            g["lf"] = {"class_type": "LoadImage", "inputs": {"image": last}}
            cond["last_frame"] = ["lf", 0]
        g["cond"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": cond}
    g.update({
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "sched": {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": steps,
                                                             "denoise": 1.0}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["cond", 0]}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                              "sigmas": ["sched", 0], "latent_image": ["cond", 1]}},
        "vdec": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vvae", 0]}},
    })
    if still:
        g["save"] = {"class_type": "SaveImage", "inputs": {"images": ["vdec", 0], "filename_prefix": prefix}}
    else:
        if "avae" not in g:
            g["avae"] = {"class_type": "VAELoader", "inputs": {"vae_name": m["audio_vae"]}}
        g["adec"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sample", 0], "vae": ["avae", 0]}}
        g["video"] = {"class_type": "CreateVideo", "inputs": {"images": ["vdec", 0], "audio": ["adec", 0], "fps": 24}}
        g["save"] = {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": prefix,
                                                           "format": "auto", "format.codec": "auto"}}
    return g


def first_output(hist: dict) -> dict:
    for out in hist.get("outputs", {}).values():
        for k in ("images", "videos", "gifs"):
            if out.get(k):
                return out[k][0]
    die("ComfyUI finished but returned no output file")
    raise AssertionError


def ref_plan(proj: dict, root: Path, unit: dict) -> tuple[list[Path], str]:
    """Reference images for a unit (subject sheets, then environment masters) and the prompt preamble naming them."""
    sb = shots_by_id(proj)
    subj = {s["id"]: s for s in proj.get("subjects", [])}
    envs = {e["id"]: e for e in proj.get("environments", [])}
    files: list[Path] = []
    notes = []
    seen_s, seen_e = [], []
    for sid in unit["shots"]:
        s = sb[sid]
        for i in s.get("subjects", []):
            if i not in seen_s and i in subj:
                seen_s.append(i)
        if s.get("env") and s["env"] not in seen_e and s["env"] in envs:
            seen_e.append(s["env"])
    # in wide shots a person is a few pixels tall: a face reference there makes H3 add a second, close-up copy
    # of that person, so wide-only units get the location reference only
    wide_only = all((sb[sid].get("shot_size") or "").lower() in WIDE for sid in unit["shots"])
    for i in ([] if wide_only else seen_s):
        sheet = [resolve(root, f) for f in subj[i].get("sheet") or []]
        sheet = [f for f in sheet if f and f.exists()][:1]   # one image per subject: more refs -> duplicated people
        if sheet:
            nums = [len(files) + k + 1 for k in range(len(sheet))]
            files += sheet
            tags = " and ".join(f"<Picture {n}>" for n in nums)
            notes.append(f"{tags} show {subj[i].get('name') or i}: keep exactly this face, hair, body and wardrobe")
    for e in seen_e:
        m = resolve(root, envs[e].get("master"))
        if m and m.exists() and len(files) < 9:
            files.append(m)
            notes.append(f"<Picture {len(files)}> is the location {envs[e].get('name') or e}: keep its layout, "
                         "materials, light direction and colors")
    return files, ("References: " + "; ".join(notes) + ".\n\n") if notes else ""


def render_unit(c: Comfy, m: dict, proj: dict, root: Path, unit: dict, *, quality: str, seed: int | None,
                wait: bool, timeout: float) -> dict:
    st = unit_state(proj, unit["id"])
    sb = shots_by_id(proj)
    secs = unit_seconds(proj, unit)
    frames = frames_for_seconds(secs)
    scale, turbo = QUALITY[quality]
    w, h = canvas(proj.get("aspect", "16:9"), scale)
    prompt = st.get("prompt") or unit_prompt(proj, unit)
    first = resolve(root, sb[unit["shots"][0]].get("first_frame"))
    last = resolve(root, sb[unit["shots"][-1]].get("last_frame"))
    refs, preamble = ref_plan(proj, root, unit)
    if first or last:
        mode = "fl2va"
    elif refs and m.get("ref2va"):
        mode = "ref2va"
    else:
        mode = "fl2va"
    seed = seed if seed is not None else st.get("seed") or random.randint(1, 2**31 - 1)
    take = len(st.get("takes", [])) + 1
    graph = build(m, mode=mode, prompt=(preamble + prompt) if mode == "ref2va" else prompt, width=w, height=h,
                  frames=frames, seed=seed, turbo=turbo, prefix=f"video/{proj_slug(root)}_{unit['id']}_t{take}",
                  first=c.upload(first) if first else None, last=c.upload(last) if last else None,
                  refs=[c.upload(f) for f in refs] if mode == "ref2va" else None)
    pid = c.queue(graph)
    st.update(prompt=prompt, seed=seed, status="queued", prompt_id=pid, mode=mode, quality=quality, frames=frames,
              canvas=[w, h], queued_at=time.time())
    st.pop("stale", None)
    info = {"unit": unit["id"], "prompt_id": pid, "mode": mode, "seconds": round(secs, 2), "frames": frames,
            "canvas": [w, h], "seed": seed, "quality": quality}
    if not wait:
        return info
    hist = c.wait(pid, timeout)
    return {**info, **finish(c, proj, root, unit["id"], hist)}


def finish(c: Comfy, proj: dict, root: Path, uid: str, hist: dict) -> dict:
    st = unit_state(proj, uid)
    take = len(st.get("takes", [])) + 1
    dst = c.download(first_output(hist), root / "clips" / f"{uid}_t{take}.mp4")
    rel = str(dst.relative_to(root))
    st.setdefault("takes", []).append({"clip": rel, "seed": st.get("seed"), "quality": st.get("quality"),
                                      "mode": st.get("mode"), "at": time.time()})
    st.update(status="done", clip=rel, rendered_at=time.time())
    st["render_seconds"] = round(time.time() - st.get("queued_at", time.time()), 1)
    return {"clip": rel, "status": "done", "render_seconds": st["render_seconds"]}


def proj_slug(root: Path) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in root.name)[:24] or "vp"


def still(c: Comfy, m: dict, proj: dict, prompt: str, dst: Path, seed: int, mode: str = "fl2va",
          refs: list[Path] | None = None) -> Path:
    w, h = canvas(proj.get("aspect", "16:9"), 1.0)
    graph = build(m, mode=mode, prompt=prompt, width=w, height=h, frames=5, seed=seed, turbo=True,
                  prefix=f"vp_stills/{dst.stem}", still=True, refs=[c.upload(f) for f in refs or []])
    hist = c.wait(c.queue(graph), 1800)
    return c.download(first_output(hist), dst)


def cmd_check(a) -> None:
    proj = None
    if a.folder:
        proj, _ = load_project(a.folder)
    c = Comfy(url_for(proj, a.url))
    stats = c.get("/system_stats")
    nodes = c.get("/object_info/MiniMaxH3ImageToVideo")
    m = models(c, proj)
    dev = (stats.get("devices") or [{}])[0]
    emit({"ok": bool(nodes) and bool(m["fl2va"]) and bool(m["clip"]), "url": c.url,
          "comfyui": stats.get("system", {}).get("comfyui_version"), "gpu": dev.get("name"),
          "vram_gb": round((dev.get("vram_total") or 0) / 2**30, 1), "h3_nodes": bool(nodes), "models": m,
          "queue": len(c.get("/queue").get("queue_running", []))})


def cmd_sheet(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    subj = {s["id"]: s for s in proj.get("subjects", [])}
    s = subj.get(a.subject) or die(f"no subject {a.subject}")
    c = Comfy(url_for(proj, a.url))
    m = models(c, proj)
    seed = a.seed or random.randint(1, 2**31 - 1)
    made = []
    views = a.views.split(",") if a.views else [v for v, _ in ANGLES_SHEET]
    for v in views:
        dst = root / "refs" / f"{a.subject}_{v}.png"
        still(c, m, proj, subject_sheet_prompt(proj, s, v), dst, seed)   # same seed keeps the identity closer
        made.append(str(dst.relative_to(root)))
    s["sheet"] = made
    s["sheet_seed"] = seed            # exported in the production kit, so a later film can rebuild the same look
    save_project(proj, path)
    emit({"ok": True, "subject": a.subject, "sheet": made, "seed": seed,
          "next": "look at every image (read_file); regenerate with another --seed if the identity drifts"})


def cmd_master(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    envs = {e["id"]: e for e in proj.get("environments", [])}
    e = envs.get(a.env) or die(f"no environment {a.env}")
    c = Comfy(url_for(proj, a.url))
    dst = root / "refs" / f"{a.env}_master.png"
    seed = a.seed or e.get("master_seed") or random.randint(1, 2**31 - 1)
    still(c, models(c, proj), proj, environment_master_prompt(proj, e), dst, seed)
    e["master"] = str(dst.relative_to(root))
    e["master_seed"] = seed
    save_project(proj, path)
    emit({"ok": True, "env": a.env, "master": e["master"]})


def cmd_keyframe(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    u = next((x for x in units(proj) if x["id"] == a.unit), None) or die(f"no unit {a.unit}")
    c = Comfy(url_for(proj, a.url))
    m = models(c, proj)
    refs, preamble = ref_plan(proj, root, u)
    mode = "ref2va" if refs and m.get("ref2va") else "fl2va"
    prompt = (preamble if mode == "ref2va" else "") + keyframe_prompt(proj, u)
    dst = root / "keyframes" / f"{a.unit}_first.png"
    still(c, m, proj, prompt, dst, a.seed or random.randint(1, 2**31 - 1), mode=mode, refs=refs)
    out = {"ok": True, "unit": a.unit, "keyframe": str(dst.relative_to(root)), "mode": mode}
    if a.set:
        shots_by_id(proj)[u["shots"][0]]["first_frame"] = out["keyframe"]
        save_project(proj, path)
        out["set_as_first_frame_of"] = u["shots"][0]
    emit(out)


def cmd_render(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    c = Comfy(url_for(proj, a.url))
    m = models(c, proj)
    todo = [u for u in units(proj) if (a.all or u["id"] == a.unit)]
    if not todo:
        die(f"no unit {a.unit}")
    results = []
    for i, u in enumerate(todo):
        st = proj.get("renders", {}).get(u["id"], {})
        if st.get("status") == "done" and not a.force and not st.get("stale"):
            results.append({"unit": u["id"], "status": "done", "clip": st.get("clip"), "skipped": True})
            continue
        seed = a.seed + i if a.seed is not None else None
        q = a.quality or st.get("quality") or proj.get("quality", "draft")   # a retake keeps the unit's quality
        r = render_unit(c, m, proj, root, u, quality=q, seed=seed,
                        wait=not a.no_wait, timeout=a.timeout)
        save_project(proj, path)
        results.append(r)
    emit({"ok": True, "results": results,
          "next": "qc.py sheet on each clip, then assemble.py" if not a.no_wait else "comfy_h3.py collect " + a.folder})


def cmd_collect(a) -> None:
    proj, path = load_project(a.folder)
    root = path.parent
    c = Comfy(url_for(proj, a.url))
    got, waiting = [], []
    for uid, st in proj.get("renders", {}).items():
        if st.get("status") != "queued" or not st.get("prompt_id"):
            continue
        hist = c.done(st["prompt_id"])
        if hist is None:
            waiting.append(uid)
            continue
        got.append({"unit": uid, **finish(c, proj, root, uid, hist)})
        save_project(proj, path)
    emit({"ok": True, "downloaded": got, "still_rendering": waiting})


def cmd_generate(a) -> None:
    c = Comfy(url_for(None, a.url))
    m = models(c)
    prompt = Path(a.prompt_file).read_text(encoding="utf-8") if a.prompt_file else a.prompt
    if not prompt:
        die("give --prompt or --prompt-file")
    scale, turbo = QUALITY[a.quality]
    w, h = canvas(a.aspect, scale)
    frames = frames_for_seconds(a.seconds)
    seed = a.seed or random.randint(1, 2**31 - 1)
    ff = c.upload(Path(a.first_frame)) if a.first_frame else None
    graph = build(m, mode="fl2va", prompt=prompt, width=w, height=h, frames=frames, seed=seed, turbo=turbo,
                  prefix="video/vp_generate", first=ff)
    hist = c.wait(c.queue(graph), a.timeout)
    dst = c.download(first_output(hist), Path(a.out))
    emit({"ok": True, "out": str(dst), "seed": seed, "frames": frames, "canvas": [w, h]})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", help="ComfyUI URL (default: $COMFYUI_URL, project comfyui_url, http://127.0.0.1:8188)")
    sub = p.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("folder", nargs="?")
    sh = sub.add_parser("sheet")
    sh.add_argument("folder")
    sh.add_argument("--subject", required=True)
    sh.add_argument("--views", help="comma list of: front,three-quarter,back,hard-light")
    sh.add_argument("--seed", type=int)
    ms = sub.add_parser("master")
    ms.add_argument("folder")
    ms.add_argument("--env", required=True)
    ms.add_argument("--seed", type=int)
    kf = sub.add_parser("keyframe")
    kf.add_argument("folder")
    kf.add_argument("--unit", required=True)
    kf.add_argument("--seed", type=int)
    kf.add_argument("--set", action="store_true", help="use it as the unit's first_frame")
    rd = sub.add_parser("render")
    rd.add_argument("folder")
    g = rd.add_mutually_exclusive_group(required=True)
    g.add_argument("--unit")
    g.add_argument("--all", action="store_true")
    rd.add_argument("--quality", choices=list(QUALITY))
    rd.add_argument("--seed", type=int)
    rd.add_argument("--force", action="store_true", help="render again even if a clip exists (new take)")
    rd.add_argument("--no-wait", action="store_true")
    rd.add_argument("--timeout", type=float, default=TIMEOUT)
    co = sub.add_parser("collect")
    co.add_argument("folder")
    ge = sub.add_parser("generate")
    ge.add_argument("--prompt")
    ge.add_argument("--prompt-file")
    ge.add_argument("--seconds", type=float, default=5)
    ge.add_argument("--aspect", default="16:9")
    ge.add_argument("--quality", choices=list(QUALITY), default="final")
    ge.add_argument("--first-frame")
    ge.add_argument("--seed", type=int)
    ge.add_argument("--out", required=True)
    ge.add_argument("--timeout", type=float, default=TIMEOUT)
    normalize_argv(["check", "sheet", "master", "keyframe", "render", "collect", "generate"], "comfy_h3.py")
    a = p.parse_args()
    {"check": cmd_check, "sheet": cmd_sheet, "master": cmd_master, "keyframe": cmd_keyframe, "render": cmd_render,
     "collect": cmd_collect, "generate": cmd_generate}[a.cmd](a)


if __name__ == "__main__":
    main()
