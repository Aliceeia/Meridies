"use strict";
// /live: the realtime half. Works with no network:
//   - the page itself is cached by a service worker (sw.js)
//   - a recording goes to IndexedDB FIRST, then uploads whenever the server answers; a failed upload is
//     retried quietly and never blocks or interrupts anything
//   - where you are is chosen by you (building + spot). Nothing tracks or stores where you walk.
// The obstacle tones themselves come from audio/engine.py on the hat; this page shows the same stream.

const $ = (id) => document.getElementById(id);
const REC_S = 10;
const L = {
  user: (() => { try { return localStorage.getItem("meridies.user") || "user-a"; } catch (_) { return "user-a"; } })(),
  warning: false, demoWarning: false, hatWarning: false,
  rec: null,            // { mr, stream, chunks, timer, t, frame, params, interrupted }
  plans: {},            // building id -> floorplan (cached in localStorage for offline loads)
};

// ---------- small helpers ----------
const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} },
};
let actx = null;
function tone(f, dur, g = 0.15, when = 0) {
  if (L.warning) return;                                    // obstacle warnings outrank every page sound
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === "suspended") actx.resume();
    const t0 = actx.currentTime + when, o = actx.createOscillator(), a = actx.createGain();
    o.frequency.value = f;
    a.gain.setValueAtTime(0, t0); a.gain.linearRampToValueAtTime(g, t0 + 0.008);
    a.gain.setValueAtTime(g, t0 + dur - 0.008); a.gain.linearRampToValueAtTime(0, t0 + dur);
    o.connect(a).connect(actx.destination); o.start(t0); o.stop(t0 + dur + 0.02);
  } catch (_) {}
}
function say(text) {
  if (L.warning) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US"; u.rate = 0.98;
    speechSynthesis.speak(u);
  } catch (_) {}
}
const isoNow = () => new Date().toISOString();

// ---------- offline queue (IndexedDB) ----------
const DB = {
  db: null,
  open() {
    if (this.db) return Promise.resolve(this.db);
    return new Promise((res, rej) => {
      const r = indexedDB.open("meridies-live", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("queue", { keyPath: "key", autoIncrement: true });
      r.onsuccess = () => res((this.db = r.result));
      r.onerror = () => rej(r.error);
    });
  },
  async tx(mode, fn) {
    const db = await this.open();
    return new Promise((res, rej) => {
      const t = db.transaction("queue", mode), s = t.objectStore("queue");
      const r = fn(s);
      t.oncomplete = () => res(r && r.result);
      t.onerror = () => rej(t.error);
    });
  },
  add(item) { return this.tx("readwrite", (s) => s.add(item)); },
  all() { return this.tx("readonly", (s) => s.getAll()); },
  del(key) { return this.tx("readwrite", (s) => s.delete(key)); },
};

let flushing = false, retryT = null;
async function flush() {
  if (flushing) return;
  flushing = true;
  clearTimeout(retryT);
  try {
    for (const item of await DB.all()) {
      const r = await fetch(`/api/memories?${new URLSearchParams(item.params)}`,
                            { method: "POST", headers: { "Content-Type": item.type }, body: item.audio });
      if (r.status >= 500 || r.status === 0) throw new Error(`server ${r.status}`);
      if (r.ok) {
        const m = await r.json();
        if (item.frame) {
          await fetch(`/api/memories/${m.id}/frame?viewer=${encodeURIComponent(item.params.author_id)}`,
                      { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: item.frame }).catch(() => {});
        }
        tone(1320, 0.05, 0.06);                               // a soft tick: it reached the gallery
      } else {
        console.warn("upload refused, kept on the device:", r.status, await r.text());
        continue;                                             // a 4xx won't fix itself; keep it, try the rest
      }
      await DB.del(item.key);
    }
  } catch (_) {
    retryT = setTimeout(flush, 15000);                        // offline or server down: try again later
  } finally {
    flushing = false;
    showQueue();
    setNet();
  }
}
window.addEventListener("online", flush);
setInterval(() => { if (!flushing) flush(); }, 30000);

async function showQueue() {
  let items = [];
  try { items = await DB.all(); } catch (_) {}
  $("chipQueue").textContent = items.length ? `${items.length} waiting to upload` : "Nothing waiting to upload";
  $("chipQueue").className = `chip${items.length ? " busy" : ""}`;
  $("queueBox").hidden = !items.length;
  $("queue").replaceChildren(...items.map((i) => {
    const li = document.createElement("li");
    li.textContent = `${new Date(i.params.recorded_at).toLocaleString("en-US")} · ${i.place}${i.frame ? " · with photo" : ""}`;
    return li;
  }));
}
async function setNet() {
  let ok = false;
  try { ok = (await fetch("/api/buildings", { cache: "no-store" })).ok; } catch (_) {}
  $("chipNet").textContent = ok ? "Gallery reachable" : "Offline: recordings stay on this device";
  $("chipNet").className = `chip ${ok ? "on" : "off"}`;
  return ok;
}

// ---------- where am I: chosen, never tracked ----------
function spotsFor(plan) {
  if (plan.kind === "interior") {
    const multi = (plan.floors || []).length > 1, out = [], seen = {};
    for (const r of plan.rooms) {
      const cx = r.polygon.reduce((s, p) => s + p[0], 0) / r.polygon.length, cy = r.polygon.reduce((s, p) => s + p[1], 0) / r.polygon.length;
      const sw = plan.sweeps.filter((s) => (s.floor ?? 0) === (r.floor ?? 0));
      if (!sw.length) continue;
      const s = sw.reduce((b, s) => Math.hypot(s.position.x - cx, s.position.y - cy) < Math.hypot(b.position.x - cx, b.position.y - cy) ? s : b);
      if (out.some((o) => o.sweep === s.index)) continue;
      seen[r.label] = (seen[r.label] || 0) + 1;
      const fl = multi ? ` (${(plan.floors[r.floor ?? 0] || {}).name || "floor"})` : "";
      out.push({ label: `${r.label}${seen[r.label] > 1 ? ` ${seen[r.label]}` : ""}${fl}`, sweep: s.index, x: s.position.x, y: s.position.y });
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }
  return plan.zones.map((z) => ({ label: z.name, x: z.center[0], y: z.center[1] }));
}
async function loadPlan(bid) {
  if (L.plans[bid]) return L.plans[bid];
  try {
    const r = await fetch(`/api/floorplan?building=${encodeURIComponent(bid)}`);
    if (r.ok) {
      const p = await r.json();
      const slim = { kind: p.kind, building: p.building, floors: p.floors, zones: p.zones,
                     rooms: (p.rooms || []).map(({ label, floor, polygon }) => ({ label, floor, polygon })),
                     sweeps: (p.sweeps || []).map(({ index, floor, position }) => ({ index, floor, position })) };
      store.set(`meridies.plan.${bid}`, slim);
    }
  } catch (_) {}
  return (L.plans[bid] = store.get(`meridies.plan.${bid}`));
}
async function initPlaces() {
  let bs = null;
  try { const r = await fetch("/api/buildings"); if (r.ok) { bs = await r.json(); store.set("meridies.buildings", bs); } } catch (_) {}
  bs = bs || store.get("meridies.buildings", []);
  $("building").replaceChildren(...bs.map((b) => Object.assign(document.createElement("option"), { value: b.id, textContent: b.name })));
  const here = store.get("meridies.live.here", {});
  $("building").value = bs.some((b) => b.id === here.building) ? here.building : (bs.find((b) => b.default) || bs[0] || {}).id || "";
  await fillSpots(here.spot);
}
async function fillSpots(want) {
  const plan = await loadPlan($("building").value);
  const spots = plan ? spotsFor(plan) : [];
  $("spot").replaceChildren(...spots.map((s, i) => Object.assign(document.createElement("option"), { value: i, textContent: s.label })));
  const i = spots.findIndex((s) => s.label === want);
  $("spot").value = i >= 0 ? i : 0;
  $("spot").spots = spots;
  remember();
}
function remember() {
  const s = currentSpot();
  store.set("meridies.live.here", { building: $("building").value, spot: s && s.label });
}
const currentSpot = () => ($("spot").spots || [])[+$("spot").value];
$("building").onchange = () => fillSpots();
$("spot").onchange = remember;

// ---------- camera ----------
let camStream = null;
async function setCamera(on) {
  if (on && !camStream) {
    try { camStream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 960 } }); }
    catch (e) { $("camOn").checked = false; $("ctxLine").textContent = `Camera not available: ${e.name}`; return false; }
    $("cam").srcObject = camStream;
  }
  if (!on && camStream) { camStream.getTracks().forEach((t) => t.stop()); camStream = null; $("cam").srcObject = null; }
  $("cam").hidden = !on;
  return true;
}
$("camOn").onchange = (e) => setCamera(e.target.checked);
async function grabFrame() {
  if (!camStream) return null;
  const v = $("cam");
  if (!v.videoWidth) await new Promise((r) => setTimeout(r, 300));
  if (!v.videoWidth) return null;
  const c = document.createElement("canvas");
  const k = Math.min(1, 1280 / v.videoWidth);
  c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
  c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
  return new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
}

// C, "what's around me": instant. Cached description + bearings from the resolver, no AI. The hat doesn't know
// which way you face on the map, so it gives distances, not left/right.
$("ctx").onclick = aroundMe;
async function aroundMe() {
  if (L.warning) return;
  const spot = currentSpot();
  if (!spot) return say("Choose where you are first.");
  try {
    const q = new URLSearchParams({ viewer: L.user, building: $("building").value, x: spot.x.toFixed(2), y: spot.y.toFixed(2) });
    const r = await (await fetch(`/api/around?${q}`)).json();
    const text = [r.bearings, r.description ? `Here: ${r.description}` : ""].filter(Boolean).join(" ");
    $("ctxLine").textContent = text;
    tone(659, 0.06); say(text);
  } catch (_) {
    const msg = `You're at ${spot.label}. The gallery isn't reachable, so that's all I know offline.`;
    $("ctxLine").textContent = msg;
    tone(233, 0.2); say(msg);
  }
}
// M, "tell me more": the ONE live AI call, only when asked. Takes a few seconds.
$("more").onclick = tellMore;
async function tellMore() {
  if (L.warning) return;
  const spot = currentSpot();
  if (!spot) return say("Choose where you are first.");
  $("ctxLine").textContent = "Looking closer…";
  say("Looking closer. This takes a few seconds.");
  const beat = setInterval(() => tone(740, 0.05, 0.04), 1400);
  try {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 25000);
    const r = await fetch("/api/tell-me-more", { method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
      body: JSON.stringify({ viewer: L.user, building: $("building").value, x: spot.x, y: spot.y, heading: null, sweep: spot.sweep ?? null }) });
    clearTimeout(to);
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    const text = `${body.bearings} ${body.text}`;
    $("ctxLine").textContent = text;
    tone(659, 0.08); say(text);
  } catch (e) {
    const offline = !navigator.onLine || e.name === "TypeError" || e.name === "AbortError";
    const msg = offline ? "No connection, so I can't look closer right now. C still works." : "I couldn't look closer right now.";
    $("ctxLine").textContent = msg;
    tone(233, 0.2); say(msg);
  } finally {
    clearInterval(beat);
  }
}

// ---------- 10 second recording: saved on the device first ----------
$("rec").onclick = () => (L.rec ? stopRec("user") : startRec());
async function startRec() {
  if (L.rec) return;
  if (L.warning) { $("recLine").textContent = "Obstacle warning: try again when it's clear."; return; }
  const spot = currentSpot();
  if (!spot) { $("recLine").textContent = "Choose where you are first."; return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch (e) { $("recLine").textContent = `Microphone not available: ${e.name}`; tone(196, 0.2); return; }
  const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  const R = (L.rec = { mr, stream, chunks: [], t: 0, interrupted: false, place: `${$("building").selectedOptions[0].text} · ${spot.label}`,
    params: { x: spot.x.toFixed(2), y: spot.y.toFixed(2), z: 1.5, building_id: $("building").value, author_id: L.user,
              heading_deg: 0, recorded_at: isoNow(), ...(spot.sweep != null ? { sweep: spot.sweep } : {}) } });
  R.framePromise = grabFrame();                                  // the hat camera frame becomes the cover
  mr.ondataavailable = (e) => e.data.size && R.chunks.push(e.data);
  mr.onstop = () => saveRec(R, new Blob(R.chunks, { type: mr.mimeType || "audio/webm" }));
  tone(660, 0.12, 0.2); tone(880, 0.12, 0.2, 0.15);
  setTimeout(() => {
    if (L.rec !== R) return;
    mr.start(1000);
    $("rec").classList.add("recording");
    $("rec").firstChild.textContent = `■ Recording… ${REC_S}`;
    R.timer = setInterval(() => {
      R.t += 1;
      if (R.t >= REC_S) return stopRec("time");
      $("rec").firstChild.textContent = `■ Recording… ${REC_S - R.t}`;
      tone(R.t >= REC_S - 3 ? 1200 : 1000, 0.04, 0.05);        // the last three seconds tick higher
    }, 1000);
  }, 350);
}
function stopRec(why) {
  const R = L.rec;
  if (!R || R.mr.state !== "recording") return;
  if (why === "warning") R.interrupted = true;
  clearInterval(R.timer);
  R.mr.stop();
}
async function saveRec(R, blob) {
  R.stream.getTracks().forEach((t) => t.stop());
  L.rec = null;
  $("rec").classList.remove("recording");
  $("rec").firstChild.textContent = `● Record ${REC_S} seconds`;
  if (!blob.size) { $("recLine").textContent = "Nothing was recorded."; return; }
  if (!R.interrupted) { tone(880, 0.12, 0.2); tone(660, 0.12, 0.2, 0.15); }
  const frame = await R.framePromise.catch(() => null);
  const params = { ...R.params, ...(R.interrupted ? { draft: "1" } : {}) };
  try {
    await DB.add({ params, type: blob.type, audio: blob, frame, place: R.place });   // on the device first, always
    $("recLine").textContent = R.interrupted ? "Stopped for the warning. Kept as a private draft." : "Saved. Private until you share it.";
    if (!R.interrupted) say("Saved.");
  } catch (e) {
    $("recLine").textContent = `Couldn't save on this device: ${e.message}`;
    return;
  }
  showQueue();
  flush();                                                        // fire and forget; walking never waits on this
}

// ---------- status board: the hat's obstacle stream ----------
const LEVEL_RANK = { urgent: 2, warn: 1 };
function side(az) {
  const a = Math.abs(az);
  if (a < 20) return "ahead";
  const s = az < 0 ? "left" : "right";
  return a < 60 ? `ahead-${s}` : a < 110 ? `on your ${s}` : `behind, ${s}`;
}
function drawRadar(ws) {
  const svg = $("radar"), NS = "http://www.w3.org/2000/svg";
  const mk = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); svg.append(e); return e; };
  svg.replaceChildren();
  for (const r of [35, 70, 100]) mk("circle", { r, fill: "none", stroke: "#3a322b", "stroke-width": 1 });
  mk("path", { d: "M 0 -14 L 9 8 L -9 8 Z", fill: "#f4ede4" });                      // you, facing up
  for (const w of ws) {
    const d = Math.min(1, (w.dist || 0) / 2) * 100, a = ((w.az || 0) * Math.PI) / 180;
    mk("circle", { cx: d * Math.sin(a), cy: -d * Math.cos(a), r: w.level === "urgent" ? 13 : 10,
                   fill: w.level === "urgent" ? "#ff6b5e" : "#f2c879", opacity: 0.9 });
  }
}
function setWarning() {
  const was = L.warning;
  L.warning = L.demoWarning || L.hatWarning;
  if (L.warning && !was) {
    try { speechSynthesis.cancel(); } catch (_) {}
    if (L.rec) stopRec("warning");                               // warnings stop a recording at once (kept as a draft)
  }
}
function showWarnings(ws, connected) {
  ws = (ws || []).filter((w) => LEVEL_RANK[w.level]);
  const top = ws.slice().sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || a.dist - b.dist)[0];
  L.hatWarning = !!top;
  const lvl = L.demoWarning && !top ? "warn" : top ? top.level : "clear";
  $("state").className = lvl;
  $("state").textContent = lvl === "clear" ? "Clear" : lvl === "urgent" ? "Stop" : "Careful";
  $("stateLine").textContent = top ? `${top.dist.toFixed(1)} m, ${side(top.az)}${ws.length > 1 ? ` (+${ws.length - 1} more)` : ""}`
    : L.demoWarning ? "Demo warning is on" : connected ? "Nothing close" : "";
  $("chipHat").textContent = connected ? "Hat connected" : "No hat data";
  $("chipHat").className = `chip ${connected ? "on" : "off"}`;
  drawRadar(ws);
  setWarning();
}
$("demoWarn").onchange = (e) => { L.demoWarning = e.target.checked; showWarnings([], false); };
function connectStream() {
  if (!window.EventSource) return;
  const es = new EventSource("/api/live/stream");
  es.onmessage = (ev) => { try { const d = JSON.parse(ev.data); showWarnings(d.warnings, d.connected); } catch (_) {} };
  es.onerror = () => showWarnings([], false);                   // EventSource reconnects by itself
}

// ---------- keys ----------
document.addEventListener("keydown", (e) => {
  if (e.target.closest("select, textarea, input:not([type=checkbox])") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "r" || e.key === "R") { e.preventDefault(); $("rec").click(); }
  else if (e.key === "c" || e.key === "C") { e.preventDefault(); aroundMe(); }
  else if (e.key === "m" || e.key === "M") { e.preventDefault(); tellMore(); }
});

// ---------- boot ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/live" }).catch(() => {});
window.live = { L, DB, flush, showWarnings, startRec, stopRec };
drawRadar([]);
initPlaces().then(() => { showQueue(); flush(); });
connectStream();
