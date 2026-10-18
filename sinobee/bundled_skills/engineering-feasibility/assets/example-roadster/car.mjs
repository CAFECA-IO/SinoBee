// The Roadster as convex live parts for the 3D mode: a wedge body cut at the wheel arches, four wheels on
// stub axles, side skirts, suction-fan vents with spinning rotors, a rear wing, and (optionally) the ten
// thruster nozzles. World units are centimetres; x forward, y left, z up; the tyres touch z = z0.
import * as G from "./kit/lathe.mjs";
import * as M from "./model.mjs";

// Side profile (x, z above the road), convex, wedge-shaped (Tesla: "Cyberfied", wedge silhouette).
export const PROFILE = [[-218, 10], [220, 10], [228, 26], [214, 50], [170, 70], [100, 86], [20, 104], [-30, 110], [-110, 106], [-190, 84], [-222, 52], [-224, 24]];
export const HALF_W = 83;
export const WHEEL = { r: 32, w: 26, x: 135, y: 69 };
export const INNER = 55.5;
export const ARCH = 70;

// z of the body's top line at x (for parts that sit on it)
export function topAt(x) {
  const top = PROFILE.slice(2, 11);
  for (let i = 0; i < top.length - 1; i++) {
    const [ax, az] = top[i], [bx, bz] = top[i + 1];
    if ((ax - x) * (bx - x) <= 0) return az + ((bz - az) * (x - ax)) / (bx - ax);
  }
  return 10;
}

// Clip a convex polygon to x0 ≤ x ≤ x1, z0 ≤ z ≤ z1 (Sutherland–Hodgman).
function clip(poly, x0, x1, z0, z1) {
  let out = poly;
  const edges = [[(p) => p[0] - x0], [(p) => x1 - p[0]], [(p) => p[1] - z0], [(p) => z1 - p[1]]];
  for (const [f] of edges) {
    const next = [];
    for (let i = 0; i < out.length; i++) {
      const a = out[i], b = out[(i + 1) % out.length];
      const fa = f(a), fb = f(b);
      if (fa >= 0) next.push(a);
      if ((fa >= 0) !== (fb >= 0)) { const t = fa / (fa - fb); next.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); }
    }
    out = next;
  }
  // drop near-duplicate points
  return out.filter((p, i) => { const q = out[(i + 1) % out.length]; return Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-3; });
}

export function carParts(T, { group, layer, z0, fans = [null, null], thrusters = false, prefix = "car", ox = 0 }) {
  const P = T.P;
  const side = (y0, y1) => ({ o: [ox, 0, z0], a: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1], s0: -y1, s1: -y0 });
  const body = (name, poly, y0, y1, extra = {}) => {
    const f = side(y0, y1);
    return T.prism({ name: `${prefix}.${name}`, group, layer, F: { o: f.o, a: f.a, u: f.u, v: f.v }, polygon: poly, s0: f.s0, s1: f.s1, tone: "hi", crease: "lo", bevel: 0.6, lit: true, ...extra });
  };
  const xw = WHEEL.x, rw = WHEEL.r;
  const xa = xw - 35, xb = xw + 35;
  const nearSide = (rules) => ({ rules: rules.map((pts) => ({ points: pts.map(([x, z]) => [-HALF_W, x, z]), normal: [0, 1, 0], tone: "lo" })) });
  // window and door lines on the near (+y) side of the middle section
  const dlo = [[90, 84], [20, 100], [-40, 104], [-96, 98], [-96, 76], [70, 76], [90, 84]];
  const door = [[60, 74], [60, 22], [-80, 22], [-80, 74]];
  const parts = {};
  parts.rear = body("body.rear", clip(PROFILE, -230, -xb, -10, 200), -HALF_W, HALF_W);
  parts.rearArch = body("body.archR", clip(PROFILE, -xb, -xa, ARCH, 200), -HALF_W, HALF_W);
  parts.rearIn = body("body.innerR", clip(PROFILE, -xb, -xa, -10, ARCH), -INNER, INNER, { bevel: 0 });
  parts.mid = body("body.mid", clip(PROFILE, -xa, xa, -10, 200), -HALF_W, HALF_W, { details: nearSide([dlo, door]) });
  parts.frontArch = body("body.archF", clip(PROFILE, xa, xb, ARCH, 200), -HALF_W, HALF_W);
  parts.frontIn = body("body.innerF", clip(PROFILE, xa, xb, -10, ARCH), -INNER, INNER, { bevel: 0 });
  parts.front = body("body.front", clip(PROFILE, xb, 240, -10, 200), -HALF_W, HALF_W, { details: { rules: [{ points: [[-HALF_W, 222, 34], [-HALF_W, 214, 46]], normal: [0, 1, 0], tone: "lo" }] } });
  // wheels: tyre, rim face, hub with five bolts; stub axle from the inner body to the tyre
  for (const [ix, x] of [[0, -xw], [1, xw]]) for (const [iy, sgn] of [[0, 1], [1, -1]]) {
    const yc = sgn * WHEEL.y;
    const F = { o: [x + ox, yc, z0 + rw], a: [0, sgn, 0], u: [1, 0, 0], v: [0, 0, 1] };
    const tag = `${prefix}.wheel${ix}${iy}`;
    T.round({ name: `${tag}.axle`, group, layer, F, s0: -WHEEL.w / 2 - (WHEEL.y - WHEEL.w / 2 - INNER), s1: -WHEEL.w / 2, r: 7, tone: "mid", material: "steel" });
    T.round({ name: `${tag}.tyre`, group, layer, F, s0: -WHEEL.w / 2, s1: WHEEL.w / 2 - 1.2, r: rw, tone: "mid", material: "rubber", details: { ribs: { s0: -WHEEL.w / 2 + 2, s1: WHEEL.w / 2 - 3.2, count: 64, fade: [0.1, 0.55], seams: true, tone: "lo" } } });
    T.round({ name: `${tag}.rim`, group, layer, F, s0: WHEEL.w / 2 - 1.2, s1: WHEEL.w / 2, r: rw - 7, tone: "hi", material: "chrome", details: { bolts: [{ s: WHEEL.w / 2, r: 6, count: 5, size: 0.5, phase: 0.3 }], rings: [{ at: [WHEEL.w / 2, 0, 0], normal: F.a, r: 10, tone: "lo" }] } });
  }
  // sliding side skirts: the seal that lets the fans hold a pressure difference
  for (const sgn of [1, -1]) {
    const y0 = sgn > 0 ? HALF_W : -HALF_W - 2.6;
    T.prism({ name: `${prefix}.skirt${sgn > 0 ? "L" : "R"}`, group, layer, F: { o: [ox, 0, z0], a: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] }, polygon: [[-xa + 4, 0.6], [xa - 4, 0.6], [xa - 4, 14], [-xa + 4, 14]], s0: -(y0 + 2.6), s1: -y0, tone: "mid", crease: "lo", material: "gunmetal", details: { dots: [-80, -40, 0, 40, 80].map((x) => ({ at: [-(y0 + (sgn > 0 ? 2.6 : 0)), x, 8], normal: [0, sgn, 0], size: 0.4 })) } });
  }
  // two suction fans exhausting through the flanks (one per side), rotors as hinge groups (6-fold, wrap 0–60°)
  const FAN = { x: -62, z: 46, r: 15 };
  const rotors = [];
  for (const [i, sgn] of [[0, 1], [1, -1]]) {
    const Fv = { o: [FAN.x + ox, sgn * HALF_W, z0 + FAN.z], a: [0, sgn, 0], u: [1, 0, 0], v: [0, 0, 1] };
    T.round({ name: `${prefix}.fan${i}.ring`, group, layer, F: Fv, s0: 0, s1: 2.4, r: FAN.r + 3, tone: "mid", material: "gunmetal", details: { bolts: [{ s: 2.4, r: FAN.r + 1.6, count: 8, size: 0.45, phase: 0.2 }] } });
    const rg = fans[i];
    if (rg) {
      const spokes = Array.from({ length: 6 }, (_, j) => { const a = (j / 6) * Math.PI * 2; return { points: [[2.9, 3.4 * Math.cos(a), 3.4 * Math.sin(a)], [2.9, (FAN.r - 1.5) * Math.cos(a + 0.35), (FAN.r - 1.5) * Math.sin(a + 0.35)]], normal: Fv.a, tone: "mid", free: "end" }; });
      T.round({ name: `${prefix}.fan${i}.rotor`, group: rg, layer, F: Fv, s0: 2.4, s1: 2.9, r: FAN.r, tone: "hi", material: "steel", details: { rules: spokes, rings: [{ at: [2.9, 0, 0], normal: Fv.a, r: 3.4, tone: "lo" }] } });
      rotors.push({ group: rg, centre: Fv.o, axis: Fv.a });
    }
  }
  // rear wing on two pylons that follow the deck line
  for (const y of [-40, 40]) {
    const x0 = -206, x1 = -196;
    T.prism({ name: `${prefix}.pylon${y > 0 ? "L" : "R"}`, group, layer, F: { o: [ox, 0, z0], a: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] }, polygon: [[x0, topAt(x0)], [x1, topAt(x1)], [x1, 114], [x0, 114]], s0: -(y + 1.5), s1: -(y - 1.5), tone: "mid", material: "gunmetal" });
  }
  T.prism({ name: `${prefix}.wing`, group, layer, F: { o: [ox, 0, z0], a: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] }, polygon: [[-236, 114], [-186, 114], [-190, 118], [-230, 119]], s0: -84, s1: 84, tone: "hi", crease: "lo", bevel: 0.4, material: "gunmetal" });
  // ten cold-gas thruster nozzles (Rocket trim), placed as in model.mjs
  const nozzles = [];
  if (thrusters) {
    for (const t of M.THRUSTERS) {
      const p = [t.at[0] * 100, t.at[1] * 100, t.at[2] * 100];
      const a = t.role === "main" ? G.unit3([-28, 0, 2]) : t.dir;
      const re = (t.de * 100) / 2;
      const rt = Math.max(1.4, (t.dt * 100) / 2);
      // the nozzle stands on the body surface along its firing direction
      const base0 = surfaceAlong(p, a, z0);
      const base = [base0[0] + ox, base0[1], base0[2]];
      const len = t.role === "main" ? 16 : t.role === "lift" ? 6 : 5;
      const F = { o: base, a, u: Math.abs(a[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1], v: null };
      T.round({ name: `${prefix}.noz.${t.id}`, group, layer, F: { o: F.o, a: F.a, u: F.u }, s0: 0, s1: len, r: [rt + 1.2, re + 0.8], tone: "hi", material: "steel", details: { seams: [len * 0.35] } });
      nozzles.push({ id: t.id, role: t.role, exit: G.add3(base, G.mul3(a, len)), dir: a, r: re });
    }
  }
  return { parts, rotors, nozzles, FAN };
}

// Where a nozzle along a stands on the body: the floor, a flank, or the rear face.
function surfaceAlong(p, a, z0) {
  if (a[2] < -0.9) return [p[0], p[1], z0 + 10];
  if (Math.abs(a[1]) > 0.9) return [p[0], Math.sign(a[1]) * HALF_W, z0 + p[2]];
  if (a[0] < -0.9) return [-224 + ((p[2] - 24) / 28) * 2, p[1], z0 + p[2]];
  return [p[0], p[1], z0 + p[2]];
}
