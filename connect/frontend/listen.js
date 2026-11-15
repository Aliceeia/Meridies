"use strict";
// Listen mode: the gallery for someone who can't see it. The screen goes fully dark; every key answers in
// speech or a tone. Keyboard and hat buttons drive the same actions:
//   ← →  previous / next place      ↑ ↓  previous / next memory here      Space  play / stop
//   D    describe this place          T    take me there (the 3D walk)       H  help     Esc  leave
// Hat buttons arrive from the server (/api/hat/events) as the same action names. Nothing is logged.
(() => {
  const { G } = window.gallery;
  const gx = window.gallery;
  const L = { on: false };
  const NUM = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
  const num = (n) => (n <= 10 ? NUM[n] : String(n));
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const vary = Speech.vary;

  // ---------- voice: everything goes through Speech (speech.js): interruptible, capped, no filler, no repeats ----------
  function say(text, { kind = "arrival", force = false, then = null } = {}) {
    const spoken = Speech.say(text, { kind, force, onend: then ? () => then() : null });
    if (spoken) $("listenLine").textContent = spoken;
    else if (then) then();
    return spoken;
  }
  const hinted = new Set();                         // hints are said once, not every time
  const once = (key, text) => (hinted.has(key) ? "" : (hinted.add(key), ` ${text}`));
  function whenWords(iso) {
    const days = Math.round((new Date(new Date().toDateString()) - new Date(new Date(iso).toDateString())) / 864e5);
    return days <= 0 ? "earlier today" : days === 1 ? "yesterday" : days < 7 ? "a few days ago" : days < 14 ? "last week"
      : days < 31 ? "a few weeks ago" : days < 62 ? "last month" : "a while back";
  }

  // ---------- tones ----------
  let ctx = null;
  function tone(f, dur, g = 0.12, when = 0) {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === "suspended") ctx.resume();
      const t0 = ctx.currentTime + when, o = ctx.createOscillator(), a = ctx.createGain();
      o.frequency.value = f;
      a.gain.setValueAtTime(0, t0); a.gain.linearRampToValueAtTime(g, t0 + 0.008);
      a.gain.setValueAtTime(g, t0 + dur - 0.008); a.gain.linearRampToValueAtTime(0, t0 + dur);
      o.connect(a).connect(ctx.destination); o.start(t0); o.stop(t0 + dur + 0.02);
    } catch (_) { /* no audio device: speech still answers */ }
  }
  const EAR = {
    move: () => tone(660, 0.05, 0.08),
    edge: () => { tone(196, 0.09, 0.12); tone(196, 0.09, 0.12, 0.13); },
    play: () => tone(880, 0.05, 0.08),
    end: () => { tone(784, 0.1, 0.08); tone(523, 0.18, 0.08, 0.1); },
    ready: () => { tone(659, 0.1, 0.1); tone(988, 0.2, 0.1, 0.12); },
    error: () => { tone(233, 0.16, 0.12); tone(175, 0.3, 0.12, 0.18); },
    on: () => { tone(660, 0.12, 0.1); tone(440, 0.12, 0.1, 0.12); tone(294, 0.3, 0.1, 0.24); },
    off: () => { tone(294, 0.12, 0.1); tone(440, 0.12, 0.1, 0.12); tone(660, 0.3, 0.1, 0.24); },
  };

  // ---------- what is in focus ----------
  const place = () => G.places[G.placeIdx];
  const mem = () => { const p = place(); return p && p.memories[Math.min(G.memIdx, p.memories.length - 1)]; };
  // Arriving at a place: the place first, then what's there, then where it sits in the list. 3-4 sentences, < 20 s.
  function placeLine() {
    const p = place();
    if (!p) return G.mode === "family" ? "Nothing shared with you yet." : "No memories yet.";
    const name = p.name, n = p.memory_count, at = `${G.placeIdx + 1} of ${G.places.length}`;
    const cnt = `${num(n)} ${n === 1 ? "memory" : "memories"}`, when = whenWords(p.latest_at);
    const where = p.building.name.replace(/ foyer, Princeton$/, ", Princeton");
    const from = G.mode === "shared" ? [...new Set(p.memories.map((m) => m.author_id))].map(gx.nameOf || ((x) => x)).join(" and ") : "";
    return from
      ? vary("place.shared", [`${name}, from ${from}. ${cap(cnt)}, the newest ${when}. ${cap(at)}.`,
                              `${name}: ${cnt} from ${from}. Place ${at}.`,
                              `${from}'s ${name.toLowerCase()}. ${cap(cnt)}. ${cap(at)}.`])
      : vary("place", [`${name}. ${cap(cnt)}, the newest ${when}. Place ${at}.`,
                       `${name}, at ${where}. ${cap(cnt)}. ${cap(at)}.`,
                       `${name}: ${cnt}, last left ${when}. Place ${at}.`,
                       `${name} — ${cnt} here. ${cap(at)}.`]);
  }
  // A memory: what was said comes first. Visibility only when it isn't "only you".
  function memLine() {
    const m = mem();
    if (!m) return "No memory here.";
    const t = (m.transcript || {}).text, when = whenWords(m.created_at);
    const w = t ? t.split(/\s+/) : [];
    const ex = t ? `“${w.slice(0, 14).join(" ").replace(/[.,;]$/, "")}${w.length > 14 ? "…" : ""}”` : "";
    const v = gx.visOf(m);
    const vis = G.mode !== "author" || v.quiet ? "" : ` ${{ family: "Family can hear it.", everyone: "Everyone who visits can hear it.",
      review: "Waiting for review.", draft: "Still a draft." }[v.cls] || ""}`;
    const who = G.mode !== "author" ? (gx.nameOf ? gx.nameOf(m.author_id) : m.author_id) : "";
    if (!ex) return `A recording from ${when}, no words.${vis}${once("space", "Space plays it.")}`;
    return (who
      ? vary("mem.shared", [`${who}, ${when}: ${ex}`, `${ex} — ${who}, ${when}.`, `From ${who}, ${when}: ${ex}`])
      : vary("mem", [`${ex} — ${when}.`, `${cap(when)}: ${ex}`, `From ${when}: ${ex}`])) + vis + once("space", "Space plays it.");
  }
  const HELP = "Left and right move between places. Up and down, between memories. Space plays or stops. " +
               "D describes the place. M looks closer, which takes a few seconds. T takes you there. Escape leaves.";

  // ---------- actions (keyboard and hat share them) ----------
  const actions = {
    // at an edge: a tone and a few words, never the whole line again (the user just heard it)
    left() { if (G.placeIdx === 0) { EAR.edge(); return say(vary("edge.first", ["First place.", "That's the first."]), { kind: "short" }); }
             gx.stopMemory(); G.placeIdx--; G.memIdx = 0; EAR.move(); say(placeLine()); },
    right() { if (G.placeIdx >= G.places.length - 1) { EAR.edge(); return say(vary("edge.last", ["Last place.", "That's the last one."]), { kind: "short" }); }
              gx.stopMemory(); G.placeIdx++; G.memIdx = 0; EAR.move(); say(placeLine()); },
    up() { if (!mem()) { EAR.edge(); return say(placeLine()); }
           if (G.memIdx === 0) { EAR.edge(); return say(vary("edge.top", ["First one here.", "Top of the list."]), { kind: "short" }); }
           gx.stopMemory(); G.memIdx--; EAR.move(); say(memLine()); },
    down() { const p = place(); if (!p) { EAR.edge(); return say(placeLine()); }
             if (G.memIdx >= p.memories.length - 1) { EAR.edge(); return say(vary("edge.bottom", ["Last one here.", "That's all here."]), { kind: "short" }); }
             gx.stopMemory(); G.memIdx++; EAR.move(); say(memLine()); },
    async play() {
      const m = mem();
      if (!m) { EAR.edge(); return; }
      if (G.audio) { gx.stopMemory(); EAR.end(); return; }          // stopping answers with a tone, not words
      Speech.cancel(); EAR.play();
      const ok = await gx.playMemory(m, { onend: () => { EAR.end(); } });
      if (!ok) Speech.oops();
    },
    async describe() {
      const m = mem();
      if (!m) { EAR.edge(); return; }
      gx.stopMemory();
      // cached only: written once when the memory was saved. The live call is M, and only M.
      if (m.description) { EAR.ready(); return say(m.description.text, { kind: "detail", force: true }); }
      EAR.edge();
      say(G.mode === "family" ? "Not described yet." : "Not described yet. M looks closer.", { kind: "short" });
    },
    async more() {
      const m = mem();
      if (!m) { EAR.edge(); return; }
      if (G.mode === "family") { EAR.edge(); return say("Not on a family link.", { kind: "short" }); }
      gx.stopMemory(); Speech.cancel();
      const beat = setInterval(() => tone(740, 0.05, 0.035), 1400);      // waiting is a tone, never words
      tone(740, 0.05, 0.035);
      try {
        const body = await gx.tellMeMore(m);
        clearInterval(beat); EAR.ready();
        if (L.on && mem() === m) say(`${body.bearings} ${body.text}`, { kind: "more", force: true });
      } catch (e) {
        clearInterval(beat);
        if (L.on && mem() === m) Speech.oops();
      }
    },
    there() {
      const m = mem();
      if (!m) { EAR.edge(); return; }
      if (G.mode === "family") { EAR.edge(); return say("Not on a family link.", { kind: "short" }); }
      gx.stopMemory(); EAR.play();
      say(vary("there", ["Taking you there. Any key when it opens.", "On the way. Press any key once it's open."]),
          { kind: "short", force: true, then: () => (location.href = gx.takeMeThere(m)) });
    },
    help() { EAR.move(); say(HELP, { kind: "help", force: true }); },
    leave() { setListen(false); },
  };
  window.gallery.listenActions = actions;

  function setListen(on) {
    if (on === L.on) return;
    L.on = on;
    $("listen").hidden = !on;
    $("listenBtn").setAttribute("aria-pressed", String(on));
    if (on) {
      if (document.activeElement) document.activeElement.blur();
      EAR.on();
      say(`${placeLine()}${G.places.length ? once("keys", "Arrows move, Space plays, H for help.") : ""}`, { force: true });
    } else {
      gx.stopMemory(); Speech.cancel(); EAR.off();
      const p = place();
      if (p) location.hash = `place=${encodeURIComponent(p.id)}`;   // the screen shows where you were
    }
  }

  const KEYS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", " ": "play",
                 d: "describe", D: "describe", m: "more", M: "more", t: "there", T: "there", h: "help", H: "help", Escape: "leave" };
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    if (!L.on && document.querySelector("dialog[open]")) return;       // a dialog owns the keys while it's open
    if (!L.on) {
      if (e.key === "l" || e.key === "L") { e.preventDefault(); setListen(true); }
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault();
    const a = KEYS[e.key];
    if (a) actions[a]();
    else if (!["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) { EAR.edge(); say("H for help.", { kind: "short" }); }
  }, true);
  $("listenBtn").hidden = false;
  $("listenBtn").onclick = () => setListen(true);

  // a reload while the server finishes transcripts must not move the cursor out of range
  G.listeners.push(() => {
    G.placeIdx = Math.min(G.placeIdx, Math.max(0, G.places.length - 1));
    const p = place();
    G.memIdx = p ? Math.min(G.memIdx, p.memories.length - 1) : 0;
  });

  // ---------- hat buttons ----------
  if (window.EventSource && G.mode !== "family") {      // the hat belongs to its wearer, not to a family link
    const es = new EventSource("/api/hat/events");
    es.onmessage = (ev) => {
      let b;
      try { b = JSON.parse(ev.data).button; } catch (_) { return; }
      Speech.cancel();                                   // a hat button interrupts speech like a key does
      if (b === "listen") return setListen(!L.on);
      if (!L.on) setListen(true);
      if (actions[b]) actions[b]();
    };
  }
})();
