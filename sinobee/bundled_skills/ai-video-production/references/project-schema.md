# project.json fields

`project.py init` creates the file. Fill it with `project.py merge <folder> plan.json` (a JSON file in this
shape) or `project.py set <folder> <field.path> <value>`. The scripts keep `renders` up to date. Paths are relative to the project folder.

## Top level
| Field | Meaning |
|---|---|
| `title`, `intent` | Name, plus the user's original words (re-read them when in doubt) |
| `genre` | film, documentary, vlog, commercial, music, explainer |
| `language` / `language_name` | e.g. `zh-TW`; optional explicit name like "Mandarin Chinese (Taiwan accent)" |
| `aspect` | 16:9, 9:16, 1:1, 4:3, 4:5, 21:9 |
| `fps`, `delivery` | Final frame rate (24/30/60) and size (720p, 1080p, 1440p, 2160p) |
| `quality` | Default render quality: draft, final or best |
| `target_quality` | Quality to deliver (default final; set draft to deliver previews) |
| `skip_tts`, `skip_sheets` | true = no TTS engine / skip reference sheets (next.py stops asking) |
| `comfyui_url` | Optional; otherwise COMFYUI_URL or http://127.0.0.1:8188 |
| `models` | Optional overrides: `fl2va`, `ref2va`, `clip`, `video_vae`, `audio_vae`, `lora_fl2va`, `lora_ref2va` (file names as ComfyUI lists them) |
| `style` | `look`, `palette` (list of hex), `lut` (.cube), `camera`, `motion_strength`, `ambience`, `music`, `negative`, `bgm_file`, `voice` (narrator TTS voice) |
| `script` | `format` (screenplay, two-column, timeline) and `body` (markdown the user reads) |

## subjects[]
`id`, `name`, `alias` (the name used in English action text, e.g. Ling Feng), `kind` (person, animal, object, presenter, self), `appearance`, `wardrobe`, `capability_ceiling`,
`negative_boundary`, `linguistic_blueprint`, `kinematic_signature`, `voice`, and `sheet` (reference images;
`comfy_h3.py sheet` fills it), `sheet_seed` (seed of the sheet, kept for the production kit).

## environments[]
`id`, `name`, `description`, `light` (direction, quality, time of day), `palette`, `materials`, and `master`
(empty establishing still; `comfy_h3.py master` fills it), `master_seed`.

## shots[] (in film order)
| Field | Meaning |
|---|---|
| `id` | SH01, SH02… |
| `group` | Unit id. Consecutive shots with the same group render together (≤ 15 s) |
| `scene` | One-line scene overview (the first shot of a group provides it) |
| `env`, `subjects` | Ids from environments / subjects |
| `duration` | Seconds. Set by `project.py timing` when there is audio |
| `narration` | Voice-over text (off camera) |
| `dialogue` | `[{subject, line, language?}]` spoken on camera |
| `audio_file`, `audio_role` | voiceover / guide / recorded, set by tts.py, or yours |
| `shot_size`, `angle`, `lens`, `focus`, `composition` | Shot grammar |
| `action`, `performance` | What happens, and how it is played (profile-consistent) |
| `camera_move`, `motion_strength` | Dynamics; `motion_strength` overrides `style.motion_strength` |
| `sfx`, `music` | Per-shot sound notes |
| `first_frame`, `last_frame` | Keyframe images (first shot / last shot of the unit) |

## renders.<unit> (written by comfy_h3.py)
- Render settings: `prompt`, `seed`, `mode`, `quality`, `frames`, `canvas`.
- Progress: `status` (queued or done), `prompt_id`, `clip`.
- History: `takes[]`.
- `stale` is set when the prompt has changed since the last render.

## Production kit
`kit.py export` writes `out/<title>-kit.zip`. `kit.py import <zip> <folder>` creates `<folder>/kit/` (the kit's files),
a pre-filled `plan.json` and `kit.lock.json`.

`kit.lock.json` lists the fields restored after each merge:
- **Top level:** genre, aspect, language, fps, delivery.
- **Style:** all of it.
- **Subjects:** the identity, profiling and voice fields, plus `sheet` and `sheet_seed`.
- **Environments:** description, light, palette, materials, plus `master` and `master_seed`.
- **Remake mode only:** the render seeds.

Its `unlock` list exempts groups from this.
