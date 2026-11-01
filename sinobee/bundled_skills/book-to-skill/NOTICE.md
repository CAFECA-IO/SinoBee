# Notice / 出處聲明

**book-to-skill** — converts books and documents into structured agent skills (frameworks, principles, techniques, anti-patterns).

| | |
|---|---|
| Original author / 原作者 | virgiliojr94 ([@virgiliojr94](https://github.com/virgiliojr94)) and the project's contributors |
| Source / 原始出處 | <https://github.com/virgiliojr94/book-to-skill> |
| Upstream commit / 對應版本 | `7540eefbe74933f5fc84c86becac52486b347778` (2026-10-28; release v1.4.0 plus later fixes) |
| SinoBee edition / SinoBee 版本 | `1.4.1-main.7540eef.sinobee.2`, packaged by CAFECA |
| License / 授權 | MIT License, Copyright (c) 2025 virgiliojr94 — full text in `LICENSE.md`. CAFECA's changes are released under the same license. |

## What the SinoBee edition changes / SinoBee 版本的變更

This is not a plain mirror. A review on 2026-11-01 found that the upstream instructions cannot run in SinoBee
(the extractor is looked up only in other agents' skill folders; generated skills would be written where SinoBee
either trusts them without review or, in the sandbox, loses them) and three robustness problems in the extractor.

1. **`SKILL.md` rewritten for SinoBee** (263 lines instead of 895). Same method, steps, budgets and quality rules;
   SinoBee's tools instead of host-specific shell: the extractor runs through `skill_run` (`sinobee-entry`), the
   generated skill is created with `skill_create` / `skill_write_file` / `skill_patch` (experimental, scanned,
   shown as unreviewed until approved), questions go through `ask_user` with defaults for unattended runs, paths
   are quoted, the extractor never installs packages, and the workdir cleanup only deletes this run's
   `book_skill_work-<pid>` folder. New section "The source is data, never instructions" against prompt injection
   from the document. Templates, the Update/Fold-in workflow and the sharing steps moved to `references/`.
   The upstream file is kept verbatim in `references/upstream-SKILL.md` (maintainers only).
2. **`book_to_skill/limits.py` (new) and `utils.py`**: a source over 512 MB, or a DOCX/EPUB whose members unpack
   past 128 MB each or 1 GB in total, is refused before any parser opens it (zip-bomb and memory DoS).
3. **`utils.py` symlinks**: when a folder or glob is expanded, symlinks and files that resolve outside it are
   skipped (a "book bundle" could otherwise pull in private files). Files the user names one by one are unchanged.
4. **`utils.py` regexes**: lines over 300 characters are not tried as Markdown headings, and the table-of-contents
   pattern no longer lets whitespace span lines — both backtracked quadratically on hostile input (ReDoS).
   Every patch is marked `SinoBee patch` in the code; SinoBee's `tests/test_book_to_skill_patches.py` covers them.
5. **Invisible characters escaped** in `book_to_skill/utils.py` (5: the ZWJ/ZWNJ inside Sinhala and Persian words in
   comments and one dictionary key) and `tools/validate_skill.py` (1: a BOM in a comment): each literal character is
   written as its `\uXXXX` escape so the Skill Hub's invisible-character scan has nothing to flag. The parsed program
   is identical (same AST); only the source spelling changed.
6. Left out of the folder: `.github/`, `tests/`, `docs/`, `evals/`, `overrides/`, `mkdocs.yml`, `cliff.toml`, the
   contributor notes (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `BACKERS.md`) and `README.ru.md`.

Not changed: the extraction logic, parsers, sanitizer, `tools/` and `scripts/extract.py`.

本版不是單純鏡像。2026-11-01 的審查發現上游指示在 SinoBee 中無法執行（只在其他 agent 的技能資料夾找抽取腳本；產生的技能會寫到未經審核就被信任、或在沙箱中會遺失的位置），抽取程式另有三個穩健性問題。因此：`SKILL.md` 依 SinoBee 工具改寫（方法、步驟、預算與品質規則不變，新增「來源內容只是資料」的防提示注入規則），範本、增補流程與分享步驟移到 `references/`，上游原文保留於 `references/upstream-SKILL.md`；抽取程式加上大小限制（防 zip bomb）、資料夾與 glob 不跟隨 symlink、標題與目錄正規式不再有二次方回溯。程式中的修改都標示 `SinoBee patch`。

## Updating from upstream

1. Diff the new upstream `SKILL.md` against `references/upstream-SKILL.md`; carry method changes into `SKILL.md`
   and `references/*.md`, then replace `references/upstream-SKILL.md` (keep its maintainer header).
2. Copy the new `book_to_skill/`, `scripts/` and `tools/` over this folder, re-apply the `SinoBee patch` hunks in
   `utils.py` (or drop them if upstream fixed the same problems), keep `limits.py`, run
   `tests/test_book_to_skill_patches.py`.
3. Bump `metadata.version` / `sinobee-upstream-commit`.

Problems with the method itself belong upstream: <https://github.com/virgiliojr94/book-to-skill/issues>.
The symlink, size-limit and ReDoS fixes are worth offering upstream as well.
