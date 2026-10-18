#!/usr/bin/env python3
"""Chart tools.

    run.py palette [categorical|sequential|diverging] [N]   the validated colours as hex
    run.py validate <#hex,#hex,…> [--bg #FFFFFF]             contrast against the background and whether
                                                            every pair stays apart for protan / deutan / tritan
                                                            vision (simulated)
    run.py fonts                                             a CJK-capable font matplotlib can use here
    run.py check <chart.png|.svg|.html|.py>                  quick lint: picture size, missing CJK font in a
                                                            script, axis labels and titles in a script / SVG

JSON out; exit 0 ok, 1 problems, 2 usage error.
"""
from __future__ import annotations

import json
import re
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SEQ = ["#DEEBF7", "#C6DBEF", "#9ECAE1", "#6BAED6", "#4292C6", "#2171B5", "#08519C", "#08306B"]
DIV = ["#B2182B", "#D6604D", "#F4A582", "#FDDBC7", "#F7F7F7", "#D1E5F0", "#92C5DE", "#4393C3", "#2166AC"]

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB
CVD = {
    "protan": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deutan": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    "tritan": [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
}


def out(d, code=0):
    print(json.dumps(d, ensure_ascii=False, indent=1))
    sys.exit(code)


def rgb(h):
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def lin(c):
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]


def lum(c):
    r, g, b = lin(c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def lab(c_lin):
    r, g, b = [min(max(x, 0), 1) for x in c_lin]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])]


def sim(c, kind):
    m = CVD[kind]
    v = lin(c)
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


def de(a, b):
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5


def validate(colors, bg="#FFFFFF"):
    problems, report = [], []
    B = rgb(bg)
    for h in colors:
        cr = contrast(rgb(h), B)
        report.append({"color": h, "contrast": round(cr, 2)})
        if cr < 3:
            problems.append(f"{h} has contrast {cr:.2f}:1 against {bg} (marks need 3:1; use it only for fills "
                            "with an outline, or darken it)")
    worst = {}
    for kind in ("normal", "protan", "deutan", "tritan"):
        labs = {h: lab(lin(rgb(h)) if kind == "normal" else sim(rgb(h), kind)) for h in colors}
        for a, b in combinations(colors, 2):
            d = de(labs[a], labs[b])
            if kind not in worst or d < worst[kind][0]:
                worst[kind] = (d, a, b)
            if d < 12:
                problems.append(f"{a} and {b} look alike for {kind} vision (ΔE {d:.0f} < 12): "
                                "do not put them next to each other, or add labels / patterns")
    return problems, report, {k: {"min_dE": round(v[0], 1), "pair": [v[1], v[2]]} for k, v in worst.items()}


def check(path: Path):
    problems, notes = [], []
    ext = path.suffix.lower()
    if not path.is_file():
        out({"status": "fix", "problems": [f"{path} does not exist"]}, 1)
    if ext == ".png":
        try:
            from PIL import Image
            w, h = Image.open(path).size
            if w < 800:
                problems.append(f"{path.name} is {w}px wide: too small to read in a report (save at ≥ 1200 px, "
                                "e.g. 8 in × 200 dpi)")
        except ImportError:
            pass
    text = path.read_text(encoding="utf-8", errors="replace") if ext in (".py", ".svg", ".html", ".htm") else ""
    if ext == ".py":
        cjk = re.search(r"[㐀-鿿]", text)
        if cjk and not re.search(r"sbplot|font\.(?:sans-serif|family)|FontProperties|rcParams\[.font", text):
            problems.append("the script draws Chinese text without choosing a CJK font: it will show boxes - "
                            "use sbplot.figure() or set rcParams['font.sans-serif']")
        if re.search(r"\.(plot|bar|barh|scatter|hist)\(", text) and not re.search(r"set_ylabel|ylabel\(|finish\(", text):
            notes.append("no y-axis label: say what is measured and its unit")
        if re.search(r"plt\.show\(\)", text) and not re.search(r"savefig|sbplot\.save", text):
            problems.append("plt.show() without savefig: nothing is written in a headless run")
        if "jet" in text or "rainbow" in text:
            problems.append("rainbow colour map (jet/rainbow) misorders values: use a sequential map such as viridis "
                            "or Blues")
    if ext in (".svg", ".html", ".htm") and "<svg" in text:
        if not re.search(r"<title|aria-label|<desc", text):
            notes.append("SVG chart without <title>/aria-label: screen readers get nothing")
    out({"status": "fix" if problems else "ok", "problems": problems, "notes": notes}, 1 if problems else 0)


def main(a):
    if not a:
        out({"status": "error", "error": __doc__}, 2)
    cmd = a[0]
    if cmd == "palette":
        kind = a[1] if len(a) > 1 else "categorical"
        n = int(a[2]) if len(a) > 2 else None
        from sbplot import C
        pal = {"categorical": C, "sequential": SEQ, "diverging": DIV}.get(kind)
        if pal is None:
            out({"status": "error", "error": "kind: categorical | sequential | diverging"}, 2)
        if n and kind != "categorical":
            step = (len(pal) - 1) / max(n - 1, 1)
            pal = [pal[round(i * step)] for i in range(n)]
        out({"status": "ok", "kind": kind, "colors": pal[:n] if kind == "categorical" and n else pal})
    if cmd == "validate" and len(a) >= 2:
        bg = a[a.index("--bg") + 1] if "--bg" in a else "#FFFFFF"
        cols = [c for c in re.split(r"[,\s]+", a[1]) if c]
        problems, report, worst = validate(cols, bg)
        out({"status": "fix" if problems else "ok", "problems": problems, "colors": report, "closest": worst},
            1 if problems else 0)
    if cmd == "fonts":
        try:
            from sbplot import cjk_font
            f = cjk_font()
        except ImportError:
            out({"status": "error", "error": "matplotlib is not installed",
                 "fix": f"{sys.executable} -m pip install matplotlib"}, 2)
        out({"status": "ok" if f else "fix", "cjk_font": f,
             "problems": [] if f else ["no CJK font found for matplotlib: install Noto Sans CJK TC "
                                       "(macOS has PingFang TC; Linux: apt install fonts-noto-cjk) and delete "
                                       "matplotlib's font cache"]}, 0 if f else 1)
    if cmd == "check" and len(a) == 2:
        check(Path(a[1]))
    out({"status": "error", "error": "usage:\n" + __doc__}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
