"use strict";
// Meridies memory layer. Map frame: metres, +X right, +Y up on screen, origin at the house.
// Heading: degrees clockwise from +Y (map "up"). Not north-aligned.

const NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const svgEl = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
const norm360 = (d) => ((d % 360) + 360) % 360;

const state = {
  plan: null,
  walker: { x: 0, y: 0, heading: 0 },
  target: null,
  listeners: [],          // functions called after the walker moves or turns
};
const onWalkerChange = (fn) => state.listeners.push(fn);

function toast(msg, ms = 1600) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ---------- geometry ----------
function insidePoly(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
const isInterior = () => state.plan && state.plan.kind === "interior";
const sweepAt = (i) => isInterior() && state.plan.sweeps.find((s) => s.index === i);
const walkable = (x, y) => isInterior()
  ? state.plan.walkable.some((poly) => insidePoly(x, y, poly))   // interior: any room
  : insidePoly(x, y, state.plan.walkable);
function currentZone() {
  if (isInterior()) {
    const s = sweepAt(state.walker.sweep);
    return s ? { name: `${s.room} · sweep ${s.index}` } : null;
  }
  const { x, y } = state.walker;
  let best = null;
  for (const z of state.plan.zones) {
    const d = Math.hypot(x - z.center[0], y - z.center[1]);
    if (d <= z.radius_m && (!best || d < best.d)) best = { z, d };
  }
  return best && best.z;
}

// ---------- map ----------
const map = $("map");
let layers = {};
function drawMap() {
  map.replaceChildren();
  const p = state.plan;
  state.k = isInterior() ? 0.38 : 1;   // glyph scale: the house is ~16 m across, the yard 50 m
  if (isInterior()) drawInterior(p); else drawYard(p);
  layers.anchors = svgEl("g", {}, map);
  layers.walker = svgEl("g", {}, map);
  const wg = svgEl("g", { transform: `scale(${state.k})` }, layers.walker);
  // heading cone (60 deg) + body + nose
  svgEl("path", { d: coneD(5, 30), fill: "rgba(255,255,255,.22)", stroke: "rgba(255,255,255,.6)", "stroke-width": 0.08 }, wg);
  svgEl("circle", { r: 0.8, fill: "var(--walker)", stroke: "#000", "stroke-width": 0.15 }, wg);
  svgEl("path", { d: "M 0 -2.1 L 0.7 -0.65 L -0.7 -0.65 Z", fill: "var(--walker)", stroke: "#000", "stroke-width": 0.1 }, wg);
}

function drawInterior(p) {
  const xs = p.rooms.flatMap((r) => r.polygon.map((q) => q[0])), ys = p.rooms.flatMap((r) => r.polygon.map((q) => q[1]));
  const pad = 1.2, x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
  map.setAttribute("viewBox", `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`);
  svgEl("rect", { x: x0, y: -y1, width: x1 - x0, height: y1 - y0, fill: "#0d1117" }, map);
  const rg = svgEl("g", {}, map);
  for (const r of p.rooms) {
    svgEl("polygon", { points: r.polygon.map(([x, y]) => `${x},${-y}`).join(" "), fill: "rgba(63,208,224,.10)" }, rg);
    const cx = r.polygon.reduce((s, q) => s + q[0], 0) / r.polygon.length, cy = r.polygon.reduce((s, q) => s + q[1], 0) / r.polygon.length;
    const t = svgEl("text", { x: cx, y: -cy, fill: "var(--dim)", "font-size": 0.42, "text-anchor": "middle", opacity: 0.85 }, rg);
    t.textContent = r.label;
  }
  for (const w of p.walls) {
    svgEl("line", { x1: w.a[0], y1: -w.a[1], x2: w.b[0], y2: -w.b[1], stroke: w.type === "wall" ? "#e6edf3" : "#5b636b",
      "stroke-width": w.type === "wall" ? Math.max(0.1, w.thickness || 0.12) : 0.04, "stroke-linecap": "round" }, rg);
  }
  for (const o of p.openings || []) {
    if (o.type !== "doorway") continue;
    svgEl("rect", { x: o.center[0] - 0.18, y: -o.center[1] - 0.18, width: 0.36, height: 0.36, fill: "#0d1117",
      stroke: "var(--community)", "stroke-width": 0.06 }, rg);
  }
  for (const h of p.hazards || []) {
    const g = svgEl("g", { transform: `translate(${h.position[0]},${-h.position[1]})` }, rg);
    svgEl("path", { d: "M -0.25 -0.25 L 0.25 0.25 M -0.25 0.25 L 0.25 -0.25", stroke: "magenta", "stroke-width": 0.1 }, g);
    const t = svgEl("text", { y: 0.65, fill: "magenta", "font-size": 0.38, "text-anchor": "middle" }, g);
    t.textContent = `${h.kind} ${h.height_m.toFixed(2)} m`;
  }
  layers.sweeps = svgEl("g", {}, map);
  for (const s of p.sweeps) {
    const c = svgEl("circle", { cx: s.position.x, cy: -s.position.y, r: 0.2, class: "sweep", "data-i": s.index,
      fill: "#30363d", stroke: "#9aa4af", "stroke-width": 0.04 }, layers.sweeps);
    c.addEventListener("click", (e) => { e.stopPropagation(); goToSweep(s.index); });
  }
  const sb = svgEl("g", { transform: `translate(${x0 + 0.5},${-y0 - 0.5})` }, map);
  svgEl("rect", { x: 0, y: -0.06, width: 1, height: 0.12, fill: "#fff" }, sb);
  const st = svgEl("text", { x: 0.5, y: -0.2, fill: "#fff", "font-size": 0.35, "text-anchor": "middle" }, sb);
  st.textContent = "1 m";
}

// highlight the sweep you stand on and the ones you can step to
function updateSweeps() {
  if (!isInterior() || !layers.sweeps) return;
  const cur = sweepAt(state.walker.sweep), nb = new Set(cur ? cur.neighbours : []);
  for (const c of layers.sweeps.children) {
    const i = +c.dataset.i;
    c.setAttribute("fill", i === state.walker.sweep ? "#fff" : nb.has(i) ? "var(--walk)" : "#30363d");
    c.setAttribute("r", nb.has(i) ? 0.26 : 0.2);
  }
}

function drawYard(p) {
  const [x0, x1, y0, y1] = p.backdrop.extent;
  map.setAttribute("viewBox", `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`); // SVG y = -map y
  svgEl("image", { href: `/data/${p.backdrop.image}`, x: x0, y: -y1, width: x1 - x0, height: y1 - y0,
    preserveAspectRatio: "none", opacity: 0.75 }, map);
  svgEl("polygon", { points: p.walkable.map(([x, y]) => `${x},${-y}`).join(" "),
    fill: "rgba(63,208,224,.14)", stroke: "var(--walk)", "stroke-width": 0.18, "stroke-linejoin": "round" }, map);
  const zg = svgEl("g", {}, map);
  for (const z of p.zones) {
    svgEl("circle", { cx: z.center[0], cy: -z.center[1], r: z.radius_m, fill: "none", stroke: "var(--zone)",
      "stroke-width": 0.12, "stroke-dasharray": "0.5 0.4", opacity: 0.8 }, zg);
    const t = svgEl("text", { x: z.center[0], y: -z.center[1] - z.radius_m - 0.4, fill: "var(--zone)", "font-size": 1.3,
      "font-weight": 700, "text-anchor": "middle", "paint-order": "stroke", stroke: "#000", "stroke-width": 0.3 }, zg);
    t.textContent = z.name.split(",")[0]; // "Yard A"; full name shows in the panel
  }
  // scale bar: 5 m
  const sb = svgEl("g", { transform: `translate(${x0 + 1.5},${-y0 - 1.5})` }, map);
  svgEl("rect", { x: 0, y: -0.25, width: 5, height: 0.5, fill: "#fff" }, sb);
  const st = svgEl("text", { x: 2.5, y: -0.8, fill: "#fff", "font-size": 1.1, "text-anchor": "middle" }, sb);
  st.textContent = "5 m";
}
function coneD(r, half) {
  const a = rad(half);
  return `M 0 0 L ${r * Math.sin(-a)} ${-r * Math.cos(a)} A ${r} ${r} 0 0 1 ${r * Math.sin(a)} ${-r * Math.cos(a)} Z`;
}
function svgPoint(evt) {
  const pt = map.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const p = pt.matrixTransform(map.getScreenCTM().inverse());
  return { x: p.x, y: -p.y };
}

function renderWalker() {
  const w = state.walker;
  layers.walker.setAttribute("transform", `translate(${w.x},${-w.y}) rotate(${w.heading})`);
  $("hdg").textContent = String(Math.round(norm360(w.heading))).padStart(3, "0");
  $("pos").textContent = `x ${w.x.toFixed(1)} m, y ${w.y.toFixed(1)} m`;
  const z = currentZone();
  $("zone").textContent = z ? z.name : "";
  for (const fn of state.listeners) fn();
}

// ---------- movement ----------
const SPEED = 1.4; // m/s, walking pace
map.addEventListener("click", (e) => {
  if (typeof rec !== "undefined" && rec.mr) return toast("Recording — stay put");
  const p = svgPoint(e);
  if (isInterior()) {   // indoors you move sweep to sweep: snap the click to the nearest sweep
    const s = state.plan.sweeps.reduce((b, s) => {
      const d = Math.hypot(s.position.x - p.x, s.position.y - p.y);
      return d < b.d ? { s, d } : b;
    }, { s: null, d: 1e9 });
    return s.d < 0.8 ? goToSweep(s.s.index) : toast("Click a highlighted sweep");
  }
  if (!walkable(p.x, p.y)) return toast("Not walkable there");
  state.target = p;
});

// Interior: walk to a NEIGHBOURING sweep (the showcase's own line-of-sight links).
function goToSweep(i, keepHeading = false) {
  if (typeof rec !== "undefined" && rec.mr) return toast("Recording — stay put");
  const cur = sweepAt(state.walker.sweep);
  if (i === state.walker.sweep || state.target) return;
  if (cur && !cur.neighbours.includes(i)) return toast("Not reachable in one step — pick a highlighted sweep");
  const s = sweepAt(i);
  state.target = { x: s.position.x, y: s.position.y, sweep: i, keepHeading };
}
// Best neighbour in a direction (map heading, degrees), within +-60 degrees.
function neighbourToward(headingDeg) {
  const cur = sweepAt(state.walker.sweep);
  if (!cur) return null;
  let best = null;
  for (const i of cur.neighbours) {
    const s = sweepAt(i), dx = s.position.x - cur.position.x, dy = s.position.y - cur.position.y;
    const off = Math.abs(((deg(Math.atan2(dx, dy)) - headingDeg + 540) % 360) - 180);
    const score = off + 8 * Math.hypot(dx, dy);  // prefer on-axis, then near
    if (off <= 60 && (!best || score < best.score)) best = { i, score };
  }
  return best && best.i;
}

document.addEventListener("keydown", (e) => {
  if (e.target.closest("select, input, textarea")) return;   // buttons keep arrow keys for walking
  if (typeof rec !== "undefined" && rec.mr) return;
  const w = state.walker;
  if (e.key === "ArrowLeft") w.heading = norm360(w.heading - 15);
  else if (e.key === "ArrowRight") w.heading = norm360(w.heading + 15);
  else if (isInterior() && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
    e.preventDefault();
    if (state.target) return;
    const back = e.key === "ArrowDown";
    const i = neighbourToward(back ? norm360(w.heading + 180) : w.heading);
    if (i == null) return toast(back ? "Nothing behind you to step back to" : "No sweep ahead — turn with ← →");
    return goToSweep(i, back);
  }
  else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    const s = e.key === "ArrowUp" ? 0.5 : -0.5;
    const nx = w.x + s * Math.sin(rad(w.heading)), ny = w.y + s * Math.cos(rad(w.heading));
    if (walkable(nx, ny)) { w.x = nx; w.y = ny; } else toast("Edge of walkable area");
  } else return;
  e.preventDefault();
  state.target = null;
  renderWalker();
});
let lastT = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - lastT) / 1000);
  lastT = now;
  const w = state.walker, t = state.target;
  if (t) {
    const dx = t.x - w.x, dy = t.y - w.y, d = Math.hypot(dx, dy);
    if (!t.keepHeading && d > 1e-6) w.heading = norm360(deg(Math.atan2(dx, dy)));
    const step = SPEED * dt;
    if (d <= step) {
      w.x = t.x; w.y = t.y; state.target = null;
      if (t.sweep != null) w.sweep = t.sweep;
    }
    else { w.x += (dx / d) * step; w.y += (dy / d) * step; }
    renderWalker();
  }
  requestAnimationFrame(tick);
}

// ---------- audio core: ONE AudioContext, created on the first user gesture ----------
const FADE = 0.008; // 8 ms fades on every start/stop
const audio = {
  _ctx: null,
  ctx() {
    if (!this._ctx) {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this._ctx.createGain();
      this.master.connect(this._ctx.destination);
    }
    if (this._ctx.state === "suspended") this._ctx.resume();
    return this._ctx;
  },
  // short sine tone with fades; dest defaults to master
  tone(freq, dur, gain = 0.2, when = 0, dest = null) {
    const c = this.ctx(), t0 = c.currentTime + when;
    const o = c.createOscillator(), g = c.createGain();
    o.frequency.value = freq;
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(gain, t0 + FADE);
    g.gain.setValueAtTime(gain, t0 + dur - FADE);
    g.gain.linearRampToValueAtTime(0, t0 + dur);
    o.connect(g).connect(dest || this.master);
    o.start(t0); o.stop(t0 + dur + 0.02);
  },
};

// ---------- memories ----------
state.user = "user-a";
state.memories = [];
const memById = (id) => state.memories.find((m) => m.id === id);

async function loadMemories() {
  const q = new URLSearchParams({ viewer: state.user, building_id: state.plan.building.id });
  state.memories = await (await fetch(`/api/memories?${q}`)).json();
  renderMemories();
}

function renderMemories() {
  layers.anchors.replaceChildren();
  state.memories.forEach((m, i) => {
    const g = svgEl("g", { transform: `translate(${m.position.x},${-m.position.y})` }, layers.anchors);
    const col = m.visibility === "community" ? "var(--community)" : "var(--mem)";
    const k = state.k || 1;   // the 4 m hearing ring stays true to scale; the pin scales with the map
    svgEl("circle", { r: 4, fill: "none", stroke: col, "stroke-width": 0.08 * k, "stroke-dasharray": `${0.3 * k} ${0.3 * k}`, opacity: 0.7 }, g);
    svgEl("circle", { r: 0.75 * k, fill: col, stroke: "#000", "stroke-width": 0.12 * k }, g);
    const t = svgEl("text", { y: 0.42 * k, fill: "#000", "font-size": 1.1 * k, "font-weight": 700, "text-anchor": "middle" }, g);
    t.textContent = i + 1;
  });
  $("memCount").textContent = state.memories.length;
  const list = $("memList");
  list.replaceChildren();
  state.memories.forEach((m, i) => {
    const d = document.createElement("div");
    d.className = "mem";
    d.id = `mem-${m.id}`;
    d.innerHTML = `<div class="dist"></div><div class="meta who"></div><div class="desc"></div><div class="row actions"></div>`;
    d.querySelector(".who").textContent = `${whoHuman(m)} · ${whenHuman(m.created_at)}`;
    list.appendChild(d);
  });
  for (const fn of memHooks) fn();
  renderWalker();
}
const memHooks = []; // later steps decorate each memory card

// ---------- record ----------
const REC_SECONDS = 10;
const rec = { mr: null, stream: null, timer: null, anchor: null };

$("rec").addEventListener("click", startRecording);
$("recStop").addEventListener("click", () => rec.mr && rec.mr.state === "recording" && rec.mr.stop());

async function startRecording() {
  if (rec.mr) return;
  audio.ctx(); // first gesture creates the AudioContext
  const w = state.walker;
  // Anchor = where the walker stands at the moment of the press. z: approx. speaking height above ground (m).
  rec.anchor = { x: +w.x.toFixed(2), y: +w.y.toFixed(2), z: 1.5 };
  rec.facing = { heading_deg: +w.heading.toFixed(1), ...(w.sweep != null ? { sweep: w.sweep } : {}) };
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true }, // AEC keeps our own cue tones out of the take
    });
  } catch (e) {
    rec.anchor = null;
    return toast(`Microphone not available: ${e.name}`, 3000);
  }
  const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  const mr = (rec.mr = new MediaRecorder(rec.stream, mime ? { mimeType: mime } : {}));
  const chunks = [];
  mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  mr.onstop = () => finishRecording(new Blob(chunks, { type: mr.mimeType || "audio/webm" }));

  // start cue: two rising tones, then record
  audio.tone(660, 0.12, 0.25); audio.tone(880, 0.12, 0.25, 0.15);
  setTimeout(() => {
    if (rec.mr !== mr) return;
    mr.start();
    document.body.classList.add("recording");
    $("recStop").hidden = false;
    state.target = null;
    showRecMarker(true);
    let left = REC_SECONDS;
    const show = () => ($("recState").innerHTML = `<span class="pulse">●</span> REC ${left}s`);
    show();
    rec.timer = setInterval(() => {
      left -= 1;
      if (left <= 0) { if (mr.state === "recording") mr.stop(); return; }
      audio.tone(1000, 0.04, 0.05); // quiet per-second blip: recording is in progress
      show();
    }, 1000);
  }, 350);
}

async function finishRecording(blob) {
  clearInterval(rec.timer);
  rec.stream.getTracks().forEach((t) => t.stop()); // mic closed
  document.body.classList.remove("recording");
  $("recStop").hidden = true;
  showRecMarker(false);
  audio.tone(880, 0.12, 0.25); audio.tone(660, 0.12, 0.25, 0.15); // end cue: falling
  $("recState").textContent = "Saving…";
  const a = rec.anchor;
  rec.facingSaved = rec.facing;
  rec.mr = null; rec.stream = null; rec.anchor = null; rec.facing = null;
  try {
    const q = new URLSearchParams({ ...a, ...rec.facingSaved, building_id: state.plan.building.id, author_id: state.user });
    const r = await fetch(`/api/memories?${q}`, { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
    const saved = await r.json();
    if (!r.ok) throw new Error(saved.error);
    $("recState").textContent = "It's here now.";
    await loadMemories();
    document.dispatchEvent(new CustomEvent("memory-saved", { detail: saved }));
  } catch (e) {
    $("recState").textContent = "";
    toast(`Save failed: ${e.message}`, 4000);
  }
  setTimeout(() => ($("recState").textContent = ""), 2500);
}

function showRecMarker(on) {
  if (showRecMarker.el) { showRecMarker.el.remove(); showRecMarker.el = null; }
  if (!on) return;
  const g = svgEl("g", { transform: `translate(${rec.anchor.x},${-rec.anchor.y})`, class: "pulse" }, map);
  svgEl("circle", { r: 1.6, fill: "none", stroke: "var(--rec)", "stroke-width": 0.3 }, g);
  svgEl("circle", { r: 0.6, fill: "var(--rec)" }, g);
  showRecMarker.el = g;
}

// ---------- proximity playback with direction ----------
const NEAR_M = 4, REARM_M = 6;
state.hatWarning = false;
// The ONLY link to the hardware half: while the hat warns, memory audio stays silent.
window.setHatWarning = (on) => {
  state.hatWarning = !!on;
  $("hat").checked = state.hatWarning;
  if (state.hatWarning) stopVoice();
};
$("hat").addEventListener("change", (e) => window.setHatWarning(e.target.checked));

const play = { near: null, panner: null, src: null, gain: null, playingId: null, armed: new Set() };

// Anchor relative to the walker, in the walker's own frame: forward / right, metres.
function relative(m) {
  const w = state.walker, h = rad(w.heading);
  const dx = m.position.x - w.x, dy = m.position.y - w.y;
  return { fwd: dx * Math.sin(h) + dy * Math.cos(h), right: dx * Math.cos(h) - dy * Math.sin(h), dist: Math.hypot(dx, dy) };
}
// Where something is, said the way a person would say it.
function describeDir({ fwd, right, dist }) {
  if (dist < 0.75) return "right where you're standing";
  const a = deg(Math.atan2(right, fwd)), side = a > 0 ? "right" : "left", abs = Math.abs(a);
  const where = abs < 25 ? "ahead of you" : abs < 70 ? `ahead, on your ${side}` : abs < 115 ? `on your ${side}`
    : abs < 155 ? `behind you, on your ${side}` : "behind you";
  const how = dist < 1.8 ? "just " : dist < 4 ? "a few steps " : dist < 8 ? "across the room, " : "a little way off, ";
  return how + where;
}
function whenHuman(iso) {
  const d = new Date(iso), now = new Date(), day = 864e5;
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / day);
  if (days <= 0) return "earlier today";
  if (days === 1) return "yesterday";
  if (days < 7) return `on ${d.toLocaleDateString([], { weekday: "long" })}`;
  return `on ${d.toLocaleDateString([], { month: "long", day: "numeric" })}`;
}
function whoHuman(m) {
  if (m.author_id === state.user) return m.visibility === "community" ? "you left this here, and shared it" : "you left this here";
  return "someone left this here for everyone";
}
function makePanner() {
  const c = audio.ctx(), p = c.createPanner();
  p.panningModel = "HRTF";
  p.distanceModel = "inverse"; p.refDistance = 1; p.rolloffFactor = 0.6;
  p.connect(audio.master);
  return p;
}
// Web Audio listener sits at the origin facing -Z, +X right, +Y up.
function placePanner(p, m) {
  const r = relative(m), t = audio.ctx().currentTime;
  if (p.positionX) {
    p.positionX.setTargetAtTime(r.right, t, 0.03);
    p.positionY.setTargetAtTime(0, t, 0.03);
    p.positionZ.setTargetAtTime(-r.fwd, t, 0.03);
  } else p.setPosition(r.right, 0, -r.fwd);
}
function chime(m) {
  if (state.hatWarning) return;
  const p = makePanner();
  placePanner(p, m);
  const c = audio.ctx(), t0 = c.currentTime + 0.02;
  for (const [f, delay] of [[1318.5, 0], [1760, 0.14]]) {  // soft two-note bell, exponential decay
    const o = c.createOscillator(), g = c.createGain();
    o.type = "sine"; o.frequency.value = f;
    g.gain.setValueAtTime(0, t0 + delay);
    g.gain.linearRampToValueAtTime(0.22, t0 + delay + FADE);
    g.gain.exponentialRampToValueAtTime(0.001, t0 + delay + 0.9);
    o.connect(g).connect(p);
    o.start(t0 + delay); o.stop(t0 + delay + 0.95);
  }
  setTimeout(() => p.disconnect(), 1400);
}

async function playVoice(m) {
  if (state.hatWarning) return toast("Hat is warning — memory muted");
  stopVoice();
  const c = audio.ctx();
  const res = await fetch(`${m.audio}?viewer=${encodeURIComponent(state.user)}`);
  if (!res.ok) return toast("Could not load this memory");
  const buf = await c.decodeAudioData(await res.arrayBuffer());
  if (state.hatWarning) return;
  const p = (play.panner = makePanner());
  placePanner(p, m);
  const g = (play.gain = c.createGain()), src = (play.src = c.createBufferSource());
  src.buffer = buf;
  const t0 = c.currentTime;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(1, t0 + FADE);
  src.connect(g).connect(p);
  src.onended = () => { if (play.src === src) { play.src = null; play.playingId = null; updateNearUI(); } };
  src.start(t0);
  play.playingId = m.id;
  updateNearUI();
}
function stopVoice() {
  if (!play.src) return;
  const c = audio.ctx(), t = c.currentTime, src = play.src, g = play.gain;
  g.gain.cancelScheduledValues(t);
  g.gain.setValueAtTime(g.gain.value, t);
  g.gain.linearRampToValueAtTime(0, t + FADE);
  src.stop(t + FADE + 0.005);
  play.src = null; play.playingId = null;
  updateNearUI();
}

function updateNearUI() {
  const m = play.near && memById(play.near);
  $("nearby").hidden = !m;
  if (!m) return;
  const where = describeDir(relative(m));
  $("nearWhere").textContent = `A memory is here — ${where}.`;
  $("listen").hidden = play.playingId === m.id;
  $("stopVoice").hidden = play.playingId !== m.id;
}

function proximity() {
  if (!state.memories.length && !play.near) return;
  // keep the playing voice's panner on its anchor as the walker moves/turns
  if (play.playingId && play.panner) placePanner(play.panner, memById(play.playingId));
  for (const m of state.memories) {
    const r = relative(m);
    const card = document.querySelector(`#mem-${m.id} .dist`);
    if (card) card.textContent = describeDir(r);
    document.getElementById(`mem-${m.id}`)?.classList.toggle("near", r.dist <= NEAR_M);
    if (r.dist > REARM_M) play.armed.delete(m.id);
  }
  if (play.near && relative(memById(play.near) || { position: { x: 1e9, y: 1e9 } }).dist > REARM_M) {
    if (play.playingId !== play.near) play.near = null;
  }
  if (!play.near && !rec.mr) {
    const cand = state.memories
      .map((m) => ({ m, r: relative(m) }))
      .filter(({ m, r }) => r.dist <= NEAR_M && !play.armed.has(m.id))
      .sort((a, b) => a.r.dist - b.r.dist)[0];
    if (cand) {
      play.near = cand.m.id;
      play.armed.add(cand.m.id);           // one chime per approach; re-arms beyond 6 m
      if (audio._ctx) chime(cand.m);       // AudioContext only exists after a user gesture
    }
  }
  updateNearUI();
}
onWalkerChange(proximity);
$("listen").addEventListener("click", () => play.near && playVoice(memById(play.near)));
$("stopVoice").addEventListener("click", stopVoice);
$("dismiss").addEventListener("click", () => { if (play.playingId === play.near) stopVoice(); play.near = null; updateNearUI(); });
// first gesture anywhere creates the AudioContext so later chimes can sound
document.addEventListener("pointerdown", () => audio.ctx(), { once: true });

// ---------- AI description of a place ----------
function speak(text) {
  if (state.hatWarning) return toast("Hat is warning — memory muted");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.95;
  speechSynthesis.speak(u);
}
const _setHat = window.setHatWarning;
window.setHatWarning = (on) => { _setHat(on); if (on) speechSynthesis.cancel(); };

memHooks.push(() => {
  for (const m of state.memories) {
    const card = document.getElementById(`mem-${m.id}`);
    const desc = card.querySelector(".desc"), actions = card.querySelector(".actions");
    const btn = (label, fn) => { const b = document.createElement("button"); b.textContent = label; b.onclick = fn; actions.appendChild(b); return b; };
    if (m.description) {
      const v = encodeURIComponent(state.user);
      // exactly what the model was given, in order, labelled
      const imgs = m.description.images || [{ kind: "aerial", label: "Vexcel aerial" }, { kind: "splat", label: "Drone scan, top-down" }];
      desc.innerHTML = `<p style="margin:8px 0;font-size:17px"></p><div class="row thumbs"></div>
        <div class="meta">AI (${m.description.model}) · images sent, in order</div>`;
      desc.querySelector("p").textContent = m.description.text;
      for (const im of imgs) {
        const fig = document.createElement("figure");
        fig.style.cssText = "margin:0;width:120px";
        fig.innerHTML = `<a target="_blank"><img width="120" height="${im.kind === "firstperson" ? 75 : 120}"
          style="border-radius:6px;object-fit:cover;display:block"></a><figcaption class="meta" style="font-size:12px;line-height:1.2"></figcaption>`;
        const src = `/api/memories/${m.id}/image/${im.kind}?viewer=${v}`;
        fig.querySelector("a").href = src;
        fig.querySelector("img").src = src;
        fig.querySelector("img").alt = im.label;
        fig.querySelector("figcaption").textContent = im.label;
        desc.querySelector(".thumbs").appendChild(fig);
      }
      btn("🔊 Speak", () => speak(m.description.text));
    }
    const d = btn(m.description ? "↻ Describe again" : "✦ Describe place", async () => {
      d.disabled = true; d.textContent = "Describing…";
      try {
        const r = await fetch(`/api/memories/${m.id}/describe`, { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ viewer: state.user }) });
        const body = await r.json();
        if (!r.ok) throw new Error(body.error);
        Object.assign(m, body);
        renderMemories();
      } catch (e) {
        d.disabled = false; d.textContent = "✦ Describe place";
        toast(`Describe failed: ${e.message}`, 6000);
      }
    });
  }
});

// ---------- the social case: who is listening, and what they may hear ----------
$("user").addEventListener("change", async (e) => {
  stopVoice(); speechSynthesis.cancel();
  state.user = e.target.value;
  play.near = null; play.armed.clear();   // a new listener hears memories fresh
  await loadMemories();
  toast(`Listening as ${e.target.selectedOptions[0].text}`);
});
memHooks.push(() => {
  for (const m of state.memories) {
    if (m.author_id !== state.user) continue;   // only the author can change who hears it
    const actions = document.querySelector(`#mem-${m.id} .actions`);
    const b = document.createElement("button");
    const next = m.visibility === "private" ? "community" : "private";
    b.textContent = next === "community" ? "Share with community" : "Make private";
    b.onclick = async () => {
      b.disabled = true;
      const r = await fetch(`/api/memories/${m.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ viewer: state.user, visibility: next }) });
      if (!r.ok) { b.disabled = false; return toast((await r.json()).error); }
      Object.assign(m, await r.json());
      renderMemories();
    };
    actions.prepend(b);
  }
});

// compass heading once the map is georeferenced against Vexcel
onWalkerChange(() => {
  const g = state.plan.georef;
  if (!g) return;
  const c = norm360(state.walker.heading - g.rotation_deg);
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  $("compass").textContent = `${names[Math.round(c / 45) % 8]} (${String(Math.round(c)).padStart(3, "0")}°)`;
});

// ---------- first-person view (interior) ----------
const COMPASS8 = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];
let viewKey = null;
onWalkerChange(() => {
  updateSweeps();
  if (!isInterior() || state.target) return;           // update once the walker has arrived
  const w = state.walker, k = Math.round(norm360(-w.heading) / 90) % 4;  // 4 pre-rendered views per sweep
  const key = `${state.plan.building.id}:${w.sweep}:${k}`;
  if (key === viewKey) return;
  viewKey = key;
  const img = $("viewImg"), s = sweepAt(w.sweep);
  const snapped = norm360(-k * 90);
  const g = state.plan.georef;
  const facing = g ? COMPASS8[Math.round(norm360(snapped - g.rotation_deg) / 45) % 8] : `${snapped}°`;
  img.classList.add("loading");
  img.onload = () => img.classList.remove("loading");
  img.onerror = () => { img.classList.remove("loading"); toast("View not available"); };
  img.src = `/api/view?building=${state.plan.building.id}&sweep=${w.sweep}&heading=${snapped}`;
  $("viewCap").textContent = `${s ? s.room : ""} · facing ${facing}`;
});

// ---------- buildings ----------
async function loadBuilding(bid) {
  state.ready = false;
  stopVoice(); speechSynthesis.cancel();
  play.near = null; play.armed.clear(); state.target = null; viewKey = null;
  state.plan = await (await fetch(`/api/floorplan?building=${encodeURIComponent(bid)}`)).json();
  $("bname").textContent = state.plan.building.name;
  $("baddr").textContent = state.plan.building.address || (isInterior() ? "Interior scan (Matterport)" : "");
  $("building").value = bid;
  $("view").hidden = !isInterior();
  drawMap();
  if (isInterior()) {
    const s = sweepAt(state.plan.start_sweep ?? state.plan.sweeps[0].index);
    Object.assign(state.walker, { x: s.position.x, y: s.position.y, heading: 0, sweep: s.index });
  } else {
    const z = state.plan.zones[0];   // start at the centre of the first zone (inside the walkable area)
    Object.assign(state.walker, { x: z.center[0], y: z.center[1], heading: 0, sweep: null });
  }
  renderWalker();
  await loadMemories();
  history.replaceState(null, "", `?building=${encodeURIComponent(bid)}`);
  state.ready = true;   // plan + memories loaded (tests wait on this)
}
$("building").addEventListener("change", (e) => loadBuilding(e.target.value));

// ---------- boot ----------
async function boot() {
  const buildings = await (await fetch("/api/buildings")).json();
  for (const b of buildings) {
    const o = document.createElement("option");
    o.value = b.id;
    o.textContent = `${b.kind === "interior" ? "Indoors" : "Yard"} — ${b.name}`;
    $("building").appendChild(o);
  }
  const want = new URLSearchParams(location.search).get("building");
  const pick = buildings.find((b) => b.id === want) || buildings.find((b) => b.default) || buildings[0];
  await loadBuilding(pick.id);
  requestAnimationFrame(tick);
}
boot();
