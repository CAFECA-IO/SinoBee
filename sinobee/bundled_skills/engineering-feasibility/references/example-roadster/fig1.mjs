// Fig 1 · A cold-gas thruster on a thrust stand: regulator → solenoid valve → settling chamber → nozzle,
// on a carriage that pushes a load cell. Every reading comes from model.mjs.
import * as k from "./kit/iso-kit.mjs";
import * as G from "./kit/lathe.mjs";
import * as A from "./kit/audit.mjs";
import { glCamera } from "./kit/gl.mjs";
import { helpers } from "./helpers.mjs";
import * as M from "./model.mjs";

export const W = 780;
export const H = 440;
const AZIMUTH = 58;
const ELEVATION = 27;
const DEG = Math.PI / 180;

// World units are centimetres. The nozzle axis runs along x; the jet leaves toward −x.
const Z0 = 34;
const XJ = -84; // the nozzle / chamber flange joint
const THROAT_R = (M.THRUSTERS[0].dt * 100) / 2; // 3.2 cm (50 kN at 100 bar)
const EXIT_R = (M.THRUSTERS[0].de * 100) / 2; // 9.1 cm
const S_THROAT = 10.5;
const S_EXIT = 32.6;
const WALL = 1.4;
const B = 1.2;
const cOf = (s) => (s < S_THROAT ? 0.9 : Math.sqrt(((EXIT_R - THROAT_R + B) ** 2 - B * B) / (S_EXIT - S_THROAT) ** 2));
const inner = (s) => THROAT_R - B + Math.sqrt(B * B + (cOf(s) * (s - S_THROAT)) ** 2);
const innerSlope = (s) => (cOf(s) ** 2 * (s - S_THROAT)) / Math.sqrt(B * B + (cOf(s) * (s - S_THROAT)) ** 2);
const outer = (s) => inner(s) + WALL;
export const XE = XJ - S_EXIT - 1.2; // exit plane (after the lip)

const PLATE = { x: -134, y: -52, w: 258, d: 104, r: 6 };
const PLATE_H = 9;
const FOOT_H = 6;
const BOX = { x: 30, y: -46, w: 42, d: 32, r: 3 };
const BOX_H = 22;
const DIAL = [56, -32];
const DIAL_R = 10.5;
const DIAL_TOP = BOX_H + 2.4;
const PEDESTAL = { x: 2, y: -13, w: 15, d: 26, r: 2 };
const PED_H = 30;

export function build({ theme = "light", audit = false } = {}) {
  const fit = [
    ...k.boxCorners(PLATE, -PLATE_H - FOOT_H, 0),
    [XE - 150, 0, Z0 + 26],
    [XE - 150, 0, Z0 - 16],
    [XE - 150, -18, Z0],
    [XE - 150, 18, Z0],
    [-76, 0, 72],
    [50, -20, 72],
  ];
  const P = k.fitProjection(fit, W, H, { pad: 22, azimuth: AZIMUTH, elevation: ELEVATION });
  const R = A.recorder(P);
  const h = helpers(P, R);
  const { at, S, Ln, Dots, frameOf, put, turned, disc, flangeAt, pipe, boxAt, cylAt, cable } = h;
  const V = h.V;
  const back = [];

  // ---- Bedplate on four levelling feet, T-slots, ruler strip and label plate
  back.push(`<path class="iso-halo" d="${k.haloOf(PLATE, -PLATE_H - FOOT_H, PLATE_H, P)}"/>`);
  const byDepth = (pts) => [...pts].sort((a, b) => k.depthOf([a[0], a[1], 0], P) - k.depthOf([b[0], b[1], 0], P));
  for (const [x, y] of byDepth(k.corners(PLATE, 16))) {
    cylAt(x, y, 7, -PLATE_H - FOOT_H, 1.6, { tone: "lo" }, { name: "stand.foot.pad", ground: true, layer: "back" });
    cylAt(x, y, 5, -PLATE_H - FOOT_H + 1.6, FOOT_H - 1.6, { tone: "mid" }, { name: "stand.foot", layer: "back", extra: Ln(k.knurl(x, y, 5, -PLATE_H - FOOT_H + 2, -PLATE_H - 0.6, 20, P), { tone: "lo" }) });
  }
  const slots = [-30, -8, 22, 36].map((y) => k.planOutline({ x: PLATE.x + 8, y: y - 1.6, w: PLATE.w - 16, d: 3.2, r: 1.6 }, 0, P, 3)).join("");
  const screws = k.corners(PLATE, 6);
  boxAt(PLATE, -PLATE_H, PLATE_H, { tone: "mid" }, {
    name: "stand.plate", bevel: 1.4, steps: 8, layer: "back",
    inner: Ln(k.sideSeam(PLATE, -PLATE_H + 3, P, 8), { tone: "faint" }),
    extra: Ln(slots, { tone: "faint" }) + Ln(screws.map(([x, y]) => k.ring(x, y, 2, 0, P, 14)).join(""), { tone: "lo" }) + Dots(screws.map(([x, y]) => at([x, y, 0])), { size: 0.5 }),
  });
  const strip = { x: PLATE.x + 12, y: PLATE.y + PLATE.d - 9, w: 170, d: 6, r: 1 };
  const ticks = k.topTicks(strip.x + 3, strip.x + strip.w - 3, 5, 5, strip.y + 0.6, 0.6, [1.8, 3.4], P);
  boxAt(strip, 0, 0.6, { tone: "lo", crease: "none" }, { name: "stand.ruler", bevel: 0, steps: 2, layer: "back", extra: Ln(ticks.minor, { tone: "lo" }) + Ln(ticks.major, { tone: "mid" }) + Dots([at([strip.x + 1.6, strip.y + 3, 0.6]), at([strip.x + strip.w - 1.6, strip.y + 3, 0.6])], { size: 0.42 }) });
  const label = { x: 84, y: 20, w: 30, d: 18, r: 1.5 };
  boxAt(label, 0, 0.8, { tone: "mid" }, {
    name: "stand.label", bevel: 0.4, steps: 3, layer: "back",
    extra: Ln(k.planOutline(k.insetPlan(label, 1.6), 0.8, P, 3), { tone: "faint" }) +
      [[4, 26], [8, 20], [12, 23]].map(([dy, len]) => Ln(k.lineOnTop([label.x + 3.4, label.y + dy], [label.x + 3.4 + len, label.y + dy], 0.8, P), { tone: "lo", free: true })).join("") +
      Dots([at([label.x + 2, label.y + 15.6, 0.8]), at([label.x + label.w - 2, label.y + 15.6, 0.8])], { size: 0.4 }),
  });

  // ---- Linear rails, end stops, carriage on four bushings
  for (const y of [-14, 9]) {
    boxAt({ x: -96, y, w: 94, d: 5, r: 1 }, 0, 3.4, { tone: "mid" }, { name: "stand.rail", bevel: 0.4, layer: "back", extra: Dots([-90, -50, -10].map((x) => at([x, y + 2.5, 3.4])), { size: 0.38 }) });
  }
  boxAt({ x: -102, y: -16, w: 5, d: 32, r: 1.2 }, 0, 7, { tone: "mid" }, { name: "stand.stop", bevel: 0.5, layer: "back", extra: Dots([at([-99.5, -11, 7]), at([-99.5, 11, 7])], { size: 0.42 }) });
  for (const x of [-88, -20]) for (const y of [-14.5, 8.5]) boxAt({ x, y, w: 12, d: 6, r: 1 }, 3.4, 3.2, { tone: "mid" }, { name: "rig.bushing", bevel: 0.3 });
  const CAR = { x: XJ - 8, y: -17, w: 86, d: 34, r: 2.5 };
  const CAR_Z = 6.6;
  const CAR_TOP = 10.4;
  boxAt(CAR, CAR_Z, CAR_TOP - CAR_Z, { tone: "mid" }, {
    name: "rig.carriage", bevel: 0.6, steps: 4,
    inner: Ln(k.sideSeam(CAR, CAR_Z + 1.4, P, 4), { tone: "faint" }),
    extra: Dots([-86, -70, -54, -38, -22, -8].map((x) => at([x, CAR.y + CAR.d - 2.2, CAR_TOP])), { size: 0.38, tone: "lo" }),
  });

  // ---- Saddles that carry the stack
  const PLENUM_R = 10.6;
  const REG_R = 8.2;
  const saddle = (x, w, r, name) => boxAt({ x: x - w / 2, y: -7, w, d: 14, r: 1 }, CAR_TOP, Z0 - r - CAR_TOP, { tone: "mid" }, { name, bevel: 0.4, extra: Dots([at([x - w / 2 + 1.6, -5, Z0 - r]), at([x + w / 2 - 1.6, -5, Z0 - r])], { size: 0.36 }) });
  saddle(XJ + 12, 8, PLENUM_R, "rig.saddle.chamber");
  boxAt({ x: XJ + 25.2, y: -9, w: 20, d: 18, r: 1.2 }, CAR_TOP, Z0 - 11 - CAR_TOP, { tone: "mid" }, { name: "rig.pedestal.valve", bevel: 0.4 });
  saddle(XJ + 46.2 + 8, 7, REG_R, "rig.saddle.regulator");

  // ---- Nozzle: convergent–divergent, flange, exit lip
  const FN = frameOf([XJ, 0, Z0], [-1, 0, 0], [0, 0, 1]);
  const knots = Array.from({ length: 29 }, (_, i) => {
    const s = 2.2 + ((S_EXIT - 2.2) * i) / 28;
    return [s, outer(s), 1];
  });
  const nozzleSeams = [6, 20].map((s) => G.arcOf(FN, s, outer(s), P, { slope: innerSlope(s) })).join("");
  {
    const paths = G.solidOf(knots, FN, P, { smooth: { slope: innerSlope, radius: outer } });
    const shape = h.record(A.solid(FN, knots, { name: "rig.nozzle", radius: outer }));
    put(G.add3(FN.o, G.mul3(FN.a, 16)), S(paths, { tone: "hi", inner: Ln(nozzleSeams, { tone: "faint" }) }), { name: "rig.nozzle", shapes: [shape] });
  }
  disc(FN, S_EXIT, S_EXIT + 1.2, outer(S_EXIT) + 0.7, { tone: "hi" }, { name: "rig.nozzle.lip", bevel: 0.3 });
  disc(FN, 0, 2.2, 13.6, { tone: "hi" }, { name: "rig.nozzle.flange" });

  // ---- Settling chamber (plenum): two flanges, a transducer boss on top
  const FC = frameOf([XJ, 0, Z0], [1, 0, 0], [0, 0, 1]);
  const boltsOn = (F, s, r, n, phase = 0.2) => Dots(G.dotsOf(F, s, r, n, P, { all: true, phase }), { size: 0.46, tone: "mid" });
  disc(FC, 0, 2.2, 13.6, { tone: "hi" }, { name: "rig.chamber.flange", extra: boltsOn(FC, 2.2, 12, 10) });
  turned(FC, [[2.2, PLENUM_R], [22, PLENUM_R]], { tone: "hi" }, { name: "rig.chamber", inner: [6.2, 18].map((s) => G.arcOf(FC, s, PLENUM_R, P)).join("") });
  disc(FC, 22, 24.2, 13.6, { tone: "hi" }, { name: "rig.chamber.flange2", extra: boltsOn(FC, 24.2, 12, 10) });
  const TX = XJ + 9;
  const bossBase = Z0 + Math.sqrt(PLENUM_R ** 2 - 2.4 ** 2);
  cylAt(TX, 0, 2.4, bossBase, Z0 + 13.6 - bossBase, { tone: "mid" }, { name: "rig.transducer.boss" });
  cylAt(TX, 0, 2.9, Z0 + 13.6, 3, { tone: "mid" }, { name: "rig.transducer.hex", steps: 6 });
  cylAt(TX, 0, 2.5, Z0 + 16.6, 8.4, { tone: "hi" }, { name: "rig.transducer", inner: Ln(k.sideArc(TX, 0, 2.5, Z0 + 21, P, 16), { tone: "faint" }) });
  cylAt(TX, 0, 1.6, Z0 + 25, 2.4, { tone: "mid" }, { name: "rig.transducer.plug", extra: Ln(k.ring(TX, 0, 0.8, Z0 + 27.4, P, 12), { tone: "lo" }) });

  // ---- Solenoid valve block with coil can and connector
  const VB = { x: XJ + 24.2, y: -11, w: 22, d: 22, r: 1.6 };
  const vbScrews = k.corners(VB, 2.6);
  boxAt(VB, Z0 - 11, 22, { tone: "hi" }, { name: "rig.valve.block", bevel: 0.6, steps: 3, inner: Ln(k.sideSeam(VB, Z0 - 4, P, 3), { tone: "faint" }), extra: Dots(vbScrews.map(([x, y]) => at([x, y, Z0 + 11])), { size: 0.45 }) });
  const COIL = [XJ + 35.2, 0];
  cylAt(COIL[0], COIL[1], 6.6, Z0 + 11, 12.4, { tone: "hi" }, { name: "rig.valve.coil", inner: Ln(k.sideArc(COIL[0], COIL[1], 6.6, Z0 + 14, P, 32) + k.sideArc(COIL[0], COIL[1], 6.6, Z0 + 20.4, P, 32), { tone: "faint" }) });
  cylAt(COIL[0], COIL[1], 7.1, Z0 + 23.4, 1.6, { tone: "mid" }, { name: "rig.valve.cap", bevel: 0.3, extra: Dots(G.dotsOf(G.frameOf([COIL[0], COIL[1], 0], [0, 0, 1], [1, 0, 0], [0, 1, 0]), Z0 + 25, 5.6, 4, P, { all: true, phase: 0.4 }), { size: 0.42 }) });
  cylAt(COIL[0], COIL[1], 2.1, Z0 + 25, 3.4, { tone: "mid" }, { name: "rig.valve.plug" });

  // ---- Dome regulator: flange, body, bonnet, spring housing, knurled set knob, inlet port, outlet gauge
  const FR = frameOf([XJ + 46.2, 0, Z0], [1, 0, 0], [0, 0, 1]);
  disc(FR, 0, 2.2, 11.4, { tone: "hi" }, { name: "rig.reg.flange", extra: boltsOn(FR, 2.2, 10, 6, 0.5) });
  turned(FR, [[2.2, REG_R], [14, REG_R]], { tone: "hi" }, { name: "rig.reg.body", inner: G.arcOf(FR, 9, REG_R, P) });
  const bonnet = (s) => REG_R + 1.5 * Math.sin((Math.PI * (s - 14)) / 13);
  const bonnetSlope = (s) => ((1.5 * Math.PI) / 13) * Math.cos((Math.PI * (s - 14)) / 13);
  const bonnetKnots = Array.from({ length: 21 }, (_, i) => [14 + (13 * i) / 20, bonnet(14 + (13 * i) / 20), 1]);
  {
    const paths = G.solidOf(bonnetKnots, FR, P, { smooth: { slope: bonnetSlope, radius: bonnet } });
    const shape = h.record(A.solid(FR, bonnetKnots, { name: "rig.reg.bonnet", radius: bonnet }));
    put(G.add3(FR.o, G.mul3(FR.a, 20.5)), S(paths, { tone: "hi", inner: Ln(G.arcOf(FR, 20.5, bonnet(20.5), P), { tone: "faint" }) }), { name: "rig.reg.bonnet", shapes: [shape] });
  }
  turned(FR, [[27, 5.4], [31, 5.4]], { tone: "mid" }, { name: "rig.reg.housing" });
  const KNOB = [31, 37, 6.2];
  disc(FR, 31, 31.8, 4.6, { tone: "mid" }, { name: "rig.reg.locknut" });
  const ribs = G.ribsOf(FR, KNOB[0] + 1.4, KNOB[1] - 0.5, KNOB[2], 40, P, { fade: [0.1, 0.55], seams: true });
  disc(FR, KNOB[0] + 0.8, KNOB[1], KNOB[2], { tone: "mid" }, { name: "rig.reg.knob", bevel: 0.35, extra: k.fadedSvg(ribs, { tone: "lo" }) });
  const KNOB_FACE = G.pointOf(FR, KNOB[1], 0, 0);
  const knobU = G.sub3(G.pointOf(FR, KNOB[1], 1, 0), KNOB_FACE);
  const knobV = G.sub3(G.pointOf(FR, KNOB[1], 1, Math.PI / 2), KNOB_FACE);
  // inlet port on the near side (+y), where the supply hose lands
  const PORT_S = 8;
  const PORT_R = 2.4;
  const portBase = Math.sqrt(REG_R ** 2 - PORT_R ** 2);
  const FPORT = frameOf([FR.o[0] + PORT_S, 0, Z0], [0, 1, 0], [0, 0, 1]);
  disc(FPORT, portBase, REG_R + 2.6, PORT_R, { tone: "mid" }, { name: "rig.reg.inlet" });
  // outlet gauge on top of the body
  const GAUGE = [FR.o[0] + 8, 0];
  const gaugeBase = Z0 + Math.sqrt(REG_R ** 2 - 1.6 ** 2);
  cylAt(GAUGE[0], GAUGE[1], 1.6, gaugeBase, Z0 + 12.6 - gaugeBase, { tone: "mid" }, { name: "rig.gauge.stem" });
  const GZ = Z0 + 12.6;
  const GR = 6.4;
  cylAt(GAUGE[0], GAUGE[1], GR, GZ, 3.2, { tone: "hi" }, {
    name: "rig.gauge", bevel: 0.4, steps: 48,
    extra: Ln(k.ring(GAUGE[0], GAUGE[1], GR - 1.2, GZ + 3.2, P, 48), { tone: "lo" }) +
      (() => { const t = k.radialTicks(GAUGE[0], GAUGE[1], GR - 1.5, 24, 4, GZ + 3.2, [0.8, 1.6], P, -0.75 * Math.PI, 1.5 * Math.PI); return Ln(t.minor, { tone: "lo" }) + Ln(t.major, { tone: "mid" }); })(),
  });

  // ---- Supply bulkhead on the bedplate and the braided hose to the regulator inlet
  const BULK = { x: FPORT.o[0] - 5, y: 31, w: 10, d: 10, r: 1.2 };
  boxAt(BULK, 0, 6, { tone: "mid" }, { name: "stand.bulkhead", bevel: 0.4, layer: "back", extra: Dots(k.corners(BULK, 1.8).map(([x, y]) => at([x, y, 6])), { size: 0.4 }) });
  const HOSE_R = 1.5;
  const hose = pipe([[FPORT.o[0], 36, 6], [FPORT.o[0], 36, Z0], [FPORT.o[0], REG_R + 2.6, Z0]], HOSE_R, {
    bend: 5, flangeR: 1.45, ferrule: 2.4, name: "rig.hose", tone: "hi", maxLength: 10,
    rings: { pitch: 1.2, twist: 1.4, cross: true, fade: [0.1, 0.55], tone: "lo" },
  });
  void hose;

  // ---- Load cell between the carriage's thrust plate and the fixed pedestal
  boxAt({ x: XJ + 69, y: -10, w: 4, d: 20, r: 0.8 }, CAR_TOP, 12, { tone: "mid" }, { name: "rig.thrustplate", bevel: 0.35, extra: Dots([at([XJ + 71, -7, CAR_TOP + 12]), at([XJ + 71, 7, CAR_TOP + 12])], { size: 0.38 }) });
  const LC_Z = CAR_TOP + 6;
  const FLC = frameOf([XJ + 73, 0, LC_Z], [1, 0, 0], [0, 0, 1]);
  disc(FLC, 0, 2.6, 1.3, { tone: "mid" }, { name: "rig.loadcell.stud" });
  const LCX = XJ + 75.6;
  boxAt({ x: LCX, y: -3.6, w: PEDESTAL.x - 2.4 - LCX, d: 7.2, r: 0.6 }, LC_Z - 6, 12, { tone: "hi" }, { name: "rig.loadcell", bevel: 0.3, steps: 2, inner: Ln(k.sideSeam({ x: LCX, y: -3.6, w: PEDESTAL.x - 2.4 - LCX, d: 7.2, r: 0.6 }, LC_Z, P, 2), { tone: "faint" }) });
  disc(frameOf([PEDESTAL.x - 2.4, 0, LC_Z], [1, 0, 0], [0, 0, 1]), 0, 2.4, 1.3, { tone: "mid" }, { name: "rig.loadcell.stud2" });
  boxAt(PEDESTAL, 0, PED_H, { tone: "mid" }, { name: "stand.pedestal", bevel: 0.6, layer: "back", inner: Ln(k.sideSeam(PEDESTAL, 4, P, 3), { tone: "faint" }), extra: Dots(k.corners(PEDESTAL, 2.4).map(([x, y]) => at([x, y, PED_H])), { size: 0.42 }) });
  for (const y of [-13, 9]) boxAt({ x: PEDESTAL.x + PEDESTAL.w, y, w: 9, d: 4, r: 0.6 }, 0, 12, { tone: "mid" }, { name: "stand.gusset", bevel: 0.3, layer: "back" });
  const lcPlug = [LCX + 3, -3.6, LC_Z];
  disc(frameOf([lcPlug[0], -3.6, LC_Z], [0, -1, 0], [0, 0, 1]), 0, 1.6, 1.2, { tone: "mid" }, { name: "rig.loadcell.plug" });

  // ---- Indicator box with a face-up thrust dial, three cable glands
  const glands = [[40, -17.5], [45, -17.5], [50, -17.5]];
  boxAt(BOX, 0, BOX_H, { tone: "mid" }, { name: "stand.indicator", bevel: 0.8, steps: 4, layer: "back", inner: Ln(k.sideSeam(BOX, 4, P, 4), { tone: "faint" }), extra: Dots(k.corners(BOX, 2.6).map(([x, y]) => at([x, y, BOX_H])), { size: 0.45 }) + Ln(k.planOutline(k.insetPlan(BOX, 2), BOX_H, P, 4), { tone: "faint" }) });
  for (const [x, y] of glands) cylAt(x, y, 1.5, BOX_H, 1.4, { tone: "mid" }, { name: "stand.gland", layer: "back", extra: Ln(k.ring(x, y, 0.8, BOX_H + 1.4, P, 12), { tone: "lo" }) });
  const dialTicks = k.radialTicks(DIAL[0], DIAL[1], DIAL_R - 2, 60, 5, DIAL_TOP, [1, 2.2], P, -0.75 * Math.PI, 1.5 * Math.PI);
  cylAt(DIAL[0], DIAL[1], DIAL_R, BOX_H, 2.4, { tone: "hi" }, {
    name: "stand.dial", bevel: 0.5, steps: 64, layer: "back",
    extra: Ln(k.ring(DIAL[0], DIAL[1], DIAL_R - 1.4, DIAL_TOP, P, 64), { tone: "lo" }) + Ln(dialTicks.minor, { tone: "lo" }) + Ln(dialTicks.major, { tone: "mid" }) + Ln(k.ring(DIAL[0], DIAL[1], 1.2, DIAL_TOP, P, 14), { tone: "mid" }),
  });

  // ---- Cables: transducer and coil run in two lanes behind the stack, the load cell's below them
  const glandTop = BOX_H + 1.4;
  cable([[TX, 0, Z0 + 27.4], [TX, 0, Z0 + 33], [TX, -17.5, Z0 + 33], [glands[1][0], -17.5, Z0 + 33], [glands[1][0], -17.5, glandTop]], { name: "rig.cable.tx", bend: 5 });
  cable([[COIL[0], 0, Z0 + 28.4], [COIL[0], 0, Z0 + 36.5], [COIL[0], -17.5, Z0 + 36.5], [glands[2][0], -17.5, Z0 + 36.5], [glands[2][0], -17.5, glandTop]], { name: "rig.cable.coil", bend: 5 });
  cable([[lcPlug[0], -5.2, LC_Z], [lcPlug[0], -17.5, LC_Z], [lcPlug[0], -17.5, PED_H + 3], [glands[0][0], -17.5, PED_H + 3], [glands[0][0], -17.5, glandTop]], { name: "rig.cable.lc", bend: 4 });

  // ---- Settle the painter's keys, split layers around the jet canvas
  A.settle(R, P);
  R.items.sort((a, b) => a.key - b.key);
  if (audit) A.auditOrExit(R, P, { argv: ["--audit"] });
  // Layers round the jet canvas: the nozzle covers the jet's root, so it and everything painted after it that
  // overlaps it go to the front SVG; back-layer parts that overlap nothing of the front stay under the jet.
  const nozzleKey = Math.min(...R.items.filter((item) => item.name.startsWith("rig.nozzle")).map((item) => item.key));
  const touching = new Map(R.items.map((item) => [item, new Set()]));
  for (const pair of A.overlapsOf(R.items, P).pairs) {
    touching.get(R.items[pair.a]).add(R.items[pair.b]);
    touching.get(R.items[pair.b]).add(R.items[pair.a]);
  }
  const front = new Set(R.items.filter((item) => item.layer !== "back" && item.key >= nozzleKey));
  let grew = true;
  while (grew) {
    grew = false;
    for (const item of R.items) {
      if (front.has(item)) continue;
      if ([...touching.get(item)].some((f) => front.has(f) && f.key < item.key)) { front.add(item); grew = true; }
    }
  }
  const backItems = R.items.filter((item) => !front.has(item));
  const frontItems = R.items.filter((item) => front.has(item));
  const late = frontItems.filter((item) => item.layer === "back");
  if (process.env.RR_DEBUG) console.log("late:", late.map((i) => i.name).join(", "));
  // live parts: Pc gauge needle, thrust dial needle (lit), knob pointer
  const needle = (c, r, z, frac, from = -0.75 * Math.PI, span = 1.5 * Math.PI) => {
    const a = from + span * frac;
    return k.lineOnTop([c[0] - Math.sin(a) * 1.6, c[1] + Math.cos(a) * 1.6], [c[0] + Math.sin(a) * r, c[1] - Math.cos(a) * r], z, P);
  };
  const knobPointer = (angle) => {
    const c = at(KNOB_FACE);
    const u = G.sub3(G.add3(KNOB_FACE, knobU), KNOB_FACE);
    void u;
    const p = (r, a) => at(G.add3(KNOB_FACE, G.add3(G.mul3(knobU, r * Math.cos(a)), G.mul3(knobV, r * Math.sin(a)))));
    return `M${p(1, angle).map((v) => v.toFixed(2)).join(" ")}L${p(KNOB[2] - 1.2, angle).map((v) => v.toFixed(2)).join(" ")}`;
  };
  void knobPointer;
  const fallback = jetFallback(P);
  const backSvg = k.figureSvg({
    width: W, height: H, className: "rr-back",
    label: "推力試驗台：底座上的線性滑軌承載一部滑車，滑車上依序為圓頂式減壓閥（帶滾花設定旋鈕與出口壓力錶）、電磁閥、穩壓室（頂部壓力感測器）與收斂—擴張噴嘴；滑車尾端頂住稱重元件，元件的電纜接到指示盒，盒頂的錶針顯示推力。",
    body: [...back, ...backItems.map((item) => item.svg), `<g class="rr-jet-fallback-slot">__FALLBACK__</g>`],
  });
  const frontSvg = `<svg xmlns="http://www.w3.org/2000/svg" class="iso-svg rr-layer" viewBox="0 0 ${W} ${H}" aria-hidden="true">${frontItems.map((item) => item.svg).join("")}
<path data-part="pc-needle" class="iso-line" data-tone="hi" data-free="start" d="${needle(GAUGE, GR - 2, GZ + 3.4, 0)}" style="stroke-width:.9"/>
<path data-part="knob-pointer" class="iso-line" data-tone="hi" d="" style="stroke-width:.9"/>
</svg>`;
  const dialSvg = `<svg xmlns="http://www.w3.org/2000/svg" class="iso-svg rr-layer" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path data-part="thrust-needle" class="iso-line" data-tone="lit" data-free="start" d="${needle(DIAL, DIAL_R - 2.6, DIAL_TOP + 0.2, 0)}" style="stroke-width:1.1"/></svg>`;

  // ---- Data for the page: the nozzle model sampled per bar, the jet geometry and camera
  const tank = M.tankAtMass(M.TANK.m0);
  const table = Array.from({ length: 221 }, (_, bar) => {
    const pc = bar * 1e5;
    const n = M.nozzle(pc, M.regulatorT(tank, pc), M.THRUSTERS[0].At, M.THRUSTERS[0].eps);
    return [+(n.F / 1000).toFixed(3), +n.mdot.toFixed(2), +n.isp.toFixed(1), +n.Te.toFixed(1), +(n.pe / M.PA).toFixed(3), n.cond ? 1 : 0, n.sep ? 1 : 0, +n.Me.toFixed(3), +(M.dewT(n.pe) - n.Te).toFixed(1)];
  });
  const DATA = {
    W, H, camera: glCamera(P), P,
    exit: [XE, 0, Z0], exitR: EXIT_R, table,
    gauge: { c: GAUGE, r: GR - 2, z: GZ + 3.4 }, dial: { c: DIAL, r: DIAL_R - 2.6, z: DIAL_TOP + 0.2, full: 60 },
    knob: { face: at(KNOB_FACE), u: G.sub3(at(G.add3(KNOB_FACE, knobU)), at(KNOB_FACE)).slice(0, 2), v: G.sub3(at(G.add3(KNOB_FACE, knobV)), at(KNOB_FACE)).slice(0, 2), r0: 1, r1: KNOB[2] - 1.2 },
    plate: [PLATE.x, PLATE.y, PLATE.x + PLATE.w, PLATE.y + PLATE.d],
  };
  return { P, R, backSvg: backSvg.replace("__FALLBACK__", fallback), frontSvg, dialSvg, fallback, DATA, late: late.length, items: R.items.length };
}

// The no-WebGL jet: three soft world-space cones drawn as SVG, hidden once the shader runs.
function jetFallback(P) {
  const ring = (s, r, n = 28) => Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2;
    return [XE - s, r * Math.cos(a), Z0 + r * Math.sin(a)];
  });
  const hull = (pts) => {
    const flat = pts.map((p) => k.iso(p, P));
    const hp = G.hullOf(flat);
    return `M${hp.map((p) => p.map((v) => v.toFixed(1)).join(" ")).join("L")}Z`;
  };
  const cone = (len, r0, r1) => hull([...ring(0, r0), ...ring(len, r1)]);
  return `<g class="rr-jet-fallback"><path class="rr-jet-fog" d="${cone(170, EXIT_R, EXIT_R + 170 * Math.tan(7 * DEG))}"/><path class="rr-jet-core" d="${cone(110, EXIT_R * 0.92, EXIT_R * 0.7)}"/></g>`;
}
