// Assembles the analysis page: intro + verdict tiles, one card per figure (stage + stats row), the limits ledger.
//   node page.mjs            → page.html (light, follows the viewer's theme at load)
//   node page.mjs --dark     → page-dark.html (for checking the dark theme)
// Add a figure: import it, push a section with k.plateHtml, push its live script with __DATAn__ replaced.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "./kit/iso-kit.mjs";
import { kitScript, glScript, turnScript } from "./kit/inline-kit.mjs";
import * as M from "./model.mjs";
import * as F1 from "./fig1.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const dark = process.argv.includes("--dark");
const FEAS = JSON.parse(readFileSync(join(HERE, "feasibility.json"), "utf8"));

const CSS = `
.rr-stage{position:relative;touch-action:pan-y;user-select:none;-webkit-user-select:none;outline:none}
.rr-stage:focus-visible{box-shadow:0 0 0 2px var(--anatomy-mid);border-radius:8px}
.rr-stage>svg,.rr-stage>canvas{display:block;width:100%;height:auto}
.rr-stage>.rr-layer{position:absolute;inset:0;height:100%!important;pointer-events:none}
.iso:not([data-theme="light"]) .rr-gl{mix-blend-mode:screen}
.rr-zone{stroke:var(--anatomy-red);stroke-width:1.4}
.rr-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:2px 14px;margin:6px 4px 0;font:12px/1.35 var(--anatomy-mono);color:var(--anatomy-muted)}
.rr-stats div{display:flex;flex-direction:column;padding:4px 0;border-top:1px solid var(--anatomy-faint)}
.rr-stats b{font-weight:500;color:var(--anatomy-strong);font-size:13px}
.rr-stats .rr-bad b{color:var(--anatomy-red)}
.rr-intro h1{font-size:22px;line-height:1.3;color:var(--anatomy-strong);margin:0 0 10px;font-weight:600;text-wrap:balance}
.rr-intro p{margin:0 0 10px;max-width:72ch}
.rr-verdict{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.rr-verdict div{border-radius:10px;padding:10px 12px;background:var(--anatomy-card);box-shadow:var(--anatomy-lift)}
.rr-verdict b{display:block;font:600 18px/1.2 var(--anatomy-mono);color:var(--anatomy-strong)}
.rr-verdict span{font-size:12px;color:var(--anatomy-muted)}
.rr-figure{margin:0 0 28px}
.rr-ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px 22px;margin:16px 0 0}
.rr-ledger div{border-top:1px solid var(--anatomy-faint);padding-top:8px}
.rr-ledger dt{font-weight:600;color:var(--anatomy-strong);font-size:13px}
.rr-ledger dd{margin:2px 0 0;font-size:12.5px;line-height:1.55}
`;
const VERDICT = { feasible: "技術可行", limited: "有條件可行", infeasible: "技術不可行" };
const sections = [];
const scripts = [];
let needTurn = false;
const extraCss = [];

{
  const f = F1.build();
  const stage = `<div class="rr-stage" tabindex="0" role="slider" aria-label="左右移動指標改變需求，方向鍵逐步調整">${f.svg}</div><div class="rr-stats" data-stats></div>`;
  sections.push(`<section class="rr-figure rr-f1">${k.plateHtml({ fig: "Fig 1", title: "需求 vs 極限", hint: "左右移動改變需求", readout: "", keys: [{ mark: "lit", label: "錶針：需求" }, { mark: "red", label: "紅區：物理極限" }], caption: "替換成主題的機構與真實數字。", body: stage })}</section>`);
  scripts.push(readFileSync(join(HERE, "fig1.live.js"), "utf8").replace("__DATA1__", JSON.stringify({ ...f.DATA, limit: M.DEMO.limit, unit: M.DEMO.unit })));
}

const intro = `<header class="rr-intro"><h1>${FEAS.subject}</h1><p><b>判定：${VERDICT[FEAS.verdict] ?? FEAS.verdict}。</b>${FEAS.summary}</p>
<div class="rr-verdict">${(FEAS.tiles ?? []).map((t) => `<div><b>${t.value}</b><span>${t.label}</span></div>`).join("")}</div></header>`;
const ledger = `<section class="rr-figure"><dl class="rr-ledger">${(FEAS.limits ?? []).map((l) => `<div><dt>${l.name}</dt><dd>${l.value}：${l.explain}</dd></div>`).join("")}</dl></section>`;
const page = k.pageHtml({
  title: FEAS.subject,
  theme: dark ? "dark" : "light",
  body: `${dark ? "" : `<script>try{if(matchMedia("(prefers-color-scheme: dark)").matches)document.body.dataset.theme="dark"}catch(e){}</script>`}<style>${CSS}${extraCss.join("\n")}</style>${intro}${sections.join("")}${ledger}`,
  script: kitScript() + glScript() + (needTurn ? turnScript() : "") + scripts.join("\n"),
  width: 860,
});
writeFileSync(join(HERE, dark ? "page-dark.html" : "page.html"), page);
console.log("wrote", dark ? "page-dark.html" : "page.html", `${(page.length / 1024).toFixed(0)} KB`);
