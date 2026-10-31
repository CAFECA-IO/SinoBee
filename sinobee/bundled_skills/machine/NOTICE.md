# Notice / 出處聲明

**Machine Skill** — "Describe any machine in one line. Claude builds the real thing, running on real physics."

| | |
|---|---|
| Original author / 原作者 | Air Supply Technologies Inc. ([Airsup](https://github.com/AirsupHQ)) |
| Source / 原始出處 | <https://github.com/AirsupHQ/machine-skill> |
| Upstream commit / 對應版本 | `e0bd066ac5beaf48512935de2fc0f0e55636853a` (2026-10-13, release 1.1.0 per `CHANGELOG.md`) |
| License / 授權 | MIT License, Copyright (c) 2026 Air Supply Technologies Inc. (Airsup) — full text in `LICENSE` |
| Live gallery | <https://airsup.ai/lab/skill> |

## What the SinoBee copy changes / SinoBee 版本的變更

This folder mirrors the upstream repository, packaged by CAFECA to ship with SinoBee as a built-in skill. Nothing in the kit, scripts, references, examples or evals was modified. The skill keeps its upstream name `machine` (the folder name follows it, as the Agent Skills spec requires).

1. `SKILL.md` frontmatter: added `license`, `compatibility` and the `metadata` fields SinoBee reads (version, author, source, upstream commit and license, tags, triggers, required tools, network use).
2. `SKILL.md` body: added the "Source and credits" note under the title.
3. `NOTICE.md`: this file.
4. Left out: `.github/` (README images, about 7 MB), `film/` (the launch film's director pages and stills, about 3.4 MB), `LAUNCH.md` and `V2-IDEAS.md` (project notes). The skill does not use any of them; the README's images therefore do not show here.

本資料夾為上游專案的鏡像，由 CAFECA 打包、隨 SinoBee 內建。工具組、腳本、參考文件、範例與評測皆未修改；技能沿用上游名稱 `machine`。僅調整上述 `SKILL.md` 的 frontmatter 與出處說明，並新增本檔；未收錄宣傳用的 `.github/`、`film/`、`LAUNCH.md`、`V2-IDEAS.md`。

To update: pull the upstream repository, copy the files listed above over this folder, re-apply the two `SKILL.md` edits, keep `NOTICE.md`, and bump `metadata.version` / `sinobee-upstream-commit`.

Bugs and feature requests for the skill itself belong upstream: <https://github.com/AirsupHQ/machine-skill/issues>.
