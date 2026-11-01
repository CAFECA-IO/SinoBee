# Notice / 出處聲明

**book-to-skill** — converts books and documents into structured agent skills (frameworks, principles, techniques, anti-patterns).

| | |
|---|---|
| Original author / 原作者 | virgiliojr94 ([@virgiliojr94](https://github.com/virgiliojr94)) and the project's contributors |
| Source / 原始出處 | <https://github.com/virgiliojr94/book-to-skill> |
| Upstream commit / 對應版本 | `7540eefbe74933f5fc84c86becac52486b347778` (2026-10-28; release v1.4.0 plus later fixes). SinoBee version `1.4.1-main.7540eef`: a pre-release of the next patch, so it sorts after 1.4.0 and before any later upstream release. |
| License / 授權 | MIT License, Copyright (c) 2025 virgiliojr94 — full text in `LICENSE.md` |

## What the SinoBee copy changes / SinoBee 版本的變更

This folder mirrors the upstream repository, packaged by CAFECA to ship with SinoBee as a built-in skill. Nothing in the instructions, the `book_to_skill` package, `scripts/` or `tools/` was modified.

1. `SKILL.md` frontmatter: added `license`, `compatibility` and the `metadata` fields SinoBee reads (version, author, source, upstream commit and license, tags, triggers, required tools).
2. `SKILL.md` body: added the "Source and credits" note under the title.
3. `NOTICE.md`: this file.
4. Left out: `.github/`, `tests/`, `docs/` (the documentation site and its images), `evals/`, `overrides/`, `mkdocs.yml`, `cliff.toml`, the contributor notes (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `BACKERS.md`) and `README.ru.md`. The skill does not use any of them.

Note for SinoBee: the instructions name other agents' skill folders (`~/.claude/skills`, `~/.agents/skills`…) as the place to write a generated skill. In SinoBee, put the result in `~/.sinobee/skills/` (global) or `<project>/.sinobee/skills/` (project), or install it with `sinobee skills install ./<folder>`.

本資料夾為上游專案的鏡像，由 CAFECA 打包、隨 SinoBee 內建。指示、`book_to_skill` 套件、`scripts/` 與 `tools/` 皆未修改；僅調整上述 `SKILL.md` 的 frontmatter 與出處說明，並新增本檔。在 SinoBee 中，產生的技能請放在 `~/.sinobee/skills/` 或 `<專案>/.sinobee/skills/`。

To update: pull the upstream repository, copy the files listed above over this folder, re-apply the two `SKILL.md` edits, keep `NOTICE.md`, and bump `metadata.version` / `sinobee-upstream-commit`.

Bugs and feature requests for the skill itself belong upstream: <https://github.com/virgiliojr94/book-to-skill/issues>.
