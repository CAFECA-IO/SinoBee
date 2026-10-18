// Builds the Roadster Rocket anatomy page: every figure, the limits panel and the captions, one model.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "./kit/iso-kit.mjs";
import { kitScript, glScript, turnScript } from "./kit/inline-kit.mjs";
import * as M from "./model.mjs";
import * as F1 from "./fig1.mjs";
import * as F2 from "./fig2.mjs";
import * as F3 from "./fig3.mjs";
import * as F4 from "./fig4.mjs";
import * as F5 from "./fig5.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const theme = argv.includes("--dark") ? "dark" : "light";
const only = argv.find((a) => a.startsWith("--only="))?.slice(7).split(",") ?? null;
const want = (n) => !only || only.includes(String(n));
const fmt = (v, d = 0) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

const CSS = `
.rr-stage{position:relative;touch-action:pan-y;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none;outline:none;cursor:crosshair}
.rr-stage:focus-visible{box-shadow:0 0 0 2px var(--anatomy-mid);border-radius:8px}
.rr-stage>svg,.rr-stage>canvas{display:block;width:100%;height:auto}
.rr-stage>.rr-layer{position:absolute;inset:0;height:100%!important;pointer-events:none}
.iso:not([data-theme="light"]) .rr-gl{mix-blend-mode:screen}
.rr-shadow{fill:var(--anatomy-halo);filter:blur(6px)}
.rr-jet-fallback{opacity:var(--rr-flow,0);transition:none}
.rr-stage[data-gl] .rr-jet-fallback{display:none}
.rr-jet-fog{fill:#c9d0d8;fill-opacity:calc(.55 * var(--rr-cond,0));mix-blend-mode:multiply}
.rr-jet-core{fill:#9fb3c8;fill-opacity:.35;mix-blend-mode:multiply}
.iso[data-theme="dark"] .rr-jet-fog{fill:#8a96a3;mix-blend-mode:screen}
.iso[data-theme="dark"] .rr-jet-core{fill:#4d6a8f;mix-blend-mode:screen}
.rr-intro{margin:0 0 40px}
.rr-intro h1{font-size:22px;line-height:1.3;color:var(--anatomy-strong);margin:0 0 10px;font-weight:600;letter-spacing:-.01em}
.rr-intro p{margin:0 0 10px;max-width:72ch}
.rr-verdict{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0 6px}
.rr-verdict div{border-radius:10px;padding:10px 12px;background:var(--anatomy-card);box-shadow:var(--anatomy-lift)}
.rr-verdict b{display:block;font:600 18px/1.2 var(--anatomy-mono);color:var(--anatomy-strong)}
.rr-verdict span{font-size:12px;color:var(--anatomy-muted)}
.rr-h2{font-size:16px;color:var(--anatomy-strong);margin:56px 0 12px;font-weight:600}
.rr-figure{margin:0 0 18px}
.rr-note{font-size:12.5px;color:var(--anatomy-muted);margin:6px 0 0;max-width:80ch}
.iso-figure .iso-caption{max-width:86ch}
.rr-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:2px 14px;margin:6px 4px 0;font:12px/1.35 var(--anatomy-mono);color:var(--anatomy-muted)}
.rr-stats div{display:flex;flex-direction:column;padding:4px 0;border-top:1px solid var(--anatomy-faint)}
.rr-stats b{font-weight:500;color:var(--anatomy-strong);font-size:13px}
.rr-stats .rr-bad b{color:var(--anatomy-red)}
.iso-plate-corner[data-corner="readout"]{max-width:62%;text-align:right}
.rr-plate5{padding:48px 22px 22px}
.rr-charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px}
.rr-charts h3{font-size:13px;font-weight:600;color:var(--anatomy-strong);margin:0 0 6px}
.rr-chart{width:100%;height:auto;display:block;--c1:#2a78d6;overflow:visible}
.iso[data-theme="dark"] .rr-chart{--c1:#3987e5}
.rr-c-grid{stroke:var(--anatomy-faint);stroke-width:1}
.rr-c-tick,.rr-c-axis,.rr-c-label,.rr-c-val,.rr-c-note{font:11px var(--anatomy-mono);fill:var(--anatomy-muted)}
.rr-c-label{fill:var(--anatomy-text);font-family:inherit;font-size:12px}
.rr-c-val{fill:var(--anatomy-strong)}
.rr-c-line{fill:none;stroke:var(--c1);stroke-width:2;stroke-linejoin:round}
.rr-c-dot{fill:var(--c1);stroke:var(--anatomy-card);stroke-width:2;opacity:0}
.rr-c-hit.on .rr-c-dot{opacity:1}
.rr-c-bar{fill:var(--c1)}
.rr-c-hit.on .rr-c-bar{opacity:.8}
.rr-c-ref{stroke:var(--anatomy-mid);stroke-dasharray:3 3}
.rr-tip{min-height:20px;margin:8px 0 0;font:12px var(--anatomy-mono);color:var(--anatomy-strong)}
.rr-ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px 22px;margin:16px 0 0}
.rr-ledger div{border-top:1px solid var(--anatomy-faint);padding-top:8px}
.rr-ledger dt{font-weight:600;color:var(--anatomy-strong);font-size:13px}
.rr-ledger dd{margin:2px 0 0;font-size:12.5px;line-height:1.55}
@media (max-width:520px){.rr-intro h1{font-size:19px}}
`;

const sections = [];
const extraCss = [];
let needTurn = false;
const scripts = [];

if (want(1)) {
  const f = F1.build({ theme });
  const stage = `<div class="rr-stage" tabindex="0" role="slider" aria-label="冷氣體推進器試驗台：左右移動指標設定減壓閥壓力，點擊或按空白鍵開閥 1.6 秒" aria-valuemin="20" aria-valuemax="200" aria-valuenow="100">${f.backSvg}<canvas class="rr-layer rr-gl" aria-hidden="true"></canvas>${f.frontSvg}${f.dialSvg}</div><div class="rr-stats" data-stats></div>`;
  const n100 = f.DATA.table[100];
  sections.push(`<section class="rr-figure rr-f1">${k.plateHtml({
    fig: "Fig 1",
    title: "冷氣體推進器 · 推力台",
    hint: "左右移動設定壓力 · 點擊開閥",
    readout: `設定 100 bar · 閥關閉`,
    keys: [
      { mark: "lit", label: "推力錶針：稱重元件讀到的推力" },
      { mark: "raised", label: "減壓閥 → 電磁閥 → 穩壓室 → 噴嘴，一條直線" },
    ],
    caption: `沒有燃燒，推力只來自把高壓空氣經收斂—擴張噴嘴加速到超音速。以 Tesla 公布的 10 顆推進器與「推力為輪胎抓地力 4–5 倍」反推，一顆後方主推進器在 100 bar 穩壓室壓力下約需 ${f.DATA.exitR.toFixed(1) * 2} cm 出口、${(M.THRUSTERS[0].dt * 100).toFixed(1)} cm 喉部：推力 ${n100[0].toFixed(1)} kN、每秒吞掉 ${n100[1].toFixed(0)} kg 空氣、Isp 只有 ${n100[2].toFixed(0)} s（火箭引擎的五分之一）。氣體在噴嘴內等熵膨脹，出口靜溫只剩 ${n100[3].toFixed(0)} K，低於空氣露點：噴流會出現冷凝霧，必須使用極乾燥的空氣。壓力低於約 35 bar 時噴流與壁面分離、推力驟降；高於設計值則欠膨脹、噴流外鼓。`,
    body: stage,
  })}</section>`);
  scripts.push(readFileSync(join(HERE, "fig1.live.js"), "utf8").replace("__DATA1__", JSON.stringify(f.DATA)));
}

if (want(2)) {
  const f = F2.build();
  const b = f.budget;
  const m = f.mission;
  const stage = `<div class="rr-stage" tabindex="0" role="slider" aria-label="COPV 放氣：左右移動指標決定已用掉多少空氣，方向鍵逐 10 公斤" aria-valuemin="0" aria-valuemax="${Math.round(b.usable)}" aria-valuenow="0">${f.backSvg}</div><div class="rr-stats" data-stats></div>`;
  sections.push(`<section class="rr-figure rr-f2">${k.plateHtml({
    fig: "Fig 2",
    title: "600 L · 690 bar 儲氣",
    hint: "左右移動放氣",
    readout: "已用 0 kg · 瓶內 690 bar · 300 K",
    keys: [
      { mark: "lit", label: "壓力錶針（0–700 bar，粗刻線＝減壓閥失壓點 110 bar，細刻線＝一次跳躍後）" },
      { mark: "raised", label: "三支 200 L 碳纖維纏繞氣瓶，共 600 L" },
    ],
    caption: `600 L、690 bar 的空氣在 300 K 下有 ${fmt(M.TANK.m0)} kg（真實氣體，CoolProp；理想氣體會高估 ${fmt((690e5 * 0.6) / (287.05 * 300) / M.TANK.m0 * 100 - 100)}%）。放氣幾乎是絕熱的：瓶內溫度隨壓力一路降到 ${fmt(b.end.T)} K，而減壓閥要把穩壓室維持在 100 bar，瓶壓一旦低於 110 bar 就失效，所以只有 ${fmt(b.usable)} kg（${fmt((b.usable / M.TANK.m0) * 100)}%）能用，${fmt(b.residual)} kg 永遠留在瓶裡。可用部分的總衝量約 ${fmt(b.impulse / 1000, 1)} kN·s：全重 2 t 的車懸停最多 ${fmt(b.hover, 1)} 秒，十顆推進器全開不到 ${fmt(b.fullThrust, 1)} 秒就用完。一次 Fig 4 的跳躍翻滾用掉 ${fmt(m.gas)} kg。瓶內儲能約 ${fmt(M.TANK.burstMJ)} MJ（≈ ${fmt(M.TANK.burstMJ / 4.184, 1)} kg TNT），充一次至少要 ${fmt(M.TANK.fillKWh)} kWh 的等溫壓縮功，實際約 ${fmt(M.TANK.fillKWh * 2)} kWh：車上的電池補不回來，只能靠站充。COPV 本身（約 0.45 kg/L 估計）約 ${fmt(b.tankKg)} kg。`,
    body: stage,
  })}</section>`);
  scripts.push(readFileSync(join(HERE, "fig2.live.js"), "utf8").replace("__DATA2__", JSON.stringify(f.DATA)));
}

if (want(3)) {
  const f = F3.build();
  const speeds = [0, 100 / 3.6, 200 / 3.6, 300 / 3.6];
  const STEP = 2;
  const rows = (on) => speeds.map((v) => Array.from({ length: 91 }, (_, i) => {
    const r = M.suction(i * STEP, v, Infinity, on);
    return [+r.margin.toFixed(3), +r.accel.toFixed(3), r.sticks ? 1 : 0, +r.fanG.toFixed(3), +(r.aero / (M.CAR.mass0 * M.G0)).toFixed(3), +(r.power / 1000).toFixed(1), +r.dp.toFixed(0)];
  }));
  const DATA = { turn: f.data, table: [...rows(true), ...rows(false)], speeds, step: STEP, mu: M.CAR.mu };
  const flat = M.suction(0), wall = M.suction(90), roof = M.suction(180), r8 = M.suction(180, 0, 8), r20 = M.suction(180, 0, 20);
  const fast = M.suction(180, 300 / 3.6);
  const stage = `<div class="rr-stage iso-turn" tabindex="0" role="slider" aria-label="倒置試驗架：左右移動指標把甲板從地面轉到牆面與天花板；點擊或按 V 切換車速，F 關閉風扇" aria-valuemin="0" aria-valuemax="180" aria-valuenow="0">${f.stageInner}</div><div class="rr-stats" data-stats></div>`;
  sections.push(`<section class="rr-figure rr-f3">${k.plateHtml({
    fig: "Fig 3",
    title: "主動吸力 × 空力 · 倒置架",
    hint: "左右移動翻轉 · 點擊換車速 · F 關風扇",
    readout: "0° 地面 · 0 km/h",
    keys: [
      { mark: "raised", label: "路面甲板與車：同一個鉸鏈剛體" },
      { mark: "flat", label: "兩側滑動裙邊＋左右各一具吸力風扇（排氣口在車側）" },
    ],
    caption: `兩具風扇把底盤下的空氣抽走，裙邊把縫隙壓到約 6 mm，車底與路面之間的負壓 ΔP ≈ ${fmt(flat.dp / 1000, 1)} kPa 乘上約 5.5 m² 的密封面積，得到 ${fmt(flat.fanG, 2)} g 的吸力（Tesla：最高 2 g），耗電約 ${fmt(flat.power / 1000)} kW。正壓力＝吸力＋下壓力＋重力在路面法向的分量：地面 ${fmt(flat.margin, 2)} g、牆面 ${fmt(wall.margin, 2)} g、天花板只剩 ${fmt(roof.margin, 2)} g，乘上 μ≈1.2 後扣掉沿路面的重力，倒掛時還有 ${fmt(roof.accel, 2)} g 可以加速或轉彎。真正的限制在密封：在半徑 R 的凹弧坡道上，軸距 2.7 m 的車底中段離開路面 L²/8R，R = 20 m 時 ${fmt(r20.sag * 1000)} mm（吸力降到 ${fmt(r20.fanG, 2)} g，倒掛只剩 ${fmt(r20.margin, 2)} g），R = 8 m 時 ${fmt(r8.sag * 1000)} mm，裙邊吃不下、吸力崩到 ${fmt(r8.fanG, 2)} g——所以倒掛坡道一定要大半徑，或靠車速：繞圈頂端 v²/R ≥ g 時根本不需要吸力（R = 8 m 只要 ${fmt(M.loopSpeed(8) * 3.6)} km/h）。300 km/h 時空力下壓再加 ${fmt(fast.aero / (M.CAR.mass0 * M.G0), 2)} g。按 F 關掉風扇：牆面與天花板立刻失去附著。`,
    body: stage,
  })}</section>`);
  scripts.push(readFileSync(join(HERE, "fig3.live.js"), "utf8").replace("__DATA3__", JSON.stringify(DATA)));
  extraCss.push(f.css);
  needTurn = true;
}

if (want(4)) {
  const f = F4.build();
  const m = F4.MIS;
  const stage = `<div class="rr-stage iso-turn" tabindex="0" role="slider" aria-label="跳躍與翻滾：左右移動指標拖動時間軸，點擊或按空白鍵播放" aria-valuemin="${(-0.3).toFixed(1)}" aria-valuemax="${F4.T_END.toFixed(1)}" aria-valuenow="0">${f.stageInner}</div><div class="rr-stats" data-stats></div>`;
  const b = m.burns;
  const one = M.nozzle(M.PC_DESIGN, M.regulatorT(M.tankAtMass(M.TANK.m0), M.PC_DESIGN), M.THRUSTERS[2].At, M.THRUSTERS[2].eps);
  sections.push(`<section class="rr-figure rr-f4">${k.plateHtml({
    fig: "Fig 4",
    title: "跳躍 · 翻滾 · 減速著地",
    hint: "左右移動拖動時間 · 點擊播放",
    readout: "t −0.30 s · 駛入",
    keys: [
      { mark: "lit", label: "Roadster：一個自由剛體，姿態來自剛體模擬" },
      { mark: "raised", label: "五台並排停放的車（順序為本圖安排，Semi 置中）" },
    ],
    caption: `以 ${fmt(m.vx * 3.6)} km/h 駛入，四顆底盤升力推進器（每顆 ${fmt(one.F / 1000, 1)} kN，合計 ${fmt((4 * one.F) / (M.CAR.mass0 * M.G0), 1)} g）全開 ${fmt(b.lift, 2)} s，把重心推到 ${fmt(m.apex.z, 1)} m 高；接著只開右側兩顆 ${fmt(b.spin, 2)} s，一邊繼續上升一邊產生 ${fmt((2 * one.F * 0.72) / 1000, 1)} kN·m 的滾轉力矩，車以約 ${fmt((((2 * one.F * 0.72) / M.CAR.Ixx) * b.spin * 180) / Math.PI)}°/s 慣性翻滾；剩下的角度剛好等於同樣力矩煞住所需時，左側兩顆開 ${fmt(b.despin, 2)} s 把它停在 360°。翻滾時推進器的方向跟著車轉：倒置時「升力」推進器其實在把車往下推，所以兩次單側脈衝都安排在車接近水平時。最後四顆全開 ${fmt(b.brake, 2)} s 把落速從約 ${fmt(Math.sqrt(2 * M.G0 * m.apex.z), 1)} m/s 煞到 ${fmt(Math.abs(m.landing), 1)} m/s 才著地——不減速的話懸吊行程 20 cm 會吃到約 ${fmt((2 * M.G0 * m.apex.z) / (2 * 0.2) / M.G0)} g。整段飛行 ${fmt(m.airtime, 2)} s、最低離 Semi 車頂 ${fmt(m.clearance * 100)} cm，用掉 ${fmt(m.gas)} kg 空氣（可用量的 ${fmt((m.gas / M.budget().usable) * 100)}%），瓶壓從 690 降到 ${fmt(M.tankAtMass(M.TANK.m0 - m.gas).p / 1e5)} bar：一罐只夠一次半。`,
    body: stage,
  })}</section>`);
  scripts.push(readFileSync(join(HERE, "fig4.live.js"), "utf8").replace("__DATA4__", JSON.stringify(f.DATA)));
  extraCss.push(f.css);
  needTurn = true;
}

if (want(5)) {
  const f = F5.build();
  sections.push(`<section class="rr-figure rr-f5"><figure class="iso-figure"><div class="iso-plate rr-plate5"><div class="iso-plate-corners" aria-hidden="true"><span class="iso-plate-corner" data-corner="fig">Fig 5</span><span class="iso-plate-corner" data-corner="title">飛行包絡與物理極限</span></div>
<div class="rr-charts"><div><h3>減壓閥設定 → 可懸停秒數</h3>${f.chartA}</div><div><h3>一罐空氣能做什麼</h3>${f.chartB}</div></div><div class="rr-tip" role="status" aria-live="polite"></div>
<dl class="rr-ledger">${f.ledger.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></div>
<figcaption class="iso-legend"><span class="iso-caption">結論：這套系統的實際飛行模式是「彈道跳躍＋脈衝姿態控制＋推力減速著地」，每次 2–3 秒、用掉一半以上的氣；持續飛行、懸停超過 4 秒、或連續做第二次特技，都超出 600 L 冷氣體的物理預算。所有數字由 model.mjs 計算，標示「假設」的輸入（整車 2,000 kg、推進器尺寸、風扇曲線）可在原始碼中調整。</span></figcaption></figure></section>`);
  scripts.push(`{const s=document.querySelector(".rr-f5");const tip=s.querySelector(".rr-tip");s.querySelectorAll("[data-tip]").forEach((g)=>{g.addEventListener("pointerenter",()=>{tip.textContent=g.dataset.tip;g.classList.add("on")});g.addEventListener("pointerleave",()=>{g.classList.remove("on")})});}`);
}

const intro = `<header class="rr-intro"><h1>Roadster Rocket 解剖：冷氣體推進、主動吸力與 600 L 超高壓儲氣</h1>
<p>2026-10-15 Tesla 在德州 Waco 的發表會展示了 Roadster 倒掛坡道、空中翻正落地，以及飛越五台車的 kickflip。這頁用同一個物理模型（CoolProp 真實氣體物性 + 理想氣體噴嘴流 + 剛體模擬）驅動每一張圖的讀數與動作。</p>
<p><b>判定：技術可行，但只在「秒級脈衝」的尺度上。</b>冷氣體推進、2 g 主動吸力與 690 bar COPV 都是成熟技術，組合起來足以做出發表會上的倒掛、翻滾與跳躍；它們同時也把「飛行」限制在每次 2–3 秒、一罐氣只夠一次半的彈道動作。</p>
<div class="rr-verdict">
<div><b>${fmt(M.TANK.m0)} kg</b><span>600 L · 690 bar 空氣（真實氣體）</span></div>
<div><b>${fmt(M.budget().usable)} kg</b><span>減壓閥失壓前可用（${fmt((M.budget().usable / M.TANK.m0) * 100)}%）</span></div>
<div><b>${fmt(M.budget().hover, 1)} s</b><span>全重懸停上限</span></div>
<div><b>${fmt(M.mission().gas)} kg</b><span>一次跳躍＋翻滾＋減速著地</span></div>
<div><b>${fmt(M.suction(0).fanG, 2)} g</b><span>風扇吸力（天花板剩 ${fmt(M.suction(180).margin, 2)} g 正壓力）</span></div>
<div><b>${fmt(M.TANK.burstMJ)} MJ</b><span>瓶內儲能 ≈ ${fmt(M.TANK.burstMJ / 4.184, 1)} kg TNT</span></div>
</div>
<p class="rr-note">公開資料：Tesla 發表會直播（youtu.be/qt5p-_tE8d0）、TechCrunch、Electrek、Tech Insider、Yahoo Finance 報導：10 顆冷氣體推進器、600 L 壓縮空氣（Lars Moravy）、COPV、吸力最高 2 g、0–60 mph 0.81 s。本頁的假設：整車 2,000 kg、690 bar（2018 年 SpaceX 套件的設計壓力）、推進器尺寸與配置、風扇曲線與裙邊間隙、五台車的排列順序。圖 3、圖 4 是可拖曳的 3D 剛體（anatomy 3D mode），圖 1、圖 4 的噴流是 WebGL 體積著色。</p></header>`;

const page = k.pageHtml({
  title: "Roadster Rocket 解剖",
  theme,
  body: `<script>try{if(matchMedia("(prefers-color-scheme: dark)").matches&&!/light/.test(location.hash))document.body.dataset.theme="dark"}catch(e){}</script><style>${CSS}${extraCss.join("\n")}</style>${intro}${sections.join("")}`,
  script: kitScript() + glScript() + (needTurn ? turnScript() : "") + scripts.join("\n"),
  width: 860,
});
const name = `page${theme === "dark" ? "-dark" : ""}.html`;
writeFileSync(join(HERE, name), page);
console.log("wrote", name, `${(page.length / 1024).toFixed(0)} KB`);
