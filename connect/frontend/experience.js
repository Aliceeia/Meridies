"use strict";
// The "leaving a note in a real place" layer, on top of app.js.
// It only CALLS the audio engine (playVoice/stopVoice) and the Gemini pipeline; it changes neither.
//
// Camera pose: the public showcase iframe sends no messages and is cross-origin, so we cannot read
// where its camera looks. Our own walker (sweep + heading) is the source of truth and DRIVES the
// iframe (ss = sweep+1, sr = pitch,yaw with yaw = -heading). If the user looks around inside the
// iframe on their own, we notice the focus change, dim the memory lights and offer a re-sync.

const SHOW = "https://my.matterport.com/show/?play=1&qs=1&help=0&hl=0&brand=0&title=0&dh=0&gt=0&hr=0&mt=0&f=0&fp=0&vr=0&search=0&pin=0&portal=0&tourcta=0&wh=0&lang=en";
const VFOV_DEG = 73;          // showcase camera, vertical (approx.; 1280x800 renders span ~100 deg horizontally)
const MAX_LIST = 1;           // only the nearest memory expands; the rest collapse to one line
const scenes = [$("sc0"), $("sc1")];
let front = 0, sceneKey = null, syncTimer = null, stale = false;

// ---------- the 3D view follows the walker ----------
function sceneUrl() {
  const w = state.walker, yaw = (-rad(w.heading)).toFixed(3);
  return `${SHOW}&m=${state.plan.building.id}&ss=${w.sweep + 1}&sr=0,${yaw}`;
}
function syncScene(force = false) {
  if (!isInterior() || state.target || state.walker.sweep == null) return;
  const key = `${state.plan.building.id}:${state.walker.sweep}:${Math.round(state.walker.heading)}`;
  if (!force && key === sceneKey) return;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => {          // coalesce quick turns into one load
    sceneKey = key;
    const back = scenes[1 - front], cur = scenes[front], url = sceneUrl();
    back.hidden = false;
    back.onload = () => setTimeout(() => {  // give WebGL a moment to draw before the crossfade
      if (back.src !== url) return;
      back.classList.remove("back"); cur.classList.add("back");
      front = 1 - front;
      setStale(false);
    }, 2200);
    back.src = url;
  }, force ? 0 : 450);
}
function setStale(on) {
  stale = on;
  $("markers").classList.toggle("stale", on);
  $("desync").hidden = !on;
}
// The user clicked into the showcase: it now moves on its own, so our lights may no longer line up.
window.addEventListener("blur", () => setTimeout(() => {
  if (isInterior() && scenes.includes(document.activeElement)) setStale(true);
}, 0));
$("resync").addEventListener("click", () => { window.focus(); syncScene(true); });

// on-screen walking pad = the arrow keys
const key = (k) => document.body.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
$("padUp").onclick = () => key("ArrowUp");
$("padDown").onclick = () => key("ArrowDown");
$("padLeft").onclick = () => key("ArrowLeft");
$("padRight").onclick = () => key("ArrowRight");

// ---------- memory lights floating in the view ----------
function placeMarkers() {
  const box = $("markers");
  if (!isInterior()) { box.replaceChildren(); return; }
  const W = box.clientWidth, H = box.clientHeight;
  const fy = (H / 2) / Math.tan(rad(VFOV_DEG / 2)), hfov = 2 * Math.atan((W / 2) / fy);
  const seen = new Set();
  let leftCue = 0, rightCue = 0;
  for (const m of state.memories) {
    const r = relative(m), a = Math.atan2(r.right, r.fwd), d = Math.max(r.dist, 0.4);
    let el = box.querySelector(`[data-id="${m.id}"]`);
    const underfoot = r.dist < 0.75;                                // standing on it: glow low in the middle
    if (!underfoot && (Math.abs(a) > hfov / 2 * 0.98 || r.fwd <= 0.05)) {   // not in view: hint at the edge instead
      if (el) el.remove();
      if (r.dist <= 6) a > 0 ? rightCue++ : leftCue++;
      continue;
    }
    if (!el) {
      el = document.createElement("div");
      el.className = "memdot";
      el.dataset.id = m.id;
      el.onclick = () => (relative(m).dist <= 4 ? playVoice(m) : toast("Walk a little closer to hear it"));
      box.appendChild(el);
    }
    seen.add(m.id);
    el.classList.toggle("shared", m.visibility === "community");
    const x = underfoot ? W / 2 : W / 2 + fy * Math.tan(a);
    const y = underfoot ? H * 0.8 : H / 2 + fy * (1.5 - 1.0) / Math.max(r.fwd, 0.3);   // a soft light at about chest height
    const size = underfoot ? 230 : Math.max(28, Math.min(220, 190 / d));
    el.style.left = `${x}px`;
    el.style.top = `${Math.min(H - size / 2, y)}px`;
    el.style.width = el.style.height = `${size}px`;
    el.style.opacity = Math.max(0.22, Math.min(1, 1.25 - d / 7)).toFixed(2);
    el.title = describeDir(r);
  }
  for (const el of [...box.querySelectorAll(".memdot")]) if (!seen.has(el.dataset.id)) el.remove();
  for (const el of [...box.querySelectorAll(".edgecue")]) el.remove();
  const cue = (n, side) => {
    if (!n) return;
    const c = document.createElement("div");
    c.className = "edgecue";
    c.style[side] = "16px";
    c.textContent = side === "left" ? `↶ ${n > 1 ? `${n} memories` : "a memory"} behind you` : `${n > 1 ? `${n} memories` : "a memory"} behind you ↷`;
    box.appendChild(c);
  };
  cue(leftCue, "left"); cue(rightCue, "right");
}
window.addEventListener("resize", placeMarkers);

// ---------- only the closest few, in plain words ----------
function tidyList() {
  const ranked = state.memories.map((m) => ({ m, d: relative(m).dist })).sort((a, b) => a.d - b.d);
  ranked.forEach(({ m }, i) => {
    const card = document.getElementById(`mem-${m.id}`);
    if (!card) return;
    card.style.order = i;
    card.classList.toggle("far-hidden", i >= MAX_LIST);
  });
  const rest = ranked.length - MAX_LIST;
  const close = ranked.slice(MAX_LIST).filter((r) => r.d <= 8).length;
  $("more").textContent = ranked.length === 0 ? "Nothing left here yet. Be the first."
    : rest <= 0 ? "" : close === rest ? `${rest} more nearby`
    : close ? `${close} more nearby, ${rest - close} elsewhere in the ${isInterior() ? "house" : "yard"}`
    : `${rest} more elsewhere in the ${isInterior() ? "house" : "yard"}`;
}

// ---------- where am I (local data only: instant, no API calls) ----------
function whereAmI() {
  const p = state.plan, w = state.walker;
  let place;
  if (isInterior()) {
    const s = sweepAt(w.sweep);
    place = !s ? "inside" : s.room === "Outside" ? "on the front porch" : `in the ${s.room.toLowerCase()}`;
  } else {
    const z = currentZone();
    place = z ? `in the ${z.name.split(",")[0].toLowerCase()}, ${z.name.split(",")[1] || ""}`.trim() : "in the yard";
  }
  // Body-relative only: what is ahead / left / right / behind, never compass points.
  const parts = [`You're ${place}.`];
  if (isInterior()) {
    const here = (sweepAt(w.sweep) || {}).room;
    const roomAt = (x, y) => (p.rooms.find((r) => insidePoly(x, y, r.polygon)) || {}).label;
    const doors = (p.openings || []).filter((o) => o.type === "doorway")
      .map((o) => {
        let to = null;   // the room on the far side of the doorway
        for (let k = 0; k < 8 && !to; k++) {
          const a = (k * Math.PI) / 4, lbl = roomAt(o.center[0] + 0.6 * Math.cos(a), o.center[1] + 0.6 * Math.sin(a));
          if (lbl && lbl !== here) to = lbl;
        }
        return { to, r: relative({ position: { x: o.center[0], y: o.center[1] } }) };
      })
      .filter((d) => d.r.dist <= 4).sort((a, b) => a.r.dist - b.r.dist).slice(0, 2);
    for (const d of doors) {
      parts.push(`There's a doorway${d.to ? ` to the ${d.to.toLowerCase()}` : ""} ${describeDir(d.r)}.`);
    }
  }
  const hz = (p.hazards || [])
    .map((h) => ({ h, r: relative({ position: { x: h.position[0], y: h.position[1] } }) }))
    .filter(({ r }) => r.dist <= 3.5).sort((a, b) => a.r.dist - b.r.dist)[0];
  if (hz) {
    const k = hz.h.kind.toLowerCase(), an = /^[aeiou]/.test(k) ? "an" : "a";
    parts.push(`Mind your head: there's ${an} ${k} at about ${hz.h.height_m.toFixed(1)} metres, ${describeDir(hz.r)}.`);
  }
  const near = state.memories.filter((m) => relative(m).dist <= 4).length;
  parts.push(near ? `${near === 1 ? "A memory is" : `${near} memories are`} close by.` : "No memories right here.");
  return parts.join(" ");
}
// "What's around me" is instant: the room and doorways from the plan (above), hazards with any note left at
// them, nearby memories and the cached description from the server's spatial resolver. No AI on this path.
async function aroundMe(kind = "around", prev = "") {
  const w = state.walker, s = sweepAt(w.sweep);
  const q = new URLSearchParams({ viewer: state.user, building: state.plan.building.id, x: w.x.toFixed(2), y: w.y.toFixed(2),
                                  heading: w.heading.toFixed(0), floor: (s && s.floor) || 0, kind, prev });
  return (await fetch(`/api/around?${q}`)).json();
}
$("whereami").addEventListener("click", async () => {
  let text;
  try {
    const r = await aroundMe();
    text = [r.bearings, r.description].filter(Boolean).join(" ");
  } catch (_) {
    text = whereAmI();                       // server unreachable: the local version still answers
  }
  $("whereText").textContent = text;
  speak(text, { kind: "detail", force: true });   // asked for: always answers, up to 30 s
  heardAround = { at: Date.now(), key: `${state.walker.sweep}:${Math.round(state.walker.heading)}` };
});
let heardAround = null;                           // M right after W at the same spot: don't repeat the bearings

// Arriving somewhere (blindfolded): say only what changed — a hazard newly ahead, a new room, a memory newly in
// range — and nothing at all when nothing did. Instant, no AI.
let arrivalSig = "", arrivalBusy = false;
onWalkerChange(async () => {
  if (typeof blind === "undefined" || !blind.on || state.target || !isInterior() || arrivalBusy) return;
  const key = `${state.walker.sweep}`;
  if (key === onWalkerChange._lastSweep) return;
  onWalkerChange._lastSweep = key;
  arrivalBusy = true;
  try {
    const r = await aroundMe("arrival", arrivalSig);
    arrivalSig = r.sig;
    if (r.text) speak(r.text, { kind: "arrival" });
  } catch (_) { /* offline: the chimes still guide */ }
  arrivalBusy = false;
});

// "Tell me more": the ONE live AI call, only when asked (button or M). Directions come from the resolver, the
// three sentences from Gemini (the render here + the notes within 5 m).
async function tellMeMore() {
  const w = state.walker;
  $("tellText").textContent = "…";
  if (typeof startThinking === "function") startThinking();           // a heartbeat tone, not words
  try {
    const r = await fetch("/api/tell-me-more", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ viewer: state.user, building: state.plan.building.id, x: w.x, y: w.y, heading: w.heading,
                             sweep: w.sweep, bearings: !(heardAround && Date.now() - heardAround.at < 60000
                                                         && heardAround.key === `${w.sweep}:${Math.round(w.heading)}`) }) });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    const text = [body.bearings, body.text].filter(Boolean).join(" ");
    $("tellText").textContent = text;
    if (typeof stopThinking === "function") stopThinking();
    speak(text, { kind: "more", force: true });
  } catch (e) {
    if (typeof stopThinking === "function") stopThinking();
    console.warn("tell me more:", e.message);
    $("tellText").textContent = "Couldn't get that — try again.";
    Speech.oops();
  }
}
$("tellmore").addEventListener("click", tellMeMore);
document.addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (e.target.closest("select, input, textarea") || (k !== "m" && k !== "w") || e.ctrlKey || e.metaKey || e.altKey) return;
  if ((typeof rec !== "undefined" && rec.mr) || window.afterRecordBusy) return;
  e.preventDefault();
  if (k === "m") tellMeMore();
  else if (!(typeof blind !== "undefined" && blind.on)) $("whereami").click();   // blindfold mode binds W itself
});

// ---------- a recording lands where it was made ----------
document.addEventListener("memory-saved", (e) => {
  const m = memById(e.detail.id);
  if (!m) return;
  placeMarkers();
  const dot = $("markers").querySelector(`[data-id="${m.id}"]`);
  if (dot) { dot.classList.remove("landed"); void dot.offsetWidth; dot.classList.add("landed"); }
  // The full playback now belongs to the after-recording flow (after_record_ui.js): reviewing -> deciding.
});

// ---------- building switch: the real place for the interior, the map for the yard ----------
const _loadBuilding = loadBuilding;
loadBuilding = async function (bid) {           // eslint-disable-line no-func-assign
  await _loadBuilding(bid);
  const inside = isInterior();
  scenes.forEach((s) => (s.hidden = !inside));
  $("mapwrap").hidden = false;
  (inside ? $("debugMap") : $("stage")).insertBefore($("mapwrap"), inside ? null : $("markers"));
  $("pad").hidden = false;
  $("whereLine").innerHTML = "";
  const b = document.createElement("b");
  b.textContent = state.plan.building.name;
  $("whereLine").append(b, inside ? " · walk with the arrows" : " · click to walk");
  sceneKey = null;
  setStale(false);
  if (inside) syncScene(true);
  placeMarkers(); tidyList();
};

onWalkerChange(() => { placeMarkers(); tidyList(); syncScene(); });
memHooks.push(() => { placeMarkers(); tidyList(); });
