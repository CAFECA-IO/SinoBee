---
name: presentations
description: Creates, reads and checks PowerPoint decks (.pptx) - pitch decks, briefings, training and project updates - from an outline with one consistent theme, native editable charts and tables, speaker notes and CJK fonts, then renders every slide and checks for overflowing text before delivery. Use when the user asks for slides, a deck, a presentation or a .pptx, or attaches one to read or revise (簡報、投影片、PPT).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: pptx, powerpoint, slides, deck, presentation, office
  sinobee-triggers: 簡報, 投影片, PPT, pptx, PowerPoint, deck, slides, presentation, 提案簡報, 報告簡報
  sinobee-requires-tools: skill_run, read_file, write_file
  sinobee-requires-python: pptx=python-pptx
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {file}"
  sinobee-checks-on: "*.pptx"
  sinobee-quickstart: "1) storyline: one message per slide; 2) charts/images as files; 3) write deck.json (references/deck.md); 4) skill_run(presentations, \"build deck.json deck.pptx\"); 5) \"check deck.pptx\" until ok; 6) \"render deck.pptx <dir>\" and read_file every slide image; fix what looks wrong."
  author: sinobee
---

# When to Use
The user wants slides (to present, send or print) or gives you a .pptx. For a document to read, use
word-documents; for a web page, write HTML.

# Reading a deck
`read_file deck.pptx` gives each slide's title, text, tables and speaker notes. `skill_run(presentations,
"inspect deck.pptx")` adds pictures/charts/tables per slide. To see the design, `render` it and look at the
slide images.

# Building a deck
1. **Storyline first.** Write the slide titles as a sequence of statements that tell the argument on their
   own ("熔鹽爐在常壓下運轉，事故時不會噴出" rather than "安全性"). One message per slide. 8-15 slides for a
   20-minute talk. Open with the conclusion; end with the ask or next steps.
2. **Evidence on each slide**: a chart, a table, a picture, or 3-5 short bullets - not paragraphs. Long
   explanation goes into speaker `notes`.
3. Make images (charts as PNG via data-visualization, diagrams, photos the user supplied) before building.
   For data the audience should be able to edit, use the `chart` layout - it is a native PowerPoint chart.
4. Write `deck.json` (`references/deck.md`) and run `build`. Layouts: title, section, bullets, two-column,
   image, chart, table, quote, big-number, closing. Theme colours and fonts are set once in `theme`.
5. `check` must pass: it measures whether each text box's text fits (CJK-aware), finds shapes off the slide,
   empty placeholders, text under 10 pt.
6. `render` and **look at every slide image** with read_file: overlaps, cramped or empty areas, unreadable
   chart labels, contrast. Fix and rebuild until it looks finished. If LibreOffice is missing, say the slides
   were not visually checked.

# Editing an existing deck
- Keep the user's template: open it with python-pptx on a copy, change text in the existing shapes
  (`shape.text_frame.paragraphs[i].runs[j].text`) so fonts and positions stay, and add slides with the
  deck's own layouts (`prs.slide_layouts[...]`).
- Do not rebuild a user's deck with `build` - that replaces their design.
- After editing, `check` and `render` the result the same way.

# Design rules
- Big type: titles 28-36 pt, body 18-24 pt, nothing under 12 pt except footers and sources.
- One accent colour plus greys; colour means something (highlight the bar you talk about).
- Align to a grid; same margins on every slide; no decorative clip art.
- Numbers: units in the axis or header, rounded to what matters, source line under the chart.
- Chinese text: set an East Asian font (build does this - Microsoft JhengHei for Traditional Chinese by
  default; `theme.east_asia` to change it); no mid-word line breaks in titles - shorten instead.

# Verification
`check` ok, every rendered slide looked at, slide titles read as the storyline, speaker notes on slides that
need explanation.
