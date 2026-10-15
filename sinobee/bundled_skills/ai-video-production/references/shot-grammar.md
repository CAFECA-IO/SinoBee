# Phase 4 - Shot grammar

Fill these fields for every shot. Use the standard terms. `validate` warns about non-standard ones because the
model understands them best.

| Field | Values | Notes |
|---|---|---|
| `shot_size` | extreme wide, wide, medium wide, medium, medium close-up, close-up, extreme close-up, over-the-shoulder, insert, pov | Faces read well from medium close-up inward. Wide shots carry geography, not identity. |
| `angle` | eye level, low angle, high angle, dutch angle, bird's-eye, overhead, worm's-eye | Low = power, high = vulnerability, dutch = unease (use rarely). |
| `lens` | 14mm, 24mm, 35mm, 50mm, 85mm, 135mm, macro 100mm | Wide = space and distortion, 85mm+ = compressed background and portraits. |
| `focus` | the subject or object in focus, plus the depth of field | "her eyes, shallow depth of field", "deep focus". |
| `composition` | rule of thirds, centered symmetry, leading lines, frame within a frame, negative space left/right | Say where the subject sits and where they look. |
| `camera_move` | static, pan, tilt, push in, pull out, dolly, tracking, truck, crane up/down, orbit, handheld, gimbal follow, whip pan, rack focus | One move per shot; give the direction and speed ("slow push in"). |

## Sequencing that reads as "good"
- Establish, then go closer: wide (where) -> medium (who and what) -> close-up (feeling or detail) -> insert
  (the telling object).
- Alternate sizes between cuts. Two consecutive shots of the same size and angle look like a jump cut.
- 180° rule: in a dialogue or chase, keep the camera on one side of the line of action. Screen direction must
  stay consistent (a subject leaving frame right enters the next shot from the left).
- Eye-line: if A looks camera-left, B looks camera-right in the reverse shot.
- Motivate camera moves: push in on a realization, track to follow action, crane up to end a sequence.
- Hold the last shot 0.5-1 s longer than its audio.

## Genre presets
| Genre | Lenses | Moves | Feel |
|---|---|---|---|
| Documentary | 24-50mm, occasional 85mm interviews | handheld, slow push in, static interviews | observational, natural light |
| Vlog | 24-35mm (phone-like) | handheld, selfie, gimbal follow | casual, slightly imperfect |
| Film | 35-85mm, anamorphic | dolly, crane, tracking | motivated light, shallow depth |
| Commercial | 50-100mm macro for products | slider, orbit, push in | clean, high detail |
| Music video | anything, bold | whip pan, orbit, fast tracking on the beat | stylized color |

## Keyframes (first frames)
`comfy_h3.py keyframe <project> --unit <id> --set` renders the first frame of a unit at full resolution from
everything you specified (plus subject sheets and the location master as references when ref2va is
installed), then sets it as that unit's `first_frame`. The render is then image-to-video from that exact
image: composition, identity and light are locked.

- Use keyframes for any unit with a recurring subject, a product, or a specific composition.
- Check every keyframe with `read_file` before rendering. A wrong keyframe wastes a whole render.
- Your own images also work: a sketch or photo can serve as `first_frame` (or `last_frame` for a hard end pose).
  This covers the Layout/Previz step: block the composition in any tool, export a frame, and use it.
