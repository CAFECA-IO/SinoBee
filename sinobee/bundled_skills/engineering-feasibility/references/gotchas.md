# Gotchas (each one cost time in a real run)

## Model and numbers
- Real gas above ~200 bar: the ideal-gas law overstates the mass of air in a tank by tens of percent (about 50% at
  700 bar). Use `calc tank`, and when the source states the mass, derive the pressure from it, not the reverse.
- An example's or an old article's numbers are not this subject's inputs. Quote the source (`source_facts`) first;
  a disagreeing figure from elsewhere is an `override` with its evidence.
- Usable ≠ stored: a regulator needs the tank ~10 bar above its set point; with adiabatic blowdown half the gas
  can stay in the bottle. The tank also cools (300 → 190 K), which cuts Isp as it drains.
- Body-fixed thrusters rotate with the body. Plan roll pulses while the body is near level; inverted "lift"
  thrusters push down.
- Clear obstacles with the lowest corner of the rotated cross-section, not the centre of gravity.
- Landing without a braking burn is 20–30 g; reserve propellant for it and show it in the budget.
- Search manoeuvre parameters (burn times, run-up) with a brute-force loop over the model; do not hand-tune.

## Anatomy build
- `A.overlapsOf(items, P)` returns `{ pairs }`; use the true overlaps to decide which back-layer items must move
  to the front SVG around a canvas. A screen-box test promotes the base plate and the canvas disappears.
- A flat saddle under a cylinder must be tangent (top at `z = axis − r`), never cut to the chord.
- Ellipsoidal heads and other curved bodies need an analytic radius and slope (`helpers.curved`), or `wobble`
  fails.
- A pipe ending on a ball tee: compute the rim distance with that pipe's own radius.
- Flanges on sloped faces sink; seat nozzles along the face normal.
- `glLayer` sends an array of length 2–4 as a `vec2`–`vec4`: declare `uniform vec4 uOn;`, not `float uOn[4]`, or
  the uniform silently stays 0.
- A jet under a car is hidden by the car from above; show where it lands (a ground sheet, masked by the body).
- Long readouts overflow the card corner: keep the corner short and put details in a `.rr-stats` row.
- Live scripts on one page: wrap each figure in `{ … }`; `kitScript() + glScript() + turnScript()` coexist.

## 3D mode
- `T.build` errors are instructions ("move it to a layer under rig"): follow them literally.
- Static parts taller than the live layer's lowest z and beside it cannot be proved: shorten them or move them.
- Respect the audit hook: `TURN.mount(…, { onHold })` must stop the figure's own loop (`st.hooked = true`), or
  `drive.mjs ["turn", …]` shots show the figure's state instead of the requested angle.
- The orbit audit takes minutes: run `--quick` in the background first; read `swaps` and `continuity` and look at
  the named angle before changing geometry (flush coaxial parts give sub-pixel swaps; fast clips give tone steps).

## Capture and delivery
- Headless Chrome renders heavy shaders at a few fps: waits must be long, and `drive.mjs` can be killed for
  memory. For a time-based figure add a hash hook (`#t4=2.4`) and use `capture.mjs` on that URL.
- Artifact pages: strip `<html>/<head>/<body>`, wrap the page in `<div class="iso" data-theme>`, set the theme from
  `document.documentElement.dataset.theme` or `prefers-color-scheme` before the module runs, and remove the kit's
  `body{…}` and `html{color-scheme}` rules.
