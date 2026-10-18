# Deck JSON for `run.py build`

```json
{
  "title": "釷基熔鹽反應爐",
  "lang": "zh-Hant",
  "footer": "CAFECA · 2026",
  "theme": {"accent": "1F4E79", "accent2": "C55A11", "font": "Calibri", "east_asia": "Microsoft JhengHei",
            "background": "FFFFFF", "text": "222222", "muted": "6B6B6B"},
  "slides": [
    {"layout": "title", "title": "釷基熔鹽反應爐 SMR", "subtitle": "可行性與原理", "byline": "2026-10"},
    {"layout": "section", "number": 1, "title": "原理", "subtitle": "釷鈾燃料循環"},
    {"layout": "bullets", "title": "熔鹽爐在常壓下運轉", "subtitle": "與輕水式比較",
     "bullets": ["燃料溶在氟化物鹽中，**常壓**運轉", {"text": "沸點約 1400 °C", "level": 1}],
     "notes": "講者備註：這頁強調低壓帶來的安全差異。"},
    {"layout": "two-column", "title": "兩種爐型",
     "left": {"heading": "輕水式", "bullets": ["15 MPa", "固體燃料"]},
     "right": {"heading": "熔鹽式", "image": "figs/loop.png"}},
    {"layout": "image", "title": "一次迴路", "image": "figs/loop.png", "caption": "圖：一次迴路與排放槽"},
    {"layout": "chart", "title": "功率逐年提升",
     "chart": {"type": "column", "categories": ["2023", "2024", "2025"],
               "series": {"熱功率 (MWt)": [2, 2, 10]}, "y_title": "MWt", "number_format": "0",
               "labels": true, "source": "來源：SINAP"}},
    {"layout": "table", "title": "主要參數",
     "table": {"header": ["項目", "數值"], "rows": [["熱功率", "2 MWt"], ["出口溫度", "650 °C"]]}},
    {"layout": "big-number", "title": "關鍵數字",
     "numbers": [{"value": "2 MWt", "label": "熱功率"}, {"value": "0.1", "label": "轉換比"}]},
    {"layout": "quote", "quote": "引用的話", "by": "出處"},
    {"layout": "closing", "title": "謝謝", "subtitle": "問題與討論"}
  ]
}
```

- Chart `type`: `column`, `bar`, `line`, `pie`, `stacked`. Several entries in `series` make a grouped chart
  with a legend.
- `**bold**` works inside any text. Bullet items can be `{"text", "level"}` for a second level.
- Images are fitted inside their area without distortion; paths are relative to the JSON file.
- `"footer": false` removes footer and slide numbers.
