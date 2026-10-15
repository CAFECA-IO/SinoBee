---
name: ai-video-production
description: Turns a one-line idea into a finished AI video (film, documentary, vlog, commercial, music or explainer) with the Universal AI Video Pipeline - script and audio-first timing, subject profiling matrix, consistency assets, shot grammar and keyframes, camera and subject dynamics, then post-production - rendered with MiniMax H3 on a ComfyUI server and assembled with ffmpeg. Use when the user asks to make, shoot, generate or edit a video, short film, clip, trailer, ad, documentary or vlog (做影片、拍短片、生成影片、短影音、紀錄片、廣告片、預告片、MV).
license: MIT
compatibility: Needs ffmpeg/ffprobe and a ComfyUI server with MiniMax H3 (fl2va; ref2va optional) reachable at COMFYUI_URL (default http://127.0.0.1:8188, e.g. through an SSH tunnel to a GPU server). TTS via edge-tts, macOS say or espeak-ng is optional.
metadata:
  version: 1.0.0
  author: sinobee
  sinobee-tags: video, film, documentary, vlog, comfyui, minimax-h3, generation
  sinobee-triggers: 影片, 短片, 拍片, 生成影片, 做影片, 短影音, 紀錄片, 廣告, 預告片, 動畫短片, MV, vlog, video, film, clip, trailer
  sinobee-requires-tools: skill_run, read_file, write_file
---

# When to Use
The user wants a video made from an idea ("幫我做一支 30 秒介紹台灣茶的影片", "拍一段貓咪的 vlog",
"做個產品廣告"). They should only need to say the intent; you make every craft decision, show the plan once,
and deliver the film. Not for editing the user's own camera footage only (use ffmpeg directly for that).

# Pipeline (run the phases in order; never render before Phases 1-4 are filled)
```
[1] Script & audio-first timing -> [2] Profiling matrix -> [3] Asset anchors (subject / location / light)
        -> [4] Shot grammar & keyframes -> [5] Camera & subject dynamics (render) -> [6] Post & assembly
```
All state lives in one file, `<project>/project.json`; the scripts read and update it. Run them with
`skill_run name=ai-video-production script=scripts/<file>.py args="…"`. Every script prints JSON and has `--help`.
`references/project-schema.md` documents every field.

# Procedure
0. **Brief from the intent** - decide without asking (ask only if the subject itself is unclear):
   genre (film / documentary / vlog / commercial / music / explainer), length (default 20-30 s, max ~60 s), aspect
   (16:9; 9:16 for 短影音/Reels/Shorts/抖音; 1:1 for feeds), language (the user's), tone, and a one-line logline.
   Then `project.py init videos/<slug> --genre <g> --title "…" --aspect 16:9 --language zh-TW --intent "<user words>"`.
   Run `comfy_h3.py check videos/<slug>` early: if ComfyUI is unreachable, tell the user how to start it or open
   the tunnel before you invest in the plan.
1. **Script & timing** (`references/script-formats.md`):
   - Write `script.body` in the genre's format: screenplay (film/commercial), two-column narration | B-roll
     (documentary/explainer), timeline (vlog/music).
   - Break it into `shots` of 2-6 s.
   - Put consecutive shots in the same place into one `group` (one H3 render, <= 15 s, real cuts inside).
   - Narration goes in `narration` (voice-over). On-camera lines go in `dialogue`.
   - Audio first: `tts.py videos/<slug>` (or the user's recordings as `audio_file`), then
     `project.py timing videos/<slug>`. This locks every shot length to its audio (3.8 s of speech -> 4.0 s shot).
2. **Profiling matrix** (`references/profiling-matrix.md`):
   - Every subject - character, interviewee, the vlogger, even an animal or a product - gets `appearance`,
     `wardrobe` and all four dimensions: `capability_ceiling`, `negative_boundary`, `linguistic_blueprint`,
     `kinematic_signature`.
   - These constrain every prompt and action you write. A novice cook never has a chef's knife skills; a reserved
     interviewee never shouts.
3. **Asset anchors**:
   - Each `environment` needs `description`, `light` (key light direction, time of day), `palette` and `materials`.
   - Set `style.palette` (3-5 hex colors) and `style.look`.
   - Then, for anything that recurs across units:
     - `comfy_h3.py sheet videos/<slug> --subject S1` builds the subject sheet (front, 3/4, back, hard light).
     - `comfy_h3.py master videos/<slug> --env E1` builds the empty establishing still.
   - Look at every image with `read_file`. Regenerate with another `--seed` until identity, wardrobe and light
     match the profile. These images become the references for every later render.
4. **Shot grammar & keyframes** (`references/shot-grammar.md`):
   - Every shot gets `shot_size`, `angle`, `lens`, `focus`, `composition` and `action`.
   - Vary sizes: wide -> medium -> close-up. Keep the 180° rule inside a scene.
   - For units whose opening image matters (recurring subject, product, final quality), run
     `comfy_h3.py keyframe videos/<slug> --unit SC01 --set`, check the still and redo it if wrong. It becomes the
     unit's first frame (image-to-video).
5. **Dynamics & render** (`references/h3-prompting.md`):
   - Give each shot a `camera_move` from the standard vocabulary and an `action` written through the subject's
     kinematic signature: "turns with his habitual stoop", not just "turns".
   - Set `motion_strength`: documentary/vlog/explainer 0.2-0.4; action, music and sci-fi 0.6-0.8.
   - Run `project.py prompts`, then `project.py validate`. It must report no errors; fix warnings when you can.
   - Show the user the plan once: logline, script, the shot list with times, and the subject sheets. Unattended,
     go on.
   - Render previews: `comfy_h3.py render videos/<slug> --all --quality draft` (pass `timeout` 3600 to skill_run,
     or add `--no-wait` and later `comfy_h3.py collect`).
   - `qc.py project videos/<slug>`, then look at every `qc/<unit>.jpg` and work through
     `references/checklist.md`.
   - Fix a failed unit by editing its shot fields (or seed, or keyframe), then
     `render --unit <id> --force` (a new take).
   - When the drafts pass, run `render --all --quality final --force`.
6. **Post & assembly**:
   - `assemble.py videos/<slug> --grade film` (or `--lut style.cube`). Add `--bgm music.mp3` if the user gave
     music.
   - Add `--subs burn` for social video, `--delivery 1080p|2160p`, and `--fps 30|60` for vlogs (motion
     interpolation is slow).
   - Then `qc.py project` again and look at `qc/final.jpg`.
   - Deliver `out/final.mp4` (+ `out/final.srt`) with a two-line summary: what it shows, length, and what to
     change for another take.

# Defaults that make a good film from a vague request
- Open on an establishing wide shot, end on a held image. Cut on action or on the narration beat.
- Draft everything first (fast, ~0.75 canvas, turbo); final quality only after QC.
- One location and lighting per group; never mix day and night inside a group.
- No on-screen text from the model. Titles and subtitles come from post (`--subs`).

# Gotchas
- **H3 clip length:**
  - H3 renders 5.2-15.1 s per unit, on a 17k+5 frame grid at 24 fps.
  - Shorter shots are rendered at 5.2 s and trimmed. Longer groups must be split (`validate` flags them).
- **Render time:** on one 48 GB GPU, draft 5 s ≈ 3-5 min and final 5 s ≈ 8-12 min. Plan the number of units;
  reuse good takes.
- **Identity drift between units** is the most common failure. Use subject sheets, a ref2va model or a keyframe
  `--set`, keep appearance strings identical, and never paraphrase the profile in shot actions.
- **Hands, small text and crowds** deform. Frame hands out or keep them simple; avoid readable signs. Use medium
  rather than extreme-wide shots for faces.
- **A long `skill_run` hits its default timeout:** pass `timeout`, or use `--no-wait` + `collect`. The job keeps
  running in ComfyUI either way.
- **Real people:** never depict real, identifiable people (public figures or private individuals) or make footage
  that could pass as real news. Label AI recreations in documentaries.

# Verification
- `project.py validate` has no errors.
- Every unit has a clip and a QC sheet you looked at.
- `out/final.mp4` duration matches `project.py plan` total_seconds (±0.2 s), and audio loudness is near -14 LUFS.
- The user received the file path and the summary.
