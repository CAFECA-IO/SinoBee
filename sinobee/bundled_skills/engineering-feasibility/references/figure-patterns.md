# Figure patterns

Read the anatomy skill's SKILL.md and `references/craft.md` before the first figure; read `3d.md` before a 3D
figure and `webgl.md` before a shader. Everything below is about *what* to draw for a feasibility analysis.

## Choose the figures from the claims
One figure per mechanism or per deciding limit, never one per paragraph. 3–5 figures make a page.

| Verdict | Figure set |
|---|---|
| feasible / limited | 1) the core mechanism as a test rig (thruster on a thrust stand, fan car on a tilt table); 2) the energy store with gauges that drain; 3) the system doing the claimed act (3D); 4) a limits panel (charts + ledger) |
| infeasible | for every impossible core claim, one **limit** or **failure** figure: the same rig driven past the limit, with the gauge in the red zone, the part that fails, or the quantity that does not add up; plus one figure of what the system *can* do, so the reader sees the gap |

Modes in `feasibility.json` → `figures[].mode`:
- `mechanism`: how it works; the readout shows the physics live (thrust, normal force, height).
- `limit`: a gauge or ledger with **required** against **limit**; a red zone on the dial (`.rr-zone`), the needle
  passing it, the stats row turning red (`rr-bad`). The template's `fig1.mjs` is this pattern.
- `failure`: the object fails in the drawing: a free group that breaks off and falls (anatomy 3D free groups), a
  car that drops from a ceiling when suction < 1 g, a jet that separates, a vessel band that opens. Drive it from
  the model, so the failure starts exactly where the margin crosses 1.

## Map the concept to an object (anatomy's rule: the object is the explanation)
| Claim | Object |
|---|---|
| a thruster's thrust and Isp | the thruster on a carriage pushing a load cell, regulator knob = control, dial = thrust |
| stored energy / propellant budget | the bottles on a skid with a pressure and a temperature dial that fall as it drains |
| grip / suction on walls and ceilings | the vehicle on a deck that turns 0°→180° on trunnions (3D hinge) |
| a jump, flip or flight path | the vehicle as a 3D free group over the real obstacles, parked as world parts in the same live layer |
| power or battery | a pack in a rack with a power meter and a fuel-gauge needle |
| structural load | a part on a test frame with a load ram and a dial; a crack or a buckle past the limit |

## Sizes and time (anatomy tiers)
- Figure (40–80 parts): one mechanism, build it without asking.
- Hero (80–150 parts, ~2–4 h): a dense rig or one 3D subject. Say so, then build.
- Epic (whole vehicle, several systems, 3D on more than one subject, 6 h+): ask the user first; offer a Hero.

## 3D recipes that worked
- Vehicle body: convex prisms of the side profile extruded across the width, cut at the wheel arches
  (`car.mjs` `clip()`); tyres as rounds on stub axles flush with the inner body; skirts and pylons as prisms.
- A free group that flies over objects: put the objects in the **same live layer** as world parts (no group).
  A static item under a moving one cannot be proved by one plane and `T.build` throws.
- A tilt rig: a hinge group with trunnion rounds that end flush on static bearing housings; parts below the
  live layer's lowest z go in the back layer, near towers in the front layer. `T.build` names the layer to use.
- A spinning rotor with 6-fold symmetry: a hinge with range [0, 60] wrapped modulo 60.
- Jets on a moving part: `controller.anchor(group, restPoint, restDir)` each frame; draw on a front canvas;
  mask with `TURN.cover` only for jets behind the body (depth of the exit vs the body centre).
