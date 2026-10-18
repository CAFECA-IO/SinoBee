---
name: data-visualization
description: Makes charts that are correct and readable - picks the chart form from the question, uses a colour-vision-safe validated palette, labels axes with units, writes a title that states the finding, renders Chinese text with a real CJK font, and checks the result. Use before drawing any chart, graph, plot, KPI tile or dashboard, in matplotlib, an HTML/SVG page, Excel or PowerPoint (圖表、統計圖、趨勢圖、儀表板、視覺化).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: chart, plot, visualization, matplotlib, dashboard
  sinobee-triggers: 圖表, 統計圖, 長條圖, 折線圖, 圓餅圖, 散佈圖, 趨勢, 儀表板, 視覺化, chart, plot, graph, visualize, dashboard
  sinobee-requires-tools: skill_run, write_file
  sinobee-requires-python: matplotlib
  sinobee-entry: scripts/run.py
  sinobee-quickstart: "1) say the one finding the chart must show; 2) pick the form (table below); 3) python: sys.path.insert(0, '<this skill>/scripts'); import sbplot; fig, ax = sbplot.figure(); …; sbplot.finish(ax, title=finding, x=…, y='量 (單位)', source=…); sbplot.save(fig, path); 4) read_file the PNG and look at it."
  author: sinobee
---

# When to Use
Before drawing any chart in any medium. The skill is about what to draw and how to make it honest and
legible; the medium-specific skills (spreadsheets, presentations, word-documents, frontend-design) say how to
put it in their file.

# 1. Start from the finding
Write the sentence the chart must prove ("TMSR-LF1 的熱功率在 2025 年從 2 升到 10 MWt"). That sentence is the
chart title. If you cannot write it, you are not ready to chart - a table may be the better answer.

# 2. Pick the form
| Question | Form |
|---|---|
| Change over time | line (bars if few periods, columns for counts per period) |
| Compare categories | horizontal bars, sorted by value (labels read easily, long names fit) |
| Part of a whole | stacked bar or 100 % bar; pie only for 2-4 parts that sum to 100 % |
| Relationship of two measures | scatter (add a fitted line only with its equation / R²) |
| Distribution | histogram or box/strip plot |
| Many series over time | small multiples sharing the y-axis, or highlight one and grey the rest |
| A single number | a big-number tile with its comparison ("+12 % vs 去年") |
| Exact values matter | a table |
Never: 3D charts, dual y-axes for unrelated measures, pie with many slices, rainbow colour maps.

# 3. Honest scales
- Bars start at zero. Lines may zoom, but say so when the axis does not start at zero.
- Same units and scale across small multiples. Log scale only when the data spans orders of magnitude, and
  label it.
- Units in the axis title (`熱功率 (MWt)`), not only in the text. Thousands separators, sensible rounding.
- Show uncertainty when it exists (error bars, ranges) and the source and date under the chart.

# 4. Colour
- Categorical: `sbplot.C` - six colours that stay distinct for protan / deutan / tritan colour vision and
  reach 3:1 contrast on white (`run.py validate` proves it; rerun it for any custom palette, and with
  `--bg` for dark backgrounds). More than six series: label directly or use small multiples.
- One message: `sbplot.HIGHLIGHT` for the series you talk about, `sbplot.CONTEXT` grey for the rest.
- Ordered values: a sequential map (`Blues`, `viridis`); values around a midpoint: diverging (`RdBu_r`)
  centred on the midpoint.
- Never rely on colour alone: label lines at their ends, add markers or patterns when series are close.
- Keep the same colour for the same thing across every chart in a report.

# 5. Text
- Title = the finding; subtitle = what and where; axis titles with units; source line.
- Label lines directly instead of a legend when there are ≤ 4.
- Chinese: matplotlib's default font has no CJK glyphs and draws boxes. `sbplot.figure()` picks an installed
  CJK font (`run.py fonts` tells you which); `sbplot.save()` refuses to save when a glyph is missing.
  In HTML/SVG use `font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif`.

# 6. Output
- PNG for documents and slides: 8 × 4.5 in at 200 dpi (sbplot defaults). SVG for web pages.
- HTML/SVG charts: viewBox sized to the content, `<title>` / aria-label, readable at 390 px wide, colours as
  CSS variables with a dark-mode set.
- Native charts (Excel, PowerPoint) when the user should be able to edit the data: see spreadsheets /
  presentations.

# Verification
`run.py check <script.py | chart.png | page.html>` passes, and you **looked at the rendered chart** with
read_file: title states the finding, axes labelled with units, nothing overlaps, every label legible,
colours distinguishable, numbers match the data.
