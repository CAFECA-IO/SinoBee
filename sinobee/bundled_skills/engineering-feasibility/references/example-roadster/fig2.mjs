// Fig 2 · The 600 L of air as three 200 L carbon-overwrapped bottles on a skid: hand valves, a manifold with
// three ball tees, the main isolation valve with a pressure dial, and a gas thermometer. The dials follow the
// isentropic blowdown from model.mjs (CoolProp real-gas states).
import * as k from "./kit/iso-kit.mjs";
import * as G from "./kit/lathe.mjs";
import * as A from "./kit/audit.mjs";
import { helpers } from "./helpers.mjs";
import * as M from "./model.mjs";

export const W = 780;
export const H = 420;
const AZIMUTH = 34;
const ELEVATION = 30;

const R0 = 17; // bottle radius, cm
const HEAD = 8.5; // 2:1 ellipsoidal heads
const CYL = 210;
const BOTTLES = [-42, 0, 42];
const ZB = 30; // bottle axis height
const X0 = -130; // pole of the far head
const X1 = X0 + HEAD * 2 + CYL; // pole of the near head
const BOSS_R = 4.2;
const SKID = { x: -150, y: -70, w: 330, d: 140, r: 5 };

export function build({ audit = false } = {}) {
  const P = k.fitProjection([...k.boxCorners(SKID, -14, ZB + R0 + 2), [X1 + 70, 0, ZB + 44]], W, H, { pad: 24, azimuth: AZIMUTH, elevation: ELEVATION });
  const R = A.recorder(P);
  const h = helpers(P, R);
  const { at, S, Ln, Dots, frameOf, put, turned, curved, disc, pipe, boxAt, cylAt, ballAt } = h;
  const back = [];
  back.push(`<path class="iso-halo" d="${k.haloOf(SKID, -14, 10, P)}"/>`);

  // ---- Skid: two channel rails on feet, cross members, lifting eyes
  for (const y of [-58, 50]) {
    boxAt({ x: SKID.x, y, w: SKID.w, d: 8, r: 1 }, -10, 10, { tone: "mid" }, { name: "stand.rail", bevel: 0.6, ground: true, inner: Ln(k.sideSeam({ x: SKID.x, y, w: SKID.w, d: 8, r: 1 }, -6, P, 3), { tone: "faint" }), extra: Dots([-140, -60, 20, 100, 170].map((x) => at([x, y + 4, 0])), { size: 0.42 }) });
  }
  for (const x of [-120, -10, 100, 165]) boxAt({ x, y: -50, w: 7, d: 100, r: 1 }, -8, 6, { tone: "mid" }, { name: "stand.cross", bevel: 0.4, ground: true });
  for (const [x, y] of [[-146, -54], [-146, 54], [176, -54], [176, 54]]) cylAt(x, y, 4, 0, 2.2, { tone: "mid" }, { name: "stand.eye", extra: Ln(k.ring(x, y, 2, 2.2, P, 16), { tone: "lo" }) });

  // ---- Bottles: liner + carbon overwrap, cut into bands at the two straps
  const F = (y) => frameOf([X0, y, ZB], [1, 0, 0], [0, 0, 1]);
  const head = (s) => (s < HEAD ? R0 * Math.sqrt(Math.max(0, 1 - ((HEAD - s) / HEAD) ** 2)) : s > HEAD + CYL ? R0 * Math.sqrt(Math.max(0, 1 - ((s - HEAD - CYL) / HEAD) ** 2)) : R0);
  const STRAPS = [HEAD + 38, HEAD + CYL - 38];
  const STRAP_T = 5;
  for (const y of BOTTLES) {
    const Fb = F(y);
    // ellipsoidal heads (2:1), cut at the polar boss radius; cylinder bands between the straps
    const uCut = Math.sqrt(1 - (BOSS_R / R0) ** 2);
    const ell = (u) => R0 * Math.sqrt(Math.max(1e-6, 1 - u * u));
    const ellSlope = (u) => (R0 * u) / (HEAD * Math.sqrt(Math.max(1e-6, 1 - u * u)));
    const sA = HEAD * (1 - uCut);
    curved(Fb, sA, HEAD, (s) => ell((HEAD - s) / HEAD), (s) => ellSlope((HEAD - s) / HEAD), 22, { tone: "hi" }, { name: "rig.copv.head0" });
    const cuts = [HEAD, STRAPS[0] - STRAP_T / 2, STRAPS[0] + STRAP_T / 2, STRAPS[1] - STRAP_T / 2, STRAPS[1] + STRAP_T / 2, HEAD + CYL];
    for (let i = 0; i < cuts.length - 1; i += 2) {
      const [s0, s1] = [cuts[i], cuts[i + 1]];
      const ribs = G.ribsOf(Fb, s0, s1, R0, 34, P, { twist: 0.9, fade: [0.12, 0.6], phase: 0.3 });
      const hoops = [];
      for (let s = s0 + 9; s < s1 - 4; s += 18) hoops.push(G.arcOf(Fb, s, R0, P));
      turned(Fb, [[s0, R0], [s1, R0]], { tone: "hi" }, { name: "rig.copv.wrap", extra: k.fadedSvg(ribs, { tone: "faint" }), inner: hoops.join("") });
    }
    const sB = HEAD + CYL;
    curved(Fb, sB, X1 - X0 - sA, (s) => ell((s - sB) / HEAD), (s) => -ellSlope((s - sB) / HEAD), 22, { tone: "hi" }, { name: "rig.copv.head1" });
    // polar boss and a hand valve on the near pole
    disc(Fb, X1 - X0 - sA, X1 - X0 + 3.4, BOSS_R, { tone: "mid" }, { name: "rig.copv.boss", extra: Dots(G.dotsOf(Fb, X1 - X0 + 3.4, BOSS_R - 1, 6, P, { all: true }), { size: 0.36 }) });
    const vb = { x: X1 + 3.4, y: y - 4.5, w: 9, d: 9, r: 1.2 };
    boxAt(vb, ZB - 4.5, 9, { tone: "mid" }, { name: "rig.copv.valve", bevel: 0.4 });
    cylAt(vb.x + 4.5, y, 1.1, ZB + 4.5, 4, { tone: "mid" }, { name: "rig.copv.stem" });
    boxAt({ x: vb.x + 4.5 - 1, y: y - 7, w: 2, d: 14, r: 0.8 }, ZB + 8.5, 1.6, { tone: "hi" }, { name: "rig.copv.handle", bevel: 0.2 });
    // straps with saddles under them
    for (const s of STRAPS) {
      const Fs = frameOf([X0 + s, y, ZB], [1, 0, 0], [0, 0, 1]);
      disc(Fs, -STRAP_T / 2, STRAP_T / 2, R0 + 1.2, { tone: "mid" }, { name: "rig.copv.strap", extra: Dots([at([X0 + s, y + R0 + 1.2, ZB])], { size: 0.4 }) });
      const sx = X0 + s + (s < 100 ? 12 : -12);
      boxAt({ x: sx - 5, y: y - 11, w: 10, d: 22, r: 1.2 }, 0, ZB - R0, { tone: "mid" }, { name: "rig.copv.saddle", bevel: 0.4 });
    }
  }

  // ---- Manifold: three ball tees joined by straight runs, branch pipes from each valve
  const XM = X1 + 32;
  const BALL = 3.6;
  const PR = 2.2;
  for (const y of BOTTLES) ballAt([XM, y, ZB], BALL, { tone: "hi", name: "rig.tee" });
  const rim = Math.sqrt(BALL * BALL - PR * PR);
  for (let i = 0; i < BOTTLES.length - 1; i++) pipe([[XM, BOTTLES[i] + rim, ZB], [XM, BOTTLES[i + 1] - rim, ZB]], PR, { flanges: [false, false], name: "rig.manifold", bands: [0.5] });
  const rimB = Math.sqrt(BALL * BALL - 1.6 * 1.6);
  for (const y of BOTTLES) pipe([[X1 + 12.4, y, ZB], [XM - rimB, y, ZB]], 1.6, { flanges: [true, false], flangeR: 1.5, name: "rig.branch" });
  // outlet: from the middle tee up to the isolation valve block on a post
  const VBLK = { x: XM + 14, y: -8, w: 16, d: 16, r: 1.4 };
  pipe([[XM + rim, 0, ZB], [VBLK.x, 0, ZB]], PR, { flanges: [false, true], flangeR: 1.6, name: "rig.outlet" });
  boxAt(VBLK, ZB - 8, 16, { tone: "hi" }, { name: "rig.iso.valve", bevel: 0.6, inner: Ln(k.sideSeam(VBLK, ZB - 2, P, 3), { tone: "faint" }), extra: Dots(k.corners(VBLK, 2.2).map(([x, y]) => at([x, y, ZB + 8])), { size: 0.42 }) });
  boxAt({ x: VBLK.x + 3, y: -5, w: 10, d: 10, r: 1 }, 0, ZB - 8, { tone: "mid" }, { name: "rig.iso.post", bevel: 0.4 });
  // pressure dial on the valve block
  const PD = [VBLK.x + 8, 0];
  const PDR = 11;
  const PDZ = ZB + 8 + 6;
  cylAt(PD[0], PD[1], 1.8, ZB + 8, 6, { tone: "mid" }, { name: "rig.pdial.stem" });
  const pTicks = k.radialTicks(PD[0], PD[1], PDR - 2, 70, 10, PDZ + 2.4, [1, 2.2], P, -0.75 * Math.PI, 1.5 * Math.PI);
  cylAt(PD[0], PD[1], PDR, PDZ, 2.4, { tone: "hi" }, { name: "rig.pdial", bevel: 0.5, steps: 64, extra: Ln(k.ring(PD[0], PD[1], PDR - 1.4, PDZ + 2.4, P, 64), { tone: "lo" }) + Ln(pTicks.minor, { tone: "lo" }) + Ln(pTicks.major, { tone: "mid" }) + Ln(k.ring(PD[0], PD[1], 1.2, PDZ + 2.4, P, 14), { tone: "mid" }) });
  // gas thermometer on the far tee
  const TD = [XM, BOTTLES[0]];
  const TDR = 8;
  const TDZ = ZB + BALL + 10;
  cylAt(TD[0], TD[1], 1.2, ZB + Math.sqrt(BALL * BALL - 1.2 * 1.2), TDZ - ZB - Math.sqrt(BALL * BALL - 1.2 * 1.2), { tone: "mid" }, { name: "rig.tdial.stem" });
  const tTicks = k.radialTicks(TD[0], TD[1], TDR - 1.8, 48, 8, TDZ + 2, [0.9, 1.9], P, -0.75 * Math.PI, 1.5 * Math.PI);
  cylAt(TD[0], TD[1], TDR, TDZ, 2, { tone: "hi" }, { name: "rig.tdial", bevel: 0.4, steps: 56, extra: Ln(k.ring(TD[0], TD[1], TDR - 1.2, TDZ + 2, P, 56), { tone: "lo" }) + Ln(tTicks.minor, { tone: "lo" }) + Ln(tTicks.major, { tone: "mid" }) + Ln(k.ring(TD[0], TD[1], 1, TDZ + 2, P, 12), { tone: "mid" }) });
  // outlet pipe to a bulkhead on the skid's edge: "to the regulators"
  const BH = { x: VBLK.x + VBLK.w + 18, y: -7, w: 8, d: 14, r: 1 };
  pipe([[VBLK.x + VBLK.w, 0, ZB], [BH.x, 0, ZB]], PR, { flanges: [true, true], flangeR: 1.6, name: "rig.feed" });
  boxAt(BH, 0, ZB + 8, { tone: "mid" }, { name: "stand.bulkhead", bevel: 0.5, inner: Ln(k.sideSeam(BH, 4, P, 3), { tone: "faint" }) });

  A.settle(R, P);
  R.items.sort((a, b) => a.key - b.key);
  if (audit) A.auditOrExit(R, P, { argv: ["--audit"] });

  const needle = (c, r, z, frac) => {
    const a = -0.75 * Math.PI + 1.5 * Math.PI * Math.max(0, Math.min(1, frac));
    return k.lineOnTop([c[0] - Math.sin(a) * 1.6, c[1] + Math.cos(a) * 1.6], [c[0] + Math.sin(a) * r, c[1] - Math.cos(a) * r], z, P);
  };
  // the tank curve, sampled by mass used, for the live page
  const b = M.budget();
  const curve = Array.from({ length: 141 }, (_, i) => {
    const used = (i / 140) * (M.TANK.m0 - 40);
    const t = M.tankAtMass(M.TANK.m0 - used);
    const Tc = M.regulatorT(t, M.PC_DESIGN);
    const n = M.nozzle(M.PC_DESIGN, Tc, 1e-3, M.EPS_DESIGN);
    return [+used.toFixed(2), +(t.p / 1e5).toFixed(2), +t.T.toFixed(2), +n.ceff.toFixed(1)];
  });
  const mis = M.mission();
  const marks = { reg: b.end.p / 1e5, stunt: M.tankAtMass(M.TANK.m0 - mis.gas).p / 1e5 };
  const markOn = (c, r, z, frac) => { const a = -0.75 * Math.PI + 1.5 * Math.PI * frac; return k.lineOnTop([c[0] + Math.sin(a) * (r - 2.6), c[1] - Math.cos(a) * (r - 2.6)], [c[0] + Math.sin(a) * (r - 0.6), c[1] - Math.cos(a) * (r - 0.6)], z, P); };
  const backSvg = k.figureSvg({
    width: W, height: H,
    label: "撬座上並排三支 200 公升碳纖維纏繞氣瓶（COPV），各有極端手閥，經球形三通歧管匯入主隔離閥；閥頂壓力錶與歧管上的氣體溫度計隨放氣而下降。",
    body: [...back, ...R.items.map((item) => item.svg),
      Ln(markOn(PD, PDR, PDZ + 2.4, marks.reg / 700), { tone: "hi" }),
      Ln(markOn(PD, PDR, PDZ + 2.4, marks.stunt / 700), { tone: "mid" }),
      `<path data-part="p-needle" class="iso-line" data-tone="lit" data-free="start" d="${needle(PD, PDR - 2.4, PDZ + 2.6, 690 / 700)}" style="stroke-width:1.1"/>`,
      `<path data-part="t-needle" class="iso-line" data-tone="hi" data-free="start" d="${needle(TD, TDR - 2, TDZ + 2.2, (300 - 100) / 220)}" style="stroke-width:.9"/>`],
  });
  const DATA = { P, curve, usable: b.usable, m0: M.TANK.m0, stunt: mis.gas, impulse: b.impulse, weight: M.CAR.mass0 * M.G0, marks, pd: { c: PD, r: PDR - 2.4, z: PDZ + 2.6, full: 700 }, td: { c: TD, r: TDR - 2, z: TDZ + 2.2, lo: 100, span: 220 } };
  return { P, backSvg, DATA, items: R.items.length, budget: b, mission: mis };
}
