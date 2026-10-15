# Phase 5 - MiniMax H3 prompting and dynamics

H3 is an omni-modal video model. It generates picture and stereo sound together: voice, effects and music. It
accepts a text prompt plus an optional first and/or last keyframe (fl2va), or up to 9 reference images (ref2va,
tagged in the prompt as `<Picture 1>` … `<Picture 9>`). `prompting.py` builds the prompt from project.json in
this order. Keep that order when you write prompts by hand:

1. **Look line:** medium, style, lens character, grain, grade, palette.
2. **Recurring subjects:** the same appearance and wardrobe strings in every unit, plus body language, skill
   level, boundaries and speech style.
3. **Location:** description, light direction and time of day, materials, palette.
4. **Scene overview:** one sentence of what happens and why.
5. **Storyboard:** `[0.0s-3.5s] Shot 1: size, angle, lens. Action (performance). Camera: move. NAME says in
   <language>, lips in sync: "line".` H3 cuts on these times, so keep them exact and add up to the unit length.
6. **Camera:** the genre's camera style, plus the motion phrase from `motion_strength`.
7. **Audio:** ambience, effects and music. Say "no singing" and keep beds low under a voice-over.
8. **Negatives:** no on-screen text or logos, no deformed hands, no morphing.

## Motion strength (expressed in words)
H3 has no numeric motion parameter, so `motion_strength` becomes words in the prompt:

| strength | wording | use for |
|---|---|---|
| 0.2-0.3 | subtle, natural, restrained motion; slow minimal camera | documentary, vlog, interviews, explainer |
| 0.4-0.6 | moderate, purposeful motion; smooth moves | drama, commercial |
| 0.7-0.8 | dynamic, energetic, bold fast moves with motion blur | action, sci-fi, music video |

Strong verbs in `action` matter more than adjectives. "Ming plucks two leaves, drops them in the basket, steps
along the row" beats "Ming works energetically".

## Dialogue and lip-sync
- On-camera lines in `dialogue` render with H3's own voice and lip movement in one pass. This is Phase 5.3;
  no separate lip-sync model is needed. Keep lines short (<= 3 s per shot), one speaker per shot, and frame
  the speaker at medium or closer.
- Narration stays off camera. The prompt says nobody speaks in that shot, and the TTS file is mixed in post.

## Modes, quality, time
| mode | when the script uses it | input |
|---|---|---|
| fl2va, text only | no keyframe and no references | prompt |
| fl2va + keyframe | `first_frame` / `last_frame` set on the unit's first / last shot | prompt + image(s) |
| ref2va | subject `sheet` and/or environment `master` exist, no keyframe, ref2va model installed | prompt + `<Picture n>` references |

| quality | canvas (16:9) | steps | 5 s render on one 48 GB GPU |
|---|---|---|---|
| draft | 1024x576 | turbo LoRA (8) | ~3-5 min |
| final | 1344x768 | turbo LoRA (8) | ~8-12 min |
| best | 1344x768 | 20 | ~25 min |

The canvas follows the aspect (9:16 -> 768x1344). Delivery size is set in post: lanczos upscale to 1080p/2160p.

## Fixing a bad take
| Symptom | Fix |
|---|---|
| Identity drifts between units | add sheets, or a keyframe with `--set`; shorten the appearance text to the distinctive traits; keep the strings identical |
| Wrong composition | set `composition` and `shot_size`; render a keyframe and edit it, or use your own image as `first_frame` |
| Over-acting | lower `motion_strength`; put the negative_boundary into `performance` ("restrained, no big gestures") |
| Physics glitches (morphing, melting hands) | simplify the action into one movement per shot; avoid close-ups of hands doing fine work; regenerate with a new seed |
| Cuts land at the wrong time | check that the storyboard times add up; give fewer shots per unit |
| The same person appears twice, or extra people appear (common with ref2va) | keep `subjects` on every shot so the prompt states the head count; references use one sheet image per subject and are skipped for wide-only units, where a face reference makes H3 add a second copy of the person; if it still happens, use a keyframe (`keyframe --set`) or a text-only render |
| Wrong language or accent | give `language_name` explicitly, e.g. "Mandarin Chinese (Taiwan accent)" |
| Unwanted text on screen | strengthen the negative line; avoid prompting signs or books with legible text |
