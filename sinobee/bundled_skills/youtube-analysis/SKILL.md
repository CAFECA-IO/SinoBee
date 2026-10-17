---
name: youtube-analysis
description: Watches a YouTube video (metadata, full transcript with timestamps, and stills about every 10 seconds) and writes a Markdown report with images - a SinoBee.ai-style cover, what the video says, a timestamped outline, an analysis section chosen from 16 video categories, and a fact-check of its claims for news, politics, science, finance, health, tech and documentary videos. Use when the user gives a YouTube link and asks to summarise, analyse, review, fact-check or report on it (YouTube 影片分析、影片摘要、幫我看這部影片、查核影片內容).
license: MIT
compatibility: Needs network access to youtube.com and i.ytimg.com, Python 3.10+, yt-dlp (installed on first use), Pillow for stills, and Playwright Chromium or cairosvg for the PNG cover (all in the SinoBee Docker image except yt-dlp).
metadata:
  version: 1.0.1
  author: sinobee
  sinobee-tags: youtube, video, summary, report, fact-check, analysis
  sinobee-triggers: YouTube, youtube, 影片分析, 影片摘要, 看影片, 這部影片, 這支影片, 查核, 懶人包, video summary, analyse video, fact-check video
  sinobee-requires-tools: skill_run, read_file, write_file, web_search, web_fetch
  sinobee-entry: scripts/run.py
  sinobee-quickstart: "1) skill_run(name=youtube-analysis, args=\"<url>\"); 2) read transcript.md and every sheets/*.jpg; 3) category + fact-check; 4) write cover.json, skill_run args=\"cover <folder>\"; 5) write report.md, skill_run args=\"check <folder>\" until ok; 6) deliver report.md."
---

# When to Use
The user shares a YouTube link (watch, youtu.be, shorts, live) and wants to know what it says, whether it is
right, or a report about it. One video per run; for a playlist, ask which videos or run once per video.

# Procedure
1. **Fetch.** `skill_run(name="youtube-analysis", args="<url>")` (add `--lang en` if the user writes English).
   It answers JSON with the `folder` (`reports/youtube/<id>/`), the video facts, chapters, the transcript kind and
   the contact sheets. `status: blocked` → tell the user the `what` and stop. `status: error` → read `error`.
2. **Watch.** This is the whole point; do not skip it.
   - `read_file` `transcript.md` to the end (use `offset` for long ones). It is the soundtrack, with `[mm:ss]`.
   - `read_file` every `sheets/sheet-NN.jpg`: 4×4 stills with times, the picture track. Note charts, on-screen
     text, demonstrations, who is on screen, sponsor segments.
   - `read_file` `source.json` for the description, tags and chapters (links in the description often name the
     sources the video relies on).
   - Without a transcript (`kind: none`) work from the stills, description and chapters, and say so in the report.
   - Auto captions (`kind: auto`) mishear names and numbers: confirm them from the stills or the description.
3. **Classify.** Read `references/categories.md`, pick one of the 16 categories and answer its questions.
4. **Fact-check** if the category says so: `references/fact-check.md`. Use `web_search` and `web_fetch` (the
   `web-research` skill's habits apply: open the page, two sources for anything important, note dates).
5. **Cover.** Write `<folder>/cover.json`:
   ```json
   {"title": "<the report's title, the user's language, ≤ 40 characters>",
    "tagline": "<one sentence: who says what>",
    "category": "<key from categories.md>",
    "points": ["<takeaway 1, ≤ 26 characters>", "<takeaway 2>", "<takeaway 3>"],
    "verdict": {"label": "<整體判定, e.g. 大致正確>", "tone": "ok | warn | err | info"}}
   ```
   Leave out `verdict` for categories that are not fact-checked. Then
   `skill_run(name="youtube-analysis", args="cover <folder>")` and look at `cover.png` with `read_file`; shorten
   the title or points if anything is cut with "…".
6. **Stills for the report.** The fetch saved stills in `frames/` (named `tMMSS.jpg`). For a specific moment:
   `skill_run(name="youtube-analysis", args="frame <folder> 12:30")`.
7. **Write** `<folder>/report.md` from `references/report-template.md`.
8. **Check.** `skill_run(name="youtube-analysis", args="check <folder>")`. Fix every listed problem and check again
   until `status: ok`.
9. **Deliver** in chat: the cover as an image, a link to the report, the one-sentence summary and the overall
   verdict, e.g.
   ```
   ![封面](reports/youtube/<id>/cover.png)
   **[開啟完整報告](reports/youtube/<id>/report.md)**　一句話摘要… 整體判定：大致正確
   ```
   SinoBee opens the linked report in its document viewer (cover, tables and stills rendered). The folder
   travels as a whole: cover.png and frames/ are referenced by relative paths.

# Rules
- Watch before writing: every statement in the report comes from the transcript, the stills, the description or
  a fetched source, never from the title alone or from memory of the topic.
- Timestamps everywhere: outline, analysis and fact-check rows link to `https://www.youtube.com/watch?v=ID&t=<s>s`.
- Separate what the video says from your assessment; separate facts from opinions.
- Quote only short phrases from the video (lyrics and scripts especially); summarise the rest.
- Health and finance reports end with a one-line disclaimer (see categories.md).
- Use `skill_run` for the scripts; do not call yt-dlp or the scripts through `shell`.

# Gotchas
- HTTP 429 or "Sign in to confirm you're not a bot": YouTube is limiting this network. The script reports
  `blocked`; try again later or from another network. Don't loop.
- Members-only, private and age-restricted videos cannot be fetched without the user's cookies; say so.
- Live streams still in progress have no final transcript; analyse the replay later.
- Very long videos (over 2 hours): read the transcript in parts and keep notes per chapter in working memory
  before writing.
- Stills are YouTube storyboard frames (about 320×180); good for seeing what is shown, too small for fine print.

# Verification
`check` returns `status: ok`; the cover shows the right category, title and verdict; every fact-check row has a
verdict and a source (except 無法查證 / 觀點); the report opens with the cover and reads well for someone who has
not seen the video.
