#!/usr/bin/env python3
"""Engineering feasibility - entry script.

  run.py scaffold <folder> [--anatomy <dir>] [--example roadster]   make a work folder wired to the anatomy kit
  run.py calc <name> key=value ...                                   physics calculators (tank, nozzle, rocket,
                                                                     ballistic, landing, suction, vessel, energy)
  run.py calc list                                                   list calculators and their parameters
  run.py check <folder>                                              check feasibility.json, report.md and the page

Every call prints one JSON object. `status`: ok | fix (problems listed) | error (read `error`).
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))

KIT_FILES = ["iso-kit", "lathe", "tube", "audit", "gl", "turn", "turn-build", "turn-audit"]


def emit(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    sys.exit(code)


def find_anatomy(hint: str | None) -> Path | None:
    home = Path.home()
    candidates = [hint, os.environ.get("ANATOMY_DIR"), SKILL.parent / "anatomy",
                  home / ".sinobee/skills/anatomy", home / ".agents/skills/anatomy", home / ".claude/skills/anatomy"]
    for c in candidates:
        if c and (Path(c) / "kit" / "iso-kit.mjs").is_file() and (Path(c) / "scripts" / "inline-kit.mjs").is_file():
            return Path(c).resolve()
    return None


def scaffold(folder: Path, anatomy: Path, example: str | None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    kit = folder / "kit"
    kit.mkdir(exist_ok=True)
    # Shims: the work folder imports ./kit/<file>.mjs; each re-exports the installed anatomy kit (never edit the skill)
    for name in KIT_FILES:
        (kit / f"{name}.mjs").write_text(f'export * from "{(anatomy / "kit" / f"{name}.mjs").as_uri()}";\n', encoding="utf-8")
    (kit / "inline-kit.mjs").write_text(f'export * from "{(anatomy / "scripts" / "inline-kit.mjs").as_uri()}";\n', encoding="utf-8")
    src = SKILL / "assets" / ("example-" + example if example else "template")
    if not src.is_dir():
        raise FileNotFoundError(f"no {src.name} in assets/")
    copied = []
    for f in sorted(src.iterdir()):
        dst = folder / f.name
        if f.is_file() and not dst.exists():
            shutil.copy2(f, dst)
            copied.append(f.name)
    fj = folder / "feasibility.json"
    if not fj.exists():
        shutil.copy2(SKILL / "assets" / "feasibility.example.json", fj)
        copied.append("feasibility.json")
    return {"folder": str(folder), "anatomy": str(anatomy), "copied": copied,
            "scripts": {"capture": str(anatomy / "scripts" / "capture.mjs"), "drive": str(anatomy / "scripts" / "drive.mjs"),
                        "lines": str(anatomy / "scripts" / "lines.mjs")}}


def calc(argv: list[str]) -> dict:
    import physics
    if not argv or argv[0] == "list":
        return {"calculators": {k: {"params": str(inspect.signature(f)), "doc": (f.__doc__ or "").strip()} for k, f in physics.CALCS.items()}}
    name = argv[0]
    if name not in physics.CALCS:
        raise KeyError(f"unknown calculator {name}; one of {', '.join(physics.CALCS)}")
    kw = {}
    for a in argv[1:]:
        k, _, v = a.partition("=")
        try:
            kw[k] = float(v)
        except ValueError:
            kw[k] = v
    return {"calc": name, "input": kw, "result": physics.CALCS[name](**kw)}


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        emit({"status": "ok", "usage": __doc__})
    cmd = argv[0]
    try:
        if cmd == "scaffold":
            folder = Path(argv[1]).expanduser().resolve()
            hint = argv[argv.index("--anatomy") + 1] if "--anatomy" in argv else None
            example = argv[argv.index("--example") + 1] if "--example" in argv else None
            anatomy = find_anatomy(hint)
            if not anatomy:
                emit({"status": "error", "error": "anatomy skill not found: install it (sinobee skills install hub:cafeca/anatomy) or pass --anatomy <dir>"}, 1)
            emit({"status": "ok", **scaffold(folder, anatomy, example),
                  "next": "fill feasibility.json and model.mjs first; build figures only after the verdict"})
        if cmd == "calc":
            emit({"status": "ok", **calc(argv[1:])})
        if cmd == "check":
            import check
            folder = Path(argv[1]).expanduser().resolve()
            res = check.check(folder)
            emit({**res, "folder": str(folder),
                  "next": "fix every problem, then run check again" if res["problems"] else "publish the page and deliver report.md"})
        emit({"status": "error", "error": f"unknown command {cmd}", "usage": __doc__}, 1)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        emit({"status": "error", "error": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    main(sys.argv[1:])
