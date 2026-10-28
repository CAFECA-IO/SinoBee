# Notice / 出處聲明

**dimensional-drawing** — "Give it a UI component, get back a one-page technical drawing of it."

| | |
|---|---|
| Original author / 原作者 | Enes Ozturk ([@enesozturk](https://github.com/enesozturk)) |
| Source / 原始出處 | <https://github.com/enesozturk/dimensional-drawing> |
| This folder / 本資料夾 | the drawing skill (`skills/dimensional-drawing/`) |
| Upstream commit / 對應版本 | `5c457437c8c83524308aad3b7443e81adcc13153` (2026-10-23). Upstream has no releases yet; SinoBee version `0.1.0-main.5c45743`. |
| License / 授權 | MIT License, Copyright (c) 2026 Enes Ozturk — full text in `LICENSE` |
| Upstream credits | Apple's accessory dimensional drawings (<https://developer.apple.com/accessories/dimensional-drawings/>), Andreas Storm (<https://x.com/avstorm>) |

## What the SinoBee copy changes / SinoBee 版本的變更

This folder mirrors the upstream skill, packaged by CAFECA to ship with SinoBee as a built-in skill. Nothing in the instructions, examples, templates or adapters was modified.

1. `SKILL.md` frontmatter: added `license`, `compatibility` and the `metadata` fields SinoBee reads (version, author, source, upstream commit and license, tags, triggers, required tools).
2. `SKILL.md` body: added the "Source and credits" note under the title.
3. `LICENSE` (copied from the repository root) and `NOTICE.md` (this file).
4. Left out: the repository's `README.md` and `assets/` (preview PNG/GIF/MP4, about 18 MB). The companion skill lives in the sibling folder (`dimensional-drawing` / `dimensional-drawing-motion`).

本資料夾為上游技能的鏡像，由 CAFECA 打包、隨 SinoBee 內建。指示、範例、範本與轉接器皆未修改；僅調整上述 `SKILL.md` 的 frontmatter 與出處說明，並新增 `LICENSE` 與本檔。

To update: pull the upstream repository, copy `skills/<name>/` over this folder, re-apply the two `SKILL.md` edits above, add `LICENSE` and `NOTICE.md` back, and bump `metadata.version` / `sinobee-upstream-commit`.

Bugs and feature requests for the skill itself belong upstream: <https://github.com/enesozturk/dimensional-drawing/issues>.
