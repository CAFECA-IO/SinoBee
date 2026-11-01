# Update / Fold-in workflow (Mode 4)

From upstream book-to-skill, adapted for SinoBee. Use it when new sources should be merged into a skill that
already exists. Only skills you may edit can be updated: a book skill made by this converter is an `agent`
skill and is editable; a builtin, verified or community skill must be forked first (`skill_fork`).

1. **Read the existing skill.** `skill_view(<skill_name>)` for SKILL.md (Chapter Index, Topic Index, author,
   chapter count, Core Frameworks), then `skill_view(<skill_name>, "glossary.md")`, `"patterns.md"`,
   `"cheatsheet.md"`. The `<skill_resources>` list shows the chapter files; note the highest number (e.g. `ch12`).
2. **Classify the new text** (this run's `Text ->` file, read in slices as in Step 2.6):
   - *Revision* of an existing chapter's topic → read that chapter file, merge the new details, rewrite it.
   - *Addition* (new chapters, papers, sections) → new chapter files numbered after the highest one
     (`ch13-*.md`, `ch14-*.md`, …).
3. **Write chapter files** with `skill_write_file(<skill_name>, "chapters/ch<NN>-<slug>.md", …)`, following the
   chapter template in `references/templates.md` and the Step 7 budget.
4. **Merge the supporting files** and write each back with `skill_write_file`:
   - `glossary.md` — add new terms, keep it alphabetical; a term that already exists gets the new chapter
     references appended (`**Term** — definition (Ch 4, Ch 13)`).
   - `patterns.md` — append new techniques in the same format; keep it under ~2,500 tokens.
   - `cheatsheet.md` — fold in new decision rules, tables and thresholds.
5. **Update SKILL.md** with `skill_patch` (one exact replacement per section): chapter count, page estimate,
   sources, `Generated` date; fold the highest-impact new frameworks into Core Frameworks (body stays under
   4,000 tokens); append rows to the Chapter Index; merge the Topic Index alphabetically, appending chapter
   links to topics that already exist (`- **Topic** → ch05, ch13`).
6. **Scan, clean up and report** as in Steps 9.5 and 10: list the added chapters, the merged glossary terms and
   the updated indexes.
