"""Builds MiniMax H3 prompts from a project: profiling matrix + asset masters + shot specs -> one prompt per unit."""
from __future__ import annotations

from vpcommon import GENRE_DEFAULTS, shots_by_id

DEFAULT_NEGATIVE = ("No on-screen text, subtitles, logos or watermarks. No extra fingers or deformed hands, no "
                    "melting faces, no morphing objects, no cartoon or CGI look unless the style asks for it.")

ANGLES_SHEET = [
    ("front", "front view, facing the camera, neutral expression, even soft light"),
    ("three-quarter", "three-quarter view from the left, natural pose"),
    ("back", "back view, standing, showing hair and clothing from behind"),
    ("hard-light", "three-quarter view under hard side light with deep shadows, same identity"),
]


LANG_NAMES = {"zh-tw": "Mandarin Chinese (Taiwan accent)", "zh-cn": "Mandarin Chinese", "zh": "Mandarin Chinese",
              "nan": "Taiwanese Hokkien", "yue": "Cantonese", "en": "English", "ja": "Japanese", "ko": "Korean"}


def language_name(proj: dict, code: str | None = None) -> str:
    code = (code or proj.get("language") or "").lower()
    return proj.get("language_name") or LANG_NAMES.get(code) or LANG_NAMES.get(code.split("-")[0], code)


def motion_phrase(strength: float) -> str:
    if strength <= 0.3:
        return "subtle, natural, restrained motion; camera moves are slow and minimal"
    if strength <= 0.6:
        return "moderate, purposeful motion; smooth camera moves"
    return "dynamic, energetic motion; bold fast camera moves with motion blur"


def subject_block(s: dict, full: bool = True) -> str:
    name = s.get("name") or s["id"]
    parts = [f"{name}: {s.get('appearance', '').strip()}".rstrip(": ")]
    if s.get("wardrobe"):
        parts.append(f"wearing {s['wardrobe']}")
    if full:
        if s.get("kinematic_signature"):
            parts.append(f"body language: {s['kinematic_signature']}")
        if s.get("capability_ceiling"):
            parts.append(f"skill level: {s['capability_ceiling']}")
        if s.get("negative_boundary"):
            parts.append(f"boundaries: {s['negative_boundary']}")
        if s.get("linguistic_blueprint"):
            parts.append(f"speech style: {s['linguistic_blueprint']}")
    return "; ".join(p for p in parts if p)


def env_block(e: dict) -> str:
    parts = [e.get("description", "")]
    if e.get("light"):
        parts.append(f"lighting: {e['light']}")
    if e.get("materials"):
        parts.append(f"materials: {e['materials']}")
    if e.get("palette"):
        parts.append(f"color palette: {e['palette']}")
    return "; ".join(p for p in parts if p)


def style_line(proj: dict) -> str:
    st = proj.get("style", {})
    look = st.get("look") or GENRE_DEFAULTS.get(proj.get("genre", "film"), {}).get("look", "")
    pal = st.get("palette")
    if isinstance(pal, list):
        pal = ", ".join(pal)
    return look + (f"; unified color palette {pal}" if pal else "")


def shot_line(proj: dict, s: dict, start: float, end: float, n: int) -> str:
    subj = {x["id"]: x for x in proj.get("subjects", [])}
    bits = []
    spec = ", ".join(x for x in (s.get("shot_size"), s.get("angle"), s.get("lens") and f"{s['lens']} lens") if x)
    if spec:
        bits.append(spec)
    if s.get("focus"):
        bits.append(f"focus on {s['focus']}")
    if s.get("composition"):
        bits.append(s["composition"])
    head = f"[{start:.1f}s-{end:.1f}s] Shot {n}: " + (", ".join(bits) + ". " if bits else "")
    action = s.get("action", "").strip()
    perf = s.get("performance", "").strip()
    # profile-driven action: append each subject's kinematic signature so a "turn" becomes "a hunched turn"
    kin = [subj[i].get("kinematic_signature") for i in s.get("subjects", []) if i in subj and
           subj[i].get("kinematic_signature")]
    body = action
    if perf:
        body += f" ({perf})"
    if kin and action:
        body += f", consistent with their habitual body language ({'; '.join(kin)})"
    names = [subj[i].get("name") or i for i in s.get("subjects", []) if i in subj]
    if names:   # stops ref2va from duplicating a referenced person and stray extras from appearing
        body += f". On screen: only {' and '.join(names)} ({len(names)} {'person' if len(names) == 1 else 'people'})" \
                f", no other people, no duplicates"
    cam = s.get("camera_move")
    if cam:
        body += f". Camera: {cam}"
    for d in s.get("dialogue") or []:
        who = subj.get(d.get("subject"), {}).get("name") or d.get("subject", "")
        lang = language_name(proj, d.get("language"))
        body += f'. {who} says{" in " + lang if lang else ""}, lips in sync: "{d.get("line", "")}"'
    if s.get("narration") and not s.get("dialogue"):
        body += ". Nobody speaks on camera in this shot (a voice-over is added later)"
    return head + body.strip() + ("." if body and not body.endswith(".") else "")


def unit_prompt(proj: dict, unit: dict) -> str:
    sb = shots_by_id(proj)
    shots = [sb[i] for i in unit["shots"]]
    st = proj.get("style", {})
    subj = {x["id"]: x for x in proj.get("subjects", [])}
    envs = {x["id"]: x for x in proj.get("environments", [])}
    used_subj = [subj[i] for s in shots for i in s.get("subjects", []) if i in subj]
    used_subj = list({s["id"]: s for s in used_subj}.values())
    used_env = list({e["id"]: e for e in (envs.get(s.get("env")) for s in shots) if e}.values())
    strength = max(float(s.get("motion_strength") or st.get("motion_strength") or 0.4) for s in shots)

    lines = [style_line(proj) + "."]
    if used_subj:
        lines.append("\nRecurring subjects (keep identity, face, hair, body shape and wardrobe identical in every shot):")
        lines += [f"- {subject_block(s)}" for s in used_subj]
    if used_env:
        lines.append("\nLocation:" if len(used_env) == 1 else "\nLocations:")
        lines += [f"- {e.get('name') or e['id']}: {env_block(e)}" for e in used_env]
    summary = unit.get("summary") or shots[0].get("scene") or ""
    if summary:
        lines.append(f"\nScene overview: {summary}")
    if len(shots) > 1:
        lines.append("\nStoryboard (each shot a separate cut, landing exactly on these times):")
    else:
        lines.append("\nShot:")
    t = 0.0
    for n, s in enumerate(shots, 1):
        d = float(s.get("duration") or 0)
        lines.append(shot_line(proj, s, t, t + d, n))
        t += d
    lines.append(f"\nCamera: {st.get('camera') or GENRE_DEFAULTS.get(proj.get('genre', 'film'), {}).get('camera', '')}; "
                 f"{motion_phrase(strength)}.")
    audio = []
    amb = "; ".join(x for x in [st.get("ambience")] + [s.get("sfx") for s in shots] if x)
    if amb:
        audio.append(amb)
    music = "; ".join(x for x in [st.get("music")] + [s.get("music") for s in shots] if x)
    if music:
        audio.append(f"music: {music}")
    if any(s.get("narration") for s in shots):
        audio.append("keep ambience and music low under the voice-over parts, no singing")
    if audio:
        lines.append("Audio: " + "; ".join(audio) + ".")
    lines.append("\n" + (st.get("negative") or DEFAULT_NEGATIVE))
    return "\n".join(lines).strip()


def subject_sheet_prompt(proj: dict, s: dict, view: str) -> str:
    desc = dict(ANGLES_SHEET).get(view, view)
    return (f"{style_line(proj)}. Character reference still, plain neutral studio background, full body visible. "
            f"{subject_block(s, full=False)}. {desc}. The subject stands still, the camera does not move. "
            f"{DEFAULT_NEGATIVE}")


def environment_master_prompt(proj: dict, e: dict) -> str:
    return (f"{style_line(proj)}. Empty establishing shot of the location, no people. {env_block(e)}. "
            f"Static locked-off camera, deep focus showing the space, depth and materials. {DEFAULT_NEGATIVE}")


def keyframe_prompt(proj: dict, unit: dict) -> str:
    """First-frame still for a unit: the first shot frozen at its start, with all anchors."""
    sb = shots_by_id(proj)
    first = sb[unit["shots"][0]]
    one = {"id": unit["id"], "shots": [first["id"]], "summary": unit.get("summary")}
    return unit_prompt(proj, one).replace("\nShot:", "\nFirst frame of the shot (a single still moment):")
