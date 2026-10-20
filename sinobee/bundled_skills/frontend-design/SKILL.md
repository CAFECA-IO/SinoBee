---
name: frontend-design
description: Designs and builds web pages, landing pages, dashboards and small web apps that look intentional rather than templated - a chosen visual direction, type scale, spacing and colour tokens with a dark scheme, responsive from 390 px phones up, accessible contrast, CJK typography, self-contained files - and checks them in a real browser on phone and desktop. Use when making or restyling any HTML page or UI (網頁、頁面、介面、UI、landing page、儀表板版面).
license: MIT
metadata:
  version: 1.0.1
  sinobee-tags: html, css, web, ui, design, frontend
  sinobee-triggers: 網頁, 網站, 頁面, 介面, 前端, 版面, 設計, landing page, web page, website, UI, frontend, CSS, 儀表板
  sinobee-requires-tools: write_file, skill_run
  sinobee-requires-python: playwright
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {file}"
  sinobee-checks-on: "*.html, *.htm"
  sinobee-quickstart: "1) one-line brief: who, what they do on the page, the feeling; 2) pick a direction + type scale + colour tokens (light and dark); 3) build mobile-first, self-contained; 4) skill_run(frontend-design, \"check page.html --shots <dir>\") until ok; 5) read_file phone.png / desktop.png / dark.png and fix what looks off."
  author: sinobee
---

# When to Use
Any HTML the user will look at: a report page, a landing page, a dashboard, an interactive explainer, a small
tool. (Charts inside it: data-visualization. 3-D mechanism figures: anatomy.)

# 1. Brief and direction
Before code, write three lines: who uses the page, the one thing they do or learn there, and the tone
(e.g. "工程師讀的技術評估：冷靜、密集、可信"). Then choose a direction that serves it and commit to it:
- **Editorial** - long reading: serif or humanist headings, generous measure (60-75 latin / 30-40 CJK
  characters), restrained colour.
- **Technical / data** - dense: neutral sans, tabular numbers, grid, one accent for state.
- **Product / marketing** - one bold idea: big type, a signature colour, plenty of space, one clear action.
Avoid the default look (centered hero + three cards + gradient + emoji icons) unless the brief asks for it.

# 2. System before pages
Define tokens on `:root` and use only them:
- Type scale (1.2-1.333 ratio): e.g. 14 / 16 / 20 / 25 / 31 / 39 px; body 16-18 px, line-height 1.6-1.8 for
  CJK (1.5 for latin). Font stacks:
  `"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif` (serif:
  `"Noto Serif TC", "Songti TC", serif`). Never letter-space CJK text; `font-variant-numeric: tabular-nums` for
  figures in tables.
- Spacing on a 4/8 px grid; one max content width (680-760 px for reading, 1200 px for dashboards).
- Colour: background, surface, text, muted text, border, accent, accent-text, success/warning/danger - each
  redefined under `@media (prefers-color-scheme: dark)`. Body text ≥ 4.5:1 contrast, large text and UI
  edges ≥ 3:1.
- Radius, shadow and motion as tokens too; motion ≤ 200 ms and off under `prefers-reduced-motion`.

# 3. Build
- Write the page in parts, never in one call: first a skeleton (the `head` with tokens and every section as an empty,
  marked placeholder such as `<!-- section: pricing -->`), then fill one section per call with `edit_file` (or
  `write_file` with `append=true`), about 30,000 characters at most each. A whole page in one reply runs into the
  model's output limit or the read timeout and is lost.
- Semantic HTML (`header`, `main`, `nav`, `section`, `figure/figcaption`, `table` with `th scope`), one `h1`,
  headings in order, `<html lang="zh-Hant">`, viewport meta, `alt` on every image.
- Mobile first: single column at 390 px, then grid at ≥ 768 px; tables scroll inside a wrapper
  (`overflow-x: auto`) instead of widening the page; long URLs `overflow-wrap: anywhere`.
- Self-contained: SinoBee opens pages offline, so inline the CSS/JS or save files next to the page; no CDN
  fonts or scripts unless the user will host it online (and then still provide a fallback stack).
- Interactive elements: real `button`/`a`, visible focus ring, 44 × 44 px touch targets, states for hover,
  active, disabled, loading, empty and error.
- No placeholder copy ("Lorem ipsum", "Feature 1") - write the real words or ask.

# 4. Check and look
1. `skill_run(frontend-design, "check page.html --shots out/")` - phone overflow, contrast in light and dark,
   tiny text, alt text, lang, viewport, network resources, script errors. Fix until `ok`.
2. read_file `out/phone.png`, `out/desktop.png`, `out/dark.png` and judge them as a visitor: hierarchy obvious
   at a glance? spacing even? anything cramped, orphaned or misaligned? dark scheme legible?
3. SinoBee's own quality gate also screenshots the page and has a vision model judge it.

# Verification
`check` ok and you looked at all three screenshots; the page matches the brief's tone, works at 390 px, and
needs no network to render.
