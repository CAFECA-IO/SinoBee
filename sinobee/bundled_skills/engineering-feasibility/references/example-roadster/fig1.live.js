{
  const D = __DATA1__;
  const stage = document.querySelector(".rr-f1 .rr-stage");
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
  const pcNeedle = stage.querySelector('[data-part="pc-needle"]');
  const thrustNeedle = stage.querySelector('[data-part="thrust-needle"]');
  const knobPointer = stage.querySelector('[data-part="knob-pointer"]');
  const P = D.P;
  const row = (bar) => {
    const b = Math.max(0, Math.min(D.table.length - 1.001, bar));
    const i = Math.floor(b), f = b - i, a = D.table[i], c = D.table[i + 1];
    return a.map((v, j) => v + (c[j] - v) * f);
  };
  const needleD = (g, frac) => {
    const ang = -0.75 * Math.PI + 1.5 * Math.max(0, Math.min(1, frac)) * Math.PI;
    return lineOnTop([g.c[0] - Math.sin(ang) * 1.6, g.c[1] + Math.cos(ang) * 1.6], [g.c[0] + Math.sin(ang) * g.r, g.c[1] - Math.cos(ang) * g.r], g.z, P);
  };
  const pointerD = (angle) => {
    const K = D.knob;
    const p = (r) => [K.face[0] + (K.u[0] * Math.cos(angle) + K.v[0] * Math.sin(angle)) * r, K.face[1] + (K.u[1] * Math.cos(angle) + K.v[1] * Math.sin(angle)) * r];
    const a = p(K.r0), b = p(K.r1);
    return `M${a[0].toFixed(2)} ${a[1].toFixed(2)}L${b[0].toFixed(2)} ${b[1].toFixed(2)}`;
  };
  const FRAG = `
uniform float uOn;
uniform float uPR;
uniform float uMe;
uniform float uCond;
uniform float uSep;
uniform float uLight;
uniform vec3 uExit;
uniform float uRe;
vec4 over(vec4 top,vec4 under){return top+under*(1.-top.a);}
void main(){
  vec2 vb=viewOf(gl_FragCoord.xy);
  vec3 V=towardViewer();
  vec3 p0=rayOf(vb);
  float Re=uRe;
  float L=175.;
  float Rb=36.;
  vec2 q0=p0.yz-uExit.yz;
  vec2 vv=V.yz;
  float A=dot(vv,vv);
  float Bq=2.*dot(q0,vv);
  float C=dot(q0,q0)-Rb*Rb;
  float disc=Bq*Bq-4.*A*C;
  if(disc<=0.||uOn<.002){gl_FragColor=vec4(0.);return;}
  float sq=sqrt(disc);
  float t0=(-Bq-sq)/(2.*A);
  float t1=(-Bq+sq)/(2.*A);
  const int N=56;
  float dt=(t1-t0)/float(N);
  float jit=hash12(gl_FragCoord.xy+fract(uTime*.37)*41.);
  float core=0.,fog=0.,shock=0.,lens=0.,up=0.;
  float pr=clamp(uPR,.2,4.);
  float barrel=clamp(pow(pr,.42),.62,1.75);
  float shift=1.-.38*uSep;
  float Lc=Re*(9.+3.*uMe)*mix(1.,.45,uSep);
  float Lcell=1.3*2.*Re*sqrt(max(uMe*uMe-1.,.3))*mix(1.,sqrt(pr),.6)*.62;
  float amp=(.55+.45*clamp(abs(log(pr))/1.1,0.,1.))*(1.-uSep);
  float disk=smoothstep(.95,.6,pr);
  vec2 wob=uSep*Re*.45*vec2(noise2(vec2(uTime*6.1,1.3))-.5,noise2(vec2(uTime*5.3,7.9))-.5);
  for(int i=0;i<N;i++){
    float t=t0+(float(i)+jit)*dt;
    vec3 p=p0+t*V;
    float s=uExit.x-p.x;
    float inside=smoothstep(-.6,.8,s)*(1.-smoothstep(.72*L,L,s))*smoothstep(1.,6.,p.z);
    if(inside<=0.)continue;
    vec2 off=p.yz-uExit.yz-wob*smoothstep(0.,3.*Re,s);
    float r=length(off);
    float grow=smoothstep(0.,1.4*Re,s);
    float fade=1.-smoothstep(.3*Lc,Lc,s);
    float rc=mix(Re*.96,Re*barrel,grow)*shift*(1.-.3*smoothstep(.3*Lc,Lc,s));
    float ph=fract(s/Lcell);
    rc*=1.+.08*amp*cos(6.2832*ph)*fade;
    float rm=rc+s*.115+.4;
    float coreD=(1.-smoothstep(rc*.86,rc*1.04,r))*(1.-smoothstep(.55*Lc,1.15*Lc,s));
    float rt=rc*abs(1.-2.*ph);
    float shell=exp(-pow((r-rt)/(.13*rc+.35),2.))*(1.-smoothstep(rc*.9,rc*1.05,r))*amp*fade*smoothstep(.2,1.2*Re,s);
    float node=exp(-pow((ph-.5)/.05,2.))*(1.-smoothstep(rc*.32*disk,rc*.42*disk+.3,r))*disk*fade;
    vec3 flow=vec3(p.x*.11+uTime*13.,p.y*.15,p.z*.15);
    float turb=.15+1.7*smoothstep(.3,.8,fbm3(flow));
    float mixD=(1.-smoothstep(rc*.9,rm*1.08,r))*smoothstep(rc*.45,rc*.9,r+s*.08)*turb*smoothstep(0.,.9*Re,s);
    float fogD=uCond*(mixD*1.9+coreD*.25)*exp(-s/130.);
    float w=dt/Re*inside;
    core+=coreD*w;
    fog+=fogD*w;
    shock+=shell*w;
    lens+=node*w;
    up+=fogD*w*clamp((p.z-uExit.z)/max(rm,1.),-1.,1.);
  }
  float lift=fog>1e-4?up/fog:0.;
  vec4 ink;
  if(uLight>.5){
    float aC=.62*(1.-exp(-core*.7));
    vec4 coreInk=vec4(vec3(.58,.69,.82)*aC,aC);
    float aS=.7*(1.-exp(-shock*2.4));
    vec4 shockInk=vec4(vec3(.36,.48,.66)*aS,aS);
    float aF=.9*(1.-exp(-fog*1.1));
    vec3 fogSeen=mix(vec3(.76,.79,.83),vec3(.95,.96,.97),smoothstep(-.6,.6,lift));
    vec4 fogInk=vec4(fogSeen*aF,aF);
    float aL=.85*(1.-exp(-lens*6.));
    vec4 lensInk=vec4(vec3(.95,.97,1.)*aL,aL);
    ink=over(fogInk,over(lensInk,over(shockInk,coreInk)));
  }else{
    vec3 col=vec3(.28,.46,.80)*core*.26+vec3(.62,.80,1.)*shock*.8+vec3(.72,.77,.84)*fog*.42+vec3(.9,.95,1.)*lens*2.;
    col=tonemap(col,1.1);
    ink=vec4(col,max(col.r,max(col.g,col.b)));
  }
  ink*=uOn;
  ink.rgb=min(ink.rgb,vec3(ink.a));
  float n=(hash12(gl_FragCoord.xy+fract(uTime*.61)*91.)+hash12(gl_FragCoord.yx*1.37+fract(uTime*.37)*57.)-1.)/255.;
  gl_FragColor=clamp(ink+vec4(n)*smoothstep(0.,1.5/255.,ink.a),0.,1.);
}`;
  const canvas = stage.querySelector("canvas");
  const layer = glLayer({ canvas, fragment: FRAG, view: [D.W, D.H], camera: D.camera, uniforms: ["uOn", "uPR", "uMe", "uCond", "uSep"], fixed: { uLight: LIGHT, uExit: D.exit, uRe: D.exitR } });
  if (layer) stage.setAttribute("data-gl", "");
  const TOUR = [[100, 1.7], [30, 1.7], [180, 1.7], [60, 1.7]];
  const st = { set: 100, knob: { x: 100, v: 0 }, pc: { x: 0, v: 0 }, flow: { x: 0, v: 0 }, cond: { x: 0, v: 0 }, sep: { x: 0, v: 0 }, open: false, until: 0, clock: 0, touring: !still.matches, tourAt: 0 };
  let frame = 0, last = 0, visible = false, idle = 0;
  const regime = (r) => {
    if (r[6] > 0.5) return "噴流分離";
    if (r[4] < 0.85) return "過膨脹";
    if (r[4] > 1.18) return "欠膨脹";
    return "接近完全膨脹";
  };
  const say = () => {
    const r = row(st.pc.x);
    const open = st.pc.x > 2;
    const text = open ? `Pc ${st.pc.x.toFixed(0)} bar · F ${r[0].toFixed(1)} kN` : `設定 ${st.set.toFixed(0)} bar · 閥關閉`;
    const show = open ? r : row(st.set);
    stats([
      ["穩壓室壓力", `${(open ? st.pc.x : st.set).toFixed(0)} bar`],
      ["推力", `${show[0].toFixed(1)} kN`],
      ["質量流率", `${show[1].toFixed(0)} kg/s`],
      ["比衝 Isp", `${show[2].toFixed(0)} s`],
      ["出口靜溫", `${show[3].toFixed(0)} K${show[8] > 0 ? " · 冷凝" : ""}`, show[8] > 0],
      ["出口/環境壓力", `${show[4].toFixed(2)} · ${regime(show)}`, show[6] > 0.5],
    ]);
    if (readout.textContent !== text) readout.textContent = text;
    stage.setAttribute("aria-valuenow", String(Math.round(st.set)));
    stage.setAttribute("aria-valuetext", text);
  };
  const draw = () => {
    const r = row(st.pc.x);
    pcNeedle.setAttribute("d", needleD(D.gauge, st.pc.x / 250));
    thrustNeedle.setAttribute("d", needleD(D.dial, r[0] / D.dial.full));
    knobPointer.setAttribute("d", pointerD(-Math.PI / 2 + (st.knob.x / 220) * 1.6 * Math.PI));
    stage.style.setProperty("--rr-flow", st.flow.x.toFixed(3));
    stage.style.setProperty("--rr-cond", st.cond.x.toFixed(3));
    if (layer) {
      if (st.flow.x > 1e-3) layer.draw({ uOn: st.flow.x, uPR: r[4] || 1, uMe: r[7] || 3.6, uCond: st.cond.x, uSep: st.sep.x }, still.matches ? 0.8 : st.clock);
      else layer.clear();
    }
    say();
  };
  function tick(now) {
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
    last = now;
    const calm = still.matches;
    st.clock += dt;
    if (st.touring) {
      st.tourAt += dt;
      const cycle = TOUR.reduce((s, [, d]) => s + d + 1.3, 0);
      let t = st.tourAt % cycle;
      for (const [bar, d] of TOUR) {
        if (t < d + 1.3) { st.set = bar; st.open = t > 0.5 && t < 0.5 + d; break; }
        t -= d + 1.3;
      }
    } else if (st.until && st.clock > st.until) { st.open = false; st.until = 0; }
    let moving = settle(st.knob, st.set, dt, 0.25, calm);
    moving = settle(st.pc, st.open ? st.set : 0, dt, 0.07, calm) || moving;
    moving = settle(st.flow, Math.min(1, st.pc.x / 22), dt, 0.05, calm) || moving;
    const r = row(st.pc.x);
    moving = settle(st.cond, st.pc.x > 2 ? Math.max(0, Math.min(1, r[8] / 10)) : st.cond.x, dt, 0.25, calm) || moving;
    moving = settle(st.sep, st.pc.x > 2 ? Math.max(0, Math.min(1, (0.42 - r[4]) / 0.12)) : st.sep.x, dt, 0.2, calm) || moving;
    draw();
    frame = visible && (moving || st.touring || st.open || st.flow.x > 1e-3) ? requestAnimationFrame(tick) : 0;
  }
  const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
  const hold = () => {
    st.touring = false;
    clearTimeout(idle);
    idle = setTimeout(() => { st.touring = !still.matches; st.tourAt = 0; run(); }, 5200);
  };
  const fire = () => { hold(); st.open = true; st.until = st.clock + 1.6; run(); };
  new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
  stage.addEventListener("pointermove", (event) => {
    const box = stage.getBoundingClientRect();
    const fx = Math.max(0, Math.min(1, ((event.clientX - box.left) / box.width - 0.1) / 0.8));
    hold();
    if (!st.open) st.set = Math.round(20 + 180 * fx);
    run();
  });
  stage.addEventListener("click", fire);
  stage.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowUp") { hold(); st.set = Math.min(200, st.set + 10); }
    else if (event.key === "ArrowLeft" || event.key === "ArrowDown") { hold(); st.set = Math.max(20, st.set - 10); }
    else if (event.key === " " || event.key === "Enter") fire();
    else return;
    event.preventDefault();
    run();
  });
  draw();
}
