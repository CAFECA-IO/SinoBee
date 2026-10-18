{
  const D = __DATA2__;
  const stage = document.querySelector(".rr-f2 .rr-stage");
  const plate = stage.closest(".iso-plate");
  const readout = plate.querySelector("[data-readout]");
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const statsEl = plate.querySelector("[data-stats]");
  let statsLast = "";
  const stats = (list) => {
    const html = list.map(([k, v, bad]) => `<div${bad ? ' class="rr-bad"' : ""}><span>${k}</span><b>${v}</b></div>`).join("");
    if (html !== statsLast) { statsEl.innerHTML = html; statsLast = html; }
  };

  const pN = stage.querySelector('[data-part="p-needle"]');
  const tN = stage.querySelector('[data-part="t-needle"]');
  const P = D.P;
  const C = D.curve;
  const at = (used) => {
    const step = C[1][0] - C[0][0];
    const f = Math.max(0, Math.min(C.length - 1.001, used / step));
    const i = Math.floor(f), t = f - i;
    return C[i].map((v, j) => v + (C[i + 1][j] - v) * t);
  };
  const impulseLeft = (used) => {
    let sum = 0;
    const n = 60;
    if (used >= D.usable) return 0;
    for (let i = 0; i < n; i++) {
      const a = used + ((D.usable - used) * (i + 0.5)) / n;
      sum += at(a)[3] * ((D.usable - used) / n);
    }
    return sum;
  };
  const needleD = (g, frac) => {
    const a = -0.75 * Math.PI + 1.5 * Math.PI * Math.max(0, Math.min(1, frac));
    return lineOnTop([g.c[0] - Math.sin(a) * 1.6, g.c[1] + Math.cos(a) * 1.6], [g.c[0] + Math.sin(a) * g.r, g.c[1] - Math.cos(a) * g.r], g.z, P);
  };
  const st = { used: { x: 0, v: 0 }, target: 0, touring: !still.matches, clock: 0 };
  let frame = 0, last = 0, visible = false, idle = 0;
  const draw = () => {
    const [used, p, T] = at(st.used.x);
    pN.setAttribute("d", needleD(D.pd, p / D.pd.full));
    tN.setAttribute("d", needleD(D.td, (T - D.td.lo) / D.td.span));
    const I = impulseLeft(used);
    const dead = used > D.usable;
    const text = dead ? `${p.toFixed(0)} bar · 減壓閥失壓` : `已用 ${used.toFixed(0)} kg · ${p.toFixed(0)} bar`;
    stats([
      ["已用空氣", `${used.toFixed(0)} / ${D.usable.toFixed(0)} kg`],
      ["瓶內壓力", `${p.toFixed(0)} bar`, dead],
      ["瓶內溫度", `${T.toFixed(0)} K`],
      ["剩餘衝量", `${(I / 1000).toFixed(1)} kN·s`, dead],
      ["可懸停", `${(I / D.weight).toFixed(1)} s`, dead],
      ["還能跳", dead ? `0 · 剩 ${(D.m0 - used).toFixed(0)} kg 用不到` : `${(Math.max(0, D.usable - used) / D.stunt).toFixed(1)} 次`, dead],
    ]);
    if (readout.textContent !== text) readout.textContent = text;
    stage.setAttribute("aria-valuenow", String(Math.round(used)));
    stage.setAttribute("aria-valuetext", text);
  };
  const max = C[C.length - 1][0];
  function tick(now) {
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
    last = now;
    st.clock += dt;
    if (st.touring) {
      const t = st.clock % 11;
      st.target = t < 7 ? (Math.min(t, 6.5) / 6.5) * (D.usable + 18) : 0;
    }
    const moving = settle(st.used, st.target, dt, st.touring && st.target === 0 ? 0.6 : 0.18, still.matches);
    draw();
    frame = visible && (moving || st.touring) ? requestAnimationFrame(tick) : 0;
  }
  const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
  const hold = () => { st.touring = false; clearTimeout(idle); idle = setTimeout(() => { st.touring = !still.matches; st.clock = 0; run(); }, 5200); };
  new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
  stage.addEventListener("pointermove", (event) => {
    const box = stage.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, ((event.clientX - box.left) / box.width - 0.08) / 0.84));
    hold();
    st.target = fx * Math.min(max, D.usable + 40);
    run();
  });
  stage.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowUp") st.target = Math.min(max, st.target + 10);
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") st.target = Math.max(0, st.target - 10);
    else if (event.key === "Home") st.target = 0;
    else if (event.key === "End") st.target = D.usable;
    else return;
    hold();
    event.preventDefault();
    run();
  });
  if (still.matches) st.used.x = st.target = D.stunt;
  draw();
}
