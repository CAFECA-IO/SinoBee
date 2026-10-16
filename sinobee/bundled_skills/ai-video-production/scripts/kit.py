#!/usr/bin/env python3
"""Production kit (素材包): export a finished project's creative assets as a zip, import them into a new project.

  kit.py export <folder> [--out out/<slug>-kit.zip] [--with-film]
  kit.py import <kit.zip | kit folder> <new folder> [--mode series|remake] [--unlock subjects,environments,style]
  kit.py lock   <folder>      re-apply the kit's locked fields after plan.json was merged (run.py does this)
  kit.py show   <kit.zip | folder>

What a kit holds (paths inside the zip):
  kit.json                 manifest: format, version, title, counts, files with sha256, locked fields
  bible.json               the reusable plan: genre, aspect, language, style, script, subjects, environments,
                           shots (shot grammar), seeds - in the shape of plan.json
  README.md                what is inside and how to reuse it
  script.md                劇本 (the script)
  characters.md            人物設定 (appearance, wardrobe, the four profiling fields, voice)
  locations.md             場景設定 (description, light, palette, materials)
  style.md                 視覺風格、音樂、語音 (look, palette, camera, music, ambience, voice, negative)
  shotlist.md              鏡頭語言 (shot size, angle, lens, focus, composition, camera move, action, performance)
  refs/subjects/<id>/…     reference sheets (identity anchors)
  refs/environments/<id>/… establishing masters
  keyframes/…              first / last frame images
  audio/voice/…            narration and dialogue audio
  audio/music/…            background music (style.bgm_file)
  style/…                  colour LUT (style.lut)
  preview/final.mp4        (only with --with-film)

Import modes:
  series (default)  same characters, places, look, music and voices; the story (script, shots) is new
  remake            everything, including script, shots, audio and seeds; edit what you want to change

After an import, plan.json in the new folder is pre-filled. Write the new story into it as usual (reuse the kit's
subject/environment ids such as S1, E1). The fields the kit locks (identity, places, style) are restored after every
merge, so the new film keeps the same look; pass --unlock to allow changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vpcommon import die, emit, normalize_argv, resolve  # noqa: E402

FORMAT = "sinobee-video-kit"
VERSION = 1
CONTENT_KEYS = ("title", "intent", "genre", "language", "language_name", "aspect", "fps", "delivery", "quality",
                "style", "script", "subjects", "environments", "shots", "models")
LOCK_SUBJECT = ("name", "alias", "kind", "appearance", "wardrobe", "capability_ceiling", "negative_boundary",
                "linguistic_blueprint", "kinematic_signature", "voice", "sheet", "sheet_seed")
LOCK_ENV = ("name", "description", "light", "palette", "materials", "master", "master_seed")
LOCK_TOP = ("genre", "aspect", "language", "language_name", "fps", "delivery")
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".cube", ".mp4",
               ".srt", ".json", ".md", ".txt"}
MAX_BYTES = 4 * 1024 ** 3          # refuse kits that unpack to more than 4 GB
MAX_FILES = 2000


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", str(text or "film")).strip("-").lower()
    return s[:48] or "film"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(folder: Path) -> dict:
    for name in ("project.json", "plan.json"):
        p = folder / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                die(f"{p} is not valid JSON: {e}")
    die(f"no project.json or plan.json in {folder}")
    raise AssertionError


# ------------------------------------------------------------------ readable documents
def _md_list(items) -> str:
    return ", ".join(str(x) for x in items) if isinstance(items, list) else str(items or "")


def doc_script(c: dict) -> str:
    sc = c.get("script") or {}
    return f"# {c.get('title') or '劇本'}\n\n> 原始需求：{c.get('intent') or '—'}\n\n格式：{sc.get('format') or '—'}\n\n" \
           f"{sc.get('body') or '（無劇本內文）'}\n"


def doc_characters(c: dict) -> str:
    out = ["# 人物設定", ""]
    names = {"appearance": "外貌", "wardrobe": "服裝與道具", "capability_ceiling": "能力上限",
             "negative_boundary": "絕不會做的事", "linguistic_blueprint": "說話方式", "kinematic_signature": "動作特徵",
             "voice": "配音聲線"}
    for s in c.get("subjects") or []:
        out += [f"## {s.get('id')} · {s.get('name') or ''}" + (f"（{s['alias']}）" if s.get("alias") else ""), ""]
        out += [f"- **{label}**：{s.get(k)}" for k, label in names.items() if s.get(k)]
        if s.get("sheet"):
            out.append(f"- **定裝照**：{_md_list(s['sheet'])}")
        out.append("")
    return "\n".join(out)


def doc_locations(c: dict) -> str:
    out = ["# 場景設定", ""]
    names = {"description": "描述", "light": "光線", "palette": "色彩", "materials": "材質"}
    for e in c.get("environments") or []:
        out += [f"## {e.get('id')} · {e.get('name') or ''}", ""]
        out += [f"- **{label}**：{_md_list(e.get(k))}" for k, label in names.items() if e.get(k)]
        if e.get("master"):
            out.append(f"- **場景定調圖**：{e['master']}")
        out.append("")
    return "\n".join(out)


def doc_style(c: dict) -> str:
    st = c.get("style") or {}
    names = {"look": "畫面風格", "palette": "主色", "camera": "運鏡習慣", "motion_strength": "動態強度",
             "ambience": "環境音", "music": "配樂方向", "bgm_file": "配樂檔", "voice": "旁白聲線", "lut": "調色 LUT",
             "negative": "禁止出現"}
    out = ["# 視覺風格、音樂與語音", "", f"- **類型**：{c.get('genre') or '—'}　**比例**：{c.get('aspect') or '—'}　"
           f"**語言**：{c.get('language') or '—'}　**影格率**：{c.get('fps') or '—'}　**輸出**：{c.get('delivery') or '—'}"]
    out += [f"- **{label}**：{_md_list(st.get(k))}" for k, label in names.items() if st.get(k) not in (None, "", [])]
    return "\n".join(out) + "\n"


def doc_shotlist(c: dict) -> str:
    cols = [("id", "鏡頭"), ("group", "段"), ("env", "場景"), ("duration", "秒"), ("shot_size", "景別"),
            ("angle", "角度"), ("lens", "鏡頭"), ("focus", "焦點"), ("composition", "構圖"), ("camera_move", "運鏡"),
            ("action", "動作"), ("performance", "表演"), ("narration", "旁白"), ("sfx", "音效"), ("music", "音樂")]
    rows = ["# 鏡頭語言（分鏡表）", "", "| " + " | ".join(n for _, n in cols) + " | 對白 |",
            "|" + "---|" * (len(cols) + 1)]
    for s in c.get("shots") or []:
        cells = [str(s.get(k) if not isinstance(s.get(k), list) else _md_list(s.get(k)) or "").replace("|", "／")
                 .replace("\n", " ") for k, _ in cols]
        dia = "；".join(f"{d.get('subject')}：{d.get('line')}" for d in s.get("dialogue") or [] if isinstance(d, dict))
        rows.append("| " + " | ".join(cells) + f" | {dia.replace('|', '／')} |")
    return "\n".join(rows) + "\n"


README = """# {title}・製作素材包

由 SinoBee「AI 影片製作」技能匯出（{created}）。用它可以拍出角色、場景、畫面風格、配樂與聲線一致的下一支影片。

## 內容
| 檔案 | 內容 |
|---|---|
| script.md | 劇本 |
| characters.md | 人物設定（外貌、服裝、四項側寫、聲線）{n_subjects} 位 |
| locations.md | 場景設定 {n_envs} 處 |
| style.md | 視覺風格、配樂、環境音、聲線、禁止事項 |
| shotlist.md | 鏡頭語言：{n_shots} 個鏡頭的景別、角度、鏡頭、構圖、運鏡、動作與表演 |
| refs/ | 角色定裝照與場景定調圖（{n_refs} 張），是維持一致性的關鍵 |
| audio/ | 旁白與對白語音 {n_voice} 段、配樂 {n_music} 首 |
| keyframes/ | 起始／結束畫格 {n_keys} 張 |
| bible.json | 機器可讀的完整企劃（給 SinoBee 再次使用） |
| kit.json | 清單與檔案校驗碼 |

## 再次使用
在 SinoBee 對話中附上這個 zip，說「用這個素材包拍續集：……」；或在「工作」分頁啟動「AI 影片製作」流程，
在「素材包」欄填入 zip 路徑。

- **續集（series）**：沿用角色、場景、風格、配樂與聲線，故事全新。
- **重拍（remake）**：全部沿用，包含劇本與分鏡，再修改想改的地方。

角色與場景的描述、定裝照、風格會被鎖定，新影片因此保持一致；需要改動時在匯入時解除鎖定。
"""


# ------------------------------------------------------------------ export
def cmd_export(a) -> None:
    root = Path(a.folder).expanduser()
    proj = _load(root)
    content = {k: json.loads(json.dumps(proj[k])) for k in CONTENT_KEYS if k in proj}
    files: list[tuple[Path, str, str]] = []          # (source, path in zip, role)
    missing = []

    def take(rel, dst_dir: str, role: str, name: str | None = None) -> str | None:
        src = resolve(root, rel) if rel else None
        if not src:
            return None
        if not src.exists():
            missing.append(str(rel))
            return None
        dst = f"{dst_dir}/{name or src.name}"
        if not any(d == dst for _, d, _ in files):
            files.append((src, dst, role))
        return dst

    for s in content.get("subjects") or []:
        s["sheet"] = [x for x in (take(f, f"refs/subjects/{s.get('id')}", "subject_sheet") for f in s.get("sheet") or [])
                      if x]
    for e in content.get("environments") or []:
        if e.get("master"):
            e["master"] = take(e["master"], f"refs/environments/{e.get('id')}", "environment_master")
    for sh in content.get("shots") or []:
        for k in ("first_frame", "last_frame"):
            if sh.get(k):
                src = resolve(root, sh[k])
                sh[k] = take(sh[k], "keyframes", "keyframe", f"{sh.get('id')}_{k}{src.suffix if src else '.png'}")
        if sh.get("audio_file"):
            src = resolve(root, sh["audio_file"])
            sh["audio_file"] = take(sh["audio_file"], "audio/voice", "voice",
                                    f"{sh.get('id')}{src.suffix if src else '.wav'}")
    st = content.get("style") or {}
    if st.get("bgm_file"):
        st["bgm_file"] = take(st["bgm_file"], "audio/music", "music")
    if st.get("lut"):
        st["lut"] = take(st["lut"], "style", "lut")
    content["seeds"] = {u: r.get("seed") for u, r in (proj.get("renders") or {}).items() if r.get("seed")}
    if a.with_film:
        for name, role in (("out/final.mp4", "film"), ("out/final.srt", "subtitles")):
            take(name, "preview", role)
    created = time.strftime("%Y-%m-%d %H:%M")
    counts = {"subjects": len(content.get("subjects") or []), "environments": len(content.get("environments") or []),
              "shots": len(content.get("shots") or []),
              "refs": sum(r in ("subject_sheet", "environment_master") for _, _, r in files),
              "voice": sum(r == "voice" for _, _, r in files), "music": sum(r == "music" for _, _, r in files),
              "keyframes": sum(r == "keyframe" for _, _, r in files)}
    manifest = {"format": FORMAT, "version": VERSION, "title": content.get("title"), "created": created,
                "source": {"folder": str(root), "intent": content.get("intent")}, "counts": counts,
                "locked": {"top": list(LOCK_TOP), "subjects": list(LOCK_SUBJECT), "environments": list(LOCK_ENV),
                           "style": True},
                "files": [{"path": d, "role": r, "bytes": s.stat().st_size, "sha256": _sha(s)} for s, d, r in files]}
    docs = {"README.md": README.format(title=content.get("title") or "影片", created=created,
                                       n_subjects=counts["subjects"], n_envs=counts["environments"],
                                       n_shots=counts["shots"], n_refs=counts["refs"], n_voice=counts["voice"],
                                       n_music=counts["music"], n_keys=counts["keyframes"]),
            "script.md": doc_script(content), "characters.md": doc_characters(content),
            "locations.md": doc_locations(content), "style.md": doc_style(content), "shotlist.md": doc_shotlist(content)}
    out = Path(a.out) if a.out else root / "out" / f"{_slug(content.get('title'))}-kit.zip"
    if not out.is_absolute() and a.out:
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("kit.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("bible.json", json.dumps(content, ensure_ascii=False, indent=2))
        for name, text in docs.items():
            z.writestr(name, text)
        for src, dst, _ in files:
            z.write(src, dst, compress_type=zipfile.ZIP_STORED if src.suffix.lower() in (".mp4", ".png", ".jpg", ".mp3")
                    else zipfile.ZIP_DEFLATED)
    tmp.replace(out)
    emit({"ok": True, "kit": str(out), "bytes": out.stat().st_size, "counts": counts,
          **({"missing_files": missing} if missing else {}),
          "contents": ["README.md", *docs.keys() - {"README.md"}, "bible.json", "kit.json"] + [d for _, d, _ in files]})


# ------------------------------------------------------------------ import
def _safe_members(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        die(f"kit has {len(infos)} files (limit {MAX_FILES})")
    if sum(i.file_size for i in infos) > MAX_BYTES:
        die("kit is larger than 4 GB unpacked")
    for i in infos:
        p = Path(i.filename)
        if p.is_absolute() or ".." in p.parts or i.filename.startswith(("/", "\\")):
            die(f"unsafe path in kit: {i.filename}")
        if p.suffix.lower() not in ALLOWED_EXT:
            die(f"unexpected file type in kit: {i.filename}")
    return infos


def _open_kit(src: Path, dest: Path) -> tuple[dict, dict]:
    """Copy the kit's files into dest (a folder inside the new project); return (manifest, bible)."""
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for f in src.rglob("*"):
            if f.is_file() and f.suffix.lower() in ALLOWED_EXT:
                t = dest / f.relative_to(src)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
    else:
        try:
            with zipfile.ZipFile(src) as z:
                for i in _safe_members(z):
                    t = dest / i.filename
                    t.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(i) as r, t.open("wb") as w:
                        shutil.copyfileobj(r, w)
        except zipfile.BadZipFile:
            die(f"{src} is not a zip file")
    try:
        manifest = json.loads((dest / "kit.json").read_text(encoding="utf-8"))
        bible = json.loads((dest / "bible.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"not a SinoBee video kit (kit.json / bible.json unreadable: {e})")
    if manifest.get("format") != FORMAT:
        die("not a SinoBee video kit (kit.json format)")
    if int(manifest.get("version") or 0) > VERSION:
        die(f"kit version {manifest.get('version')} is newer than this skill supports ({VERSION})")
    bad = []
    for f in manifest.get("files") or []:
        p = dest / f["path"]
        if not p.exists() or (f.get("sha256") and _sha(p) != f["sha256"]):
            bad.append(f["path"])
    if bad:
        die(f"kit files missing or damaged: {', '.join(bad[:10])}")
    return manifest, bible


def _rebase(bible: dict, prefix: str) -> dict:
    """Paths inside the kit -> paths relative to the new project folder."""
    b = json.loads(json.dumps(bible))

    def rp(v):
        return f"{prefix}/{v}" if isinstance(v, str) and v and not Path(v).is_absolute() else v
    for s in b.get("subjects") or []:
        s["sheet"] = [rp(x) for x in s.get("sheet") or []]
    for e in b.get("environments") or []:
        e["master"] = rp(e.get("master"))
    for sh in b.get("shots") or []:
        for k in ("first_frame", "last_frame", "audio_file"):
            if sh.get(k):
                sh[k] = rp(sh[k])
    st = b.get("style") or {}
    for k in ("bgm_file", "lut"):
        if st.get(k):
            st[k] = rp(st[k])
    return b


def cmd_import(a) -> None:
    src = Path(a.kit).expanduser()
    if not src.exists():
        die(f"kit not found: {src}")
    root = Path(a.folder).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "project.json").exists() and not a.force:
        die(f"{root} already has a project; import into a new folder (or pass --force)")
    manifest, bible = _open_kit(src, root / "kit")
    b = _rebase(bible, "kit")
    unlock = {x.strip() for x in (a.unlock or "").split(",") if x.strip()}
    if a.mode == "series":
        plan = {k: b.get(k) for k in ("genre", "language", "language_name", "aspect", "fps", "delivery", "style",
                                      "subjects", "environments", "models") if b.get(k) is not None}
        plan.update(title="", intent="", script={"format": (b.get("script") or {}).get("format") or "screenplay",
                                                 "body": ""}, shots=[])
    else:
        plan = {k: v for k, v in b.items() if k != "seeds"}
    plan["kit"] = {"title": manifest.get("title"), "mode": a.mode, "source": str(src)}
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    lock = {"title": manifest.get("title"), "mode": a.mode, "unlock": sorted(unlock),
            "top": {k: b.get(k) for k in LOCK_TOP if b.get(k) is not None},
            "style": b.get("style") or {},
            "subjects": [{k: s.get(k) for k in ("id", *LOCK_SUBJECT) if s.get(k) not in (None, "")}
                         for s in b.get("subjects") or []],
            "environments": [{k: e.get(k) for k in ("id", *LOCK_ENV) if e.get(k) not in (None, "")}
                             for e in b.get("environments") or []],
            "seeds": b.get("seeds") if a.mode == "remake" else {}}
    (root / "kit.lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    subj = ", ".join(f"{s.get('id')} {s.get('name')}" for s in b.get("subjects") or [])
    envs = ", ".join(f"{e.get('id')} {e.get('name')}" for e in b.get("environments") or [])
    emit({"ok": True, "folder": str(root), "mode": a.mode, "kit": manifest.get("title"),
          "subjects": subj, "environments": envs, "locked": [x for x in ("subjects", "environments", "style")
                                                             if x not in unlock],
          "next": ("Write the NEW story into plan.json: title, intent, script.body and shots. Reuse the kit's "
                   f"subjects ({subj}) and environments ({envs}) by id; you may add new ones with new ids. Do not "
                   "rewrite their descriptions or the style - they are locked to keep the look. Then run the "
                   "autopilot on this folder.") if a.mode == "series" else
                  ("plan.json is a full copy of the kit's film. Change what the user asked for (the story, a shot, "
                   "a line), then run the autopilot on this folder.")})


# ------------------------------------------------------------------ lock (after every plan merge)
def cmd_lock(a) -> None:
    root = Path(a.folder).expanduser()
    lp, pp = root / "kit.lock.json", root / "project.json"
    if not lp.exists() or not pp.exists():
        emit({"ok": True, "locked": False})
        return
    lock = json.loads(lp.read_text(encoding="utf-8"))
    proj = json.loads(pp.read_text(encoding="utf-8"))
    unlock = set(lock.get("unlock") or [])
    notes = []
    if "style" not in unlock:
        for k, v in (lock.get("top") or {}).items():
            if proj.get(k) != v:
                notes.append(f"{k} kept as {v}")
                proj[k] = v
        st = proj.setdefault("style", {})
        for k, v in (lock.get("style") or {}).items():
            if v not in (None, "", []) and st.get(k) != v:
                notes.append(f"style.{k} kept from the kit")
                st[k] = v
    for kind, fields, key in (("subjects", LOCK_SUBJECT, "subject"), ("environments", LOCK_ENV, "env")):
        if kind in unlock:
            continue
        items = proj.setdefault(kind, [])
        for want in lock.get(kind) or []:
            cur = next((x for x in items if x.get("id") == want["id"]), None)
            if cur is None:        # same name under another id: give it back the kit's id everywhere
                cur = next((x for x in items if want.get("name") and x.get("name") == want.get("name")), None)
                if cur is not None:
                    old = cur["id"]
                    if not any(x.get("id") == want["id"] for x in items):
                        _rename(proj, kind, old, want["id"])
                        notes.append(f"{kind} {old} is the kit's {want['id']} ({want.get('name')})")
                    else:
                        cur = None
            if cur is None:
                cur = {"id": want["id"]}
                items.append(cur)
            for f in fields:
                if f in want and cur.get(f) != want[f]:
                    if cur.get(f) not in (None, "", []):
                        notes.append(f"{key} {want['id']}.{f} kept from the kit")
                    cur[f] = want[f]
    renders = proj.setdefault("renders", {})
    for unit, seed in (lock.get("seeds") or {}).items():
        r = renders.setdefault(unit, {})
        if not r.get("seed"):
            r["seed"] = seed
    pp.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
    emit({"ok": True, "locked": True, "notes": notes})


def _rename(proj: dict, kind: str, old: str, new: str) -> None:
    for x in proj.get(kind) or []:
        if x.get("id") == old:
            x["id"] = new
    for sh in proj.get("shots") or []:
        if kind == "subjects":
            sh["subjects"] = [new if s == old else s for s in sh.get("subjects") or []]
            for d in sh.get("dialogue") or []:
                if isinstance(d, dict) and d.get("subject") == old:
                    d["subject"] = new
        elif sh.get("env") == old:
            sh["env"] = new


def cmd_show(a) -> None:
    src = Path(a.kit).expanduser()
    if src.is_dir() and (src / "kit.json").exists():
        manifest = json.loads((src / "kit.json").read_text(encoding="utf-8"))
        bible = json.loads((src / "bible.json").read_text(encoding="utf-8"))
    else:
        try:
            with zipfile.ZipFile(src) as z:
                _safe_members(z)
                manifest = json.loads(z.read("kit.json"))
                bible = json.loads(z.read("bible.json"))
        except (zipfile.BadZipFile, KeyError, OSError) as e:
            die(f"not a SinoBee video kit: {e}")
    emit({"ok": True, "title": manifest.get("title"), "created": manifest.get("created"), "counts": manifest.get("counts"),
          "genre": bible.get("genre"), "aspect": bible.get("aspect"),
          "subjects": [f"{s.get('id')} {s.get('name')}: {s.get('appearance', '')[:120]}" for s in bible.get("subjects") or []],
          "environments": [f"{e.get('id')} {e.get('name')}" for e in bible.get("environments") or []],
          "style": (bible.get("style") or {}).get("look")})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("folder")
    e.add_argument("--out")
    e.add_argument("--with-film", action="store_true")
    i = sub.add_parser("import")
    i.add_argument("kit")
    i.add_argument("folder")
    i.add_argument("--mode", choices=["series", "remake"], default="series")
    i.add_argument("--unlock", default="", help="comma list: subjects, environments, style")
    i.add_argument("--force", action="store_true")
    lk = sub.add_parser("lock")
    lk.add_argument("folder")
    sh = sub.add_parser("show")
    sh.add_argument("kit")
    normalize_argv(["export", "import", "lock", "show"], "kit.py")
    a = p.parse_args()
    {"export": cmd_export, "import": cmd_import, "lock": cmd_lock, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
