"""Matplotlib defaults for SinoBee charts: a CJK-capable font, the validated palette, quiet axes.

    import sys; sys.path.insert(0, "<skill folder>/scripts")
    import sbplot
    fig, ax = sbplot.figure()               # sets everything up; 8x4.5 in at 200 dpi when saved
    ax.plot(x, y, color=sbplot.C[0])
    sbplot.finish(ax, title="熱功率逐年上升", x="年", y="熱功率 (MWt)", source="來源：SINAP 2025")
    sbplot.save(fig, "reports/x/power.png")   # refuses to save when glyphs fell back to boxes

No dependency other than matplotlib.
"""
from __future__ import annotations

import warnings
from pathlib import Path

# categorical: every pair stays apart for protan / deutan / tritan vision and >= 3:1 against white
# (run.py validate proves it). More than 6 series: label lines directly or split into small multiples.
C = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#222222", "#666666"]
HIGHLIGHT, CONTEXT = "#D55E00", "#BFBFBF"           # one thing to look at, the rest in grey
SEQUENTIAL = "Blues"                                 # perceptually ordered, prints in greyscale
DIVERGING = "RdBu_r"                                 # centre the norm at the meaningful midpoint (0, target)
CJK_FONTS = ["Noto Sans CJK TC", "Noto Sans TC", "PingFang TC", "Heiti TC", "Microsoft JhengHei",
             "Noto Sans CJK HK", "Noto Sans CJK JP", "Noto Sans CJK SC", "Source Han Sans TC", "PingFang SC",
             "Microsoft YaHei", "WenQuanYi Zen Hei", "Arial Unicode MS", "IPAGothic"]


def cjk_font() -> str | None:
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    return next((f for f in CJK_FONTS if f in have), None)


def setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from cycler import cycler
    font = cjk_font()
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ([font] if font else []) + ["DejaVu Sans"],
        "axes.unicode_minus": False,               # CJK fonts often lack U+2212
        "axes.prop_cycle": cycler(color=C),
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": "#E5E5E5", "grid.linewidth": 0.8,
        "axes.titlesize": 14, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 11, "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.frameon": False,
        "figure.dpi": 100, "savefig.dpi": 200, "savefig.bbox": "tight",
    })
    return font


def figure(w: float = 8, h: float = 4.5):
    setup()
    import matplotlib.pyplot as plt
    return plt.subplots(figsize=(w, h), layout="constrained")


def finish(ax, title: str = "", x: str = "", y: str = "", source: str = "", legend: bool | None = None):
    if title:
        ax.set_title(title)
    if x:
        ax.set_xlabel(x)
    if y:
        ax.set_ylabel(y)
    handles, labels = ax.get_legend_handles_labels()
    if legend or (legend is None and len(labels) > 1):
        ax.legend()
    if source:
        ax.figure.text(0.01, -0.02, source, fontsize=8, color="#6B6B6B", ha="left", va="top")


def save(fig, path: str):
    """Save, but fail loudly if a glyph was missing (Chinese shown as empty boxes)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.canvas.draw()
        fig.savefig(path)
    missing = [str(w.message) for w in caught if "missing from" in str(w.message) or "Glyph" in str(w.message)]
    if missing:
        raise RuntimeError("characters missing from the font (they render as boxes): " + missing[0]
                           + " - install a CJK font (e.g. Noto Sans CJK TC) or pass text the font has")
    return path
