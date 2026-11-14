"use strict";
// Audio first. Text is for the sighted judge to check what was said; a blind user never needs the screen.
// Uses the audio engine (audio.tone, playVoice) and the Gemini pipeline as they are; changes neither.

// ---------- a gentle, natural voice ----------
// Best results: Microsoft Edge ships natural neural voices (Aria, Jenny, Ava, Emma...). Chrome falls back to Google's.
const VOICE_PREFS = [
  /(Ava|Aria|Jenny|Emma|Michelle|Sonia|Libby|Natasha|Clara|Ana).*(Natural|Online)/i,
  /(Natural|Neural).*(female|Ava|Aria|Jenny|Emma)/i,
  /Google US English/i, /Google UK English Female/i, /Samantha/i, /Zira/i,
];
let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
  for (const re of VOICE_PREFS) { const v = vs.find((x) => re.test(x.name)); if (v) return (voice = v); }
  return (voice = vs[0] || null);
}
speechSynthesis.onvoiceschanged = pickVoice;
pickVoice();
speak = function (text, { interrupt = true } = {}) {      // eslint-disable-line no-func-assign
  if (state.hatWarning) return earcon("muted");
  if (interrupt) speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";                     // never let a Chinese system voice read English text
  if (voice || pickVoice()) u.voice = voice;
  u.rate = 0.92; u.pitch = 1.08; u.volume = 0.9;
  speechSynthesis.speak(u);
};

// ---------- earcons: every action answers with its own short sound ----------
const EARCONS = {
  tap:     [[880, 0.04, 0.06, 0]],                                            // a button registered
  step:    [[520, 0.03, 0.05, 0], [390, 0.04, 0.04, 0.05]],                   // arrived at the next spot
  bump:    [[196, 0.09, 0.12, 0], [196, 0.09, 0.12, 0.13]],                   // can't go / didn't work
  saved:   [[523, 0.12, 0.14, 0], [659, 0.12, 0.14, 0.12], [784, 0.22, 0.14, 0.24]],   // memory stayed here
  ready:   [[659, 0.10, 0.12, 0], [988, 0.22, 0.12, 0.12]],                   // description ready
  nothing: [[392, 0.14, 0.1, 0], [294, 0.24, 0.1, 0.15]],                     // nothing nearby
  error:   [[233, 0.16, 0.12, 0], [175, 0.3, 0.12, 0.18]],                    // something failed
  dark:    [[660, 0.12, 0.1, 0], [440, 0.12, 0.1, 0.12], [294, 0.3, 0.1, 0.24]],      // blindfold on
  light:   [[294, 0.12, 0.1, 0], [440, 0.12, 0.1, 0.12], [660, 0.3, 0.1, 0.24]],      // blindfold off
  muted:   [[300, 0.06, 0.05, 0]],
};
function earcon(name) {
  try { audio.ctx(); for (const [f, dur, g, when] of EARCONS[name] || []) audio.tone(f, dur, g, when); } catch (_) {}
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b && !["rec", "recStop", "listen", "whereami"].includes(b.id)) earcon("tap");   // those have their own sounds
}, true);
const _toast = toast;
toast = function (msg, ms) {                               // eslint-disable-line no-func-assign
  _toast(msg, ms);
  earcon("bump");
  if (blind.on) speak(msg);
};
let lastSweep = null;
onWalkerChange(() => {
  if (state.target || state.walker.sweep == null) return;
  if (lastSweep !== null && state.walker.sweep !== lastSweep) earcon("step");
  lastSweep = state.walker.sweep;
});
document.addEventListener("memory-saved", (e) => {
  if (!e.detail.interrupted && !state.hatWarning) earcon("saved");   // an obstacle warning outranks every memory sound
});

// ---------- speak, don't show ----------
// While Gemini thinks, say so once and keep a soft heartbeat going, so silence never means "broken".
let thinking = null;
function startThinking() {
  stopThinking();
  speak("Looking around. This takes a few seconds.");
  thinking = setInterval(() => audio.tone(740, 0.05, 0.035), 1600);
}
function stopThinking() { clearInterval(thinking); thinking = null; }
document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b && /Describe/.test(b.textContent)) startThinking();
}, true);
document.addEventListener("described", (e) => { stopThinking(); earcon("ready"); setTimeout(() => speak(e.detail.description.text), 450); });
document.addEventListener("describe-failed", (e) => { stopThinking(); earcon("error"); speak(e.detail); _toast(e.detail, 6000); });

// Listen must always answer: if a recording can't be played, say so instead of staying silent.
window.addEventListener("unhandledrejection", (e) => {
  earcon("error");
  speak("Sorry, I couldn't play that memory.");
  console.warn("playback failed:", e.reason);
});
$("listen").addEventListener("click", () => audio.tone(880, 0.04, 0.06), true);   // the press registered
$("whereami").addEventListener("click", () => {
  if (!state.memories.some((m) => relative(m).dist <= NEAR_M)) earcon("nothing");
}, true);

// every card can be heard directly, from its direction
memHooks.push(() => {
  for (const m of state.memories) {
    const actions = document.querySelector(`#mem-${m.id} .actions`);
    if (!actions || actions.querySelector(".play")) continue;
    const b = document.createElement("button");
    b.className = "play";
    b.textContent = "▶ Listen";
    b.onclick = () => { audio.tone(880, 0.04, 0.06); playVoice(m); };
    actions.prepend(b);
  }
});

// text collapses to one line a judge can open
memHooks.push(() => {
  for (const m of state.memories) {
    const card = document.getElementById(`mem-${m.id}`);
    if (!card || !m.description || card.querySelector(".showtext")) continue;
    const t = document.createElement("button");
    t.className = "showtext";
    t.textContent = "▸ show text";
    t.onclick = () => { const o = card.classList.toggle("textopen"); t.textContent = o ? "▾ hide text" : "▸ show text"; };
    card.querySelector(".desc").before(t);
    const say = [...card.querySelectorAll(".actions button")].find((x) => x.textContent.includes("Speak"));
    if (say) say.textContent = "🔊 Say it again";   // the existing Speak button, now a replay
  }
});

// ---------- blindfold mode ----------
// No path is kept: only which memories were reached and heard. We never map where someone went.
const blind = { on: false, t0: 0, reached: new Set(), heard: new Set(), timer: null };
function setBlind(on) {
  if (on === blind.on) return;
  blind.on = on;
  document.body.classList.toggle("blind", on);
  $("blind").hidden = !on;
  document.activeElement && document.activeElement.blur();
  if (on) {
    Object.assign(blind, { t0: performance.now(), reached: new Set(), heard: new Set() });
    blind.timer = setInterval(() => { if (play.playingId) blind.heard.add(play.playingId); }, 250);
    earcon("dark");
    setTimeout(() => speak("Blindfold on. Arrows to walk. Enter to listen. W, where am I. R, record. Space to see again."), 500);
  } else {
    clearInterval(blind.timer);
    earcon("light");
    showReport();
  }
}
onWalkerChange(() => {
  if (!blind.on) return;
  for (const m of state.memories) if (relative(m).dist <= NEAR_M) blind.reached.add(m.id);
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("select, input, textarea")) return;
  if (e.code === "Space") {
    if (!blind.on && e.target.closest("button")) return;   // Space on a focused button presses that button
    e.preventDefault(); if (!rec.mr) setBlind(!blind.on); return;
  }
  if (!blind.on) return;
  if (e.key === "Enter") {
    if (rec.mr || window.afterRecordBusy) return;      // Enter belongs to the recording flow right now
    e.preventDefault();
    if (play.near) playVoice(memById(play.near)); else { earcon("nothing"); speak("Nothing to listen to here."); }
  } else if (e.key === "w" || e.key === "W") $("whereami").click();
  else if ((e.key === "r" || e.key === "R") && !rec.mr) $("rec").click();   // while recording, R stops (after_record_ui)
}, true);
$("blindBtn").addEventListener("click", () => setBlind(true));

function showReport() {
  const p = state.plan, secs = Math.round((performance.now() - blind.t0) / 1000);
  const total = state.memories.length, found = blind.reached.size, heard = blind.heard.size;
  const svg = $("reportMap");
  svg.replaceChildren();
  const pts = isInterior() ? p.rooms.flatMap((r) => r.polygon) : p.walkable;
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  const x0 = Math.min(...xs) - 1, x1 = Math.max(...xs) + 1, y0 = Math.min(...ys) - 1, y1 = Math.max(...ys) + 1;
  svg.setAttribute("viewBox", `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`);
  const k = (x1 - x0) / 20;
  if (isInterior()) {
    for (const r of p.rooms) svgEl("polygon", { points: r.polygon.map(([x, y]) => `${x},${-y}`).join(" "), fill: "#221d19" }, svg);
    for (const w of p.walls) if (w.type === "wall")
      svgEl("line", { x1: w.a[0], y1: -w.a[1], x2: w.b[0], y2: -w.b[1], stroke: "#6b5f54", "stroke-width": 0.12 }, svg);
  } else {
    svgEl("polygon", { points: p.walkable.map(([x, y]) => `${x},${-y}`).join(" "), fill: "#221d19", stroke: "#6b5f54", "stroke-width": 0.2 }, svg);
  }
  for (const m of state.memories) {
    const got = blind.reached.has(m.id);
    svgEl("circle", { cx: m.position.x, cy: -m.position.y, r: (got ? 0.55 : 0.35) * k, fill: got ? "var(--glow)" : "#5a5048",
      opacity: got ? 0.95 : 0.6 }, svg);
    if (blind.heard.has(m.id)) svgEl("circle", { cx: m.position.x, cy: -m.position.y, r: 0.85 * k, fill: "none", stroke: "#fff", "stroke-width": 0.08 * k }, svg);
  }
  const line = `${secs} seconds without sight. You found ${found} of ${total} ${total === 1 ? "memory" : "memories"} by sound${heard ? `, and listened to ${heard}` : ""}.`;
  $("reportLine").textContent = line;
  $("report").hidden = false;
  setTimeout(() => speak(line), 600);
}
$("reportClose").addEventListener("click", () => ($("report").hidden = true));
