---
name: engineering-plant-diagram
description: Draws a large engineering plant (wastewater or water treatment, power, incineration, chemical or fab utilities) from one model as a zoomable SVG with a switchable isometric 3D view and top-down plan - modular facility groups built from their real structures, colour- and line-coded pipes per fluid with flow arrows, reusable defs components, names and process figures. Use for 廠區配置圖、污水處理廠、淨水場、管線流向圖、工程圖、鳥瞰圖、立體圖、俯視圖, plant layout, process flow layout, isometric plant diagram.
license: MIT
compatibility: Python 3.10+. Full check (layout + PNG previews) needs Playwright with Chromium; without it the check falls back to Node.js for the model lint only.
metadata:
  version: 1.0.0
  author: sinobee
  sinobee-tags: engineering, plant, layout, pipes, svg, isometric, diagram, water, wastewater
  sinobee-triggers: 廠區配置圖, 工程圖, 污水處理廠, 污水廠, 淨水場, 管線, 流向, 水力, 鳥瞰圖, 立體圖, 俯視圖, 等角圖, 配置圖, plant layout, process flow, piping, isometric, site plan
  sinobee-requires-tools: skill_run, write_file, read_file
  sinobee-entry: scripts/plantcheck.py
  sinobee-checks: "scripts/plantcheck.py check {file}"
  sinobee-checks-on: "reports/plant/*/plant.html"
  sinobee-quickstart: "1) write the process table (units, fluids, recycles, by-products, utilities); 2) skill_run(engineering-plant-diagram, \"scaffold reports/plant/<slug>\"); 3) rewrite model.js: facilities from real parts, orthogonal pipes in flow order; 4) skill_run(..., \"build reports/plant/<slug>/model.js reports/plant/<slug>/plant.html\"); 5) skill_run(..., \"check reports/plant/<slug>/plant.html reports/plant/<slug>/preview\"), read the flow table line by line and look at both PNGs; 6) fix until 0 errors; deliver plant.html + model.js."
---

# Engineering plant diagram (isometric 3D / plan)

One **plant model** (`model.js`) renders two views of the same plant: an isometric clay-model 3D view and a
top-down engineering plan. The output is one self-contained HTML file with a structured SVG: view toggle
(立體圖 / 俯視圖, key V), wheel zoom, drag pan, double-click reset, and "download current view as SVG".

Split of work: **you make the engineering decisions** (breakdown, scale, flow, figures); **the renderer does the
geometry** (projection, occlusion, arrows, label placement). Both views always come from the same model, so they
never disagree. Never hand-write or hand-edit the output SVG; change `model.js` and rebuild.

# When to Use
Plants with many facilities and several fluids: wastewater and water treatment, power and CHP, incineration,
chemical plants, fab utilities, campus piping. Not for single-equipment cross sections or circuit diagrams.

# Output contract (enforced by the renderer and the check)
- Canvas `viewBox="0 0 1600 900"`, no fixed width/height.
- `<defs>` holds every reusable part: one flow marker per pipe type, `sym-flow` mid-run arrow (currentColor),
  equipment symbols (pump, valve, screen, mixer, diffuser, scraper, blower, dosing, centrifuge, tree, pylon),
  3D sprites (`iso-*`), gradients and the shadow filter.
- One group per facility: plan `<g class="facility" id="fac-…" data-name data-type>`, 3D
  `<g class="facility-iso" data-fac>`.
- One path per pipe: `<path class="pipe pipe--<type>" data-from data-to>`, end marker plus automatic mid-run arrows.
- The legend lists every pipe type used; title, subtitle, design basis and north arrow sit in a shared overlay.

# Procedure

## 1. Engineering analysis (write it down before modelling)
Process table: units in order, each unit's inflows and outflows, recycles (RAS, internal recycle, supernatant),
by-products (sludge, biogas, ash), utilities (air, chemicals, power, steam). If the user gives little detail, use
the typical design for that plant type and say so in the design basis ("示意").

## 2. Facilities and layout (world metres)
- x = east, y = south, z = up. `world: [W, D]`, typically 200-400 m by 120-250 m.
- Build each facility from its real structure; one facility can combine parts (wet well `basin` + control room
  `box`; digester `cyl` + flare `stack`; building `box` + roof units `box` with `z` = building height).
- Realistic scale: clarifiers 15-40 m diameter, bioreactors 5-7 m deep, buildings 6-15 m, stacks 30-80 m.
- Main process in one west-to-east row; sludge / utilities / admin in a second row; a road and a 10 m+ pipe
  corridor between rows; 6-10 m gaps between neighbours for pipes; footprints never overlap.

| part `kind` | fields | 3D | plan |
|---|---|---|---|
| `basin` open tank | x,y,w,d,h, water, walls:[{x}/{y}], t | walls, rim, water, baffles | structure + water |
| `ctank` round tank | cx,cy,r,h, water, bridge | cylinder, water, scraper bridge | circle + water |
| `box` building | x,y,w,d,h, z, windows | three shaded faces, window band | outline (z>0 dashed = roof unit) |
| `cyl` vertical tank | cx,cy,r,h, z, dome | shaded cylinder, optional dome | circle |
| `stack` chimney / flare | cx,cy,r,h, z | tall cylinder with mouth | double circle |
| `sphere` gas holder | cx,cy,r | sphere on legs | sphere |

`water`: `water` clear, `ml` mixed liquor, `an` anaerobic, `ax` anoxic, `sl` sludge, `ch` chemical.

Equipment `equip:[{sym,x,y,z,s,rot,iso:false,plan:false}]` uses a defs symbol name. Symbols with an `iso-*`
sprite (pump, blower, tree, pylon) stand upright in 3D; the others are painted on the ground plane. `z` defaults
to the facility top; on a water surface use `z = h - 0.6`. Plan-only symbols (scrapers, indoor machines) get
`iso:false`. Keep equipment off the facility's centre line so the name has room.
New equipment: add `sym-<name>` (40x40, origin at centre) to the defs in `assets/plant-kit.js`, and `iso-<name>`
if it needs a 3D look.

## 3. Pipes and hydraulic flow
- `pts` is an orthogonal polyline and **its order is the flow direction**. The first point lies on the `from`
  facility's edge, the last on the `to` facility's edge (2.5 m tolerance; the lint fails otherwise - usually a
  reversed flow).
- Off-site ends use `ext-` ids (`ext-sewer`, `ext-river`, `ext-cake`, `ext-grid`) placed on the world boundary.
- One fluid = one `type`; never invent colours. New types go in `pipeTypes:{key:{c,w,d,n}}`.
- Route around facilities. When a crossing is unavoidable, let utility lines (air, chemicals) cross the main
  process; every pipe gets a background halo, so later pipes read as bridging over earlier ones.
- Run recycle lines (RAS, internal recycle, supernatant) outside the main line so "backwards" is obvious.
- `valves:[[x,y]]` where control matters; `label:{text, seg}` for flows and ratios (auto-placed; override with
  `plan:[dx,dy]` / `iso:[dx,dy]` only when the check still complains). `z` = pipe height in 3D (default 0.4 m,
  4-6 for racks).

| type | colour | line | fluid |
|---|---|---|---|
| sewage | brown #8D5524 | solid, thick | raw sewage |
| primary | gold #B7950B | solid | primary effluent |
| mixed | green #1E8449 | solid | mixed liquor / internal recycle |
| treated | blue #2E86DE | solid | secondary effluent |
| effluent | navy #0B3D91 | solid, thickest | final effluent |
| raw / potable / reclaimed | olive / teal / slate blue | solid | raw / drinking / reclaimed water |
| ras | purple #7D3C98 | long dash | return activated sludge |
| was | red #C0392B | short dash | waste activated sludge |
| sludge | dark brown #4E342E | dash-dot | primary, thickened, digested sludge, cake |
| supernatant | grey #7F8C8D | fine dash | supernatant / filtrate return |
| overflow | red #E74C3C | long dash | overflow / bypass |
| air | cyan #00A6C8 | dash-dot | aeration air |
| chem | orange #E67E22 | dotted | chemical dosing |
| biogas / gas | yellow | long dash-dot | biogas / fuel gas |
| steam / oil / fire / signal | grey / ink / orange / light grey | various | steam / oil / fire water / instrument |

## 4. Labels and process figures
- Each facility has `name` + `metrics` (1-3 lines; the plan shows all, the 3D callout the first). Use the
  figures engineers expect: HRT, SOR, DO, MLSS, SRT, removal %, dose, units (with standby), capacity, temperature.
- Labels are placed automatically by scoring candidate spots against placed labels, pipes, equipment, other
  facilities and the canvas edge. Use `planLabel:[dx,dy]` / `isoLabel:[dx,dy]` only as a last resort.
- `basis:[...]` = design basis (influent / effluent quality, design flow); the last line is always
  "示意圖，非施工圖".
- `roads:[{pts,w}]`, `trees:[[x,y]]` and `sprites:[{sym,x,y,s}]` add site context but must not cover facilities
  or main pipes.

Top-level model fields: `title, subtitle, world:[W,D], view:'iso'|'plan', basisTitle, basis, pipeNames,
pipeTypes, roads, trees, sprites, facilities:[{id,name,type,metrics,parts,equip,standalone,planLabel,isoLabel}],
pipes:[{id,type,from,to,pts,z,valves,label}]`. File format: `const MODEL = {...};` - see
`references/model-example-wwtp.js` (A²O wastewater plant, 15 facilities, 21 pipes).

## 5. Build and check (required, until 0 errors)
Working folder: `reports/plant/<slug>/`.
1. `skill_run(engineering-plant-diagram, "scaffold reports/plant/<slug>")` copies the example to `model.js`;
   rewrite it for the task.
2. `skill_run(engineering-plant-diagram, "build reports/plant/<slug>/model.js reports/plant/<slug>/plant.html")`
3. `skill_run(engineering-plant-diagram, "check reports/plant/<slug>/plant.html reports/plant/<slug>/preview")`
   prints the **flow table**, the model lint and layout collisions in both views, and writes `preview-iso.png`
   and `preview-plan.png`.
4. **Read the flow table line by line** against the process table from step 1 (RAS goes from the secondary
   clarifiers to the head of the bioreactor; supernatant returns to the inlet; air only leaves the blowers).
5. Look at both PNGs: proportions, occlusion, arrow directions, readable labels. ERROR must reach 0; judge each
   WARN (in 3D, "pipe passes a label box" is fine when the callout floats above the pipe).
6. Fix by moving facilities, rerouting pipes or moving equipment before reaching for manual offsets.

# Gotchas
- Pipe endpoints are checked against the facility footprint, not its centre; start on the edge facing the flow.
- `marker-mid` is never used: it points diagonally at corners. The renderer places `sym-flow` arrows on long
  straight runs instead.
- A facility that has no pipe (admin building) needs `standalone: true`, otherwise the lint warns.
- Text in the 3D view stays upright (callouts), never skewed with the ground plane.
- Without Playwright the check only lints the model; say so when delivering and suggest
  `python -m pip install playwright && python -m playwright install chromium`.

# Common plant logic
- **Wastewater (A²O / AO / SBR / MBR)**: inlet → screens / grit → primary → (anaerobic → anoxic → aerobic) →
  secondary clarifier → disinfection → outfall; RAS to the anaerobic or anoxic head, internal recycle from the
  aerobic end to anoxic, WAS to thickening, supernatant and filtrate back to the inlet; air only to aerobic tanks
  and aerated grit.
- **Water treatment**: intake → raw water pumps → rapid mix (PAC) → flocculation → sedimentation → filters →
  clear well (chlorine) → distribution; backwash water → recovery tank → head of works; sludge → thickening →
  dewatering.
- **Thermal power / CHP**: fuel → boiler → turbine → condenser → cooling tower; flue gas → SCR → dust removal →
  FGD → stack; feedwater / condensate loop; switchyard → grid.
- **Incineration**: refuse pit → furnace → boiler → semi-dry scrubber → baghouse → stack; bottom ash and fly ash
  handled on separate lines.

# Verification
- `check` exits 0, the flow table matches the process table, both PNGs were looked at.
- Deliver `plant.html` (works offline) with `model.js`; summarise facilities, pipes, main flow and assumptions,
  and mention the "download current view as SVG" button.
