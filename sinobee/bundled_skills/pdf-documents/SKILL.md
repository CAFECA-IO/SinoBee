---
name: pdf-documents
description: Reads and produces PDF files - extracts text and tables, looks at scanned or visual pages, merges, splits, rotates, stamps watermarks or letterheads, fills PDF forms, and prints HTML reports to PDF with correct Chinese fonts; checks fonts are embedded and no page is blank. Use when the user attaches a PDF or asks for a PDF, a form to be filled, files to be combined, or a printable version of a report (PDF、表單、合併、分割、轉 PDF).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: pdf, document, form, office, print
  sinobee-triggers: PDF, pdf, 合併 PDF, 分割, 表單, 填表, 轉成 PDF, 列印, 掃描, 浮水印, 簽名
  sinobee-requires-tools: skill_run, read_file
  sinobee-requires-python: pypdf
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {file}"
  sinobee-checks-on: "*.pdf"
  sinobee-quickstart: "read: \"info f.pdf\" then \"text\" / \"tables\"; image-only pages -> \"render\" and read_file the PNGs. make: write HTML (print CSS) -> \"from-html page.html out.pdf\" -> \"check\" -> \"render\" and look. forms: \"fields\" -> values.json -> \"fill\"."
  author: sinobee
---

# When to Use
Any task where a PDF is the input or the output. A Word file the user will keep editing is word-documents;
slides are presentations.

# Reading a PDF
1. `skill_run(pdf-documents, "info file.pdf")`: pages, sizes, fonts, form fields, and **image-only pages**.
2. Text: `read_file file.pdf` (whole document, page by page) or `"text file.pdf --pages 3-5"`.
   Tables: `"tables file.pdf --pages 4 --csv out/"` - check the columns against the page image; merged
   header cells and wrapped lines often split or shift.
3. Image-only (scanned) pages, charts, drawings, signatures, stamps: `"render file.pdf out/ --pages 2"` and
   read_file the PNG - you can read the page yourself. Quote what you read and say it came from the image.
4. Cite page numbers for every fact you take from a PDF.
5. Encrypted PDF: ask the user for the password; never try to guess or remove protection they did not ask to
   remove.

# Making a PDF
- **From a report**: write a clean HTML page (print CSS: `@page { size: A4; margin: 18mm }`,
  `break-inside: avoid` on tables and figures, `h2 { break-after: avoid }`, fonts
  `"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif`) and run
  `"from-html report.html report.pdf"`. Chromium embeds the fonts and keeps colours. Local images must sit
  next to the HTML.
- **From a .docx/.pptx/.xlsx**: `soffice --headless --convert-to pdf --outdir out/ file.docx`.
- Then `check` (fonts embedded, no blank pages, no placeholder text) and `render` a few pages to look at.

# Changing PDFs
- Combine: `"merge out.pdf a.pdf b.pdf c.pdf"` (adds a bookmark per input).
- Split: `"split file.pdf out/ --ranges 1-3,4-10,11-"` (default: one file per page).
- Rotate: `"rotate file.pdf out.pdf 90 --pages 2,5"`.
- Watermark / letterhead / signature block: make a one-page PDF of the stamp (HTML with a transparent
  background → `from-html`), then `"stamp file.pdf stamp.pdf out.pdf --pages 1-"`.
- Always write to a new file; keep the original.

# Forms
1. `"fields form.pdf"` lists every fillable field (name, type, options such as `/Yes` for a checkbox).
2. Write `values.json` mapping field names to values; ask the user for anything you do not know - never
   invent personal data, ID numbers or signatures.
3. `"fill form.pdf values.json filled.pdf"`, then `render` and look: every value in its box, nothing cut off.
4. A form without fillable fields: put the text on with `stamp` (an HTML page absolutely positioned in mm at
   the same page size), and check the rendered result carefully.

# Verification
`check` ok; for anything visual you rendered and looked at the pages; facts taken from a PDF carry page
numbers.
