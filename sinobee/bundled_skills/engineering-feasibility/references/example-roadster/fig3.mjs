// Fig 3 · An inversion rig in the 3D mode: a road deck on two trunnions turns from floor (0°) through wall (90°)
// to ceiling (180°) with the Roadster held to it by its suction fans. Static: bed, two towers with bearings,
// a gear motor. Live: the deck, the car, two fan rotors.
import * as k from "./kit/iso-kit.mjs";
import * as G from "./kit/lathe.mjs";
import * as A from "./kit/audit.mjs";
import { turning } from "./kit/turn-build.mjs";
import * as TA from "./kit/turn-audit.mjs";
import { helpers } from "./helpers.mjs";
import { carParts, HALF_W } from "./car.mjs";

export const W = 780;
export const H = 470;
const AZIMUTH = 30;
const ELEVATION = 24;
const ZH = 152;
const DECK = { x: -250, y: -100, w: 500, d: 200, r: 6 };
const DECK_T = 10;
const TR = 14; // trunnion radius
const BEAR = { x0: 268, len: 20, r: 26 };
const BED = { x: -330, y: -150, w: 660, d: 300, r: 8 };

export const follow = (values) => {
  const spin = values.spin ?? 0;
  const wrap = ((spin % 60) + 60) % 60;
  return { tilt: values.tilt ?? 0, "fan0": wrap, "fan1": wrap };
};
export const poses = () => {
  const out = [];
  for (let a = 0; a <= 180; a += 1.5) out.push(follow({ tilt: a, spin: (a * 7) % 60 }));
  return out;
};

export function build({ audit = false, quick = false, verify = false } = {}) {
  const swept = [];
  for (const x of [-240, 240]) for (let a = 0; a < 360; a += 15) swept.push([x, 130 * Math.cos((a * Math.PI) / 180), ZH + 130 * Math.sin((a * Math.PI) / 180)]);
  const P = k.fitProjection([...k.boxCorners(BED, -16, 0), ...swept, [BEAR.x0 + BEAR.len + 40, 0, ZH + 30], [-(BEAR.x0 + BEAR.len), 0, ZH + 30]], W, H, { pad: 18, azimuth: AZIMUTH, elevation: ELEVATION });
  const R = A.recorder(P);
  const h = helpers(P, R);
  const { at, S, Ln, Dots, frameOf, boxAt, cylAt, disc } = h;
  const T = turning(P, { recorder: R });
  const tilt = T.group("tilt", { hinge: { point: [0, 0, ZH], axis: [-1, 0, 0], range: [0, 180] } });
  const fan0 = T.group("fan0", { parent: tilt, hinge: { point: [-62, HALF_W, ZH + DECK_T / 2 + 46], axis: [0, 1, 0], range: [0, 60] } });
  const fan1 = T.group("fan1", { parent: tilt, hinge: { point: [-62, -HALF_W, ZH + DECK_T / 2 + 46], axis: [0, -1, 0], range: [0, 60] } });
  const live = T.layer("rig");
  T.stack(["back", "rig", "front"]);

  // ---- Static bed and towers (back = far tower at −x, front = near tower at +x)
  const back = [`<path class="iso-halo" d="${k.haloOf(BED, -16, 12, P)}"/>`];
  const put = (name, layer, svg, shapes, bias = 0, where) => R.put({ name, layer, at: where ?? centreOf(shapes[0]), bias, svg, shapes: shapes.map((s) => R.solid(Object.assign(s, { name: s.name ?? name }))) });
  for (const [x, y] of k.corners(BED, 18)) put(`stand.foot.${x}.${y}`, "back", S(k.cylinder(x, y, 8, -16, 1.8, P, 28), { tone: "lo" }) + S(k.cylinder(x, y, 6, -14.2, 2.2, P, 28), { tone: "mid" }), [A.cylinder(x, y, 8, -16, 1.8, { ground: true }), A.cylinder(x, y, 6, -14.2, 2.2)]);
  const bedScrews = k.corners(BED, 7);
  const grid = [];
  for (let x = BED.x + 40; x < BED.x + BED.w - 20; x += 60) grid.push(k.lineOnTop([x, BED.y + 10], [x, BED.y + BED.d - 10], 0, P));
  put("stand.bed", "back", S(k.slabOf(BED, -12, 12, P, 8, 1.4), { tone: "mid", inner: Ln(k.sideSeam(BED, -8, P, 8), { tone: "faint" }) }) + Ln(k.planOutline(k.insetPlan(BED, 10), 0, P), { tone: "faint" }) + Ln(grid.join(""), { tone: "faint", free: true }) + Ln(bedScrews.map(([x, y]) => k.ring(x, y, 2.2, 0, P, 16)).join(""), { tone: "lo" }) + Dots(bedScrews.map(([x, y]) => at([x, y, 0])), { size: 0.5 }), [A.slab(BED, -12, 12, { ground: true })], -4);
  for (const sgn of [-1, 1]) {
    const layer = sgn > 0 ? "front" : "back";
    const x0 = sgn > 0 ? BEAR.x0 : -BEAR.x0 - BEAR.len;
    const col = { x: x0 - 2, y: -22, w: BEAR.len + 4, d: 44, r: 2 };
    const foot = { x: x0 - 14, y: -46, w: BEAR.len + 28, d: 92, r: 3 };
    const colTop = ZH - BEAR.r;
    put(`stand.tower.foot${sgn}`, "back", S(k.slabOf(foot, 0, 8, P, 4, 0.8), { tone: "mid" }) + Dots(k.corners(foot, 5).map(([x, y]) => at([x, y, 8])), { size: 0.5 }), [A.slab(foot, 0, 8)]);
    put(`stand.tower.col${sgn}`, layer, S(k.slabOf(col, 8, colTop - 8, P, 4, 0.8), { tone: "mid", inner: Ln(k.sideSeam(col, 30, P, 4) + k.sideSeam(col, colTop - 18, P, 4), { tone: "faint" }) }), [A.slab(col, 8, colTop - 8)]);
    for (const y of [-26, 22]) put(`stand.tower.gusset${sgn}.${y}`, "back", S(k.slabOf({ x: x0 + 2, y, w: BEAR.len - 4, d: 4, r: 0.8 }, 8, 16, P, 2, 0.4), { tone: "mid" }), [A.slab({ x: x0 + 2, y, w: BEAR.len - 4, d: 4, r: 0.8 }, 8, 16)]);
    const FB = frameOf([x0, 0, ZH], [1, 0, 0], [0, 0, 1]);
    const bearing = G.disc(0, BEAR.len, BEAR.r, FB, P);
    const face = sgn > 0 ? BEAR.len : 0;
    put(`stand.bearing${sgn}`, layer, S(bearing, { tone: "hi", inner: Ln(G.arcOf(FB, BEAR.len / 2, BEAR.r, P), { tone: "faint" }) }) + (sgn > 0 ? Dots(G.dotsOf(FB, face, BEAR.r - 5, 8, P, { all: true, phase: 0.2 }), { size: 0.5 }) + Ln(G.circleOf(FB, face, BEAR.r - 9, P, 48), { tone: "lo" }) : ""), [A.disc(FB, 0, BEAR.len, BEAR.r)]);
    if (sgn > 0) {
      // gear motor on the near bearing: gearbox, motor can, cooling ribs, junction box
      const FG = frameOf([x0 + BEAR.len, 0, ZH], [1, 0, 0], [0, 0, 1]);
      put("stand.gearbox", layer, S(G.disc(0, 12, 18, FG, P), { tone: "mid" }) + Dots(G.dotsOf(FG, 12, 14.5, 6, P, { all: true }), { size: 0.45 }), [A.disc(FG, 0, 12, 18)]);
      const ribs = G.ribsOf(FG, 13.2, 33.8, 12, 30, P, { fade: [0.1, 0.55], seams: true });
      put("stand.motor", layer, S(G.disc(12, 35, 12, FG, P), { tone: "hi" }) + k.fadedSvg(ribs, { tone: "lo" }), [A.disc(FG, 12, 35, 12)]);
      put("stand.motor.cap", layer, S(G.disc(35, 38, 10, FG, P), { tone: "mid" }) + Ln(G.circleOf(FG, 38, 5, P, 32), { tone: "lo" }), [A.disc(FG, 35, 38, 10)]);
    }
  }

  // ---- Live: the deck with trunnions, the car on it
  const z0 = ZH + DECK_T / 2;
  const laneRule = (y) => ({ points: [[z0, DECK.x + 14, y], [z0, DECK.x + DECK.w - 14, y]], normal: [0, 0, 1], tone: "faint" });
  T.prism({ name: "deck", group: tilt, layer: live, plan: DECK, z: ZH - DECK_T / 2, h: DECK_T, steps: 4, bevel: 0.8, tone: "mid", crease: "lo", material: "steel", details: { seams: [ZH - 1], dots: [-230, -115, 0, 115, 230].flatMap((x) => [{ at: [z0, x, DECK.y + 7], normal: [0, 0, 1], size: 0.5 }, { at: [z0, x, DECK.y + DECK.d - 7], normal: [0, 0, 1], size: 0.5 }]) } });
  void laneRule;
  for (const sgn of [-1, 1]) {
    const F = { o: [sgn * (DECK.x + DECK.w), 0, ZH], a: [sgn, 0, 0], u: [0, 0, 1], v: [0, 1, 0] };
    T.round({ name: `deck.trunnion${sgn}`, group: tilt, layer: live, F, s0: 0, s1: BEAR.x0 - (DECK.x + DECK.w), r: TR, tone: "hi", material: "steel", details: { seams: [4] } });
  }
  // deck stiffeners underneath (seen at 90–180°)
  if (false) for (const y of [-60, 0, 60]) T.prism({ name: `deck.rib${y}`, group: tilt, layer: live, plan: { x: DECK.x + 20, y: y - 2, w: DECK.w - 40, d: 4, r: 1 }, z: ZH - DECK_T / 2 - 6, h: 6, steps: 2, tone: "mid", material: "steel" });
  const car = carParts(T, { group: tilt, layer: live, z0, fans: [fan0, fan1], prefix: "car" });

  const out = T.build({ statics: R.items, poses, verify });
  A.settle(R, P);
  const layerOf = (name) => R.items.filter((item) => item.layer === name).sort((a, b) => a.key - b.key).map((item) => item.svg);
  if (audit) {
    A.auditOrExit(R, P, { argv: ["--audit"] });
    TA.orbitOrExit(T, R, P, { argv: ["--audit"], sweep: { at: (x) => follow({ tilt: x, spin: (x * 7) % 60 }), from: 0, to: 180, frame: 0.3 }, quick });
  }
  const LABEL = "倒置試驗架：兩座塔架以軸承撐起一塊路面甲板，甲板可從 0°（地面）轉到 90°（牆面）再到 180°（天花板）。Roadster 只靠底盤吸力風扇與側裙貼在甲板上，風扇從車身兩側排氣。";
  const backSvg = k.figureSvg({ width: W, height: H, label: LABEL, body: [...back, ...layerOf("back")] });
  const frontSvg = `<svg xmlns="http://www.w3.org/2000/svg" class="iso-svg rr-layer" viewBox="0 0 ${W} ${H}" aria-hidden="true">${layerOf("front").join("")}</svg>`;
  const stageInner = `${backSvg}${out.layerSvg("rig", { width: W, height: H })}${frontSvg}`;
  return { P, T, R, out, stageInner, css: out.css, data: out.data, rotors: car.rotors, report: out.report };
}

function centreOf(shape) {
  const b = A.boundsOf(shape);
  return b ? [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2] : [0, 0, 0];
}
