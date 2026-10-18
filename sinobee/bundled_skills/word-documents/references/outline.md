# Outline JSON for `run.py build`

```json
{
  "title": "釷基熔鹽反應爐可行性評估",
  "subtitle": "2026 年 10 月",
  "author": "SinoBee",
  "lang": "zh-Hant",
  "page": {"size": "A4", "orientation": "portrait", "margins_cm": 2.5},
  "fonts": {"latin": "Calibri", "east_asia": "Microsoft JhengHei", "size": 11},
  "header": "內部文件",
  "footer": "CAFECA",
  "page_numbers": true,
  "toc": true,
  "blocks": [
    {"h1": "摘要"},
    {"p": "一段文字，可用 **粗體**、*斜體*、`程式碼` 與 [連結](https://example.org)。"},
    {"bullets": ["第一點", {"text": "第二層", "level": 1}, "第三點"]},
    {"numbered": ["步驟一", "步驟二"]},
    {"h2": "主要參數"},
    {"table": {"caption": "主要參數", "header": ["項目", "數值", "單位"],
               "rows": [["熱功率", 2, "MWt"], ["出口溫度", 650, "°C"]],
               "widths_cm": [6, 4, 3], "header_fill": "D9E2F3"}},
    {"image": {"path": "fig1.png", "width_cm": 15, "caption": "一次迴路示意"}},
    {"quote": "引用的原文"},
    {"code": "python model.py --power 2"},
    {"page_break": true},
    {"h1": "參考資料"},
    {"numbered": ["作者，〈標題〉，網址，取用日期"]}
  ]
}
```

- Block keys: `h1`-`h4`, `p` (with `align: "center"`), `bullets`, `numbered`, `table`, `image`, `quote`, `code`,
  `page_break`. A plain string in `blocks` is a paragraph.
- Image paths are relative to the JSON file.
- `lang` is optional (detected from the text); it picks the default East Asian font and the 圖/表 vs
  Figure/Table caption words: zh-Hant → Microsoft JhengHei, zh-Hans → Microsoft YaHei, ja → Yu Gothic,
  ko → Malgun Gothic. On macOS Word substitutes PingFang when those fonts are missing.
- `page.size`: `A4` (default) or `Letter`.
