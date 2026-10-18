---
name: word-documents
description: Creates, reads and edits Word documents (.docx) - reports, proposals, contracts, letters, manuals - with real headings, lists, tables, figures, table of contents, page numbers and correct CJK fonts; edits existing files with tracked changes. Use when the user asks for a Word file / .docx, attaches one, or wants a document to print or send as Word (Word 文件、docx、報告、合約、公文、提案書).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: docx, word, document, report, office
  sinobee-triggers: Word, docx, .docx, Word 檔, 文件, 報告, 合約, 契約, 提案書, 公文, 修訂, 追蹤修訂, 改稿
  sinobee-requires-tools: skill_run, read_file, write_file
  sinobee-requires-python: docx=python-docx
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {file}"
  sinobee-checks-on: "*.docx"
  sinobee-quickstart: "new: write <name>.json (outline, see references/outline.md) -> skill_run(word-documents, \"build <name>.json <name>.docx\") -> \"check\" -> \"render\" and look at the pages. existing: read_file the .docx (text + tables + tracked changes), then \"replace … --track\" for edits."
  author: sinobee
---

# When to Use
- The user wants a **.docx** (or a document they will open in Word, print, or send as an attachment).
- The user attached a .docx to read, summarise, review, fill or edit.
Not for: a page to view in the browser (write HTML/Markdown), a PDF to sign or merge (pdf-documents), slides
(presentations), a table to calculate in (spreadsheets).

# Reading a .docx
`read_file` returns the document as Markdown: headings, lists, tables, comments, and tracked changes shown as
`{+inserted+}` / `[-deleted-]`. For structure and fonts use `skill_run(word-documents, "inspect <file>")`.
Never decode the zip yourself and never guess content from the file name.

# Creating a document
1. Plan the outline first: purpose, reader, sections. A report has a title, a short summary up front, numbered
   sections, tables for figures that are compared, and sources at the end.
2. Make figures as image files first (PNG at 150-200 dpi; see data-visualization for charts).
3. Write the outline JSON (`references/outline.md` has every block type) and build:
   `skill_run(word-documents, "build reports/x/doc.json reports/x/x.docx")`.
   The builder sets page size (A4 default), margins, an East Asian font for Chinese/Japanese/Korean text
   (Microsoft JhengHei for Traditional Chinese unless `fonts.east_asia` says otherwise), real list and heading
   styles, a repeating table header row, captions numbered 圖 n / 表 n, page numbers and an optional TOC field.
4. `check` it, fix every problem it reports, then `render` it and look at every page image with read_file:
   does the table fit the page, are figures readable, any empty page, any wrong font (boxes or mixed fonts)?
5. Deliver the .docx path. Mention that the table of contents fills in when Word updates fields (F9).

Need something the outline cannot express (multi-column sections, footnotes, content controls)? Write a short
python-docx script in the workspace; keep using styles (never fake headings with bold 16 pt text) and set the
`w:eastAsia` font on styles for CJK text.

# Editing an existing document
- Keep the user's file: write the result to a new name (`…-revised.docx`) unless told to overwrite.
- Text changes: `skill_run(word-documents, "replace in.docx out.docx \"舊文字\" \"新文字\" --track")`.
  With `--track` the edit is a real tracked change (author SinoBee) the user can accept or reject in Word; use it
  for contracts and anything another person reviews. It works across runs and keeps the original formatting.
- Several edits: chain them (output of one is the input of the next), then `read_file` the result to confirm
  every `{+…+}` / `[-…-]` is where you meant.
- Structural edits (new sections, tables): python-docx on a copy; never rebuild the user's document from its
  text - you would lose their styles, numbering and layout.

# Pitfalls
- Markdown in a .docx is not formatting: `#`, `**`, `|---|` must never appear in the output text.
- Old binary .doc files: convert first (`soffice --headless --convert-to docx file.doc`).
- Page images need LibreOffice; without it say that the layout was not visually checked.
- Numbers: right-aligned in tables, with units in the header, same decimals in a column.

# Verification
`check` passes (no placeholder text, heading levels in order, CJK font set, no empty table header, no runs of
empty paragraphs), and you looked at the rendered pages - or said plainly that you could not render them.
