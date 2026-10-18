// Fig 5 · Flight envelope and physical limits: two charts and a limits ledger, all from model.mjs.
import * as M from "./model.mjs";
const fmt = (v, d = 0) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export function build() {
  // A · regulator set point vs hover seconds (one series)
  const pts = [];
  for (let bar = 40; bar <= 200; bar += 10) { const b = M.budget(bar * 1e5); pts.push({ bar, hover: b.hover, usable: b.usable, isp: b.isp }); }
  const W = 380, H = 200, L = 40, R = 12, T = 14, B = 30;
  const x = (bar) => L + ((bar - 40) / 160) * (W - L - R);
  const ymax = 6;
  const y = (v) => T + (1 - v / ymax) * (H - T - B);
  const grid = [0, 2, 4, 6].map((v) => `<line class="rr-c-grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="rr-c-tick" x="${L - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`).join("");
  const xt = [40, 80, 120, 160, 200].map((b) => `<text class="rr-c-tick" x="${x(b)}" y="${H - B + 15}" text-anchor="middle">${b}</text>`).join("");
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.bar).toFixed(1)} ${y(p.hover).toFixed(1)}`).join("");
  const dots = pts.map((p) => `<g class="rr-c-hit" data-tip="穩壓室 ${p.bar} bar · 可用 ${fmt(p.usable)} kg · Isp ${fmt(p.isp)} s · 懸停 ${fmt(p.hover, 1)} s"><rect x="${x(p.bar) - 9}" y="${T}" width="18" height="${H - T - B}" fill="transparent"/><circle class="rr-c-dot" cx="${x(p.bar)}" cy="${y(p.hover)}" r="3.4"/></g>`).join("");
  const d100 = pts.find((p) => p.bar === 100);
  const chartA = `<svg class="rr-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="減壓閥設定壓力與可懸停秒數：40 bar 約 ${fmt(pts[0].hover, 1)} 秒，200 bar 約 ${fmt(pts[pts.length - 1].hover, 1)} 秒">${grid}${xt}<text class="rr-c-axis" x="${(W + L) / 2}" y="${H - 3}" text-anchor="middle">穩壓室壓力（bar）</text><path class="rr-c-line" d="${path}"/><line class="rr-c-ref" x1="${x(100)}" x2="${x(100)}" y1="${T}" y2="${H - B}"/><text class="rr-c-note" x="${x(100) + 5}" y="${T + 10}">本頁設計點 100 bar · ${fmt(d100.hover, 1)} s</text>${dots}</svg>`;
  // B · what one fill buys (single series bars + a reference line at the usable mass)
  const b = M.budget();
  const mis = M.mission();
  const tank = M.tankAtMass(M.TANK.m0);
  const all = M.THRUSTERS.reduce((s, t) => s + M.nozzle(M.PC_DESIGN, M.regulatorT(tank, M.PC_DESIGN), t.At, t.eps).mdot, 0);
  const rows = [
    ["跳躍＋360° 翻滾＋減速著地", mis.gas],
    ["2 t 全重懸停 1 秒", (M.CAR.mass0 * M.G0) / b.ceff],
    ["兩顆後推進器 1 秒（100 kN）", 2 * M.nozzle(M.PC_DESIGN, M.regulatorT(tank, M.PC_DESIGN), M.THRUSTERS[0].At, M.EPS_DESIGN).mdot],
    ["十顆全開 1 秒", all],
  ];
  const BW = 380, BH = 34 * rows.length + 40, BL = 172, BR = 40;
  const bx = (v) => BL + (v / 300) * (BW - BL - BR);
  const bars = rows.map(([label, v], i) => {
    const yy = 12 + i * 34;
    return `<g class="rr-c-hit" data-tip="${label}：${fmt(v)} kg（可用量的 ${fmt((v / b.usable) * 100)}%）"><rect x="0" y="${yy - 4}" width="${BW}" height="30" fill="transparent"/><text class="rr-c-label" x="${BL - 8}" y="${yy + 14}" text-anchor="end">${label}</text><path class="rr-c-bar" d="M${BL} ${yy + 3}h${(bx(v) - BL - 4).toFixed(1)}a4 4 0 0 1 4 4v8a4 4 0 0 1 -4 4h-${(bx(v) - BL - 4).toFixed(1)}z"/><text class="rr-c-val" x="${Math.min(bx(v), BW - BR) + 6}" y="${yy + 14}">${fmt(v)}</text></g>`;
  }).join("");
  const chartB = `<svg class="rr-chart" viewBox="0 0 ${BW} ${BH}" role="img" aria-label="一罐可用 ${fmt(b.usable)} 公斤空氣能做什麼">${bars}<line class="rr-c-ref" x1="${bx(b.usable)}" x2="${bx(b.usable)}" y1="4" y2="${BH - 26}"/><text class="rr-c-note" x="${bx(b.usable)}" y="${BH - 12}" text-anchor="middle">可用 ${fmt(b.usable)} kg</text><text class="rr-c-axis" x="${BL - 8}" y="${BH - 12}" text-anchor="end">單位：kg 空氣</text></svg>`;
  const H2 = M.hazards();
  const r8 = M.suction(180, 0, 8);
  const ledger = [
    ["氣體預算", `可用 ${fmt(b.usable)} kg、總衝量 ${fmt(b.impulse / 1000, 1)} kN·s：懸停上限 ${fmt(b.hover, 1)} s，十顆全開 ${fmt(b.fullThrust, 2)} s 用完。「飛行」只能是秒級的跳與滾，不是飛。`],
    ["比衝", `冷空氣 Isp ≈ ${fmt(b.isp)} s（實際平均有效排氣速度 ${fmt(b.ceff)} m/s），約為化學火箭的 1/5：同樣衝量要吞下五倍質量的工質。`],
    ["絕熱降溫", `放到失壓點時瓶內 ${fmt(b.end.T)} K；噴嘴出口靜溫 70–80 K，低於空氣露點，必須用露點 < −70 °C 的乾燥空氣並加熱閥體，否則冰堵。`],
    ["殘氣", `減壓閥需要瓶壓高於穩壓室 10 bar，${fmt(b.residual)} kg（${fmt((b.residual / M.TANK.m0) * 100)}%）永遠用不到；降低設定壓力能多用一點，但噴嘴要變大、推力變小（左圖）。`],
    ["補充能量", `充滿一次至少 ${fmt(H2.fillKWh)} kWh 等溫壓縮功，實際約 ${fmt(H2.fillReal)} kWh，接近整顆電池；車上無法自己補氣。`],
    ["儲能風險", `690 bar × 600 L 約 ${fmt(H2.burstMJ)} MJ（≈ ${fmt(H2.tnt, 1)} kg TNT）。COPV 曾牽涉 SpaceX 的數次火箭事故；Musk 在發表會上說「安全不是主要目標」。`],
    ["吸力極限", `平地 2 g，天花板只剩 0.9 g 正壓力；凹弧坡道半徑 8 m 時裙邊漏氣、吸力崩到 ${fmt(r8.fanG, 2)} g，只能靠速度（v²/R ≥ g）撐住。`],
    ["著地", `不用推進器減速，從 5.6 m 落下在 20 cm 懸吊行程裡是 ~28 g；每次特技都得留 ~${fmt(mis.burns.brake * 4 * 23)} kg 氣做煞車。`],
  ];
  return { chartA, chartB, ledger, budget: b };
}
