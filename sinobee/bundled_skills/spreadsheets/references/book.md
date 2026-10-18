# Workbook JSON for `run.py build`

```json
{
  "font": "Calibri",
  "sheets": [
    {
      "name": "預算",
      "title": "2027 年度 IT 預算",
      "columns": [
        {"name": "項目"},
        {"name": "單價", "format": "twd", "input": true},
        {"name": "數量", "format": "int", "input": true},
        {"name": "小計", "format": "twd", "width": 14}
      ],
      "rows": [
        ["伺服器", 30000, 2, "=B4*C4"],
        ["授權", 12000, 5, "=B5*C5"]
      ],
      "totals": ["小計"],
      "totals_label": "合計",
      "freeze": true,
      "filter": true,
      "header_fill": "305496",
      "charts": [
        {"type": "column", "title": "各項小計", "x": "項目", "y": ["小計"], "y_title": "NT$", "at": "F3"}
      ]
    }
  ],
  "names": {"稅率": "'假設'!$B$2"}
}
```

- Row numbers in formulas: with a `title`, the header is row 3 and data starts at row 4; without a title,
  the header is row 1 and data starts at row 2. `build` reports `header_row` for each sheet.
- `format`: `int`, `number`, `percent`, `currency`, `twd`, `usd`, `date`, `text`, or any Excel format string
  (`"0.000"`, `"#,##0,\"K\""`).
- `input: true` paints typed values blue; formulas in that column stay black.
- Chart `type`: `column`, `bar` (horizontal), `line`, `pie`. `x` is the category column, `y` one or more
  value columns (by header name).
