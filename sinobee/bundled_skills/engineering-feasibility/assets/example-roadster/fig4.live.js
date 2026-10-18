{
  const D = __DATA4__;
  const stage = document.querySelector(".rr-f4 .rr-stage");
  const plate = stage.closest(".iso-plate");
  const readout = plate.querySelector("[data-readout]");
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const statsEl = plate.querySelector("[data-stats]");
  let statsLast = "";
  const stats = (list) => {
    const html = list.map(([k, v, bad]) => `<div${bad ? ' class="rr-bad"' : ""}><span>${k}</span><b>${v}</b></div>`).join("");
    if (html !== statsLast) { statsEl.innerHTML = html; statsLast = html; }
  };
  const LIGHT = (stage.closest("[data-theme]")?.getAttribute("data-theme") ?? "dark") !== "dark" ? 1 : 0;
  const P = D.P;
  const F = D.frames;
  const T0 = F[0][0], T1 = F[F.length - 1][0];
  const PHASE = ["起跳：四顆升力推進器全開", "翻滾起動：右側兩顆", "慣性翻滾", "翻滾停止：左側兩顆", "自由落下", "減速：四顆全開", "著地", "著地 · 煞停"];
  const frameAt = (t) => {
    const f = Math.max(0, Math.min(F.length - 1.001, (t - T0) * 60));
    const i = Math.floor(f), s = f - i, a = F[i], b = F[i + 1];
    return { t, x: a[1] + (b[1] - a[1]) * s, y: a[2] + (b[2] - a[2]) * s, z: a[3] + (b[3] - a[3]) * s, phi: a[4] + (b[4] - a[4]) * s, gas: a[5] + (b[5] - a[5]) * s, phase: s < 0.5 ? a[6] : b[6], fire: s < 0.5 ? a[7] : b[7], p: a[8] + (b[8] - a[8]) * s, vz: a[9] + (b[9] - a[9]) * s };
  };
  const rot = (phi) => { const c = Math.cos(phi), s = Math.sin(phi); return [1, 0, 0, 0, c, -s, 0, s, c]; };
  const controller = TURN.mount(stage, D.turn, { follow: (v) => v, onHold: () => { st.touring = false; st.hooked = true; } });
  const shadow = stage.querySelector('[data-part="shadow"]');
  const shadowD = (fr) => {
    const [L, Wd, Hh] = D.foot;
    const half = (Math.abs(Math.cos(fr.phi)) * Wd + Math.abs(Math.sin(fr.phi)) * Hh) / 2;
    const cx = D.origin[0] + fr.x, cy = fr.y;
    const pts = [[cx - L / 2, cy - half], [cx + L / 2, cy - half], [cx + L / 2, cy + half], [cx - L / 2, cy + half]].map(([x, y]) => iso([x, y, 0], P));
    return `M${pts.map((p) => p.map((v) => v.toFixed(1)).join(" ")).join("L")}Z`;
  };
  // ---- cold-gas jets: front canvas, anchored to the four lift nozzles, clipped at the ground and at roofs
  const FRAG = `${GLSL_TURN}
uniform float uLight;
uniform vec3 uE[4];
uniform vec3 uDir[4];
uniform vec4 uOn;
uniform vec4 uBack;
uniform vec4 uRoof[5];
uniform float uRoofH[5];
uniform float uRe;
vec4 over(vec4 top,vec4 under){return top+under*(1.-top.a);}
float roofAt(vec2 q){
  float h=0.;
  for(int k=0;k<5;k++){vec4 b=uRoof[k];if(q.x>b.x&&q.x<b.y&&q.y>b.z&&q.y<b.w)h=max(h,uRoofH[k]);}
  return h;
}
void main(){
  vec2 vb=viewOf(gl_FragCoord.xy);
  vec3 V=towardViewer();
  vec3 p0=rayOf(vb);
  float px=uView.x/uCanvas.x;
  float soft=.7*px;
  float cover=coverOf(vb,soft);
  float jet=0.,sheet=0.,mask=0.;
  for(int i=0;i<4;i++){
    float on=uOn[i];
    if(on<.003)continue;
    vec3 E=uE[i];
    vec3 d=normalize(uDir[i]);
    float Lj=150.;
    // stop where the jet meets the ground or a roof under it
    if(d.z<-.05){
      float lo=0.,hi=Lj;
      for(int s=0;s<8;s++){float m=(lo+hi)*.5;vec3 q=E+d*m;if(q.z>roofAt(q.xy))lo=m;else hi=m;}
      Lj=hi;
    }
    vec3 w0=p0-E;
    float b=dot(V,d);
    float dd=dot(V,w0),e=dot(d,w0);
    float den=max(1.-b*b,1e-4);
    float u=clamp((e-b*dd)/den,0.,Lj);
    vec3 q=E+d*u;
    float t=dot(q-p0,V);
    float r=length(p0+V*t-q);
    float rj=uRe*(1.+u*.045)+.5;
    vec3 flow=vec3(u*.09-uTime*14.,r*.12,float(i)*3.1);
    float turb=.35+1.2*smoothstep(.3,.8,fbm3(flow));
    float body=exp(-pow(r/rj,2.))*(1.-smoothstep(.55*Lj,Lj+20.,u))*smoothstep(-.5,2.,u)*turb;
    float behind=uBack[i]*cover;
    jet+=body*on*(1.-behind);
    // the sheet that spreads where it lands
    vec3 hit=E+d*Lj;
    float ground=roofAt(hit.xy);
    vec3 g=onFloor(vb,ground+.5);
    float rr=length(g.xy-hit.xy);
    float near=smoothstep(260.,20.,length(E-hit));
    float reach=60.+220.*near;
    float ringD=exp(-pow(rr/reach,2.))*(.45+.9*smoothstep(.25,.8,fbm3(vec3(g.xy*.025,uTime*1.1+float(i)*1.7))));
    sheet+=ringD*on*near*(1.-cover);
  }
  vec4 ink;
  if(uLight>.5){
    float aJ=.82*(1.-exp(-jet*.9));
    vec4 jetInk=vec4(vec3(.80,.84,.89)*aJ,aJ);
    float aS=.78*(1.-exp(-sheet*1.3));
    vec4 sheetInk=vec4(vec3(.80,.83,.87)*aS,aS);
    ink=over(jetInk,sheetInk);
  }else{
    vec3 col=vec3(.72,.78,.86)*jet*.55+vec3(.6,.62,.66)*sheet*.4;
    col=tonemap(col,1.1);
    ink=vec4(col,max(col.r,max(col.g,col.b)));
  }
  ink.rgb=min(ink.rgb,vec3(ink.a));
  float n=(hash12(gl_FragCoord.xy+fract(uTime*.61)*91.)+hash12(gl_FragCoord.yx*1.37+fract(uTime*.37)*57.)-1.)/255.;
  gl_FragColor=clamp(ink+vec4(n)*smoothstep(0.,1.5/255.,ink.a),0.,1.);
}`;
  const roofs = D.roofs;
  const layer = glLayer({ canvas: stage.querySelector("canvas"), fragment: FRAG, view: [D.W, D.H], camera: D.camera, uniforms: ["uE", "uDir", "uOn", "uBack", "uCoverEdge", "uCoverCount"], fixed: { uLight: LIGHT, uRe: D.nozzles[0].r, uRoof: new Float32Array(roofs.flatMap((b) => [b[0], b[1], b[2], b[3]])), uRoofH: new Float32Array(roofs.map((b) => b[4])) } });
  if (layer) stage.setAttribute("data-gl", "");
  const V = (() => { const a = (P.azimuth ?? 45) * Math.PI / 180, e = (P.elevation ?? 30) * Math.PI / 180; return [Math.cos(a) * Math.cos(e), Math.sin(a) * Math.cos(e), Math.sin(e)]; })();
  const jets = D.nozzles.map(() => ({ x: 0, v: 0 }));
  const st = { t: { x: T0, v: 0 }, target: T0, touring: !still.matches, clock: 0, mode: "play", dir: 1 };
  let frame = 0, last = 0, visible = false, idle = 0;
  const render = (dt) => {
    if (st.hooked) return;
    const fr = frameAt(st.t.x);
    controller.set({ car: fr.t <= T0 + 1e-6 && fr.x === F[0][1] && false ? null : { R: rot(fr.phi), t: [fr.x, fr.y, fr.z] } });
    shadow.setAttribute("d", shadowD(fr));
    shadow.style.opacity = (0.55 / (1 + fr.z / 180)).toFixed(3);
    const playing = !(st.mode === "play" && st.dir < 0);
    const E = [], Dd = [], On = [], Back = [];
    const C = [D.origin[0] + fr.x, fr.y, D.origin[2] + fr.z];
    D.nozzles.forEach((n, i) => {
      const a = controller.anchor("car", n.exit, n.dir);
      const want = playing && fr.fire & n.bit ? 1 : 0;
      settle(jets[i], want, dt, want ? 0.03 : 0.08, still.matches);
      E.push(...a.world);
      Dd.push(...a.direction);
      On.push(jets[i].x);
      Back.push((a.world[0] - C[0]) * V[0] + (a.world[1] - C[1]) * V[1] + (a.world[2] - C[2]) * V[2] < 0 ? 1 : 0);
    });
    stage.style.setProperty("--rr-flow", Math.max(...On).toFixed(3));
    if (layer) {
      if (Math.max(...On) > 0.003) {
        const cov = TURN.cover(controller, D.cover);
        layer.draw({ uE: new Float32Array(E), uDir: new Float32Array(Dd), uOn: new Float32Array(On), uBack: new Float32Array(Back), uCoverEdge: cov.uCoverEdge, uCoverCount: cov.uCoverCount }, still.matches ? 0.8 : st.clock);
      } else layer.clear();
    }
    const firing = ["L1", "L2", "L3", "L4"].filter((_, i) => fr.fire & (1 << i));
    const text = `t ${fr.t.toFixed(2)} s · ${PHASE[fr.phase] ?? "駛入"}`;
    if (readout.textContent !== text) readout.textContent = text;
    stats([
      ["重心升高", `${(fr.z / 100).toFixed(2)} m`],
      ["翻滾角", `${((fr.phi * 180) / Math.PI).toFixed(0)}°`],
      ["垂直速度", `${fr.vz.toFixed(1)} m/s`, fr.phase === 6 && Math.abs(fr.vz) > 1],
      ["推進器", firing.length ? firing.join(" ") : "—"],
      ["已用空氣", `${fr.gas.toFixed(0)} / ${D.usable.toFixed(0)} kg`],
      ["瓶壓", fr.p ? `${fr.p.toFixed(0)} bar` : fr.t > 0 ? `${D.endP.toFixed(0)} bar` : "690 bar"],
    ]);
    stage.setAttribute("aria-valuenow", fr.t.toFixed(2));
    stage.setAttribute("aria-valuetext", text);
  };
  function tick(now) {
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
    last = now;
    st.clock += dt;
    let moving = false;
    if (st.touring) {
      if (st.mode === "play") {
        st.t.x = Math.min(T1, st.t.x + dt * st.dir * (st.dir > 0 ? 1 : 3));
        st.target = st.t.x;
        if (st.dir > 0 && st.t.x >= T1) { st.mode = "wait"; st.until = st.clock + 1.4; st.next = -1; }
        if (st.dir < 0 && st.t.x <= T0) { st.t.x = T0; st.mode = "wait"; st.until = st.clock + 1.0; st.next = 1; }
      } else if (st.clock > st.until) { st.mode = "play"; st.dir = st.next; }
      moving = true;
    } else {
      moving = settle(st.t, st.target, dt, 0.12, still.matches);
      if (st.mode === "play" && st.dir > 0) { st.target = Math.min(T1, st.target + dt); st.t.x = st.target; moving = st.target < T1; if (!moving) st.mode = "wait"; }
    }
    render(dt);
    const jetsOn = jets.some((j) => j.x > 0.003);
    frame = visible && (moving || st.touring || jetsOn) ? requestAnimationFrame(tick) : 0;
  }
  const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
  const hold = () => { st.touring = false; clearTimeout(idle); idle = setTimeout(() => { st.touring = !still.matches; st.mode = "play"; st.dir = st.t.x > (T0 + T1) / 2 ? -1 : 1; run(); }, 6000); };
  new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
  stage.addEventListener("pointermove", (event) => {
    const box = stage.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, ((event.clientX - box.left) / box.width - 0.06) / 0.88));
    hold();
    st.mode = "scrub";
    st.target = T0 + fx * (T1 - T0);
    run();
  });
  stage.addEventListener("click", () => { hold(); st.mode = "play"; st.dir = 1; st.target = st.t.x = st.t.x >= T1 - 0.01 ? T0 : st.t.x; run(); });
  stage.addEventListener("keydown", (event) => {
    if (event.key === " " || event.key === "Enter") { hold(); st.mode = "play"; st.dir = 1; st.target = st.t.x = st.t.x >= T1 - 0.01 ? T0 : st.t.x; }
    else if (event.key === "ArrowRight") { hold(); st.mode = "scrub"; st.target = Math.min(T1, st.target + 0.1); }
    else if (event.key === "ArrowLeft") { hold(); st.mode = "scrub"; st.target = Math.max(T0, st.target - 0.1); }
    else if (event.key === "Home") { hold(); st.mode = "scrub"; st.target = T0; }
    else return;
    event.preventDefault();
    run();
  });
  if (still.matches) { st.t.x = st.target = D.apexT; }
  const hashT = /[#&]t4=([-\d.]+)/.exec(location.hash);
  if (hashT) { st.touring = false; st.mode = "scrub"; st.t.x = st.target = +hashT[1]; jets.forEach((j) => (j.x = 1)); }
  render(1 / 60);
}
