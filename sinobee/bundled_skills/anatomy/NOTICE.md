# Notice / 出處聲明

**Anatomy** — "A Claude skill that explains an idea by building it as a real, working object."

| | |
|---|---|
| Original author / 原作者 | Ryan ([@wheresryan22](https://github.com/wheresryan22)) and the project's contributors |
| Source / 原始出處 | <https://github.com/wheresryan22/anatomy> |
| Upstream commit / 對應版本 | `e9488296a44211c42701ff71705b7901470ec78f` (2026-10-15; release 1.1.0 plus unreleased changes, see `CHANGELOG.md`). Hub version `1.1.1-main.e948829`: a pre-release of the next patch, so it sorts after 1.1.0 and before any later upstream release. |
| License / 授權 | MIT License, Copyright (c) 2026 Ryan — full text in `LICENSE` |
| Website | <https://skills.wheresryan.sh/anatomy> |
| Community gallery | <https://github.com/wheresryan22/anatomy-community> |

## What the SinoBee Hub copy changes / SinoBee Hub 版本的變更

This folder is a mirror of the upstream repository, packaged by CAFECA: it ships with SinoBee as a built-in skill and is published on the SinoBee Skill Hub (sinobee.ai) by the `cafeca` publisher. Nothing in the kit, scripts, references or examples was modified. In the SinoBee Docker image the skill finds Node.js and Chromium on its own (`node` and `/usr/bin/chromium` are provided for it).

1. `SKILL.md` frontmatter: added the fields the Hub requires or reads (`metadata.version`, `author`, source, upstream commit and license, tags, triggers, required tools) and named SinoBee in `compatibility`. The original `opencode/slash` key is kept.
2. `SKILL.md` body: added the "Source and credits" note under the title. The rest of the instructions are unchanged.
3. `NOTICE.md`: this file.
4. Left out: the repository's `.github/` folder (CI, issue templates and README screenshots) and `.gitattributes`, which the Hub skips as hidden files anyway.

本資料夾為上游專案的鏡像，由 CAFECA 打包：隨 SinoBee 內建，並由 `cafeca` 發布者上架至 SinoBee Skill Hub。工具組、腳本、參考文件與範例皆未修改；僅調整上述 `SKILL.md` 的 frontmatter 與出處說明，並新增本檔。

To update: pull the upstream repository, copy it over this folder, re-apply the two `SKILL.md` edits above and bump `metadata.version` to the new upstream release/commit.

Bugs and feature requests for the skill itself belong upstream: <https://github.com/wheresryan22/anatomy/issues>.
