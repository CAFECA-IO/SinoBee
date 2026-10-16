---
name: ai-video-production
description: Turns a one-line idea into a finished AI video (film, documentary, vlog, commercial, music or explainer) with the Universal AI Video Pipeline - script and audio-first timing, subject profiling matrix, consistency assets, shot grammar and keyframes, camera and subject dynamics, then post-production - rendered with MiniMax H3 on a ComfyUI server and assembled with ffmpeg. Use when the user asks to make, shoot, generate or edit a video, short film, clip, trailer, ad, documentary or vlog (做影片、拍短片、生成影片、短影音、紀錄片、廣告片、預告片、MV).
license: MIT
compatibility: Needs ffmpeg/ffprobe and a ComfyUI server with MiniMax H3 (fl2va; ref2va optional) reachable at COMFYUI_URL (default http://127.0.0.1:8188, e.g. through an SSH tunnel to a GPU server). TTS via edge-tts, macOS say or espeak-ng is optional.
metadata:
  version: 1.3.0
  author: sinobee
  sinobee-tags: video, film, documentary, vlog, comfyui, minimax-h3, generation
  sinobee-triggers: 影片, 短片, 拍片, 生成影片, 做影片, 短影音, 紀錄片, 廣告, 預告片, 動畫短片, MV, vlog, video, film, clip, trailer
  sinobee-requires-tools: skill_run, read_file, write_file
  sinobee-entry: scripts/run.py
  sinobee-workflow: video-production
  sinobee-complexity: high
  sinobee-quickstart: "1) write videos/<slug>/plan.json (shape of assets/example-film-duel.json); 2) skill_run(name=ai-video-production, args=videos/<slug>); 3) repeat step 2 while status is working or needs_content (fix plan.json first); 4) status done: deliver the film path."
---

# When to Use
The user wants a video made from an idea ("幫我做一支 30 秒介紹台灣茶的影片", "拍一段古裝武打片", "做個產品廣告").
They only state the intent: you make every craft decision and deliver `out/final.mp4`. Do not use it to edit the
user's own camera footage (use ffmpeg directly for that).

# The easiest way: the fixed workflow (prefer this)
```
run_workflow(name="video-production", inputs={"intent": "<the user's request, in their words>"}, wait=false)
```
Optional inputs: `aspect` (16:9 / 9:16 / 1:1), `length` (e.g. "30 秒"), `draft_only` (true = faster, no final render).

The workflow then runs on its own:
1. A planner writes and checks the whole plan (script, profiles, locations, shots).
2. It renders and QCs drafts and makes a draft preview.
3. It asks the user to approve the costly final render. A "no" delivers the drafts.
4. It renders the final version and assembles it.

After starting it, tell the user it is running. Progress, the preview, approvals and the finished film all
appear in the 工作 tab. You do not need to read the rest of this file unless the user wants to change
something by hand.

# The short way without the workflow (do exactly this)
1. Write the whole film as ONE JSON file with `write_file`: `videos/<slug>/plan.json`. Copy the structure of
   `assets/example-film-duel.json` (read it: `skill_view(name="ai-video-production",
   path="assets/example-film-duel.json")`). It holds title, genre, aspect, language, style, script.body,
   subjects (with the four profiling fields), environments and shots.
2. Run the skill on the folder:
   `skill_run(name="ai-video-production", args="videos/<slug>")`
   (no `script` needed: this runs `scripts/run.py`, the autopilot).
3. Read the answer's `status` and act on it:

   | status | what you do |
   |---|---|
   | `needs_content` | Fix plan.json: fill exactly the fields listed in `missing`, then repeat step 2. |
   | `working` | Renders are running on the GPU server. Repeat step 2 with the same args until the status changes (each call works up to ~4 min). |
   | `blocked` | Stop and tell the user what to start (the `what` field, e.g. ComfyUI or an SSH tunnel). |
   | `error` | Read `error`, fix the cause in plan.json, and repeat step 2. Never repeat an identical failing call. |
   | `done` | Send the user `film` (the .mp4 path). Look at the `review` images with read_file to describe it. |

The autopilot does every mechanical step itself:
- narration audio and audio-locked timing;
- prompts and reference sheets;
- draft renders, QC with one automatic retake, final renders;
- assembly, mixing, subtitles and the final QC.

Your job is the content (plan.json) and the conversation with the user.

Rules that prevent the usual mistakes:
- **Only plan.json.** Put everything in `videos/<slug>/plan.json`. Other files (characters.txt, script.md…)
  are ignored.
- **Use skill_run.** Never run the scripts with `shell` and never invent commands ("profiling", "render
  project.json"…).
- **What `script` is.** `script` is only ever a file inside this skill (scripts/….py). Folders and project
  files go in `args`.
- **Don't stop to ask.** Do not stop after a step to ask whether to continue; keep calling until `done`,
  `blocked` or a real question for the user.

# Manual control (optional)
The single steps are separate scripts. Call them as
`skill_run(name="ai-video-production", script="scripts/<file>.py", args="<command> videos/<slug> …")`.

| Script | Commands |
|---|---|
| `project.py` | init, set, merge, remove, timing, prompts, validate, plan |
| `tts.py` | `<folder>` or `check` |
| `comfy_h3.py` | check, sheet, master, keyframe, render, collect, generate |
| `qc.py` | project, clip |
| `assemble.py` | `<folder>` |
| `next.py` | `<folder>`: prints the exact next manual call |

Use `project.py set videos/<slug> <field.path> <value>` for small edits. The field reference is
`references/project-schema.md`.

# Pipeline
```
[1] Script & audio-first timing -> [2] Profiling matrix -> [3] Asset anchors (subject / location / light)
        -> [4] Shot grammar & keyframes -> [5] Camera & subject dynamics (render) -> [6] Post & assembly
```

# Procedure (what to put in plan.json, phase by phase)
1. **Brief:** decide everything from the intent without asking, unless the subject itself is unclear:
   - genre: film, documentary, vlog, commercial, music or explainer;
   - length: default 20-30 s, max ~60 s;
   - aspect: 16:9 by default, 9:16 for 短影音/Reels/Shorts, 1:1 for feeds;
   - language and tone.

   Put these in plan.json: `title`, `genre`, `aspect`, `language`, and `intent` (the user's words).
2. **Fill in the rest of plan.json.** It covers Phases 1-4 in one file:
   - `script.body`: the script as text, in the genre's format (`references/script-formats.md`).
   - `subjects[]`:
     - `name` (+ `alias` = the name you use in English actions), `appearance`, `wardrobe`;
     - the four profiling fields `capability_ceiling`, `negative_boundary`, `linguistic_blueprint`,
       `kinematic_signature` (`references/profiling-matrix.md`).
     - Every action you write must respect them.
   - `environments[]`: `description`, `light` (direction and time of day), `palette`, `materials`.
   - `style`: `look`, `palette` (3-5 hex colors), `camera`, `motion_strength`, `ambience`, `music`, `negative`.
   - `shots[]`, 2-6 s each:
     - `group` (consecutive shots in one place, ≤ 15 s, rendered together), `env`, `subjects`, `duration`;
     - shot grammar (`references/shot-grammar.md`): `shot_size`, `angle`, `lens`, `focus`, `composition`;
     - `action` (English, one clear movement), `performance`, `camera_move`;
     - `narration` (voice-over) or `dialogue` (on camera), plus `sfx` and `music`.
3. **Run the autopilot** (`skill_run(name="ai-video-production", args="videos/<slug>")`) until `done`. It
   performs, in order:
   - `tts.py` for narration and dialogue, then `project.py timing` (audio-first shot lengths);
   - `project.py prompts`;
   - `comfy_h3.py sheet` / `master` for recurring subjects and locations;
   - `comfy_h3.py render --quality draft`, then `qc.py project`;
   - the final render, then `assemble.py`, then the final QC.

   Also:
   - **Look at images.** After sheet, keyframe and QC steps, `read_file` the images
     (refs/*.png, keyframes/*.png, qc/*.jpg) and check them against `references/checklist.md`.
   - **Fix a bad unit.** Change its shot fields (fewer moves, simpler action, a different seed or keyframe; see the
     fix table in `references/h3-prompting.md`), then `comfy_h3.py render videos/<slug> --unit <id> --force`.
   - **Optional keyframe.** For a unit whose opening image must be exact, run
     `comfy_h3.py keyframe videos/<slug> --unit <id> --set` before rendering.
   - **Long renders.** Several minutes per unit is normal. Pass `timeout` as next.py says. If a call times
     out, the render keeps going in ComfyUI; run next.py again (it will say `collect`).
4. **Deliver.** Send `videos/<slug>/out/final.mp4` (and `final.srt`) with two lines: what it shows and its
   length, and what could be changed in another take.

# Gotchas
- **H3 clip length:**
  - One render (a `group`) lasts 5.2-15.1 s; shorter groups are rendered at 5.2 s and trimmed.
  - Split groups longer than 15 s.
  - Action and fight scenes: 2-3 s shots, one movement per shot, at most 2 fighters, each holding exactly
    one weapon. Say the weapon's look in the wardrobe text.
- **Keep identity:**
  - Same `appearance`/`wardrobe` strings in every shot, and the names in actions must match `name`/`alias`.
  - Use sheets or keyframes for recurring people.
- **No generated text:** the model must not draw on-screen text. Titles and subtitles come from post
  (`assemble.py --subs burn`).
- **Hands, crowds and readable signs** deform. Frame them out or keep them simple.
- **Real people:** never depict real, identifiable people, or make footage that could pass as real news.
  Label AI recreations in documentaries.

# Verification
- `next.py` reports `done: true`.
- You looked at every QC sheet.
- `out/final.mp4` exists, its duration matches `project.py plan` total_seconds (±0.2 s), and the user got the
  path.
