// Fig 1 template · a small instrument: a base plate, a post and a face-up dial whose needle shows demand
// against a red-zone limit (the shape every "limit" figure needs). Replace the object with the subject's own
// machine; keep the recorder, settle and audit wiring. Run `node fig1.mjs --audit` until `audit passed`.
import * as k from "./kit/iso-kit.mjs";
import * as A from "./kit/audit.mjs";
import { helpers } from "./helpers.mjs";
import * as M from "./model.mjs";

export const W = 640;
export const H = 380;
const PLATE = { x: -90, y: -60, w: 180, d: 120, r: 6 };
const DIAL = [20, 0];
const DIAL_R = 26;
const DIAL_Z = 40;

export function build({ audit = false } = {}) {
  const P = k.fitProjection([...k.boxCorners(PLATE, -14, 0), [DIAL[0], DIAL[1], DIAL_Z + 6]], W, H, { pad: 26, azimuth: 42 });
  const R = A.recorder(P);
  const { at, Ln, Dots, boxAt, cylAt } = helpers(P, R);
  const back = [`<path class="iso-halo" d="${k.haloOf(PLATE, -14, 10, P)}"/>`];
  for (const [x, y] of k.corners(PLATE, 16)) {
    cylAt(x, y, 6, -14, 1.6, { tone: "lo" }, { name: "stand.foot.pad", ground: true });
    cylAt(x, y, 4.6, -12.4, 3.4, { tone: "mid" }, { name: "stand.foot" });
  }
  const screws = k.corners(PLATE, 6);
  boxAt(PLATE, -9, 9, { tone: "mid" }, { name: "stand.plate", bevel: 1.2, steps: 8, inner: Ln(k.sideSeam(PLATE, -5, P, 8), { tone: "faint" }), extra: Ln(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "faint" }) + Dots(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }) });
  cylAt(DIAL[0], DIAL[1], 5, 0, 4, { tone: "mid" }, { name: "rig.collar" });
  cylAt(DIAL[0], DIAL[1], 3, 4, DIAL_Z - 4, { tone: "mid", crease: "none" }, { name: "rig.post" });
  const ticks = k.radialTicks(DIAL[0], DIAL[1], DIAL_R - 3, 40, 5, DIAL_Z + 4, [1.4, 3], P, -0.75 * Math.PI, 1.5 * Math.PI);
  // the red zone: the limit as a heavier tick band from the limit to full scale
  const full = M.DEMO.limit * 1.6;
  const zone = [];
  for (let v = M.DEMO.limit; v <= full + 1e-6; v += full / 40) {
    const a = -0.75 * Math.PI + 1.5 * Math.PI * (v / full);
    zone.push(k.lineOnTop([DIAL[0] + Math.sin(a) * (DIAL_R - 5), DIAL[1] - Math.cos(a) * (DIAL_R - 5)], [DIAL[0] + Math.sin(a) * (DIAL_R - 2.4), DIAL[1] - Math.cos(a) * (DIAL_R - 2.4)], DIAL_Z + 4, P));
  }
  cylAt(DIAL[0], DIAL[1], DIAL_R, DIAL_Z, 4, { tone: "hi" }, { name: "rig.dial", bevel: 0.6, steps: 64, extra: Ln(k.ring(DIAL[0], DIAL[1], DIAL_R - 2, DIAL_Z + 4, P, 64), { tone: "lo" }) + Ln(ticks.minor, { tone: "lo" }) + Ln(ticks.major, { tone: "mid" }) + `<path class="iso-line rr-zone" d="${zone.join("")}"/>` + Ln(k.ring(DIAL[0], DIAL[1], 1.4, DIAL_Z + 4, P, 14), { tone: "mid" }) });
  A.settle(R, P);
  R.items.sort((a, b) => a.key - b.key);
  if (audit) A.auditOrExit(R, P, { argv: ["--audit"] });
  const needle = (frac) => {
    const a = -0.75 * Math.PI + 1.5 * Math.PI * Math.max(0, Math.min(1, frac));
    return k.lineOnTop([DIAL[0] - Math.sin(a) * 2, DIAL[1] + Math.cos(a) * 2], [DIAL[0] + Math.sin(a) * (DIAL_R - 4), DIAL[1] - Math.cos(a) * (DIAL_R - 4)], DIAL_Z + 4.2, P);
  };
  const svg = k.figureSvg({ width: W, height: H, label: "A dial gauge on a post over a base plate; the needle shows demand against the red zone that starts at the limit.", body: [...back, ...R.items.map((i) => i.svg), `<path data-part="needle" class="iso-line" data-tone="lit" data-free="start" d="${needle(0)}" style="stroke-width:1.1"/>`] });
  return { P, svg, DATA: { P, dial: { c: DIAL, r: DIAL_R - 4, z: DIAL_Z + 4.2, full } }, items: R.items.length };
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop())) {
  const out = build({ audit: process.argv.includes("--audit") });
  console.log("fig1 items", out.items);
}
