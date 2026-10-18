---
name: spreadsheets
description: Creates, reads and fixes Excel workbooks (.xlsx/.xlsm/.csv) - budgets, financial models, trackers, data tables, reports with charts - with live formulas instead of pasted numbers, clear input/formula styling, number formats and a recalculation check that catches formula errors (DIV/0, REF, NAME) before delivery. Use when the user wants an Excel file or attaches a spreadsheet to analyse, clean or change (Excel、試算表、xlsx、預算表、報表).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: xlsx, excel, spreadsheet, csv, finance, office
  sinobee-triggers: Excel, xlsx, 試算表, 工作表, 預算, 財務模型, 報表, 樞紐, 公式, csv, spreadsheet, workbook
  sinobee-requires-tools: skill_run, read_file, write_file
  sinobee-requires-python: openpyxl
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {file}"
  sinobee-checks-on: "*.xlsx, *.xlsm"
  sinobee-quickstart: "new: write <name>.json (references/book.md) -> skill_run(spreadsheets, \"build <name>.json <name>.xlsx\") -> \"check <name>.xlsx\" until ok -> \"recalc\" if the reader needs stored values. existing: read_file or \"inspect\" first; change a copy with openpyxl; \"check\" before delivering."
  author: sinobee
---

# When to Use
The deliverable is a spreadsheet, or the input is one. Not for a one-off table in the answer (Markdown), or
a chart for a report (data-visualization).

# Reading
`read_file book.xlsx` lists every sheet with each cell's formula and its cached value (`=B4*C4 → 60000`).
`skill_run(spreadsheets, "inspect book.xlsx --rows 30")` adds size, merged cells, freeze panes, charts.
For analysis load it with pandas/openpyxl in a workspace script; report numbers with the sheet and cell they
came from. A formula marked "(not calculated)" has no stored value - run `recalc` on a copy to see it.

# Building
1. Design before typing: which cells are **inputs** (assumptions the user will change), which are
   **calculations**, which are **outputs**. Put assumptions in one place (top of the sheet or an "假設" sheet),
   never inside formulas (`=B4*1.05` hides the 5 %; write the rate in a cell and reference it).
2. Every derived number is a formula: totals `=SUM()`, growth `=B5*(1+$B$2)`, shares `=B5/B$10`. Never compute
   in Python and paste the result - the user's edits would not flow through.
3. Describe the workbook in JSON (`references/book.md`) and `build` it: header row styled and frozen, filters,
   column formats (`twd`, `usd`, `int`, `number`, `percent`, `date` or any Excel format string), input columns in
   blue font, totals row with `=SUM`, native Excel charts.
4. `check` and fix until it passes. It recalculates with LibreOffice and reports formula errors, numbers stored
   as text, and totals typed in as constants.
5. Openpyxl saves formulas without values. If the user will view it somewhere that does not calculate (a
   preview, a phone, SinoBee's own file view) run `recalc book.xlsx book.xlsx`.

For things the JSON cannot express (conditional formatting, data validation lists, several tables on a sheet,
cross-sheet models) write an openpyxl script; keep the same conventions and run `check` afterwards.

# Editing an existing workbook
- Work on a copy (`…-updated.xlsx`) unless told otherwise.
- openpyxl keeps formulas, styles, merged cells and defined names, but **drops pivot tables, slicers, images
  and charts it cannot read, and VBA unless `keep_vba=True` for .xlsm**. If the file has those, say so before
  saving, or edit with LibreOffice headless macros instead.
- Insert rows/columns with care: openpyxl does not rewrite formulas that point past the insertion - fix the
  references or rebuild the affected ranges as formulas.
- Match the existing style (fonts, fills, number formats) for new cells.

# Conventions
- Blue font = typed input, black = formula, green = link to another sheet/file.
- One unit per column, stated in the header (`金額（千元）`); same decimals down a column; negatives in
  parentheses or red for finance.
- No merged cells in data tables (they break sorting and filters); use center-across-selection or a title row.
- Dates as real dates, not text. Percentages as fractions with a % format (0.05 → 5.0%).
- A model has a short notes sheet: purpose, sources of the assumptions, date.

# CSV
Plain data in, plain data out: UTF-8 with BOM (`utf-8-sig`) so Excel on Windows shows Chinese correctly; quote
fields with commas; ISO dates. Convert to .xlsx when the user wants formatting or formulas.

# Verification
`check` returns ok (recalculated, no formula errors, no text numbers, totals are formulas). Spot-check two or
three key outputs by hand and state them in the answer with their cell addresses.
