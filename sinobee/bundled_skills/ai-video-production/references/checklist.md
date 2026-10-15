# Pipeline checklist (run before every render and again on every QC sheet)

`project.py validate` checks the mechanical items automatically. The rest needs your judgement: look at
`qc/<unit>.jpg` with `read_file`.

| Check | Before render | On the QC sheet |
|---|---|---|
| **[腳本] Script** | Does the shot have an exact audio length as its upper bound (`audio_file` + `timing`)? Does each unit fit in 5-15 s? | Does the clip last at least the planned length? Is speech cut off? |
| **[側寫] Profile** | Do the action, performance and lines respect the capability ceiling and the negative boundary? Is the motion consistent with the kinematic signature? | Does anyone move or emote beyond their profile (big gestures from a reserved person, expert skill from a novice)? |
| **[資產] Assets** | Are the appearance and wardrobe strings identical to the subject sheet? Does the shot sit in a known environment, with light direction and palette set? | Same face, hair and clothes as the sheet? Exactly the planned people, no duplicates of the same person? Is light coming from the master's direction? Any color-temperature jump between units? |
| **[構圖] Composition** | Are shot size, angle, lens, focus and composition set? Do sizes vary between cuts? Do the 180° rule and eye-lines hold? | Is the framing as specified? Is the subject in focus? Is the perspective believable? |
| **[動態] Dynamics** | Is there one motivated camera move per shot, and does motion strength suit the genre? | Is the camera steady, not wobbling? Any morphing, melting hands, limbs passing through objects, floating feet, frozen stretches (`frozen_seconds`)? |

Also confirm:
- **Audio**: no accidental singing or speech in voice-over shots, `flags` empty, and the final loudness about
  -14 LUFS.
- **Text**: no text, logos or watermarks generated in the picture.
- **Ethics**: no real identifiable person, recreations labelled, and claims in the narration checked.

When a unit fails a check:
1. Fix the cause in project.json (the shot fields, the profile, or the keyframe), not only the prompt text.
2. Run `project.py prompts`.
3. Run `comfy_h3.py render --unit <id> --force`.

Keep the old take until the new one is better. Every take is kept in `renders.<unit>.takes`.
