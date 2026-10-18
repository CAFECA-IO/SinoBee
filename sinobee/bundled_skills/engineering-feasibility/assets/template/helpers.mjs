// Drawing helpers shared by the 2D figures: every helper records the true shape of what it draws
// into the figure's recorder (A.recorder), so the audit sees everything. Adapted from the Raptor's helpers.
import * as k from "./kit/iso-kit.mjs";
import * as G from "./kit/lathe.mjs";
import { tubePieces, tubeSvg, crossingsOf } from "./kit/tube.mjs";
import * as A from "./kit/audit.mjs";

export function helpers(P, R) {
  const V = G.viewOf(P);
  const KS = G.scaleOf(P);
  const at = (p) => k.iso(p, P);
  const S = (paths, style) => k.solidSvg(paths, style);
  const Ln = (d, style) => k.lineSvg(d, style);
  const Dots = (points, style) => k.dotsSvg(points, style);
  const record = R.solid;
  const { nameFor } = R;
  const frameOf = (o, a, u) => {
    const axis = G.unit3(a);
    const uu = u ? G.unit3(G.sub3(u, G.mul3(axis, G.dot3(u, axis)))) : G.frameAlong(o, axis).u;
    return { o, a: axis, u: uu, v: G.cross3(axis, uu) };
  };
  const put = (ref, svg, { bias = 0, shapes = [], name, layer = "front", route, chunk, key } = {}) =>
    R.put({ piece: "rig", at: ref, bias, svg, shapes, name: name ?? nameFor("item"), layer, route, chunk, key });

  function tubeItems(route, r, { name, tone = "hi", bias = 0, layer, maxLength, spacing, owner, touch, bundle, limp = false, breaks = [], gaps = [], caps = true, ends = [true, true], rims, free = [false, false], rings } = {}) {
    const id = nameFor(name ?? "tube");
    R.route(id, route, r, { owner: owner ?? id, touch, bundle, limp, gaps, ends, rims, free, piece: "rig" });
    tubePieces(route, r, P, { maxLength, spacing, breaks, gaps, caps, rings }).forEach((part, index) =>
      put(part.mid, tubeSvg(part, { tone }), { bias, layer, name: `${id}:${index}`, route: id, chunk: index, shapes: [A.tube(part.points, part.radii)] }),
    );
    return id;
  }

  function turned(F, profile, style = {}, { extra = "", inner = "", bias = 0, layer, rims, bevel = 0, steps, name, owner, smooth, ground = false } = {}) {
    const soft = smooth ?? profile.some((point) => point[2]);
    const paths = G.solidOf(profile, F, P, { bevel, smooth: soft === true ? { rows: Math.max(...profile.map(([, r]) => r)) < 20 ? 3 : 5 } : soft, ...(rims ? { rims } : {}), ...(steps ? { steps } : {}) });
    const mid = (profile[0][0] + profile[profile.length - 1][0]) / 2;
    const id = nameFor(name ?? "turned");
    const shape = record(A.solid(F, profile, { name: id, owner, ground, curve: soft && !soft.slope ? profile : undefined }));
    put(G.add3(F.o, G.mul3(F.a, mid)), S(paths, { tone: "hi", ...style, inner: inner ? Ln(inner, { tone: "faint" }) : "" }) + extra, { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  // A curved turned body from an analytic radius r(s) and its slope, sampled at n knots (smooth shading, no wobble).
  function curved(F, s0, s1, radius, slope, n = 24, style = {}, { extra = "", inner = "", bias = 0, layer, name, owner } = {}) {
    const knots = Array.from({ length: n + 1 }, (_, i) => { const s = s0 + ((s1 - s0) * i) / n; return [s, radius(s), 1]; });
    const id = nameFor(name ?? "curved");
    const shape = record(A.solid(F, knots, { name: id, owner, radius }));
    const paths = G.solidOf(knots, F, P, { smooth: { slope, radius } });
    put(G.add3(F.o, G.mul3(F.a, (s0 + s1) / 2)), S(paths, { tone: "hi", ...style, inner: inner ? Ln(inner, { tone: "faint" }) : "" }) + extra, { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  function disc(F, s0, s1, r, style = {}, { extra = "", inner = "", bias = 0, layer, name, owner, bevel = 0 } = {}) {
    const id = nameFor(name ?? "disc");
    const shape = record(A.disc(F, s0, s1, r, { name: id, owner }));
    put(G.add3(F.o, G.mul3(F.a, (s0 + s1) / 2)), S(G.disc(s0, s1, r, F, P, { bevel }), { tone: "hi", ...style, inner }) + extra, { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  function flangeAt(centre, axis, r, thick, { bolts = 8, tone = "hi", bias = 0, boltR, name, owner, layer } = {}) {
    const F = G.frameAlong(centre, axis);
    const id = nameFor(name ?? "flange");
    const paths = G.disc(-thick / 2, thick / 2, r, F, P);
    const faces = [];
    const towards = G.dot3(F.a, V) > 0 ? thick / 2 : -thick / 2;
    if (Math.abs(G.dot3(F.a, V)) > 0.12 && bolts) faces.push(Dots(G.dotsOf(F, towards, boltR ?? r * 0.74, bolts, P, { all: true }), { size: 0.42, tone: "mid" }));
    const shape = record(A.disc(F, -thick / 2, thick / 2, r, { name: id, owner }));
    put(centre, S(paths, { tone }) + faces.join(""), { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  function pipe(control, r, { bend = 2.6, tone = "hi", flanges = [true, true], flangeR = 1.55, bands = [], move, bias = 0, maxLength = 26, spacing, name, touch, breaks = [], gaps: extra = [], free, layer, rings, ferrule = null } = {}) {
    const flangeRs = [flangeR].flat().length > 1 ? [flangeR].flat() : [flangeR, flangeR].flat();
    const line = G.fillet(control, Math.max(r * bend, 3), r > 3 ? 24 : 12, { P, tube: r });
    const total = G.pathLength(line);
    const collars = [];
    [[0, -1], [total, 1]].forEach(([along, sign], index) => {
      if (!flanges[index]) return;
      const thick = ferrule ? ferrule : Math.max(0.9, r * 0.55);
      const tangent = G.tangentAlong(line, along);
      collars.push({ kind: ferrule ? "ferrule" : "flange", centre: G.add3(G.pointAlong(line, along), G.mul3(tangent, (-sign * thick) / 2)), tangent, radius: r * flangeRs[index], thick, gap: sign < 0 ? [0, thick] : [total - thick, total] });
    });
    for (const share of bands) {
      const along = total * share;
      const thick = Math.max(0.8, r * 0.7);
      collars.push({ kind: "band", centre: G.pointAlong(line, along), tangent: G.tangentAlong(line, along), radius: r * 1.18, thick, gap: [along - thick / 2, along + thick / 2] });
    }
    const gaps = [...collars.map(({ gap }) => gap), ...extra];
    const owner = tubeItems(line, r, { name: name ?? "pipe", tone, bias, layer, maxLength, spacing: spacing ?? Math.max(0.8, Math.min(2.4, r * 0.18)), touch, gaps, breaks, free, rings, rims: flanges.map((flanged, index) => (flanged ? r * flangeRs[index] : r)) });
    for (const { kind, centre, tangent, radius, thick } of collars) flangeAt(centre, tangent, radius, thick, { bolts: kind === "flange" && r > 3 ? 6 : 0, tone: kind === "band" ? "mid" : tone, bias, name: `${owner}.${kind}`, owner, layer });
    line.owner = owner;
    return line;
  }

  function boxAt(plan, z, h, style = {}, { extra = "", inner = "", bias = 0, bevel = 0.5, steps = 3, name, owner, layer, ground = false } = {}) {
    const centre = [plan.x + plan.w / 2, plan.y + plan.d / 2, z + h / 2];
    const id = nameFor(name ?? "box");
    const shape = record(A.slab(plan, z, h, { name: id, owner, ground }, steps));
    put(centre, S(k.slabOf(plan, z, h, P, steps, bevel), { tone: "hi", ...style, inner }) + extra, { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  function cylAt(x, y, r, z, h, style = {}, { extra = "", inner = "", bias = 0, steps = 32, bevel = 0, name, owner, layer, ground = false } = {}) {
    const id = nameFor(name ?? "cyl");
    const shape = record(A.cylinder(x, y, r, z, h, { name: id, owner, ground }));
    put([x, y, z + h / 2], S(k.cylinder(x, y, r, z, h, P, steps, bevel), { tone: "hi", ...style, inner }) + extra, { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  function ballAt(centre, r, { tone = "mid", bias = 0, name, owner, layer } = {}) {
    const id = nameFor(name ?? "ball");
    const shape = record(A.ball(centre, r, { name: id, owner }));
    put(centre, S(G.sphereOf(centre, r, P, { steps: G.stepsFor(r, 32) }), { tone }), { bias, layer, name: id, shapes: [shape] });
    return shape;
  }

  const CLAMP_HALF = 0.9;
  function clampAt(centre, axis, r, { bias = 0.3, name = "clamp", bundle, layer } = {}) {
    const F = G.frameAlong(centre, axis);
    const id = nameFor(name);
    put(centre, S(G.disc(-CLAMP_HALF, CLAMP_HALF, r, F, P), { tone: "mid" }), { bias, layer, name: id, shapes: [record(A.disc(F, -CLAMP_HALF, CLAMP_HALF, r, { name: id, anchor: false, bundle, fitting: true }))] });
    return { centre, axis, radius: r };
  }

  function cable(points, { bias = 0.2, r = 0.62, bend = 6, name, bundle, touch, clamps = [], shells = [false, false], free, layer } = {}) {
    const route = G.fillet(points, bend, 12, { P, tube: r });
    const total = G.pathLength(route);
    const SHELL = 2.2;
    const gaps = [...clamps.flatMap(({ centre, axis, radius }) => crossingsOf(route, centre, axis, radius).map((s) => [s - CLAMP_HALF - 0.05, s + CLAMP_HALF + 0.05])), ...(shells[0] ? [[0, SHELL]] : []), ...(shells[1] ? [[total - SHELL, total]] : [])];
    const id = tubeItems(route, r, { name: name ?? "cable", tone: "mid", bias, layer, maxLength: 14, spacing: 1, bundle, touch, gaps, free, limp: true, rims: shells.map((on) => (on ? r * 2 : r)) });
    [0, 1].forEach((index) => {
      const end = index ? route[route.length - 1] : route[0];
      if (!shells[index]) {
        put(end, Dots([at(end)], { size: 0.5, tone: "hi" }), { bias: bias + 0.15, layer, name: `${id}.end`, shapes: [A.ball(end, 0.5 / KS)] });
        return;
      }
      const tangent = G.tangentAlong(route, index ? total : 0);
      const F = G.frameAlong(end, tangent);
      const [s0, s1] = index ? [-SHELL, 0] : [0, SHELL];
      put(G.add3(end, G.mul3(tangent, (s0 + s1) / 2)), S(G.disc(s0, s1, r * 2, F, P, { bevel: 0.25 }), { tone: "mid" }), { bias: bias + 0.1, layer, name: `${id}.shell`, shapes: [record(A.disc(F, s0, s1, r * 2, { name: `${id}.shell`, owner: id }))] });
    });
    return route;
  }

  return { V, KS, at, S, Ln, Dots, frameOf, put, tubeItems, turned, curved, disc, flangeAt, pipe, boxAt, cylAt, ballAt, clampAt, cable, record, nameFor };
}
