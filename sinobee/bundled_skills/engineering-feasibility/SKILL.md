---
name: engineering-feasibility
description: Analyses whether a technology shown in a video or described in text can work - extracts every testable claim, checks it against physics with a numeric model (required vs limit), gives a verdict (feasible, feasible within limits, infeasible) and explains the engineering and the physical limits. Then builds an interactive 3D / isometric anatomy page with the anatomy skill - how the system works and where it stops if feasible, or why each key property cannot be achieved if not - plus a Markdown report. Use for 技術可行性、工程原理、物理極限、這可能嗎、真的做得到嗎、科技產品或發表會影片分析, feasibility study, is this physically possible, how does it work, debunk or explain a tech demo.
license: MIT
compatibility: Needs the anatomy skill (Node.js 20.10+, Chrome or Chromium for checks) and, for video input, the youtube-analysis skill. Python 3.10+; CoolProp (pip) for real-gas tank states, optional.
metadata:
  version: 1.1.1
  author: sinobee
  sinobee-tags: engineering, physics, feasibility, analysis, 3d, animation, report, anatomy
  sinobee-triggers: 可行性, 技術可行, 工程原理, 物理極限, 做得到嗎, 可能嗎, 原理分析, 科技影片, 發表會, 黑科技, feasibility, physically possible, how does it work, engineering analysis, debunk
  sinobee-requires-tools: skill_run, read_file, write_file, web_search, web_fetch, shell
  sinobee-entry: scripts/run.py
  sinobee-checks: "scripts/run.py check {dir}"
  sinobee-checks-on: "reports/feasibility/*/report.md"
  sinobee-complexity: high
  sinobee-quickstart: "1) video: skill_run(youtube-analysis, url) and read transcript.md / text: web research; 2) skill_run(engineering-feasibility, \"scaffold reports/feasibility/<slug>\"); 3) source_facts: quote every number the source states (exact words + time) into feasibility.json BEFORE assuming anything; 4) claims, assumptions, model.mjs from those facts (calc helpers: \"calc list\"); 5) verdict; 6) figures with anatomy, then \"audit <folder>\" until every figure passes; 7) node page.mjs, report.md; 8) \"check <folder>\" until ok; 9) publish the page, deliver report."
---

# When to Use
The user gives a video (YouTube link), an article, a press release or a description of a technology and asks
whether it can work, how it works, or what limits it. Also when the user wants a tech demo explained or debunked
with animation. One subject per run; a video with several products gets one run per product the user picks.

Not for: pure market or business feasibility (no physics to test), or a figure of a known mechanism with no
feasibility question (use anatomy alone).

# Procedure
1. **Intake.**
   - Video: `skill_run(name="youtube-analysis", args="<url>")`, then read `transcript.md` to the end and every
     `sheets/*.jpg` (the youtube-analysis Procedure step 2). Note timestamps for every claim.
   - Text or a link: read it fully (`web_fetch`), then search for independent coverage.
   - Research with the web-research habits: two sources for every number that matters, note dates, separate the
     company's claims from independent measurements. Search for the published specs the source leaves out.
2. **Scaffold.** `skill_run(name="engineering-feasibility", args="scaffold reports/feasibility/<slug>")`. It finds
   the installed anatomy skill, makes `kit/` shims to it and copies the template (`model.mjs`, `fig1.mjs`,
   `fig1.live.js`, `page.mjs`, `helpers.mjs`, `feasibility.json` with TODO placeholders) and records what it
   copied in `.scaffold.json`. Worked examples are never copied: read `references/example-*/` for figure and model
   *code* patterns only. Their numbers belong to another analysis (and `check` fails a folder that reuses them).
3. **Source facts, then claims.** First fill `source_facts`: every number the source itself states, as the exact
   words from `transcript.md` or the article, its time or URL, the value and unit, and which input uses it. `check`
   finds each quote in the transcript and fails a fact the model does not use, unless `override` gives the
   evidence for a different value. A press report or an old spec that disagrees with the presenters is an
   override with a reason, never a silent replacement. For an article or a web page, save the fetched text as `sources/<name>.md`
   so check can find each quote. Then list every testable claim with its number and its
   source (`references/method.md` §1); translate marketing words into the physical requirement they imply.
4. **Model and verdict.** Write `model.mjs` (one pure function per claim returning required, limit, margin) and
   fill `feasibility.json`. Use `skill_run(… args="calc list")` and `calc <name> key=value` for tanks (real gas),
   nozzles, rockets, ballistics, landings, suction, vessels and batteries; `references/physics-checklist.md` lists
   the test and the trap per domain. Classify each claim and the whole (`references/method.md` §4–5): run the
   favourable case before calling anything impossible. The hardest problems of the subject (materials, fuel or energy cycle,
   safety case, licensing, cost) are claims too: every entry of the limits ledger is tested by a claim
   (`limits[].claims`) or says in `verdict_impact` why it cannot change the verdict. Every input comes from a source fact or is an
   assumption with a reason.
5. **Tell the user the verdict first** (a short message with the deciding numbers), then size the animation with
   anatomy's tiers: a Figure or a Hero you build; an Epic (a whole vehicle, several systems, 3D on more than one
   subject) needs the user's yes, unless they already asked for it or the run is unattended.
6. **Figures.** Pick them from the claims with `references/figure-patterns.md`: mechanism figures when feasible,
   limit and failure figures for every impossible core claim. Build each with the anatomy skill's workflow (read
   its SKILL.md first): real object, parts list, recorder + `A.settle` + `--audit` to zero, `scripts/lines.mjs`,
   3D figures with `T.build` and the orbit audit (`--quick` in the background first). Record the audits with
   `skill_run(name="engineering-feasibility", args="audit reports/feasibility/<slug>")`: it runs each figure's
   `build({ audit: true })` and writes `audits/<fig>.txt` with the figure's hash. Check accepts only these logs. Every readout and every
   motion comes from `model.mjs`.
7. **Page.** Add the figures to `page.mjs` one at a time, one edit each (scaffold first, never the whole page in one
   call: it runs into the output limit), one `.rr-figure` section and one `{ … }` live block each; the verdict
   tiles and the limits ledger come from `feasibility.json`. `node page.mjs` and `node page.mjs --dark`; capture
   desktop and 390 px in both themes with anatomy's `scripts/capture.mjs` and look at every shot.
8. **Report.** Write `report.md` from `references/report-template.md`, linking the page.
9. **Check.** `skill_run(name="engineering-feasibility", args="check reports/feasibility/<slug>")`; fix every
   problem and run it again until `status: ok`.
10. **Deliver.** Publish `page.html` where the host allows (an artifact or the document viewer), then reply with the
    verdict in one sentence, 3–6 deciding numbers, the page and the report. State the assumptions that could
    change the verdict.

# Rules
- The source's own numbers come first (`source_facts`); a worked example's or another run's numbers are never
  inputs, and memory of an earlier run on the same subject is a lead to re-verify, not a result to reuse.
- Numbers decide. No verdict without required vs limit for each core claim; no figure value that is not computed
  by `model.mjs`.
- Separate what the source claims, what is independently reported, and what you assumed. Label assumptions on
  the page and in the report.
- Be fair to the claim: test its most favourable reasonable reading before calling it impossible, and the
  conservative reading before calling it fine.
- Say "limited" with the envelope (how long, how many times, on what surface) rather than a bare yes.
- The animation explains, it does not decorate: each figure answers one claim or one limit.
- Never modify the installed anatomy skill; work in the scaffolded folder through `kit/` shims.
- Use `skill_run` for this skill's scripts; anatomy's build and check scripts run with `shell` (`node …`).

# Gotchas
Read `references/gotchas.md` before building; the ones that cost the most:
- Real gas: ideal-gas tank mass at 700 bar is ~50% too high; usable gas can be half of the stored gas.
- Body-fixed thrusters rotate with the body; landings need a braking burn in the propellant budget.
- `glLayer` turns an array of length 2–4 into a vec2–vec4: declare `vec4`, not `float[4]`.
- In 3D, objects a free group flies over must be world parts in the same live layer.
- Headless captures of shader pages are slow; use a time hash hook and long waits.

# Verification
`check` returns `status: ok` (SinoBee also runs it by itself before the task may finish, once `report.md` is
written, and sends every problem back to fix); every anatomy figure's `--audit` ends `audit passed` (3D: `turn
audit` with no `separation`, `cycles`, `posed` or `layers` problems; remaining `swaps`/`continuity` inspected at the named angle
and reported); the page has no console errors and no sideways scroll at 390 px in light and dark; every number in
the report appears in `feasibility.json` or the page and traces to `model.mjs`.

# Worked example (code patterns only)
`references/example-roadster/` shows how a finished run is built: a WebGL jet on a thrust stand, a tank skid
draining on dials, a 3D tilt rig, a 3D free-body jump with a roll, a limits panel and the page assembly. Read the
code; never take its inputs or results. Its own README explains why: its tank inputs contradict what the
presenters said on stage, which is the mistake `source_facts` exists to catch.
