---
name: scroll-world
description: 'Builds an immersive scroll-scrubbed "fly through the world" landing page for any industry or brand - as the visitor scrolls, a pre-rendered camera flies from outside each scene into its interior and on to the next with no cuts. Interviews the user (subject, story sections, brand kit, art direction, camera style, mobile, budget), generates cohesive scene stills and frame-locked camera clips with Higgsfield or Monid (Seedance 2.0), and wires a framework-agnostic vanilla-JS scroll-scrub engine. Use when the user wants a scrollable 3D world / diorama landing page, a scroll cinematic, or to turn a business into a fly-through hero (滾動式 3D 世界、捲動飛越、等角模型著陸頁、滾動電影感首頁).'
license: MIT
compatibility: 'SinoBee edition of scroll-world (upstream written for Claude Code and Codex). Needs, in the shell SinoBee runs: the Higgsfield CLI signed in (scene stills; also the fallback video biller) and/or the Monid CLI with a funded key (default video biller), ffmpeg/ffprobe, jq and curl; optional cwebp, Pillow (knockout) and the Codex CLI. Generation is paid and needs network access to those services.'
metadata:
  version: "0.8.0-main.71cc36d.sinobee.1"
  author: "cyw (@oso95)"
  sinobee-source: "https://github.com/oso95/scroll-world"
  sinobee-upstream-commit: "71cc36d3bb150248ae36a2c552f9cbf88802a79c"
  sinobee-upstream-license: "MIT, Copyright (c) 2026 cyw"
  sinobee-packaged-by: "CAFECA (SinoBee edition: SKILL.md condensed and adapted to SinoBee's tools; references unchanged; upstream SKILL.md kept in references/upstream-SKILL.md; see NOTICE.md)"
  sinobee-tags: "landing page, scroll, video, 3d, diorama, cinematic, higgsfield, seedance, web"
  sinobee-triggers: "滾動網頁, 捲動飛越, 3D 世界, 等角模型, 立體模型首頁, 著陸頁, 品牌首頁, 滾動動畫, 電影感網頁, scroll world, scroll-through, fly-through, diorama, landing page, scroll cinematic, higgsfield, seedance"
  sinobee-requires-tools: "shell, ask_user, write_file"
  sinobee-network: "true"
---

# scroll-world

> **Source and credits.** This skill is **scroll-world** by **cyw ([@oso95](https://github.com/oso95))**, published at <https://github.com/oso95/scroll-world> under the MIT License (`LICENSE`, Copyright (c) 2026 cyw). This is CAFECA's SinoBee edition of commit `71cc36d` (release 0.8.0 plus later fixes): these instructions are a condensed version of the upstream `SKILL.md` adapted to SinoBee's tools; the method, the prompts, the pipeline scripts and the scrub engine are the author's and unchanged. The full upstream text is in `references/upstream-SKILL.md`; see `NOTICE.md`. Report problems with the method upstream.
>
> **出處與作者。** 本技能為 **cyw（[@oso95](https://github.com/oso95)）** 所著的 **scroll-world**，原始碼於 <https://github.com/oso95/scroll-world>，以 MIT 授權釋出（版權所有 © 2026 cyw，見 `LICENSE`）。這是 CAFECA 依 commit `71cc36d`（0.8.0 版之後的修正）製作的 SinoBee 版：指示為上游 `SKILL.md` 的精簡版並改用 SinoBee 的工具；方法、提示詞、流程腳本與捲動引擎皆為原作者所有、未修改。上游全文見 `references/upstream-SKILL.md`，變更說明見 `NOTICE.md`。方法本身的問題請回報原專案。

A landing page where **scroll drives a camera**: it dives from outside a scene into its interior, then
flies on into the next scene, continuously, with no visible cuts. The visuals are AI-generated and the page
only scrubs pre-rendered video by scroll position (the technique behind Apple's scroll-through pages).

**You generate:** N scene stills → N camera clips → (architecture B) N-1 connector clips → a portable
scrub engine that plays the chain as one flight.

**The one rule that makes or breaks it:** seams must be *frame-identical*. Every chained clip starts
(and every connector ends) on the **actual rendered frame** of its neighbour, never on the original still.

The value is the pipeline, the prompts and the seam method - not a framework. `references/scrub-engine.js`
is self-contained vanilla JS (builds its own DOM, injects its own CSS) and drops into anything.

## Where things are

| Need | File (in this skill; `skill_view` lists the paths) |
|---|---|
| Intake checklist, style preamble, every prompt template | `references/prompts.md` |
| Copy-paste batch scripts (stills, dives, frames, connectors, encodes, Monid backend, 9:16 chain) | `references/pipeline.md` |
| The scrub engine and a standalone page | `references/scrub-engine.js`, `references/index-template.html` |
| Background knockout for floating scenes | `references/knockout.py` |
| The author's full instructions, every detail and measured number | `references/upstream-SKILL.md` |

Read `prompts.md` before writing any prompt and `pipeline.md` before running any batch. When this page and
the upstream text differ on a detail of the method, the upstream text wins; on how to do it in SinoBee, this
page wins.

## Running it in SinoBee

- **Paid generation - the user decides before anything renders.** Stills and clips cost Higgsfield credits
  or Monid USD. Never start a batch before the user approved the estimate (Step 1.7). Unattended run (no
  one to answer `ask_user`): do the interview defaults and the plan, then stop before generating.
- **Tools where your shell runs.** Check first: `higgsfield --version`, `higgsfield workspace list`
  (auth), `monid --version`, `monid keys list`, `monid balance`, `ffmpeg -version`, `jq --version`.
  - `higgsfield auth login` is an interactive OAuth sign-in: you cannot run it. Ask the user to sign in
    where SinoBee runs its tools, or to switch this conversation to tools on their computer.
  - Never ask for API keys or tokens in the chat. A Monid key goes in 設定 → 權限與安全 → 任務金鑰, and you
    request it with `request_secret`.
  - Missing CLIs you cannot install, or no credits: say exactly what is missing and stop.
- **Network.** In the sandbox, ask for the services' domains once with `request_network` (Higgsfield,
  Monid and the CDN hosts the result URLs point to). Downloaded result URLs expire in
  24-48 h: download at once.
- **Long jobs.** Each generation takes 3-8 minutes. Write the batch script into `/workspace/<project>/`,
  start it with `nohup bash script.sh > progress.log 2>&1 &`, and poll the log with short shell calls.
  Never block one shell call on a whole batch (its timeout kills it).
- **Questions.** Where the upstream text says `AskUserQuestion`, use `ask_user`: structured options only
  for enumerable choices (art direction, camera, mobile, tier), with an "other" path. Ask the subject as an
  open question in plain text, never as invented multiple choice.
- **Files.** Work in `/workspace/<project>/` (prompts, sources, frames in `work/`; the site in `site/` with
  `assets/`). Copy the engine and template from this skill's `references/` with the shell. Scripts are
  bash-3.2 safe; keep array loops in `#!/bin/bash` scripts run with `bash script.sh`.
- **Codex stills** (upstream option): only when `codex` is on `$PATH` where your shell runs and
  `codex login status` reports a ChatGPT login. Otherwise do not offer it.

## Step 0 - Bootstrap

Run the checks above. Monid is the default video biller (pay-per-clip USD); Higgsfield is still required
for the scene stills (`gpt_image_2`) and is the fallback biller and the only home of `kling3_0` /
`seedance_2_0_mini`. Confirm `ffmpeg`/`ffprobe`, `jq`, `curl`; optional `cwebp` or Pillow for webp and
knockout. Details: `references/upstream-SKILL.md` → Step 0.

## Step 1 - Interview the user

Ask only what you cannot sensibly default. Record each answer in `work/brief.md`.

1. **Subject** - open question: "What should this world be about? Your business, a client's, or any
   idea." Capture the industry/product, a one-line pitch and the brand name if any.
2. **Brand kit** - import from a URL (`higgsfield marketing-studio brand-kits fetch --url <site> --wait`),
   take the user's palette, or propose one. Capture 4-6 named hex values, a display name, a tone.
3. **Art direction** - default "soft matte low-poly clay diorama, isometric, tilt-shift miniature, warm
   light"; alternatives in `prompts.md`. The chosen text becomes the **style preamble**, reused byte for
   byte in every scene prompt.
4. **Camera style - always ask** (`ask_user`, by feel, one-line trade-off each), record as `CAMERA`:
   - "Fly through the world" → architecture **B** (dives + aerial connectors; default for diorama).
   - "One continuous walkthrough" → architecture **A** (one forward flight; default for photoreal).
   - "Locked isometric glide" → architecture **A + the locked-iso clause** (calmest, cheapest re-rolls).
5. **The journey** - 5-7 ordered sections from the subject's own value chain. Each: what is in the
   scene, eyebrow, headline, one line of body, 0-3 tags. The last is usually the hero product + CTA.
6. **Mobile - always ask, never generate both silently.** "Desktop only" or "Desktop + mobile (native 9:16
   portrait chain - about 2× the generation cost: state the number)". Yes → the parallel portrait chain
   (`pipeline.md` §6b). The centre-crop encodes (§6) are only a stopgap the user must approve.
7. **Budget - decided before anything renders.** Present the tiers, compute
   `N stills + (2N-1) videos [×2 with mobile] + ~15% re-roll headroom`, state the total against
   `monid balance` / Higgsfield credits, and get an explicit go.
   - Video tiers: draft `seedance_2_0_mini` 720p (~¼), standard `seedance_2_0` 1080p, alternate
     `kling3_0` 720p. On Monid the draft is the same endpoint at 480p. Measured Monid prices: 1080p dive
     ≈ $2.99, connector ≈ $1.87; N=6 desktop ≈ $27 (1080p) / ≈ $11 (720p).
   - **Calibrate, don't guess:** render ONE still and ONE clip first, compare the balance before and
     after, extrapolate, warn above ~70% of the balance.

Only models that **frame-lock seams** (accept a start image; connectors also an end image) are allowed. A
requested model that cannot is declined with one line of why, not substituted silently.

## Step 2 - Scene stills

One still per section, every prompt starting with the identical style preamble (`prompts.md`). Default
`gpt_image_2`, 3:2, 2k, high quality; run them concurrently in the background script (`pipeline.md` §1),
download each `.[0].result_url`, re-roll single failures (503 is transient). **Review the stills for
cohesion** (same angle, palette, light) before going on; regenerate an off-style one, optionally with an
approved still as `--image`.

## Step 3 - (Optional) Float the scenes

Knock out the flat background with `references/knockout.py` (border-connected flood fill) and encode to
webp, or simply make the page background the scene background colour. Keep the stills: they are the video
posters and the reduced-motion fallback.

## Step 4 - Camera architecture and model

Implement the `CAMERA` answer; never re-decide it. **One model for every chained clip** (default
`seedance_2_0`, through Monid unless the user chose Higgsfield credits).

- **A - continuous forward take.** Legs rendered **sequentially**: leg 0 starts from scene 0's still; each
  next leg's start image is the **previous leg's actual last frame** (`ffmpeg -sseof -0.15 …`), with **no
  end image**. Inside a leg the camera may orbit, crane or track (pick the move from the concept table in
  `references/upstream-SKILL.md` → Camera grammar), but every leg ends settling into a slow forward drift
  and the next begins continuing it - the camera must never reverse across a seam. Eyeball each last frame
  before chaining the next. No connectors; wire `connectors: []` and a small crossfade (~0.08).
- **B - dive-in + aerial connector** (diorama/miniature only). One dive per scene (start image = the
  solid-background still; seedance `--mode std --resolution 1080p --aspect_ratio 16:9 --duration 8`, no
  `--generate-audio`), rendered concurrently. Warn if B was chosen for a photoreal world: the pull-out
  reverses the camera at every seam and reads as a rewind.
- **Monid I/O** (`pipeline.md` §7): images go by URL through Monid's free `sfs` file system (inline base64
  is rejected; `/cat` takes the same relative path you gave `/put`), pass `ratio` explicitly (`16:9`, or
  `9:16` for the mobile chain), read `cost.value` off every run. Re-`inspect` the endpoint before each
  build - the catalog changes.

## Step 5 - Connectors (architecture B only)

For each pair, extract from the **rendered** dives:

```bash
ffmpeg -sseof -0.15 -i dive_i.mp4 -frames:v 1 -q:v 2 dive_i_last.png      # start image
ffmpeg -ss 0 -i dive_next.mp4 -frames:v 1 -q:v 2 dive_next_first.png     # end image
```

and render a 5 s connector between them with the connector prompt (`prompts.md`). Seedance lands close to,
not exactly on, the end image, so the engine adds a short crossfade - which hides a near miss, never a
wrong frame.

## Step 6 - Encode for scrubbing

Native resolution (never upscale; encode what `ffprobe` reports), crf ~20, GOP 8, light sharpen, no
audio, faststart - the `enc()` function in `pipeline.md` §5, the same for every clip. Mobile (only if
opted in): the portrait chain encoded 720 wide, GOP 4, crf 23 as `-m.mp4`, plus each portrait dive's
first frame as the `stillMobile` poster (§6b).

## Step 7 - Assemble the page

Copy `scrub-engine.js` (and `index-template.html` for a standalone page) into `site/`, then mount it:

```js
mountScrollWorld(document.getElementById('world'), {
  brand: { name: 'Pearl & Co.' }, diveScroll: 1.3, connScroll: 0.9,
  sections: [{ id: 'farm', label: 'The Farms', still: 'assets/farm.webp', clip: 'assets/vid/farm.mp4',
               scroll: 1.6, linger: 0.45, accent: '#8FB98A', eyebrow: 'From leaf to last sip',
               title: 'It starts in the hills.', body: '…', tags: ['Single-origin'] } /* …one per section */],
  connectors: ['assets/vid/conn1.mp4' /* …N-1, or [] for architecture A */],
});
```

Mobile builds add `clipMobile`, `stillMobile` and `connectorsMobile`. Pacing: per-section `scroll` (dwell)
and `linger` (≤ 0.6; the camera settles while the copy peaks). Theme with the CSS variables in the
engine's header (`--sw-bg`, `--sw-ink`, `--sw-accent`, fonts). The engine loads clips as blobs (seekable on
any host), lazy-prefetches, crossfades seams, respects `prefers-reduced-motion` and hardens phones.

## Step 8 - QA the seams (don't skip)

Serve `site/` (for example `python3 -m http.server` in the background) and check it in the browser:

- Screenshot just before and just after every seam: the two frames must show the **same composition**
  (at 720p/1080p a correct seam can read 18-25 dB PSNR from shimmer alone; judge composition, not PSNR).
  A pop means a still was used instead of the rendered frame, or the crossfade band is too short.
- No console errors; `video.seekable.end(0) > 0`; `currentTime` follows the scroll in every clip's band.
- Desktop-only build: one phone-width check (page loads, posters show, nothing overlaps). Mobile build:
  the full checklist in `references/upstream-SKILL.md` → Step 8 (throttled fast scroll, iOS first frame,
  `videoWidth < videoHeight` for the `-m.mp4` files, no jump when the URL bar collapses, rotation).
- Reduced motion falls back to the stills.

Cite the screenshots as evidence in the checklist. Report the page, the per-clip cost actually billed, and
anything left as a stopgap (crop encodes, a `null` connector).

## Gotchas

The upstream list (`references/upstream-SKILL.md` → Gotchas) is long and hard-won; read it when anything
misbehaves. The ones that bite first:

- **Seam pop** → connector endpoints were the stills, not the neighbouring clips' actual frames.
- **Stutter / camera jumps backward** → velocity reverses across a seam (inherent to B); use A for
  grounded worlds.
- **Frozen at frame 0** → the host does not serve byte ranges; keep the engine's blob loading.
- **NSFW false positives** (bed, pool, spa, wine, swim) → re-roll; strip trigger words and add "empty,
  unoccupied, no people, architectural"; re-render that one clip on `kling3_0` with the same frames; last
  resort a `null` connector (the engine crossfades the seam).
- **Seam pop only where credits were "saved"** → models were mixed mid-chain, or a start-only model was
  used for a connector.
- **Monid wrong aspect** → `ratio` not passed. **Monid poll timeout** → only the local wait died; re-poll
  the run id.
- **Kling flags** → no `--resolution`, sound defaults on (`--sound off`), duration 10 for legs.
- **zsh arrays are 1-indexed** → keep the chain loops in bash scripts.
- **Parallel `codex exec` hangs** → append `< /dev/null` to every backgrounded call.
