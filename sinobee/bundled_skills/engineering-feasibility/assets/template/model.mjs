// The one physics model. Every number a figure draws, a readout shows or a caption states comes from here.
// Rules: SI units inside; mark every input that is not published with ASSUMED and a reason; keep functions pure
// (no DOM), so the build, the audit and the page share them. Precompute anything heavy (real-gas tables from
// `run.py calc tank`, simulations) at build time and pass tables to the page through DATA.
export const G0 = 9.80665;

// ---- Published inputs (cite the source in feasibility.json)
export const SPEC = {
  // mass: 2000,          // kg   ASSUMED: reported "4,000+ lb"
  // claim: 0.81,         // s    0–60 mph, published
};

// ---- Requirement vs limit, one function per claim. Return both sides so a gauge can show the margin.
// Example shape:
// export function hoverTime(impulse, mass) { return { required: 5, limit: impulse / (mass * G0), unit: "s" }; }

// ---- Example claim check used by the template figure: a demand against a hard limit
export const DEMO = { limit: 100, unit: "kN", label: "load" };
export function demand(share) {
  // share 0..1 from the figure's control; replace with the subject's real relation
  const required = 20 + 140 * share;
  return { required, limit: DEMO.limit, margin: DEMO.limit / required, ok: required <= DEMO.limit };
}
