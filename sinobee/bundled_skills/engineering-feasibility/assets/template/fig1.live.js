{
  // One block per figure: no top-level names leak into the next figure's script.
  const D = __DATA1__;
  const stage = document.querySelector(".rr-f1 .rr-stage");
  const plate = stage.closest(".iso-plate");
  const readout = plate.querySelector("[data-readout]");
  const statsEl = plate.querySelector("[data-stats]");
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const needle = stage.querySelector('[data-part="needle"]');
  const st = { share: { x: 0.3, v: 0 }, target: 0.3, touring: !still.matches, clock: 0 };
  let frame = 0, last = 0, visible = false, idle = 0, statsLast = "";
  const demand = (share) => { const required = 20 + 140 * share; return { required, limit: D.limit, ok: required <= D.limit }; };
  const needleD = (frac) => {
    const g = D.dial, a = -0.75 * Math.PI + 1.5 * Math.PI * Math.max(0, Math.min(1, frac));
    return lineOnTop([g.c[0] - Math.sin(a) * 2, g.c[1] + Math.cos(a) * 2], [g.c[0] + Math.sin(a) * g.r, g.c[1] - Math.cos(a) * g.r], g.z, D.P);
  };
  const draw = () => {
    const d = demand(st.share.x);
    needle.setAttribute("d", needleD(d.required / D.dial.full));
    const text = `${d.required.toFixed(0)} / ${d.limit} ${D.unit}${d.ok ? "" : " · 超出極限"}`;
    if (readout.textContent !== text) readout.textContent = text;
    const html = [["需求", `${d.required.toFixed(0)} ${D.unit}`, !d.ok], ["極限", `${d.limit} ${D.unit}`], ["餘裕", `${(d.limit / d.required).toFixed(2)}×`, !d.ok]]
      .map(([k, v, bad]) => `<div${bad ? ' class="rr-bad"' : ""}><span>${k}</span><b>${v}</b></div>`).join("");
    if (html !== statsLast) { statsEl.innerHTML = html; statsLast = html; }
    stage.setAttribute("aria-valuetext", text);
  };
  function tick(now) {
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
    last = now;
    st.clock += dt;
    if (st.touring) st.target = 0.5 - 0.5 * Math.cos(st.clock * 0.6);
    const moving = settle(st.share, st.target, dt, 0.25, still.matches);
    draw();
    frame = visible && (moving || st.touring) ? requestAnimationFrame(tick) : 0;
  }
  const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
  const hold = () => { st.touring = false; clearTimeout(idle); idle = setTimeout(() => { st.touring = !still.matches; run(); }, 5000); };
  new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
  stage.addEventListener("pointermove", (event) => {
    const box = stage.getBoundingClientRect();
    hold();
    st.target = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    run();
  });
  stage.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") st.target = Math.min(1, st.target + 0.1);
    else if (event.key === "ArrowLeft") st.target = Math.max(0, st.target - 0.1);
    else return;
    hold();
    event.preventDefault();
    run();
  });
  draw();
}
