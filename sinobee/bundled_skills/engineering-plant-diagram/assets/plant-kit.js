/* plant-kit.js — 單一工程模型 → 俯視圖(plan) + 等角立體圖(iso)，輸出結構化 SVG
 * PlantKit.render(model, svgEl) ; PlantKit.setView(svgEl,'iso'|'plan') ; PlantKit.lint(model) ; PlantKit.exportSVG(svgEl)
 * 世界座標：x 向東、y 向南、z 向上（公尺）。所有管線為正交折線 [[x,y],...]。 */
const PlantKit = (() => {
  const NS = 'http://www.w3.org/2000/svg', C30 = Math.cos(Math.PI / 6);
  const VB = { w: 1600, h: 900 }, AREA = { x: 30, y: 92, w: 1340, h: 790 };
  const PIPES = {
    sewage:      { c: '#8D5524', w: 4,   d: '',          n: '原污水' },
    primary:     { c: '#B7950B', w: 3.5, d: '',          n: '初沉出流水' },
    mixed:       { c: '#1E8449', w: 3.5, d: '',          n: '混合液' },
    treated:     { c: '#2E86DE', w: 3.5, d: '',          n: '二級處理水' },
    effluent:    { c: '#0B3D91', w: 4.5, d: '',          n: '放流水' },
    reclaimed:   { c: '#7B68EE', w: 3,   d: '',          n: '再生水' },
    potable:     { c: '#0097A7', w: 3,   d: '',          n: '自來水' },
    raw:         { c: '#6D8B3A', w: 4,   d: '',          n: '原水' },
    ras:         { c: '#7D3C98', w: 3,   d: '10 5',      n: '回流污泥 RAS' },
    was:         { c: '#C0392B', w: 2.5, d: '6 4',       n: '廢棄污泥 WAS' },
    sludge:      { c: '#4E342E', w: 3,   d: '12 4 3 4',  n: '污泥' },
    supernatant: { c: '#7F8C8D', w: 2.5, d: '4 4',       n: '上澄液 / 濾液' },
    overflow:    { c: '#E74C3C', w: 2.5, d: '14 6',      n: '溢流 / 繞流' },
    air:         { c: '#00A6C8', w: 3,   d: '8 3 2 3',   n: '曝氣空氣' },
    chem:        { c: '#E67E22', w: 2.5, d: '1 5',       n: '加藥' },
    biogas:      { c: '#D4AC0D', w: 3,   d: '14 4 2 4',  n: '沼氣' },
    gas:         { c: '#F1C40F', w: 3,   d: '14 4 2 4',  n: '燃氣' },
    steam:       { c: '#AAB7B8', w: 3,   d: '2 3',       n: '蒸汽' },
    oil:         { c: '#2C3E50', w: 3,   d: '',          n: '油料' },
    fire:        { c: '#D35400', w: 3,   d: '',          n: '消防水' },
    signal:      { c: '#95A5A6', w: 1.5, d: '2 2',       n: '儀控訊號' },
  };
  const WATER = { water: '#CFE5F5', ml: '#CDEBD9', an: '#E3DCEE', ax: '#D7E8F3', sl: '#E6D8C8', ch: '#F7E3CF' };
  const el = (tag, a = {}, p) => { const e = document.createElementNS(NS, tag);
    for (const k in a) if (a[k] != null) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  const txt = (p, x, y, s, cls, a = {}) => { const t = el('text', { x, y, class: cls, ...a }, p); t.textContent = s; return t; };
  const pts = a => a.map(q => q.map(v => +v.toFixed(1)).join(',')).join(' ');

  /* ---------- 幾何 ---------- */
  function partBox(p) {               // 世界座標外框 [x0,y0,x1,y1,zTop]
    const z = p.z || 0;
    if (p.r != null) { const h = p.kind === 'sphere' ? 2 * p.r : p.h; return [p.cx - p.r, p.cy - p.r, p.cx + p.r, p.cy + p.r, z + h + (p.dome ? p.r * .45 : 0)]; }
    return [p.x, p.y, p.x + p.w, p.y + p.d, z + p.h];
  }
  function facBox(f) { return f.parts.map(partBox).reduce((a, b) =>
    [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3]), Math.max(a[4], b[4])]); }
  function distToPart(x, y, p) {
    if (p.r != null) return Math.max(0, Math.hypot(x - p.cx, y - p.cy) - p.r);
    const dx = Math.max(p.x - x, 0, x - p.x - p.w), dy = Math.max(p.y - y, 0, y - p.y - p.d); return Math.hypot(dx, dy);
  }

  /* ---------- lint：工程邏輯與模型檢查 ---------- */
  function lint(m, tol = 2.5) {
    const E = [], W = [], ids = new Set(), fac = {};
    for (const f of m.facilities) {
      if (ids.has(f.id)) E.push(`id 重複 ${f.id}`); ids.add(f.id); fac[f.id] = f;
      if (!/^fac-/.test(f.id)) W.push(`${f.id} 建議 fac- 前綴`);
      if (!f.name) E.push(`${f.id} 缺 name`);
      if (!f.parts || !f.parts.length) E.push(`${f.id} 沒有 parts`);
    }
    const fs = m.facilities;
    for (let i = 0; i < fs.length; i++) for (let j = i + 1; j < fs.length; j++) {
      const a = facBox(fs[i]), b = facBox(fs[j]);
      if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) W.push(`設施平面重疊 ${fs[i].id} ↔ ${fs[j].id}`);
    }
    const edges = [];
    for (const p of m.pipes) {
      if (ids.has(p.id)) E.push(`id 重複 ${p.id}`); ids.add(p.id);
      if (!PIPES[p.type] && !(m.pipeTypes || {})[p.type]) E.push(`${p.id} 未知管線類別 ${p.type}`);
      if (!p.pts || p.pts.length < 2) { E.push(`${p.id} 點數不足`); continue; }
      for (let i = 1; i < p.pts.length; i++) { const [a, b] = [p.pts[i - 1], p.pts[i]];
        if (a[0] !== b[0] && a[1] !== b[1]) W.push(`${p.id} 第 ${i} 段為斜線，管線應正交`);
        if (a[0] === b[0] && a[1] === b[1]) E.push(`${p.id} 第 ${i} 段長度為 0`); }
      for (const [end, q] of [[p.from, p.pts[0]], [p.to, p.pts.at(-1)]]) {
        if (/^ext-/.test(end)) continue;
        const f = fac[end]; if (!f) { E.push(`${p.id} 端點 ${end} 不存在`); continue; }
        const d = Math.min(...f.parts.map(pt => distToPart(q[0], q[1], pt)));
        if (d > tol) E.push(`${p.id} ${end === p.from ? '起點' : '終點'} 距 ${f.name} ${d.toFixed(1)}m，未接上設施（流向可能畫反）`);
      }
      if (p.from === p.to) E.push(`${p.id} 起訖相同`);
      edges.push(`[${p.type}] ${fac[p.from]?.name || p.from} → ${fac[p.to]?.name || p.to} (${p.id})`);
    }
    const deg = {}; m.pipes.forEach(p => { deg[p.from] = 1; deg[p.to] = 1; });
    fs.forEach(f => { if (!deg[f.id] && !f.standalone) W.push(`${f.name} 沒有任何管線（若屬實設 standalone:true）`); });
    return { errors: E, warnings: W, edges: edges.sort() };
  }

  /* ---------- defs：可重用元件 ---------- */
  function buildDefs(svg, used) {
    const defs = el('defs', {}, svg);
    defs.innerHTML = `
<filter id="f-shadow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="5"/></filter>
<linearGradient id="g-cyl" x1="0" x2="1"><stop offset="0" stop-color="#E4E9EF"/><stop offset=".35" stop-color="#F7F9FB"/><stop offset="1" stop-color="#AEB9C7"/></linearGradient>
<radialGradient id="g-sphere" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#FFFFFF"/><stop offset=".6" stop-color="#DCE2EA"/><stop offset="1" stop-color="#A9B5C4"/></radialGradient>
<linearGradient id="g-iso-bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#E8ECF2"/><stop offset="1" stop-color="#D3DAE4"/></linearGradient>
<symbol id="sym-flow" viewBox="-7 -6 14 12" overflow="visible"><path d="M-7,-6 L7,0 L-7,6 z" fill="currentColor"/></symbol>
<symbol id="sym-pump" viewBox="-20 -20 40 40" overflow="visible"><circle r="14" fill="#fff" stroke="#34495E" stroke-width="2.5"/><path d="M-6,-9 L10,0 L-6,9 z" fill="#34495E"/><path d="M-14,15 H14" stroke="#34495E" stroke-width="2.5"/></symbol>
<symbol id="sym-valve" viewBox="-12 -8 24 16" overflow="visible"><path d="M-10,-7 L10,7 L10,-7 L-10,7 z" fill="#fff" stroke="#34495E" stroke-width="1.8"/></symbol>
<symbol id="sym-screen" viewBox="-20 -20 40 40" overflow="visible"><rect x="-16" y="-16" width="32" height="32" fill="none" stroke="#34495E" stroke-width="2"/><path d="M-10,-16 V16 M-4,-16 V16 M2,-16 V16 M8,-16 V16" stroke="#7F8C8D" stroke-width="1.6"/></symbol>
<symbol id="sym-mixer" viewBox="-20 -20 40 40" overflow="visible"><circle r="5" fill="#34495E"/><path d="M0,0 L14,-6 L14,6 z M0,0 L-14,6 L-14,-6 z" fill="#5D6D7E"/></symbol>
<symbol id="sym-diffuser" viewBox="-20 -20 40 40" overflow="visible"><path d="M-18,0 H18" stroke="#00A6C8" stroke-width="3"/><g fill="none" stroke="#00A6C8" stroke-width="1.6"><circle cx="-12" cy="-7" r="3"/><circle cx="0" cy="-10" r="3.5"/><circle cx="12" cy="-7" r="3"/></g></symbol>
<symbol id="sym-scraper" viewBox="-50 -50 100 100" overflow="visible"><path d="M-46,0 H46" stroke="#34495E" stroke-width="3"/><circle r="8" fill="#fff" stroke="#34495E" stroke-width="2.5"/><circle r="38" fill="none" stroke="#85929E" stroke-width="1.4" stroke-dasharray="4 4"/></symbol>
<symbol id="sym-blower" viewBox="-20 -20 40 40" overflow="visible"><circle r="15" fill="#fff" stroke="#34495E" stroke-width="2.5"/><path d="M0,0 L10,-6 M0,0 L-3,11 M0,0 L-9,-6" stroke="#00A6C8" stroke-width="2.5"/></symbol>
<symbol id="sym-dosing" viewBox="-20 -20 40 40" overflow="visible"><rect x="-12" y="-16" width="24" height="22" rx="3" fill="#FDEBD0" stroke="#E67E22" stroke-width="2"/><circle cy="12" r="6" fill="#fff" stroke="#34495E" stroke-width="2"/></symbol>
<symbol id="sym-centrifuge" viewBox="-30 -12 60 24" overflow="visible"><path d="M-24,-9 L24,-5 V5 L-24,9 z" fill="#fff" stroke="#34495E" stroke-width="2.2"/><circle cx="-24" r="8" fill="#fff" stroke="#34495E" stroke-width="2.2"/></symbol>
<symbol id="sym-tree" viewBox="-20 -20 40 40" overflow="visible"><circle r="15" fill="#C9D6C7" stroke="#9CB09A" stroke-width="1.5"/><circle cx="-4" cy="-4" r="6" fill="#DCE6DA"/></symbol>
<symbol id="sym-pylon" viewBox="-20 -20 40 40" overflow="visible"><path d="M-14,-14 L14,14 M14,-14 L-14,14" stroke="#7F8C8D" stroke-width="2"/><rect x="-14" y="-14" width="28" height="28" fill="none" stroke="#7F8C8D" stroke-width="2"/></symbol>
<symbol id="iso-tree" viewBox="-20 -46 40 50" overflow="visible"><ellipse cy="2" rx="12" ry="4" fill="#5C6B80" opacity=".18"/><rect x="-1.5" y="-10" width="3" height="12" fill="#A3AEBD"/><circle cy="-22" r="13" fill="#E6EBF1" stroke="#B7C1CE"/><circle cx="-7" cy="-14" r="8" fill="#DDE3EA" stroke="#B7C1CE"/><circle cx="7" cy="-15" r="8" fill="#CFD7E1" stroke="#B7C1CE"/><circle cx="-4" cy="-28" r="5" fill="#F6F8FA"/></symbol>
<symbol id="iso-pylon" viewBox="-20 -90 40 92" overflow="visible"><g fill="none" stroke="#8C98A8" stroke-width="1.4"><path d="M-12,0 L-3,-80 L3,-80 L12,0 M-9,-25 L9,-25 M-6,-50 L6,-50 M-12,0 L6,-50 M12,0 L-6,-50 M-9,-25 L3,-80 M9,-25 L-3,-80"/><path d="M-18,-62 H18 M-14,-74 H14"/></g></symbol>
<symbol id="iso-pump" viewBox="-20 -24 40 28" overflow="visible"><ellipse cy="0" rx="13" ry="5" fill="#9AA6B6"/><rect x="-10" y="-14" width="20" height="14" fill="#E4E9EF" stroke="#8C98A8"/><ellipse cy="-14" rx="10" ry="4" fill="#F7F9FB" stroke="#8C98A8"/><rect x="-3" y="-22" width="6" height="8" fill="#BFC8D4"/></symbol>
<symbol id="iso-blower" viewBox="-20 -26 40 30" overflow="visible"><ellipse rx="15" ry="6" fill="#9AA6B6"/><path d="M-14,0 V-12 A14,7 0 0 1 14,-12 V0 A14,7 0 0 1 -14,0" fill="url(#g-cyl)" stroke="#8C98A8"/><path d="M8,-18 V-26 H16" fill="none" stroke="#00A6C8" stroke-width="3"/></symbol>`;
    for (const t of used) {        // 每種管線一個終點箭頭（userSpaceOnUse：大小不隨線寬變）
      const P = PIPES[t];
      el('marker', { id: `arr-${t}`, viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 12, markerHeight: 12, markerUnits: 'userSpaceOnUse', orient: 'auto' }, defs)
        .appendChild(el('path', { d: 'M0,0 L10,5 L0,10 z', fill: P.c }));
    }
    return defs;
  }

  function buildStyle(svg, used) {
    let css = `
svg{font-family:'Noto Sans TC','Noto Sans CJK TC','PingFang TC','Microsoft JhengHei',sans-serif}
.view{transition:opacity .35s} svg[data-view=plan] #view-iso, svg[data-view=iso] #view-plan{opacity:0;pointer-events:none;visibility:hidden}
.title{font-size:24px;font-weight:700;fill:#1B2631}.subtitle{font-size:13px;fill:#566573}
.fac-name{font-size:14px;font-weight:700;fill:#1B2631}.fac-metric{font-size:11px;fill:#4D5B69}
.halo-text{paint-order:stroke;stroke:var(--halo);stroke-width:4px;stroke-linejoin:round}
.pipe-label{font-size:11px;font-weight:700;paint-order:stroke;stroke:var(--halo);stroke-width:4px}
.pipe{fill:none;stroke-linejoin:round}.pipe-halo{fill:none;stroke:var(--halo);stroke-linejoin:round}
#view-plan{--halo:#F7F9FB}#view-iso{--halo:#DDE3EA}
.p-struct{fill:#FFFFFF;stroke:#34495E;stroke-width:2}.p-bldg{fill:#ECEFF1;stroke:#455A64;stroke-width:2}
.iso-top{fill:#F5F7FA;stroke:#9AA6B6;stroke-width:.8}.iso-l{fill:#DAE0E8;stroke:#9AA6B6;stroke-width:.8}
.iso-r{fill:#BCC6D3;stroke:#9AA6B6;stroke-width:.8}.iso-in{fill:#C5CED9}
.iso-lbl rect{fill:#FFFFFF;stroke:#8C98A8;stroke-width:1;opacity:.96}.iso-lbl line{stroke:#5D6D7E;stroke-width:1}
.road-plan{fill:#D5DAE1}.road-iso{fill:#3E4553}.road-line{fill:none;stroke:#F4F6F8;stroke-width:1.4;stroke-dasharray:8 7}
.legend-box{fill:#FFFFFF;stroke:#AAB7B8}`;
    for (const t of used) { const P = PIPES[t];
      css += `\n.pipe--${t}{stroke:${P.c};stroke-width:${P.w};${P.d ? `stroke-dasharray:${P.d};` : ''}marker-end:url(#arr-${t})}.flow--${t}{color:${P.c}}`; }
    el('style', {}, svg).textContent = css;
  }

  /* ---------- 投影 ---------- */
  function makeProj(m) {
    const [W, D] = m.world, Hmax = Math.max(...m.facilities.map(f => facBox(f)[4]), 10);
    const sp = Math.min(AREA.w / W, AREA.h / D);
    const plan = (x, y) => [AREA.x + (AREA.w - W * sp) / 2 + x * sp, AREA.y + (AREA.h - D * sp) / 2 + y * sp];
    const raw = (x, y, z = 0) => [(x - y) * C30, (x + y) * .5 - z];
    const cs = [[0, 0, 0], [W, 0, 0], [0, D, 0], [W, D, 0], [0, 0, Hmax], [W, 0, Hmax]].map(c => raw(...c));
    const bx = [Math.min(...cs.map(c => c[0])), Math.min(...cs.map(c => c[1])), Math.max(...cs.map(c => c[0])), Math.max(...cs.map(c => c[1]))];
    const si = Math.min(AREA.w / (bx[2] - bx[0]), AREA.h / (bx[3] - bx[1])) * (m.isoZoom || 1);
    const ox = AREA.x + (AREA.w - (bx[2] - bx[0]) * si) / 2 - bx[0] * si, oy = AREA.y + (AREA.h - (bx[3] - bx[1]) * si) / 2 - bx[1] * si;
    const iso = (x, y, z = 0) => { const r = raw(x, y, z); return [ox + r[0] * si, oy + r[1] * si]; };
    return { plan, iso, sp, si, samples: {} };
  }

  /* ---------- 俯視圖零件 ---------- */
  function planPart(g, p, P) {
    if (p.r != null) {
      const [cx, cy] = P.plan(p.cx, p.cy), r = p.r * P.sp;
      if (p.kind === 'stack') { el('circle', { cx, cy, r, class: 'p-bldg' }, g); el('circle', { cx, cy, r: r * .55, fill: '#7F8C8D' }, g); return; }
      if (p.kind === 'sphere') { el('circle', { cx, cy, r, fill: 'url(#g-sphere)', stroke: '#8C98A8', 'stroke-width': 1.5 }, g); return; }
      el('circle', { cx, cy, r, class: p.kind === 'ctank' ? 'p-struct' : 'p-bldg' }, g);
      if (p.kind === 'ctank') el('circle', { cx, cy, r: r - 3, fill: WATER[p.water || 'water'] }, g);
      return;
    }
    const [x, y] = P.plan(p.x, p.y), w = p.w * P.sp, d = p.d * P.sp;
    el('rect', { x, y, width: w, height: d, class: p.kind === 'basin' ? 'p-struct' : 'p-bldg', rx: p.kind === 'basin' ? 0 : 2 }, g);
    if (p.kind === 'basin') el('rect', { x: x + 3, y: y + 3, width: w - 6, height: d - 6, fill: WATER[p.water || 'water'] }, g);
    if (p.kind === 'box' && p.z) el('rect', { x, y, width: w, height: d, fill: 'none', stroke: '#455A64', 'stroke-dasharray': '3 2' }, g);
  }

  /* ---------- 立體圖零件（黏土模型風格） ---------- */
  const poly = (g, a, cls, extra = {}) => el('polygon', { points: pts(a), class: cls, ...extra }, g);
  function isoBoxFaces(g, P, x, y, w, d, z0, z1, cls = ['iso-top', 'iso-l', 'iso-r']) {
    const I = P.iso;
    poly(g, [I(x, y + d, z0), I(x + w, y + d, z0), I(x + w, y + d, z1), I(x, y + d, z1)], cls[1]);
    poly(g, [I(x + w, y, z0), I(x + w, y + d, z0), I(x + w, y + d, z1), I(x + w, y, z1)], cls[2]);
    poly(g, [I(x, y, z1), I(x + w, y, z1), I(x + w, y + d, z1), I(x, y + d, z1)], cls[0]);
  }
  function isoCyl(g, P, cx, cy, r, z0, z1, top) {
    const [ax, ay] = P.iso(cx, cy, z0), [, by] = P.iso(cx, cy, z1), rx = r * P.si * C30 * Math.SQRT2, ry = r * P.si * .5 * Math.SQRT2;
    el('path', { d: `M${ax - rx},${by} V${ay} A${rx},${ry} 0 0 0 ${ax + rx},${ay} V${by} Z`, fill: 'url(#g-cyl)', stroke: '#9AA6B6', 'stroke-width': .8 }, g);
    el('ellipse', { cx: ax, cy: by, rx, ry, fill: top || '#F5F7FA', stroke: '#9AA6B6', 'stroke-width': .8 }, g);
    return { x: ax, y: by, rx, ry };
  }
  function isoPart(g, p, P) {
    const z0 = p.z || 0;
    if (p.kind === 'box') { isoBoxFaces(g, P, p.x, p.y, p.w, p.d, z0, z0 + p.h);
      if (p.windows) { const I = P.iso, n = Math.max(2, Math.floor(p.w / 4)), zz = z0 + p.h * .55;
        for (let i = 0; i < n; i++) { const xx = p.x + (i + .5) * p.w / n;
          el('line', { x1: I(xx - .8, p.y + p.d, zz)[0], y1: I(xx - .8, p.y + p.d, zz)[1], x2: I(xx + .8, p.y + p.d, zz)[0], y2: I(xx + .8, p.y + p.d, zz)[1], stroke: '#8C98A8', 'stroke-width': 2.5 }, g); } }
      return; }
    if (p.kind === 'basin') {
      const t = p.t || 1, z1 = z0 + p.h, zw = z1 - Math.min(1, p.h * .25), I = P.iso;
      isoBoxFaces(g, P, p.x, p.y, p.w, p.d, z0, z1);
      const [a, b, c, d] = [[p.x + t, p.y + t], [p.x + p.w - t, p.y + t], [p.x + p.w - t, p.y + p.d - t], [p.x + t, p.y + p.d - t]];
      poly(g, [I(...a, z1), I(...b, z1), I(...c, z1), I(...d, z1)], 'iso-in');
      poly(g, [I(...a, zw), I(...b, zw), I(...c, zw), I(...d, zw)], '', { fill: WATER[p.water || 'water'], stroke: '#9AA6B6', 'stroke-width': .6 });
      for (const wl of p.walls || []) {   // 內部隔牆：{x} 或 {y}
        if (wl.x != null) isoBoxFaces(g, P, wl.x - .3, p.y + t, .6, p.d - 2 * t, zw, z1);
        else isoBoxFaces(g, P, p.x + t, wl.y - .3, p.w - 2 * t, .6, zw, z1);
      }
      return;
    }
    if (p.kind === 'ctank') { const e = isoCyl(g, P, p.cx, p.cy, p.r, z0, z0 + p.h);
      el('ellipse', { cx: e.x, cy: e.y + 2, rx: e.rx * .9, ry: e.ry * .9, fill: WATER[p.water || 'water'], stroke: '#9AA6B6', 'stroke-width': .6 }, g);
      if (p.bridge !== false) { const I = P.iso, q1 = I(p.cx - p.r, p.cy, z0 + p.h + .6), q2 = I(p.cx + p.r, p.cy, z0 + p.h + .6);
        el('line', { x1: q1[0], y1: q1[1], x2: q2[0], y2: q2[1], stroke: '#5D6D7E', 'stroke-width': 3 }, g);
        el('circle', { cx: e.x, cy: e.y - .6 * P.si, r: 4, fill: '#fff', stroke: '#5D6D7E', 'stroke-width': 1.5 }, g); }
      return; }
    if (p.kind === 'cyl' || p.kind === 'stack') { const e = isoCyl(g, P, p.cx, p.cy, p.r, z0, z0 + p.h);
      if (p.kind === 'stack') el('ellipse', { cx: e.x, cy: e.y, rx: e.rx * .7, ry: e.ry * .7, fill: '#6B7787' }, g);
      if (p.dome) el('path', { d: `M${e.x - e.rx},${e.y} A${e.rx},${e.rx * .55} 0 0 1 ${e.x + e.rx},${e.y} A${e.rx},${e.ry} 0 0 1 ${e.x - e.rx},${e.y}`, fill: 'url(#g-sphere)', stroke: '#9AA6B6', 'stroke-width': .8 }, g);
      return; }
    if (p.kind === 'sphere') { const [x, y] = P.iso(p.cx, p.cy, z0 + p.r), R = p.r * P.si;
      el('rect', { x: x - R * .55, y, width: 3, height: R, fill: '#A3AEBD' }, g); el('rect', { x: x + R * .5, y, width: 3, height: R, fill: '#A3AEBD' }, g);
      el('circle', { cx: x, cy: y, r: R, fill: 'url(#g-sphere)', stroke: '#9AA6B6', 'stroke-width': .8 }, g); }
  }
  function isoShadow(g, p, P) {          // 光源左上 → 陰影投向右下
    const b = partBox(p), h = b[4] - (p.z || 0), s = h * .45, I = P.iso;
    if (p.z) return;
    poly(g, [I(b[0], b[1]), I(b[2] + s, b[1]), I(b[2] + s, b[3] + s * .3), I(b[0] + s * .2, b[3] + s * .3), I(b[0], b[3])], '', { fill: '#3E4A5C', opacity: .16, filter: 'url(#f-shadow)' });
  }

  /* ---------- 設備元件 ---------- */
  const ISO_SPRITE = { pump: 'iso-pump', blower: 'iso-blower', tree: 'iso-tree', pylon: 'iso-pylon' };
  function planEquip(g, e, P) { const [x, y] = P.plan(e.x, e.y), s = (e.s || 4) * P.sp;
    el('use', { href: `#sym-${e.sym}`, x: -s / 2, y: -s / 2, width: s, height: s, transform: `translate(${x.toFixed(1)},${y.toFixed(1)})${e.rot ? ` rotate(${e.rot})` : ''}` }, g); }
  function isoEquip(g, e, P, zDefault) { const z = e.z ?? zDefault, [x, y] = P.iso(e.x, e.y, z);
    if (ISO_SPRITE[e.sym]) { const s = (e.s || 4) * P.si * 1.1;
      el('use', { href: `#${ISO_SPRITE[e.sym]}`, x: -s / 2, y: -s / 2, width: s, height: s, transform: `translate(${x.toFixed(1)},${y.toFixed(1)})` }, g); return; }
    const k = (e.s || 4) * P.si / 40;      // 無立體版 → 平面符號貼在地平面（等角矩陣）
    el('use', { href: `#sym-${e.sym}`, x: -20, y: -20, width: 40, height: 40, transform: `translate(${x.toFixed(1)},${y.toFixed(1)}) matrix(${(C30 * k).toFixed(3)},${(.5 * k).toFixed(3)},${(-C30 * k).toFixed(3)},${(.5 * k).toFixed(3)},0,0)${e.rot ? ` rotate(${e.rot})` : ''}` }, g);
  }

  /* ---------- 道路 ---------- */
  function roads(g, m, P, view) {
    for (const r of m.roads || []) { const w = (r.w || 7) / 2;
      const Q = view === 'plan' ? (x, y) => P.plan(x, y) : (x, y) => P.iso(x, y, 0);
      for (let i = 1; i < r.pts.length; i++) { const [a, b] = [r.pts[i - 1], r.pts[i]];
        const x0 = Math.min(a[0], b[0]) - (a[1] === b[1] ? 0 : w), x1 = Math.max(a[0], b[0]) + (a[1] === b[1] ? 0 : w);
        const y0 = Math.min(a[1], b[1]) - (a[0] === b[0] ? 0 : w), y1 = Math.max(a[1], b[1]) + (a[0] === b[0] ? 0 : w);
        poly(g, [Q(x0, y0), Q(x1, y0), Q(x1, y1), Q(x0, y1)], view === 'plan' ? 'road-plan' : 'road-iso'); }
      for (const v of r.pts.slice(1, -1)) poly(g, [Q(v[0] - w, v[1] - w), Q(v[0] + w, v[1] - w), Q(v[0] + w, v[1] + w), Q(v[0] - w, v[1] + w)], view === 'plan' ? 'road-plan' : 'road-iso');
      el('polyline', { points: pts(r.pts.map(q => Q(...q))), class: 'road-line' }, g);
    }
  }

  /* ---------- 管線 ---------- */
  function drawPipes(layer, m, P, view) {
    const Q = view === 'plan' ? (q) => P.plan(q[0], q[1]) : (q, z) => P.iso(q[0], q[1], z);
    const gH = el('g', { class: 'pipe-halos' }, layer), gP = el('g', { class: 'pipes' }, layer), gF = el('g', { class: 'flows' }, layer), gV = el('g', { class: 'valves' }, layer);
    const smp = P.samples[view] = [];
    for (const p of m.pipes) {
      const S = p.pts.map(q => Q(q, p.z ?? .4)), d = 'M' + S.map(s => s.map(v => v.toFixed(1)).join(',')).join(' L');
      el('path', { d, class: 'pipe-halo', 'stroke-width': PIPES[p.type].w + 5 }, gH);
      el('path', { id: `${p.id}${view === 'iso' ? '-iso' : ''}`, d, class: `pipe pipe--${p.type}`, 'data-from': p.from, 'data-to': p.to, 'data-pipe': p.id }, gP);
      S.forEach((s, i) => { if (!i) return; const a = S[i - 1], L = Math.hypot(s[0] - a[0], s[1] - a[1]);
        for (let t = 0; t <= L; t += 5) smp.push([a[0] + (s[0] - a[0]) * t / L, a[1] + (s[1] - a[1]) * t / L]);
        if (L < 110) return;
        const n = Math.floor(L / 260) + 1, ang = Math.atan2(s[1] - a[1], s[0] - a[0]) * 180 / Math.PI;
        for (let k = 1; k <= n; k++) { const t = (p.label && p.label.seg === i - 1) ? k / (n + 1) * .6 : k / (n + 1);
          const x = a[0] + (s[0] - a[0]) * t, y = a[1] + (s[1] - a[1]) * t;
          el('use', { href: '#sym-flow', class: `flow flow--${p.type}`, 'data-pipe': p.id, x: -7, y: -6, width: 14, height: 12, transform: `translate(${x.toFixed(1)},${y.toFixed(1)}) rotate(${ang.toFixed(1)})` }, gF); } });
      for (const v of p.valves || []) { const [x, y] = Q(v, p.z ?? .4); el('use', { href: '#sym-valve', x: -11, y: -7, width: 22, height: 14, transform: `translate(${x.toFixed(1)},${y.toFixed(1)})` }, gV); }
    }
  }
  /* ---------- 自動標籤佈置：候選位置評分（避開已放標籤、管線、其他設施、畫布邊界） ---------- */
  const bb = e => { const b = e.getBBox(); return [b.x, b.y, b.x + b.width, b.y + b.height]; };
  const ovl = (a, b) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  function placer(P, view) {
    const placed = [], obst = [];
    return { placed, obst, best(lb, cands, self) {
      let bi = 0, bs = Infinity;
      cands.forEach((c, i) => {
        const b = [lb[0] + c[0] - 3, lb[1] + c[1] - 2, lb[2] + c[0] + 3, lb[3] + c[1] + 2]; let sc = i * 6 + (c[2] || 0);
        if (b[0] < 4 || b[1] < 76 || b[2] > 1385 || b[3] > VB.h - 4) sc += 1e6;
        for (const p of placed) sc += ovl(b, p) * 4;
        for (const o of obst) if (o.id !== self) sc += ovl(b, o.b) * o.w;
        for (const q of P.samples[view]) if (q[0] > b[0] && q[0] < b[2] && q[1] > b[1] && q[1] < b[3]) sc += 30;
        if (sc < bs) { bs = sc; bi = i; } });
      const c = cands[bi]; placed.push([lb[0] + c[0] - 3, lb[1] + c[1] - 2, lb[2] + c[0] + 3, lb[3] + c[1] + 2]); return c; } };
  }
  function pipeLabels(layer, m, P, view, PL) {
    const Q = view === 'plan' ? (q) => P.plan(q[0], q[1]) : (q, z) => P.iso(q[0], q[1], z);
    for (const p of m.pipes) { if (!p.label) continue; const L = p.label, S = p.pts.map(q => Q(q, p.z ?? .4));
      const t = txt(layer, 0, 0, L.text, 'pipe-label', { fill: PIPES[p.type].c, 'text-anchor': 'middle' }), lb = bb(t), hw = (lb[2] - lb[0]) / 2;
      const segs = L.seg != null ? [L.seg] : S.slice(1).map((_, i) => i).sort((i, j) =>
        Math.hypot(S[j + 1][0] - S[j][0], S[j + 1][1] - S[j][1]) - Math.hypot(S[i + 1][0] - S[i][0], S[i + 1][1] - S[i][1]));
      const man = view === 'plan' ? L.plan : L.iso, cands = [];
      for (const i of segs) { const a = S[i], b = S[i + 1], vert = Math.abs(b[0] - a[0]) < Math.abs(b[1] - a[1]);
        if (man) { cands.push([(a[0] + b[0]) / 2 + man[0], (a[1] + b[1]) / 2 + man[1]]); break; }
        for (const f of [.5, .3, .7]) { const x = a[0] + (b[0] - a[0]) * f, y = a[1] + (b[1] - a[1]) * f;
          if (vert) cands.push([x + hw + 7, y + 4], [x - hw - 7, y + 4]); else cands.push([x, y - 8], [x, y + 17]); } }
      const c = PL.best(lb, cands); t.setAttribute('x', c[0].toFixed(1)); t.setAttribute('y', c[1].toFixed(1)); }
  }
  function labelBody(g, f, nMetrics) {      // 名稱 + 指標，局部座標以 (0,0) 為中心上緣
    const body = el('g', { class: 'lbl-body' }, g);
    txt(body, 0, 0, f.name, 'fac-name', { 'text-anchor': 'middle' });
    (f.metrics || []).slice(0, nMetrics).forEach((s, i) => txt(body, 0, 16 + i * 14, s, 'fac-metric', { 'text-anchor': 'middle' }));
    return body;
  }

  /* ---------- 主渲染 ---------- */
  function render(m, svg) {
    svg.innerHTML = ''; svg.setAttribute('viewBox', `0 0 ${VB.w} ${VB.h}`); svg.setAttribute('data-view', m.view || 'iso');
    const used = [...new Set(m.pipes.map(p => p.type))]; Object.assign(PIPES, m.pipeTypes || {});
    buildStyle(svg, used); buildDefs(svg, used); el('title', {}, svg).textContent = m.title;
    const P = makeProj(m), [W, D] = m.world, sorted = (a, k) => [...a].sort((u, v) => k(u) - k(v));

    /* 俯視圖 */
    const VP = el('g', { id: 'view-plan', class: 'view' }, svg);
    el('rect', { width: VB.w, height: VB.h, fill: '#F7F9FB' }, VP);
    const g0 = P.plan(0, 0), g1 = P.plan(W, D);
    el('rect', { x: g0[0], y: g0[1], width: g1[0] - g0[0], height: g1[1] - g0[1], fill: '#EEF2F5', stroke: '#C5CED8', 'stroke-dasharray': '6 4' }, VP);
    roads(el('g', { id: 'plan-roads' }, VP), m, P, 'plan');
    const pTrees = el('g', { id: 'plan-trees' }, VP); (m.trees || []).forEach(t => planEquip(pTrees, { sym: 'tree', x: t[0], y: t[1], s: 5 }, P));
    const pFac = el('g', { id: 'plan-facilities' }, VP);
    for (const f of m.facilities) {
      const g = el('g', { id: f.id, class: 'facility', 'data-name': f.name, 'data-type': f.type || '' }, pFac);
      f.parts.forEach(p => planPart(g, p, P)); (f.equip || []).filter(e => e.plan !== false).forEach(e => planEquip(g, e, P));
    }
    const pPipes = el('g', { id: 'plan-pipes' }, VP); drawPipes(pPipes, m, P, 'plan');
    const PLp = placer(P, 'plan'), fbox = {};
    for (const f of m.facilities) { const b = facBox(f), p0 = P.plan(b[0], b[1]), p1 = P.plan(b[2], b[3]); fbox[f.id] = [...p0, ...p1]; PLp.obst.push({ id: f.id, b: fbox[f.id], w: 1.5 }); }
    pFac.querySelectorAll('use').forEach(u => PLp.obst.push({ id: null, b: bb(u), w: 8 }));   // 設備符號不可被字壓住
    m.facilities.forEach(f => f.parts.filter(p => p.z).forEach(p => { const b = partBox(p), q0 = P.plan(b[0], b[1]), q1 = P.plan(b[2], b[3]); PLp.obst.push({ id: null, b: [...q0, ...q1], w: 2 }); }));
    for (const f of m.facilities) {       // 先試設施內置中，放不下改放外側
      const g = document.getElementById(f.id), lg = el('g', { class: 'fac-label halo-text' }, g), body = labelBody(lg, f, 9), lb = bb(body);
      const fb = fbox[f.id], cx = (fb[0] + fb[2]) / 2, cy = (fb[1] + fb[3]) / 2, mid = -(lb[1] + lb[3]) / 2;
      const fits = lb[2] - lb[0] < fb[2] - fb[0] - 8 && lb[3] - lb[1] < fb[3] - fb[1] - 8;
      let cands = [[cx, cy + mid, fits ? 0 : 400], [cx, fb[3] + 5 - lb[1]], [cx, fb[1] - 5 - lb[3]], [fb[2] + 8 - lb[0], cy + mid], [fb[0] - 8 - lb[2], cy + mid]];
      if (f.planLabel) cands = [[cx + f.planLabel[0], cy + mid + f.planLabel[1]]];
      const c = PLp.best(lb, cands, f.id); body.setAttribute('transform', `translate(${c[0].toFixed(1)},${c[1].toFixed(1)})`);
    }
    pipeLabels(el('g', { id: 'plan-pipe-labels' }, VP), m, P, 'plan', PLp);

    /* 立體圖 */
    const VI = el('g', { id: 'view-iso', class: 'view' }, svg);
    el('rect', { width: VB.w, height: VB.h, fill: 'url(#g-iso-bg)' }, VI);
    const I = P.iso, slab = el('g', { id: 'iso-site' }, VI);
    poly(slab, [I(0, D, -2), I(W, D, -2), I(W, D, 0), I(0, D, 0)], 'iso-l'); poly(slab, [I(W, 0, -2), I(W, D, -2), I(W, D, 0), I(W, 0, 0)], 'iso-r');
    poly(slab, [I(0, 0), I(W, 0), I(W, D), I(0, D)], '', { fill: '#DDE3EA', stroke: '#B5BFCC' });
    roads(el('g', { id: 'iso-roads' }, VI), m, P, 'iso');
    const sh = el('g', { id: 'iso-shadows' }, VI); m.facilities.forEach(f => f.parts.forEach(p => isoShadow(sh, p, P)));
    drawPipes(el('g', { id: 'iso-pipes' }, VI), m, P, 'iso');
    const vol = el('g', { id: 'iso-volumes' }, VI);
    const items = [...m.facilities.map(f => ({ f, k: (() => { const b = facBox(f); return (b[0] + b[2]) / 2 + (b[1] + b[3]) / 2; })() })),
                   ...(m.trees || []).map(t => ({ t, k: t[0] + t[1] })), ...(m.sprites || []).map(s => ({ s, k: s.x + s.y }))];
    for (const it of sorted(items, u => u.k)) {
      if (it.t) { isoEquip(vol, { sym: 'tree', x: it.t[0], y: it.t[1], s: 9 }, P, 0); continue; }
      if (it.s) { isoEquip(vol, it.s, P, 0); continue; }
      const f = it.f, g = el('g', { id: `${f.id}-iso`, class: 'facility-iso', 'data-fac': f.id }, vol);
      sorted(f.parts, p => { const b = partBox(p); return (b[0] + b[2]) / 2 + (b[1] + b[3]) / 2 + (p.z || 0) * 2; }).forEach(p => isoPart(g, p, P));
      const top = facBox(f)[4]; (f.equip || []).filter(e => e.iso !== false).forEach(e => isoEquip(g, e, P, top - .8));
    }
    const lab = el('g', { id: 'iso-labels' }, VI), PLi = placer(P, 'iso');
    vol.querySelectorAll('g.facility-iso').forEach(g => PLi.obst.push({ id: g.dataset.fac, b: bb(g), w: .25 }));
    for (const f of m.facilities) {     // 引線標籤：從設施頂部中心拉出，自動挑選不重疊的位置
      const b = facBox(f), [ax, ay] = I((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[4]);
      const g = el('g', { class: 'iso-lbl', 'data-fac': f.id }, lab), line = el('line', { x1: ax, y1: ay }, g);
      el('circle', { cx: ax, cy: ay, r: 2.2, fill: '#5D6D7E' }, g);
      const body = labelBody(g, f, 1), rect = el('rect', { rx: 4 }, body); body.insertBefore(rect, body.firstChild);
      const tb = bb(body); Object.entries({ x: tb[0] - 6, y: tb[1] - 3, width: tb[2] - tb[0] + 12, height: tb[3] - tb[1] + 6 }).forEach(([k, v]) => rect.setAttribute(k, v.toFixed(1)));
      const lb = bb(body), up = -lb[3] - 6;
      let cands = [[0, up - 18], [0, up - 44], [-70, up - 10], [70, up - 10], [-110, up + 10], [110, up + 10], [0, up - 76], [-90, up - 50], [90, up - 50], [-140, up - 30], [140, up - 30], [0, 40, 60]];
      if (f.isoLabel) cands = [f.isoLabel];
      const c = PLi.best(lb, cands.map(q => [ax + q[0], ay + q[1], q[2]]), f.id);
      body.setAttribute('transform', `translate(${c[0].toFixed(1)},${c[1].toFixed(1)})`); line.setAttribute('x2', c[0].toFixed(1)); line.setAttribute('y2', (c[1] + lb[3]).toFixed(1));
    }
    pipeLabels(el('g', { id: 'iso-pipe-labels' }, VI), m, P, 'iso', PLi);

    /* 共用：標題、圖例、設計基準（螢幕座標，不隨視角變動） */
    const OV = el('g', { id: 'overlay' }, svg);
    txt(OV, 30, 40, m.title, 'title'); if (m.subtitle) txt(OV, 30, 64, m.subtitle, 'subtitle');
    const lg = el('g', { id: 'legend', transform: `translate(1395,${m.legendY || 92})` }, OV), rows = used.length;
    el('rect', { width: 190, height: 44 + rows * 22 + 24, rx: 6, class: 'legend-box' }, lg); txt(lg, 14, 26, '圖例 LEGEND', 'fac-name');
    used.forEach((t, i) => { const r = el('g', { 'data-pipe': t, transform: `translate(14,${48 + i * 22})` }, lg);
      el('path', { d: 'M0,0 H44', class: `pipe pipe--${t}` }, r); txt(r, 56, 4, (m.pipeNames || {})[t] || PIPES[t].n, 'fac-metric'); });
    el('use', { href: '#sym-valve', x: -11, y: -7, width: 22, height: 14, transform: `translate(36,${48 + rows * 22})` }, lg); txt(lg, 70, 52 + rows * 22, '閥門', 'fac-metric');
    if (m.basis) { const by = (m.legendY || 92) + 44 + rows * 22 + 40, bg = el('g', { id: 'design-basis', transform: `translate(1395,${by})` }, OV);
      el('rect', { width: 190, height: 34 + m.basis.length * 17, rx: 6, class: 'legend-box' }, bg); txt(bg, 14, 24, m.basisTitle || '設計基準', 'fac-name');
      m.basis.forEach((s, i) => txt(bg, 14, 44 + i * 17, s, 'fac-metric')); }
    const na = el('g', { id: 'north', transform: 'translate(1350,120)' }, OV); el('path', { d: 'M0,-16 L7,8 L0,3 L-7,8 z', fill: '#34495E' }, na); txt(na, 0, -20, 'N', 'fac-metric', { 'text-anchor': 'middle' });
    setView(svg, m.view || 'iso');
    return P;
  }

  function setView(svg, v) { svg.setAttribute('data-view', v);
    const n = svg.querySelector('#north'); if (n) n.setAttribute('transform', v === 'iso' ? 'translate(1350,120) rotate(60)' : 'translate(1350,120)'); }

  function enableZoom(svg) {             // 滾輪縮放、拖曳平移、雙擊復原
    const home = svg.viewBox.baseVal, H = { x: home.x, y: home.y, w: home.width, h: home.height }; let vb = { ...H }, drag = null;
    const apply = () => svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    const pt = e => { const r = svg.getBoundingClientRect(); return [vb.x + (e.clientX - r.left) / r.width * vb.w, vb.y + (e.clientY - r.top) / r.height * vb.h]; };
    svg.addEventListener('wheel', e => { e.preventDefault(); const k = e.deltaY > 0 ? 1.12 : 1 / 1.12, [px, py] = pt(e);
      const nw = Math.min(H.w * 1.5, Math.max(H.w / 8, vb.w * k)), s = nw / vb.w; vb = { x: px - (px - vb.x) * s, y: py - (py - vb.y) * s, w: nw, h: vb.h * s }; apply(); }, { passive: false });
    svg.addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY, vb.x, vb.y]; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', e => { if (!drag) return; const r = svg.getBoundingClientRect();
      vb.x = drag[2] - (e.clientX - drag[0]) / r.width * vb.w; vb.y = drag[3] - (e.clientY - drag[1]) / r.height * vb.h; apply(); });
    svg.addEventListener('pointerup', () => drag = null); svg.addEventListener('dblclick', () => { vb = { ...H }; apply(); });
  }

  function exportSVG(svg, name) {        // 下載目前視角的獨立 SVG
    const c = svg.cloneNode(true), v = svg.getAttribute('data-view');
    c.querySelector(v === 'iso' ? '#view-plan' : '#view-iso').remove(); c.setAttribute('xmlns', NS); c.setAttribute('viewBox', `0 0 ${VB.w} ${VB.h}`);
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([c.outerHTML], { type: 'image/svg+xml' }));
    a.download = `${name || 'plant'}-${v}.svg`; a.click();
  }
  return { render, setView, lint, enableZoom, exportSVG, PIPES };
})();
