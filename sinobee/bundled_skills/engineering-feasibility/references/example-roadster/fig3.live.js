{
  const D = __DATA3__;
  const stage = document.querySelector(".rr-f3 .rr-stage");
  const plate = stage.closest(".iso-plate");
  const readout = plate.querySelector("[data-readout]");
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const statsEl = plate.querySelector("[data-stats]");
  let statsLast = "";
  const stats = (list) => {
    const html = list.map(([k, v, bad]) => `<div${bad ? ' class="rr-bad"' : ""}><span>${k}</span><b>${v}</b></div>`).join("");
    if (html !== statsLast) { statsEl.innerHTML = html; statsLast = html; }
  };

  const st = { tilt: { x: 0, v: 0 }, target: 0, speed: 0, spin: 0, touring: !still.matches, clock: 0, fans: true };
  let frame = 0, last = 0, visible = false, idle = 0, own = true;
  const follow = (values) => {
    const spin = values.spin ?? 0;
    const wrap = ((spin % 60) + 60) % 60;
    return { tilt: values.tilt ?? 0, fan0: wrap, fan1: wrap };
  };
  const controller = TURN.mount(stage, D.turn, { follow, onHold: () => { st.touring = false; st.hooked = true; clearTimeout(idle); }, onFrame: (c) => { if (!own) { st.tilt.x = st.target = c.values.tilt; } } });
  const rowAt = (theta) => {
    const tab = D.table[st.fans ? st.speed : D.speeds.length + st.speed];
    const f = Math.max(0, Math.min(tab.length - 1.001, theta / D.step));
    const i = Math.floor(f), t = f - i;
    return tab[i].map((v, j) => v + (tab[i + 1][j] - v) * t);
  };
  const place = (theta) => (theta < 30 ? "地面" : theta < 135 ? "牆面" : "天花板");
  const say = () => {
    const th = st.tilt.x;
    const [N, acc, , fanG, aeroG, kW, dp] = rowAt(th);
    const v = D.speeds[st.speed];
    const head = `${th.toFixed(0)}° ${place(th)} · ${Math.round(v * 3.6)} km/h`;
    const holds = N > 0 && N * D.mu >= Math.abs(Math.sin((th * Math.PI) / 180)) - 1e-6;
    const text = holds ? head : `${head} · 抓不住，車會掉下來`;
    stats([
      ["正壓力", `${N.toFixed(2)} g`, !holds],
      ["風扇吸力", st.fans ? `${fanG.toFixed(2)} g` : "關閉", !st.fans],
      ["空力下壓", `${aeroG.toFixed(2)} g`],
      ["剩餘抓地", `${holds ? acc.toFixed(2) : "0.00"} g`, !holds],
      ["車底負壓", `${(dp / 1000).toFixed(1)} kPa`],
      ["風扇功率", `${kW.toFixed(0)} kW`],
    ]);
    if (readout.textContent !== text) readout.textContent = text;
    stage.setAttribute("aria-valuenow", String(Math.round(th)));
    stage.setAttribute("aria-valuetext", text);
  };
  const render = () => {
    if (st.hooked) return;
    own = true;
    controller.set({ tilt: st.tilt.x, spin: st.spin });
    say();
  };
  function tick(now) {
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
    last = now;
    st.clock += dt;
    if (st.touring) {
      const t = st.clock % 16;
      st.target = t < 2 ? 0 : t < 5.5 ? ((t - 2) / 3.5) * 90 : t < 7.5 ? 90 : t < 11 ? 90 + ((t - 7.5) / 3.5) * 90 : t < 13 ? 180 : 180 - ((t - 13) / 3) * 180;
      st.speed = t > 13 && t < 16 ? 0 : st.speed;
    }
    const moving = settle(st.tilt, st.target, dt, st.touring ? 0.35 : 0.22, still.matches);
    if (st.fans && !still.matches) st.spin += dt * 900;
    render();
    frame = visible && (moving || st.touring || st.fans) ? requestAnimationFrame(tick) : 0;
  }
  const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
  const hold = () => { st.touring = false; clearTimeout(idle); idle = setTimeout(() => { st.touring = !still.matches; st.clock = 0; run(); }, 6000); };
  new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
  stage.addEventListener("pointermove", (event) => {
    const box = stage.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, ((event.clientX - box.left) / box.width - 0.1) / 0.8));
    hold();
    st.target = fx * 180;
    run();
  });
  stage.addEventListener("click", () => { hold(); st.speed = (st.speed + 1) % D.speeds.length; run(); });
  stage.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowUp") st.target = Math.min(180, st.target + 15);
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") st.target = Math.max(0, st.target - 15);
    else if (event.key === "v" || event.key === "V" || event.key === " ") st.speed = (st.speed + 1) % D.speeds.length;
    else if (event.key === "f" || event.key === "F") st.fans = !st.fans;
    else return;
    hold();
    event.preventDefault();
    run();
  });
  render();
}
