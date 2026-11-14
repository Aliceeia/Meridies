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
const walkable = (x, y) => insidePoly(x, y, state.plan.walkable);
function currentZone() {
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
  const p = state.plan;
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

  layers.anchors = svgEl("g", {}, map);
  layers.walker = svgEl("g", {}, map);
  // heading cone (60 deg) + body + nose
  svgEl("path", { d: coneD(5, 30), fill: "rgba(255,255,255,.22)", stroke: "rgba(255,255,255,.6)", "stroke-width": 0.08 }, layers.walker);
  svgEl("circle", { r: 0.8, fill: "var(--walker)", stroke: "#000", "stroke-width": 0.15 }, layers.walker);
  svgEl("path", { d: "M 0 -2.1 L 0.7 -0.65 L -0.7 -0.65 Z", fill: "var(--walker)", stroke: "#000", "stroke-width": 0.1 }, layers.walker);
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
  if (!walkable(p.x, p.y)) return toast("Not walkable there");
  state.target = p;
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("button, select, input, textarea")) return;
  if (typeof rec !== "undefined" && rec.mr) return;
  const w = state.walker;
  if (e.key === "ArrowLeft") w.heading = norm360(w.heading - 15);
  else if (e.key === "ArrowRight") w.heading = norm360(w.heading + 15);
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
    w.heading = norm360(deg(Math.atan2(dx, dy)));
    const step = SPEED * dt;
    if (d <= step) { w.x = t.x; w.y = t.y; state.target = null; }
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
    svgEl("circle", { r: 4, fill: "none", stroke: col, "stroke-width": 0.08, "stroke-dasharray": "0.3 0.3", opacity: 0.7 }, g);
    svgEl("circle", { r: 0.75, fill: col, stroke: "#000", "stroke-width": 0.12 }, g);
    const t = svgEl("text", { y: 0.42, fill: "#000", "font-size": 1.1, "font-weight": 700, "text-anchor": "middle" }, g);
    t.textContent = i + 1;
  });
  $("memCount").textContent = state.memories.length;
  const list = $("memList");
  list.replaceChildren();
  state.memories.forEach((m, i) => {
    const d = document.createElement("div");
    d.className = "mem";
    d.id = `mem-${m.id}`;
    const when = new Date(m.created_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
    d.innerHTML = `<div><b>#${i + 1}</b> <span class="tag ${m.visibility}">${m.visibility}</span>
      <span class="meta">by ${m.author_id === state.user ? "you" : m.author_id} · ${when}</span></div>
      <div class="meta dist"></div><div class="desc"></div><div class="row actions"></div>`;
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
  rec.mr = null; rec.stream = null; rec.anchor = null;
  try {
    const q = new URLSearchParams({ ...a, building_id: state.plan.building.id, author_id: state.user });
    const r = await fetch(`/api/memories?${q}`, { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
    if (!r.ok) throw new Error((await r.json()).error);
    $("recState").textContent = "Saved ✓";
    await loadMemories();
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
function describeDir({ fwd, right, dist }) {
  const a = norm360(deg(Math.atan2(right, fwd)));
  const names = ["ahead", "ahead right", "right", "behind right", "behind", "behind left", "left", "ahead left"];
  return `${names[Math.round(a / 45) % 8]}, ${dist.toFixed(1)} m`;
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
  const idx = state.memories.indexOf(m) + 1;
  $("nearWhere").textContent = `#${idx} — ${describeDir(relative(m))}`;
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
      desc.innerHTML = `<p style="margin:8px 0;font-size:17px"></p>
        <div class="row"><img alt="Aerial view" src="/api/memories/${m.id}/image/aerial?viewer=${v}" width="120" height="120" style="border-radius:6px">
        <img alt="Drone scan view" src="/api/memories/${m.id}/image/splat?viewer=${v}" width="120" height="120" style="border-radius:6px"></div>
        <div class="meta">AI (${m.description.model}) from aerial + drone imagery</div>`;
      desc.querySelector("p").textContent = m.description.text;
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

// compass heading once the map is georeferenced against Vexcel
onWalkerChange(() => {
  const g = state.plan.georef;
  if (!g) return;
  const c = norm360(state.walker.heading - g.rotation_deg);
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  $("compass").textContent = `${names[Math.round(c / 45) % 8]} (${String(Math.round(c)).padStart(3, "0")}°)`;
});

// ---------- boot ----------
async function boot() {
  state.plan = await (await fetch("/api/floorplan")).json();
  $("bname").textContent = state.plan.building.name;
  $("baddr").textContent = state.plan.building.address;
  drawMap();
  // start at the centre of the first zone (inside the walkable area)
  const z = state.plan.zones[0];
  Object.assign(state.walker, { x: z.center[0], y: z.center[1], heading: 0 });
  renderWalker();
  await loadMemories();
  requestAnimationFrame(tick);
}
boot();
