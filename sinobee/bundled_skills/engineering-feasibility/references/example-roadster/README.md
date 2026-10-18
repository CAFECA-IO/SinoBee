# Code reference: Roadster Rocket figures

**Read for code patterns only. Do not copy this folder's numbers or its model inputs into an analysis.**

These files show how a finished run builds its figures: a WebGL jet on a thrust stand (`fig1`), a tank skid
draining on dials (`fig2`), a 3D tilt rig (`fig3`), a 3D free-body jump with a roll (`fig4`), a limits panel
(`fig5`), the page assembly (`page.mjs`) and a mission search (`model.mjs`).

Its inputs are known to be wrong for the real event: it assumed 690 bar and 313 kg of air in 600 L, while the
presenters said "two of them ... 600 L ... about 250 kilos of air" (Tesla Roadster Event, 12:11) — about
450 bar. That is exactly why an example's numbers must never become a new run's inputs: take every input from
the subject's own sources (`source_facts` in feasibility.json) and recompute.

`scaffold` never copies this folder, and `check` fails a work folder whose files are identical to any file here
or whose tiles repeat this example's tiles.
