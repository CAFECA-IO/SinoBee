# Phase 2 - Subject profiling matrix

Every subject that appears (fictional character, interviewee, the vlogger, an animal, even a hero product) gets
these fields. They are the top-level constraint for every prompt, action and line you write. They prevent OOC
behaviour: a character doing what they never could or would.

| Field | Meaning | Sci-fi film | Documentary | Vlog |
|---|---|---|---|---|
| `capability_ceiling` | Knowledge, skill and physical limits; never exceed them | Low-level hacker, knows nothing of quantum physics, average stamina | Geologist: reads landforms professionally, never speculates beyond evidence | Cooking beginner: hesitant, clumsy knife work, never a chef's fluency |
| `negative_boundary` | Moral lines, stress reactions, things they never do | Flees and takes cover when attacked, never fights hand-to-hand; terrified of fire | Guarded and careful on sensitive history, never shouts or turns emotional | Introverted and gentle, no exaggerated variety-show gestures or loud laughter |
| `linguistic_blueprint` | Pace, vocabulary, verbal tics, pauses | Very fast, street slang and machine jargon, interrupts people | Slow and measured, precise written-style words, long thinking pauses | Casual fragments, habitual fillers 「其實」「就…」 |
| `kinematic_signature` | Posture, centre of gravity, unconscious micro-movements, gait | Slight hunch, eyes down, right hand keeps pressing the metal prosthetic | Sits upright, hands folded on the table, barely moves the head | One hand in a pocket, glances away from the lens, loose casual walk |

Plus the visual identity (keep these strings identical everywhere; English works best for the model):
- `appearance`: age range, ethnicity, face, hair, build, distinctive marks.
  Example: "woman in her late 20s, East Asian, round face, shoulder-length black hair with a side part, slim".
- `wardrobe`: the specific outfit, with colors and materials.
- `voice` (optional): a TTS voice name for guide tracks and narration in their voice.

## How the matrix is used
- **Prompts:** `prompting.py` puts each subject's appearance, wardrobe, body language, limits and boundaries
  at the top of every unit prompt. It also appends the kinematic signature to each action.
- **Actions you write:** pass every `action` and `performance` through the matrix before rendering.
  - "Runs to the fire to save the cat" breaks the sci-fi hacker's boundary (terrified of fire). Rewrite it
    as "shouts for help and backs away from the flames".
  - "Expertly julienned carrots" breaks the vlog beginner's capability ceiling. Rewrite it as "slowly cuts
    uneven carrot sticks, checking each one".
- **Dialogue and narration:** write lines in the linguistic blueprint, using the subject's sentence length,
  words and tics.
- **Non-human subjects:** do the same.
  - A cat's capability ceiling: cannot open doors.
  - A product's negative boundary: never shown scratched or with a competitor's logo.

## Documentaries about real people
Never invent opinions or facts for a real person. A recreation uses an unidentifiable stand-in described by the
role ("a geologist in her 50s"), not the real person's likeness. Label recreations in the edit.
