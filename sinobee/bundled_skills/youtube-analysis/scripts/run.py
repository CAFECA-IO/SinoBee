#!/usr/bin/env python3
"""YouTube analysis - entry script.

  run.py <youtube url> [--lang zh-TW] [--dir reports/youtube]   fetch metadata, transcript, thumbnail and stills
  run.py cover <folder>                                          render cover.png from <folder>/cover.json
  run.py frame <folder> <mm:ss>                                  save the still nearest to a moment for the report
  run.py check <folder>                                          check report.md before delivering it

Every call prints one JSON object. `status`: ok | fix (report problems listed) | blocked (tell the user `what`) |
error (read `error`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def emit(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def main(argv: list[str]) -> None:
    import fetch
    if not argv or argv[0] in ("-h", "--help"):
        emit({"status": "ok", "usage": __doc__})
    cmd = argv[0]
    try:
        if cmd == "cover":
            import cover
            folder = Path(argv[1]).expanduser().resolve()
            out = cover.render(folder)
            emit({"status": "ok", **out, "folder": str(folder),
                  "next": "look at the cover with read_file; then write report.md and run `check`"})
        if cmd == "frame":
            folder = Path(argv[1]).expanduser().resolve()
            emit({**fetch.frame_at(folder, argv[2]), "folder": str(folder),
                  "use": "embed it in report.md as ![<what it shows>](<file>)"})
        if cmd == "check":
            import check
            folder = Path(argv[1]).expanduser().resolve()
            res = check.check(folder)
            emit({**res, "folder": str(folder),
                  "next": "fix every problem in report.md, then run check again" if res["problems"] else
                  "deliver report.md (with cover.png and frames/) to the user"})
        p = argparse.ArgumentParser()
        p.add_argument("url")
        p.add_argument("--lang", default="zh-TW")
        p.add_argument("--dir", default="reports/youtube")
        a = p.parse_args(argv)
        res = fetch.fetch(a.url, Path(a.dir).expanduser().resolve(), a.lang)
        meta = res["meta"]
        tr = meta["transcript"]
        fr = meta["frames"]
        emit({
            "status": "ok",
            "folder": res["folder"],
            "video": {k: meta.get(k) for k in ("id", "title", "channel", "date", "duration", "view_count", "language", "webpage_url")},
            "chapters": [f"{fetch.clock(c['start'])} {c['title']}" for c in meta["chapters"]],
            "transcript": tr,
            "frames": {k: fr.get(k) for k in ("count", "every_s", "sheets", "note") if fr.get(k) is not None},
            "files": ["source.json", "transcript.md", "transcript.json", "thumbnail.jpg", "frames/", "sheets/"],
            "next": [
                "read source.json (description, chapters, tags) and transcript.md (all of it; it is the soundtrack)",
                "look at every sheets/sheet-NN.jpg with read_file (the picture track, a still about every 10-30 s)",
                "pick the category from references/categories.md and follow its analysis questions",
                "fact-check with web_search/web_fetch as references/fact-check.md says (categories marked check)",
                "write cover.json, run `cover <folder>`, write report.md from references/report-template.md, run `check <folder>`",
            ],
        })
    except fetch.Blocked as e:
        emit({"status": "blocked", "what": str(e)}, 1)
    except (ValueError, FileNotFoundError) as e:
        emit({"status": "error", "error": str(e)}, 1)
    except Exception as e:  # noqa: BLE001
        emit({"status": "error", "error": f"{type(e).__name__}: {e}"}, 1)


if __name__ == "__main__":
    main(sys.argv[1:])
