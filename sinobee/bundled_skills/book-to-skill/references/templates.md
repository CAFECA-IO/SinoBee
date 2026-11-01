# Templates

From upstream book-to-skill (SKILL.md Steps 3, 7 and 9), adapted for SinoBee: the generated skill is written with
`skill_create` / `skill_write_file` / `skill_patch`, so paths below are relative to the generated skill.

## Analyze-only report (Mode 2, end of Step 3)

```
## Extraction Report — <Title>

### Author's Core Frameworks
- **<Framework Name>**: <what it is and when to apply>

### Key Principles
- <Principle>: <actionable rule>

### Techniques & Methods
- <Technique>: <step-by-step or how-to>

### Anti-patterns
- <What to avoid>: <why>

### Suggested Skill Name
`{author-lastname}-{core-concept}` — e.g. `cialdini-influence`

### Chapters Detected
| # | Title | Main Frameworks |
```

## Chapter file — `chapters/ch<NN>-<slug>.md` (Step 7)

Adapt emphasis to `BOOK_TYPE`: `technical` → Code Examples, Reference Tables, exact syntax; `text` → Frameworks,
Mental Models, Key Takeaways, and drop the empty technical sections.

````markdown
# Chapter N: <Full Title>

## Core Idea
<1–2 sentences: the single most important thing this chapter teaches>

## Frameworks Introduced
- **<Framework Name>**: <exact formulation — preserve the author's naming>
  - When to use: <specific situation>
  - How: <steps or criteria>

## Key Concepts
- **<Term>**: <precise definition in 1 sentence>
(5–10 most important terms from this chapter)

## Mental Models
<2–4 frameworks or thinking tools. Write as "Use X when Y" or "Think of X as Y">

## Anti-patterns
- **<What to avoid>**: <why it fails>

## Code Examples *(technical books only — omit if BOOK_TYPE=text)*
```<language>
<the most instructive snippet from the chapter, indentation preserved>
```
- **What it demonstrates**: <one line>

## Reference Tables *(technical books only — omit if BOOK_TYPE=text)*
<any comparison matrix, parameter table or decision table from the chapter, in markdown>

## Worked Example *(DEPTH=study only — omit for DEPTH=reference)*
<one concrete example the author works through — a sample document, a dialogue, a filled-in template, a
before/after, or a decision walked end to end — reconstructed compactly, never long raw passages>

## Key Takeaways
1. <Actionable insight>
2. <Actionable insight>
3. <Actionable insight>
(3–7 takeaways a practitioner must remember)

## Connects To
- **Ch N**: <why this chapter relates>
- **<Concept>**: <external concept or standard it connects with>
````

## Master SKILL.md of the generated skill (Step 9)

Body under 4,000 tokens; compaction truncates from the end, so the most important content goes first.
Create it in Step 6 with the frontmatter and the placeholder line, then replace the placeholder with the body
using `skill_patch`.

```markdown
---
name: <skill_name>
description: "Knowledge base from \"<Full Title>\" by <Author(s)>. Use when applying <author>'s frameworks for <key topics, 3–6 terms>, studying the book, or referencing its concepts."
metadata:
  version: "1.0.0"
  sinobee-tags: "<3–6 topic tags>"
  sinobee-triggers: "<title words, author, key framework names, in the user's languages>"
  sinobee-generated-by: "book-to-skill"
---

<!-- book-to-skill: body pending -->
```

The body that replaces the placeholder:

```markdown
# <Full Title>
**Author**: <Author(s)> | **Pages**: ~<N> | **Chapters**: <N> | **Generated**: <YYYY-MM-DD> with book-to-skill

## How to Use This Skill
- **Without arguments** — use the core frameworks below
- **With a topic** — find it in the Topic Index and read that chapter file (skill_view(name, "chapters/…"))
- **With a chapter** — ask for `ch05`; read that file
- **Browse** — the Chapter Index lists every chapter

When a question is not covered by Core Frameworks, read the relevant chapter file before answering.

## Core Frameworks & Mental Models
<~2,000 tokens: the author's most important named frameworks and principles, exact names kept, written as
"Use X when Y" / "Prefer X over Y because Z" — a toolkit, not a summary>

## Chapter Index
| # | Title | Key Frameworks |
|---|-------|----------------|
| [ch01](chapters/ch01-<slug>.md) | <Title> | <framework1>, <framework2> |

## Topic Index
- **<Term>** → ch<N>[, ch<N>]   (alphabetical)

## Supporting Files
- [glossary.md](glossary.md) — key terms with definitions
- [patterns.md](patterns.md) — techniques and design patterns
- [cheatsheet.md](cheatsheet.md) — decision rules and quick-reference tables

## Scope & Limits
Covers the book's content only; the chapter files are synthesized summaries, not the book's text.
<if images_dropped > 5: state that N source images were not read>
```
