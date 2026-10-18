// Roadster Rocket: one pure physics model that drives every figure, readout and caption.
// Real-gas tank states come from CoolProp (blowdown.json, generated in the build session):
// isentropic (adiabatic) blowdown of 600 L of air from 690 bar / 300 K, plus the isenthalpic
// (Joule–Thomson) throttle across the regulator. Nozzle flow is ideal-gas, γ = 1.4.
// Inputs marked ASSUMED are not published by Tesla; they are engineering estimates.
import TABLE from "./blowdown.json" with { type: "json" };

export const G0 = 9.80665;
export const GAMMA = 1.4;
export const R_AIR = 287.05;
export const CP = (GAMMA * R_AIR) / (GAMMA - 1);
export const PA = 1.013e5;
export const RHO_AIR = 1.2;

// Published: 600 L of compressed air (Lars Moravy), 10 thrusters, COPVs; 10,000 psi from the 2018 SpaceX-package design.
export const TANK = { volume: TABLE.V, p0: TABLE.P0 * 1e5, T0: TABLE.T0, m0: TABLE.m0, burstMJ: TABLE.burst_MJ, fillKWh: TABLE.wIso_kWh };
// ASSUMED: vehicle with full tanks (MKBHD quoted "4,000+ lb"), roll inertia of a 1.9 m × 1.1 m section with mass held low.
export const CAR = { mass0: 2000, length: 4.3, width: 1.95, height: 1.12, wheelbase: 2.7, track: 1.7, Ixx: 640, mu: 1.2 };
export const COPV_KG_PER_L = 0.45; // ASSUMED: Type IV / SpaceX-class carbon overwrap at 690 bar (hydrogen tanks are 0.6–0.7)

const ROWS = TABLE.rows; // sorted by falling p
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// Tank state from the mass still inside (isentropic blowdown, no wall heat: the fast case).
export function tankAtMass(m) {
  if (m >= ROWS[0].m) return { p: ROWS[0].p * 1e5, T: ROWS[0].T, m };
  for (let i = 1; i < ROWS.length; i++) {
    const a = ROWS[i - 1], b = ROWS[i];
    if (m >= b.m) {
      const t = (a.m - m) / (a.m - b.m);
      return { p: lerp(a.p, b.p, t) * 1e5, T: lerp(a.T, b.T, t), m, row: i - 1 + t };
    }
  }
  const z = ROWS[ROWS.length - 1];
  return { p: z.p * 1e5, T: z.T, m };
}
export function tankAtPressure(p) {
  const bar = p / 1e5;
  for (let i = 1; i < ROWS.length; i++) {
    const a = ROWS[i - 1], b = ROWS[i];
    if (bar >= b.p) {
      const t = (a.p - bar) / (a.p - b.p);
      return { p, T: lerp(a.T, b.T, t), m: lerp(a.m, b.m, t), row: i - 1 + t };
    }
  }
  const z = ROWS[ROWS.length - 1];
  return { p: z.p * 1e5, T: z.T, m: z.m };
}
// Gas temperature after the regulator (isenthalpic throttle tank → Pc), interpolated from the CoolProp rows.
export function regulatorT(tank, pc) {
  const r = tank.row ?? 0;
  const i = Math.floor(r);
  const pick = (row) => {
    const keys = Object.keys(row.tc).map(Number).sort((x, y) => x - y);
    if (!keys.length) return row.T;
    const bar = pc / 1e5;
    if (bar <= keys[0]) return row.tc[keys[0]];
    for (let j = 1; j < keys.length; j++) if (bar <= keys[j]) return lerp(row.tc[keys[j - 1]], row.tc[keys[j]], (bar - keys[j - 1]) / (keys[j] - keys[j - 1]));
    return row.tc[keys[keys.length - 1]];
  };
  const a = ROWS[clamp(i, 0, ROWS.length - 1)], b = ROWS[clamp(i + 1, 0, ROWS.length - 1)];
  const ta = pick(a) ?? a.T, tb = pick(b) ?? b.T;
  return Math.min(tank.T + 0.01, lerp(ta, tb, r - i));
}

// Supersonic exit Mach number for an area ratio (bisection on the isentropic area relation).
export function machOf(eps) {
  const f = (M) => (1 / M) * ((2 / (GAMMA + 1)) * (1 + ((GAMMA - 1) / 2) * M * M)) ** ((GAMMA + 1) / (2 * (GAMMA - 1))) - eps;
  let lo = 1.0001, hi = 12;
  for (let k = 0; k < 80; k++) { const mid = (lo + hi) / 2; if (f(mid) > 0) hi = mid; else lo = mid; }
  return (lo + hi) / 2;
}
const CHOKE = Math.sqrt(GAMMA) * (2 / (GAMMA + 1)) ** ((GAMMA + 1) / (2 * (GAMMA - 1)));
// Saturation temperature of air (dew line) at the nozzle exit pressure: a fit to CoolProp's dew line, 0.2–3 bar.
export const dewT = (p) => 81.6 + 9.9 * Math.log(p / 1e5);
export const LAMBDA = 0.983; // 15° conical divergence loss

// One cold-gas nozzle: chamber pressure pc, chamber temperature Tc, throat area At, area ratio eps.
export function nozzle(pc, Tc, At, eps) {
  if (pc <= PA * 1.9) return { F: 0, mdot: 0, ve: 0, pe: PA, Te: Tc, Me: 0, sep: false, cond: false, isp: 0 };
  const Me = machOf(eps);
  const ratio = 1 + ((GAMMA - 1) / 2) * Me * Me;
  const pe = pc * ratio ** (-GAMMA / (GAMMA - 1));
  const Te = Tc / ratio;
  const ve = Me * Math.sqrt(GAMMA * R_AIR * Te);
  const mdot = (pc * At * CHOKE) / Math.sqrt(R_AIR * Tc);
  const Ae = At * eps;
  const sep = pe < 0.35 * PA; // Summerfield criterion: the jet leaves the wall, thrust falls off
  const pressure = sep ? 0 : (pe - PA) * Ae;
  const F = LAMBDA * mdot * ve + pressure;
  return { F, mdot, ve, pe, Te, Me, sep, cond: Te < dewT(pe), isp: F / mdot / G0, ceff: F / mdot };
}

// The ten thrusters. Rocket trim: rear mains plus perimeter thrusters (Tesla). Sizes ASSUMED, at the design chamber pressure.
export const PC_DESIGN = 100e5;
export const EPS_DESIGN = 8.1; // perfectly expanded to 1 atm at 100 bar
const throatFor = (F) => F / (PC_DESIGN * 1.55);
export const THRUSTERS = [
  { id: "R1", role: "main", F: 50e3, at: [-2.23, -0.42, 0.4], dir: [-1, 0, 0] },
  { id: "R2", role: "main", F: 50e3, at: [-2.23, 0.42, 0.4], dir: [-1, 0, 0] },
  { id: "L1", role: "lift", F: 15e3, at: [0.92, -0.72, 0.1], dir: [0, 0, -1] },
  { id: "L2", role: "lift", F: 15e3, at: [0.92, 0.72, 0.1], dir: [0, 0, -1] },
  { id: "L3", role: "lift", F: 15e3, at: [-0.92, -0.72, 0.1], dir: [0, 0, -1] },
  { id: "L4", role: "lift", F: 15e3, at: [-0.92, 0.72, 0.1], dir: [0, 0, -1] },
  { id: "A1", role: "attitude", F: 3e3, at: [1.95, -0.83, 0.45], dir: [0, -1, 0] },
  { id: "A2", role: "attitude", F: 3e3, at: [1.95, 0.83, 0.45], dir: [0, 1, 0] },
  { id: "A3", role: "attitude", F: 3e3, at: [-1.95, -0.83, 0.45], dir: [0, -1, 0] },
  { id: "A4", role: "attitude", F: 3e3, at: [-1.95, 0.83, 0.45], dir: [0, 1, 0] },
].map((t) => ({ ...t, At: throatFor(t.F), eps: EPS_DESIGN, dt: 2 * Math.sqrt(throatFor(t.F) / Math.PI), de: 2 * Math.sqrt((throatFor(t.F) * EPS_DESIGN) / Math.PI) }));

// The usable part of a fill: the regulator holds pc only while the tank stays above pc + 10 bar.
export function budget(pc = PC_DESIGN, eps = EPS_DESIGN, steps = 120) {
  const pEnd = pc + 10e5;
  const end = tankAtPressure(pEnd);
  const usable = TANK.m0 - end.m;
  const At = 1e-3;
  let impulse = 0;
  let ceffSum = 0;
  const curve = [];
  for (let k = 0; k <= steps; k++) {
    const m = TANK.m0 - (usable * k) / steps;
    const tank = tankAtMass(m);
    const Tc = regulatorT(tank, pc);
    const n = nozzle(pc, Tc, At, eps);
    curve.push({ used: (usable * k) / steps, p: tank.p, T: tank.T, Tc, ceff: n.ceff, Te: n.Te, cond: n.cond });
    if (k) { const a = curve[k - 1]; impulse += ((a.ceff + n.ceff) / 2) * (usable / steps); ceffSum += (a.ceff + n.ceff) / 2; }
  }
  const ceff = ceffSum / steps;
  const weight = CAR.mass0 * G0;
  return {
    pc, usable, residual: end.m, impulse, ceff, isp: ceff / G0, end,
    hover: impulse / weight, // seconds of hovering if every newton held the car up (mass loss ignored: conservative by ~4%)
    dv: ceff * Math.log(CAR.mass0 / (CAR.mass0 - usable)),
    fullThrust: usable / (THRUSTERS.reduce((s, t) => s + nozzle(pc, regulatorT(tankAtMass(TANK.m0), pc), t.At, t.eps).mdot, 0)),
    tankKg: TANK.volume * 1000 * COPV_KG_PER_L,
    curve,
  };
}

// Active suction (two underbody fans behind a sliding skirt) plus aero, on a surface at angle theta
// (0 floor, 90 wall, 180 ceiling), at speed v, on a ramp of radius R (Infinity = flat).
// ASSUMED fan and skirt: sealed area 5.5 m², fan curve ΔP0 8.35 kPa, Qmax 10.5 m³/s (2.0 g held on a flat floor, Tesla: "up to 2 Gs"), skirt travel 40 mm, base gap 6 mm.
export const FAN = { area: 5.5, dp0: 8350, qmax: 10.5, eta: 0.62, perimeter: 11.2, gap: 0.006, travel: 0.04, cd: 0.62, clA: 2.4 };
export function suction(theta, v = 0, R = Infinity, on = true) {
  const M = CAR.mass0;
  const sag = Number.isFinite(R) ? (CAR.wheelbase * CAR.wheelbase) / (8 * R) : 0;
  const gap = FAN.gap + Math.max(0, sag - FAN.travel);
  const leakA = FAN.cd * FAN.perimeter * gap;
  // Fan curve ΔP = ΔP0 (1 − (Q/Qmax)²) meets the leak Q = leakA·√(2ΔP/ρ).
  let dp = 0;
  if (on) {
    let lo = 0, hi = FAN.dp0;
    for (let k = 0; k < 60; k++) {
      const mid = (lo + hi) / 2;
      const q = leakA * Math.sqrt((2 * mid) / RHO_AIR);
      if (FAN.dp0 * (1 - (q / FAN.qmax) ** 2) > mid) lo = mid; else hi = mid;
    }
    dp = lo;
  }
  const q = leakA * Math.sqrt((2 * dp) / RHO_AIR);
  const fan = dp * FAN.area;
  const power = on ? (dp * q) / FAN.eta : 0;
  const aero = 0.5 * RHO_AIR * v * v * FAN.clA;
  const centripetal = Number.isFinite(R) ? (M * v * v) / R : 0;
  const t = (theta * Math.PI) / 180;
  const N = fan + aero + centripetal + M * G0 * Math.cos(t);
  const along = M * G0 * Math.abs(Math.sin(t));
  const grip = CAR.mu * Math.max(0, N);
  return {
    theta, v, R, sag, gap, dp, q, fan, aero, centripetal, power, N,
    sticks: N > 0 && grip >= along,
    margin: N / (M * G0), // normal force in g
    accel: Math.max(0, grip - along) / M / G0, // usable traction left for driving, in g
    fanG: fan / (M * G0),
  };
}
// Lowest speed at which the loop alone holds the car at the top (no fans): v = √(gR).
export const loopSpeed = (R) => Math.sqrt(G0 * R);

// Jump + kickflip mission over five parked vehicles, a rigid-body simulation with body-fixed thrusters.
// Lift thrusters fire along body −z (pushing +z); a roll pulse fires one side only (torque ±F·0.85 m).
export const ROW = [ // side by side across the path, widths and roof heights in m (Tesla published figures, rounded)
  { name: "Model 3", w: 1.85, h: 1.44 }, { name: "Cybercab", w: 1.9, h: 1.45 }, { name: "Semi", w: 2.6, h: 4.1 },
  { name: "Cybertruck", w: 2.2, h: 1.79 }, { name: "Model Y", w: 1.92, h: 1.62 },
];
export const ROW_GAP = 0.5;
// Order across the path is ours (the Semi in the middle, under the apex); Tesla parked them in a row.
export function mission({ lift = 0.37, spin = 0.12, land = 0.5, lead = 5.25, dt = 1 / 240 } = {}) {
  const lifts = THRUSTERS.filter((t) => t.role === "lift");
  const arm = Math.abs(lifts[0].at[1]);
  const span = ROW.reduce((s, c) => s + c.w, 0) + ROW_GAP * (ROW.length - 1);
  const Ix = CAR.Ixx;
  const run = () => {
    let s = { t: 0, y: 0, z: 0, vy: 0, vz: 0, phi: 0, w: 0, m: TANK.m0 };
    const out = [];
    let phase = "lift";
    let downAt = null;
    for (let k = 0; k < 6000; k++) {
      const tank = tankAtMass(s.m);
      const Tc = regulatorT(tank, PC_DESIGN);
      const pcOk = tank.p > PC_DESIGN + 10e5;
      const M = CAR.mass0 - (TANK.m0 - s.m);
      const one = nozzle(PC_DESIGN, Tc, lifts[0].At, lifts[0].eps);
      const fire = { L1: 0, L2: 0, L3: 0, L4: 0 };
      if (phase === "lift" && s.t >= lift) phase = "spin";
      if (phase === "spin" && s.t >= lift + spin) phase = "coast";
      const alpha = (2 * one.F * arm) / Ix;
      if (phase === "coast" && 2 * Math.PI - s.phi <= (s.w * s.w) / (2 * alpha)) { phase = "despin"; downAt = s.t; }
      if (phase === "despin" && s.w <= 1e-3) phase = "fall";
      const brakeA = (4 * one.F) / M - G0;
      if ((phase === "fall" || phase === "coast") && s.vz < 0 && s.z <= (s.vz * s.vz - land * land) / (2 * brakeA) + 0.02) phase = "brake";
      if (phase === "brake" && s.vz >= -land) phase = "touch";
      if (phase === "lift" || phase === "brake") fire.L1 = fire.L2 = fire.L3 = fire.L4 = 1;
      if (phase === "spin") fire.L2 = fire.L4 = 1;
      if (phase === "despin") fire.L1 = fire.L3 = 1;
      let fz = 0, fy = 0, tau = 0, mdot = 0;
      for (const th of lifts) {
        if (!fire[th.id] || !pcOk) continue;
        fz += one.F * Math.cos(s.phi);
        fy -= one.F * Math.sin(s.phi);
        tau += one.F * (th.at[1] > 0 ? arm : -arm);
        mdot += one.mdot;
      }
      out.push({ t: s.t, y: s.y, z: s.z, phi: s.phi, w: s.w, vz: s.vz, m: s.m, p: tank.p, T: tank.T, Tc, fire: { ...fire }, phase, F: fz, g: fz / (M * G0) });
      if (s.z <= 0 && s.t > 0.2 && s.vz <= 0) break;
      s = { t: s.t + dt, y: s.y + s.vy * dt, z: Math.max(0, s.z + s.vz * dt), vy: s.vy + (fy / M) * dt, vz: s.vz + (fz / M - G0) * dt, phi: Math.min(2 * Math.PI, s.phi + s.w * dt), w: Math.max(0, s.w + (tau / Ix) * dt), m: s.m - mdot * dt };
    }
    return { out, downAt };
  };
  const { out, downAt } = run();
  const apex = out.reduce((a, b) => (b.z > a.z ? b : a));
  const x0 = -span / 2 - CAR.length / 2 - lead;
  const vx = -x0 / apex.t; // the apex lands over the centre of the row
  for (const f of out) f.x = x0 + vx * f.t;
  const last = out[out.length - 1];
  let xs = -span / 2;
  const spans = ROW.map((c) => { const a = xs; xs += c.w + ROW_GAP; return { ...c, x0: a, x1: a + c.w }; });
  let clearance = Infinity, over = null;
  for (const f of out) {
    const low = f.z + CAR.height / 2 - (Math.abs(Math.cos(f.phi)) * CAR.height) / 2 - (Math.abs(Math.sin(f.phi)) * CAR.width) / 2;
    for (const c of spans) if (f.x + CAR.length / 2 > c.x0 && f.x - CAR.length / 2 < c.x1 && low - c.h < clearance) { clearance = low - c.h; over = c.name; }
  }
  const gasAt = (name) => out.filter((f) => f.phase === name).length * dt;
  return {
    frames: out, apex, last, spans, x0, vx, span, downAt, clearance, over,
    gas: TANK.m0 - last.m, landing: last.vz, airtime: last.t, roll: (last.phi * 180) / Math.PI,
    landX: last.x, clearAfter: last.x - CAR.length / 2 - span / 2, drift: last.y,
    burns: { lift: gasAt("lift"), spin: gasAt("spin"), despin: gasAt("despin"), brake: gasAt("brake") },
  };
}

// Energy and safety numbers for the caption.
export function hazards() {
  return { burstMJ: TANK.burstMJ, tnt: TANK.burstMJ / 4.184, fillKWh: TANK.fillKWh, fillReal: TANK.fillKWh / 0.5 };
}
