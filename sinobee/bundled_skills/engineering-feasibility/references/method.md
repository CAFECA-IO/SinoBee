# Feasibility method

The verdict is a calculation, not an impression. Every claim becomes a pair of numbers, **required** and
**limit**, with the physics that links them, and the verdict follows from the margins.

## 1. Extract claims
From the transcript, the stills, the description and the press coverage, list every statement physics can test.
Write each as one line with its number and its source time or URL:

| id | claim (as stated) | source | number | test |
|---|---|---|---|---|
| C1 | "flies for a short time" | 12:40 | ? | hover time from stored impulse |
| C2 | "holds 2 g, even upside down" | Moravy, 6:10 | 2 g | ΔP × sealed area vs 1 g |

- A claim with no number gets the number the demonstration implies (a jump over a 4 m truck needs ~5 m apex).
- Mark the claims the product **depends on** as `core: true`; side remarks are `core: false`.
- Marketing words ("flies", "rocket") are claims too: translate them into the physical requirement they imply.

## 2. Fill the inputs
- Published numbers first, two sources each when they matter; note the date.
- Everything else is an **assumption** with a reason (a reported weight, a size scaled from a photo, a typical
  efficiency). Assumptions go in `feasibility.json` and on the page; never present them as published.
- Use real-property data where it decides the answer (gas density at 700 bar is 40% off ideal; use
  `run.py calc tank`, which uses CoolProp when installed).

## 3. Model
`model.mjs` holds one pure function per claim that returns `{ required, limit, margin }` and everything a figure
or readout needs. Simulations (a jump, a blowdown, a landing) run at build time and pass sampled frames to the
page. Search parameters (burn times, lead distances) with a small brute-force loop to find the least-cost
manoeuvre that satisfies the claim, as `mission()` does in the Roadster example.

## 4. Classify each claim
`margin = limit / required` (for an upper bound such as stress or temperature: `limit / actual`).

| status | rule |
|---|---|
| `ok` | margin ≥ 1.5 with conservative assumptions |
| `limited` | margin 1–1.5, or works only for a short time / once / in a narrow envelope (state the envelope) |
| `impossible` | margin < 1 under the most favourable assumptions you can defend, or a conservation law is broken |
| `unverified` | no physical test is possible from public data (say what data would settle it) |

Run the favourable case for `impossible` and the conservative case for `ok`: the verdict must survive both.

## 5. Overall verdict
- `feasible`: every core claim `ok`.
- `limited`: no core claim impossible, at least one limited. State the envelope in one sentence (seconds, cycles,
  surfaces, loads).
- `infeasible`: at least one core claim impossible. Name the law or the material limit that breaks it.

## 6. The limits ledger
At least three physical limits with numbers, each answering "what stops it from doing more?": energy or
propellant budget, power, thermal (heating, freezing, condensation), structural loads (landing g, stress),
control (thrust-to-weight, torque, latency), safety (stored energy), and refuelling or recharging cost.
