# Notice / 出處聲明

**scroll-world** — builds an immersive, scroll-scrubbed "fly through the world" landing page for any industry
or brand with AI-generated scenes and frame-locked camera clips.

| | |
|---|---|
| Original author / 原作者 | cyw ([@oso95](https://github.com/oso95)) |
| Source / 原始出處 | <https://github.com/oso95/scroll-world> |
| Upstream commit / 對應版本 | `71cc36d3bb150248ae36a2c552f9cbf88802a79c` (2026-07-28; release 0.8.0 plus later fixes) |
| SinoBee edition / SinoBee 版本 | `0.8.0-main.71cc36d.sinobee.1`, packaged by CAFECA |
| License / 授權 | MIT License, Copyright (c) 2026 cyw — full text in `LICENSE`. CAFECA's changes are released under the same license. |

## What the SinoBee edition changes / SinoBee 版本的變更

1. **`SKILL.md` condensed and adapted to SinoBee** (225 lines instead of 762). Same method, steps, seam rules,
   model roster, prices and QA. Added: a "Running it in SinoBee" section (paid generation only after the
   user's go, never in an unattended run; checking the CLIs and sign-in where SinoBee's shell runs; keys
   through 任務金鑰 / `request_secret`, never in the chat; `request_network` in the sandbox; long batches
   with `nohup` and a polled log; `ask_user` instead of `AskUserQuestion`; a `/workspace/<project>/`
   layout). Frontmatter: the Agent Skills fields SinoBee reads (`license`, `compatibility`, `metadata`); the
   upstream `allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, Skill` (Claude Code tool names) is
   left out because SinoBee's tools have other names.
2. **`references/upstream-SKILL.md`**: the upstream `SKILL.md`, verbatim below a short maintainer header.
   The condensed page points into it for detail (camera grammar, mobile QA, the full Gotchas).
3. **`NOTICE.md`**: this file.
4. **Not changed:** `references/prompts.md`, `references/pipeline.md`, `references/scrub-engine.js`,
   `references/index-template.html`, `references/knockout.py`, `README.md`, `LICENSE`.
5. **Left out:** `.claude-plugin/` (the Claude Code plugin manifest and marketplace entry) and `.gitignore`.

本版不是單純鏡像：`SKILL.md` 精簡為 225 行（上游 762 行）並改用 SinoBee 的工具（方法、步驟、接縫規則、模型清單、價格與檢查不變；新增「在 SinoBee 中執行」一節：付費生成須先經使用者同意、無人值守時不生成、金鑰走任務金鑰、沙盒網路以 `request_network` 申請、長時間批次以 `nohup` 背景執行並輪詢、以 `ask_user` 取代 `AskUserQuestion`）。上游 `SKILL.md` 全文保留於 `references/upstream-SKILL.md`；`references/` 其餘檔案、`README.md` 與 `LICENSE` 未修改；未收錄 Claude Code 外掛設定 `.claude-plugin/`。

## Updating from upstream

1. Diff the new upstream `skills/scroll-world/SKILL.md` against `references/upstream-SKILL.md`; carry method
   changes into `SKILL.md`, then replace `references/upstream-SKILL.md` (keep its maintainer header).
2. Copy the new `skills/scroll-world/references/*`, `README.md` and `LICENSE` over this folder.
3. Bump `metadata.version` / `sinobee-upstream-commit` (and the version in this file), then re-sync the Skill
   Hub seed in SinoBee.ai (`npm run seed:sync`).

Problems with the method itself belong upstream: <https://github.com/oso95/scroll-world/issues>.
