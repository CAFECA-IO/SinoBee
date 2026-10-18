// Fig 4 · The jump and kickflip in the 3D mode: the Roadster (a free group) lifts on its four lift thrusters,
// rolls 360° on one-sided pulses, brakes on all four and lands, over five parked vehicles (world parts in the
// same live layer, so every crossing is solved by separating planes). The flight is model.mjs's mission().
import * as k from "./kit/iso-kit.mjs";
import * as G from "./kit/lathe.mjs";
import * as A from "./kit/audit.mjs";
import { turning } from "./kit/turn-build.mjs";
import { TURN } from "./kit/turn.mjs";
import * as TA from "./kit/turn-audit.mjs";
import { glCamera } from "./kit/gl.mjs";
import { carParts, HALF_W } from "./car.mjs";
import * as M from "./model.mjs";

export const W = 820;
export const H = 500;
const AZIMUTH = 36;
const ELEVATION = 21;
const CG = 56; // car's centre of gravity above the road at rest, cm
const APPROACH = 0.25;
const ROLLOUT = 0.9;
const BRAKE_G = 0.95;

export const MIS = M.mission();
const U = 100;
const X0 = MIS.x0 * U; // car centre at lift-off
const VX = MIS.vx * U;
export const T_END = MIS.airtime + ROLLOUT;
// car state at clip time t (t < 0: rolling in; past touchdown: braking to a stop)
export function stateAt(t) {
  const f = MIS.frames;
  if (t <= 0) return { x: X0 + VX * t, y: 0, z: 0, phi: 0, frame: null };
  if (t >= MIS.airtime) {
    const last = f[f.length - 1];
    const tt = Math.min(t - MIS.airtime, MIS.vx / (BRAKE_G * M.G0));
    const dx = (MIS.vx * tt - 0.5 * BRAKE_G * M.G0 * tt * tt) * U;
    return { x: last.x * U + dx, y: last.y * U, z: 0, phi: 2 * Math.PI, frame: null };
  }
  const i = Math.min(f.length - 2, Math.floor(t / (f[1].t - f[0].t)));
  const a = f[i], b = f[i + 1];
  const s = Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t)));
  return { x: (a.x + (b.x - a.x) * s) * U, y: (a.y + (b.y - a.y) * s) * U, z: (a.z + (b.z - a.z) * s) * U, phi: a.phi + (b.phi - a.phi) * s, frame: s < 0.5 ? a : b };
}
const poseOf = (t) => {
  const s = stateAt(t);
  const R = TURN.rotation([1, 0, 0], (s.phi * 180) / Math.PI);
  return { R, t: [s.x - X0, s.y, s.z] };
};
export const follow = (values) => ({ car: values.car ?? null });
export const poses = () => {
  const out = [{ car: null }];
  for (let t = -APPROACH; t <= T_END + 1e-6; t += 1 / 30) out.push({ car: poseOf(t) });
  return out;
};

// convex hull for hand-made vehicle profiles
const hull = (pts) => G.hullOf(pts.map((p) => p.slice()));
const VEH = {
  "Model 3": { L: 469, prof: [[-234, 14], [234, 14], [238, 46], [205, 74], [95, 86], [15, 144], [-70, 142], [-185, 98], [-236, 88], [-238, 46]], mat: null },
  Cybercab: { L: 360, prof: [[-178, 14], [178, 14], [182, 52], [128, 82], [35, 145], [-70, 140], [-178, 92]], mat: "gold" },
  Semi: { L: 640, cab: [[100, 42], [320, 42], [324, 150], [300, 300], [250, 410], [100, 410]], chassis: [[-320, 42], [99.5, 42], [99.5, 128], [-320, 128]], mat: null },
  Cybertruck: { L: 568, prof: [[-284, 22], [284, 22], [286, 96], [40, 179], [-284, 112]], mat: "steel" },
  "Model Y": { L: 475, prof: [[-236, 16], [236, 16], [240, 50], [205, 80], [100, 92], [10, 162], [-90, 158], [-200, 110], [-238, 98], [-240, 50]], mat: null },
};
const WHEEL_R = 36;
const BELT = 2 * WHEEL_R + 4;

function clipZ(poly, z0, z1) {
  let out = poly;
  for (const f of [(p) => p[1] - z0, (p) => z1 - p[1]]) {
    const next = [];
    for (let i = 0; i < out.length; i++) {
      const a = out[i], b = out[(i + 1) % out.length];
      const fa = f(a), fb = f(b);
      if (fa >= 0) next.push(a);
      if ((fa >= 0) !== (fb >= 0)) { const t = fa / (fa - fb); next.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); }
    }
    out = next;
  }
  return out.filter((p, i) => Math.hypot(p[0] - out[(i + 1) % out.length][0], p[1] - out[(i + 1) % out.length][1]) > 1e-3);
}

export function build({ audit = false, quick = false } = {}) {
  const spanX = [X0 - VX * APPROACH - 240, stateAt(T_END).x + 250];
  const pts = [[spanX[0], -360, 0], [spanX[1], -360, 0], [spanX[0], 360, 0], [spanX[1], 360, 0], [0, 0, MIS.apex.z * U + CG + 120], [0, 0, 420], [spanX[0], 0, 160], [spanX[1], 0, 160]];
  const P = k.fitProjection(pts, W, H, { pad: 16, azimuth: AZIMUTH, elevation: ELEVATION });
  const R = A.recorder(P);
  const T = turning(P, { recorder: R });
  const car = T.group("car", { free: { origin: [X0, 0, CG] } });
  const live = T.layer("air");
  T.stack(["ground", "air"]);

  // ---- Ground: an asphalt pad with painted launch and landing boxes, a centreline
  const PAD = { x: spanX[0] - 40, y: -380, w: spanX[1] - spanX[0] + 80, d: 760, r: 10 };
  const at = (p) => k.iso(p, P);
  const ln = (a, b) => k.lineOnTop(a, b, 0, P);
  const box = (x0, x1, y0, y1) => k.planOutline({ x: x0, y: y0, w: x1 - x0, d: y1 - y0, r: 2 }, 0, P, 2);
  const marks = [
    box(X0 - 240, X0 + 240, -110, 110),
    box(MIS.landX * U - 260, stateAt(T_END).x + 230, -110, 110),
    ln([spanX[0], 0], [X0 - 240, 0]),
  ];
  const groundSvg = `<path class="iso-halo" d="${k.haloOf(PAD, -6, 6, P)}"/>` + k.solidSvg(k.slabOf(PAD, -6, 6, P, 6, 1), { tone: "mid", inner: k.lineSvg(k.sideSeam(PAD, -3, P, 6), { tone: "faint" }) }) + k.lineSvg(k.planOutline(k.insetPlan(PAD, 16), 0, P, 6), { tone: "faint" }) + k.lineSvg(marks.join(""), { tone: "lo" });
  R.put({ name: "stand.pad", layer: "ground", at: [0, 0, -3], svg: groundSvg, shapes: [R.solid(A.slab(PAD, -6, 6, { name: "stand.pad", ground: true }))] });

  // ---- Parked vehicles: world parts in the live layer (body above the wheels, chassis between them, tyres)
  const vehicles = [];
  for (const span of MIS.spans) {
    const v = VEH[span.name];
    const xc = ((span.x0 + span.x1) / 2) * U;
    const wv = (span.x1 - span.x0) * U;
    const tag = `veh.${span.name.replace(/\s/g, "")}`;
    const F = { o: [0, 0, 0], a: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1] };
    const inner = wv / 2 - 26;
    if (v.cab) {
      T.prism({ name: `${tag}.cab`, layer: live, F, polygon: clipZ(hull(v.cab), BELT, 500), s0: xc - wv / 2, s1: xc + wv / 2, tone: "hi", crease: "lo", bevel: 0.6, material: v.mat ?? undefined });
      T.prism({ name: `${tag}.step`, layer: live, F, polygon: clipZ(hull(v.cab), 0, BELT), s0: xc - inner, s1: xc + inner, tone: "mid", crease: "lo", material: "gunmetal" });
      T.prism({ name: `${tag}.chassis`, layer: live, F, polygon: hull(v.chassis), s0: xc - inner, s1: xc + inner, tone: "mid", crease: "lo", material: "gunmetal" });
    } else {
      const up = clipZ(hull(v.prof), BELT, 400);
      const low = clipZ(hull(v.prof), 0, BELT);
      T.prism({ name: `${tag}.body`, layer: live, F, polygon: up, s0: xc - wv / 2, s1: xc + wv / 2, tone: "hi", crease: "lo", bevel: 0.6, material: v.mat ?? undefined });
      T.prism({ name: `${tag}.chassis`, layer: live, F, polygon: low, s0: xc - inner, s1: xc + inner, tone: "mid", crease: "lo", material: v.mat ?? undefined });
    }
    const wy = v.L * (v.cab ? 0.38 : 0.31);
    const wheelYs = v.cab ? [-wy, wy * 0.2, wy * 1.05] : [-wy, wy];
    for (const yy of wheelYs) for (const sgn of [-1, 1]) {
      const Fw = { o: [xc + sgn * inner, yy, WHEEL_R], a: [sgn, 0, 0], u: [0, 1, 0], v: [0, 0, 1] };
      T.round({ name: `${tag}.tyre${yy.toFixed(0)}${sgn}`, layer: live, F: Fw, s0: 0, s1: 22, r: WHEEL_R, tone: "mid", material: "rubber", details: { bolts: [{ s: 22, r: 9, count: 5, size: 0.45 }], rings: [{ at: [22, 0, 0], normal: Fw.a, r: 20, tone: "lo" }] } });
    }
    vehicles.push({ name: span.name, xc, w: wv, h: span.h });
  }

  // ---- The Roadster, ten thrusters on, in the free group
  const C = carParts(T, { group: car, layer: live, z0: 0, fans: [null, null], thrusters: true, prefix: "rs", ox: X0 });
  // shift: carParts builds at x = 0; the car rests with its centre at X0. Parts were built at x = 0, so we move
  // the free group's origin there instead (see poseOf): the build sees every pose relative to rest.

  const out = T.build({ statics: R.items, poses });
  A.settle(R, P);
  if (audit) {
    A.auditOrExit(R, P, { argv: ["--audit"] });
    TA.orbitOrExit(T, R, P, { argv: ["--audit"], quick, clips: [{ name: "jump", at: (t) => ({ car: poseOf(t) }), from: -APPROACH, to: T_END, frame: 1 / 60 }] });
  }
  const groundItems = R.items.filter((i) => i.layer === "ground").map((i) => i.svg);
  const LABEL = "一片柏油試驗場上並排停著五台車：Model 3、Cybercab、Semi、Cybertruck、Model Y。Roadster 從左側駛來，以底盤四顆升力推進器起跳，單側脈衝讓它沿縱軸翻滾 360°，越過 Semi 的車頂後再以四顆推進器減速，落在右側的著陸框裡。";
  const backSvg = k.figureSvg({ width: W, height: H, label: LABEL, body: [...groundItems, `<path data-part="shadow" class="rr-shadow" d=""/>`] });
  const stageInner = `${backSvg}${out.layerSvg("air", { width: W, height: H })}<canvas class="rr-layer rr-gl" aria-hidden="true"></canvas>`;
  const frames = [];
  for (let t = -APPROACH; t <= T_END + 1e-6; t += 1 / 60) {
    const s = stateAt(t);
    const f = s.frame;
    frames.push([+t.toFixed(4), +(s.x - X0).toFixed(2), +s.y.toFixed(2), +s.z.toFixed(2), +s.phi.toFixed(5), f ? +(M.TANK.m0 - f.m).toFixed(2) : t > 0 ? +MIS.gas.toFixed(2) : 0, f ? ["lift", "spin", "coast", "despin", "fall", "brake", "touch"].indexOf(f.phase) : t <= 0 ? -1 : 7, f ? (f.fire.L1 ? 1 : 0) | (f.fire.L2 ? 2 : 0) | (f.fire.L3 ? 4 : 0) | (f.fire.L4 ? 8 : 0) : 0, f ? +(f.p / 1e5).toFixed(1) : 0, f ? +f.vz.toFixed(2) : 0]);
  }
  const nozzleOn = { L1: 1, L2: 2, L3: 4, L4: 8 };
  const DATA = {
    turn: out.data, frames, W, H, camera: glCamera(P), P, origin: [X0, 0, CG], cg: CG,
    nozzles: C.nozzles.filter((n) => n.role === "lift").map((n) => ({ id: n.id, exit: n.exit, dir: n.dir, r: n.r, bit: nozzleOn[n.id] })),
    foot: [M.CAR.length * U, M.CAR.width * U, M.CAR.height * U],
    roofs: vehicles.map((v) => { const s = MIS.spans.find((x) => x.name === v.name); const L = VEH[v.name].L; return [s.x0 * U, s.x1 * U, -L / 2, L / 2, s.h * U]; }),
    endP: M.tankAtMass(M.TANK.m0 - MIS.gas).p / 1e5, apexT: MIS.apex.t,
    gas: MIS.gas, usable: M.budget().usable, apex: MIS.apex.z, clearance: MIS.clearance,
    cover: [["rs.body.mid"], ["rs.body.rear"], ["rs.body.front"], ["rs.body.archF"], ["rs.body.archR"], ["rs.body.innerF", "rs.body.innerR"]],
  };
  return { P, T, out, stageInner, css: out.css, DATA, report: out.report, vehicles };
}
