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
  const count = (n, one, many) => `${n <= 10 ? NUM[n][0].toUpperCase() + NUM[n].slice(1) : n} ${n === 1 ? one : many}`;

  // ---------- voice: a natural English voice where the browser has one ----------
  const PREFS = [/(Ava|Aria|Jenny|Emma|Michelle|Sonia|Libby|Natasha|Clara).*(Natural|Online)/i, /Google US English/i,
                 /Google UK English Female/i, /Samantha/i, /Zira/i];
  let voice = null;
  const pickVoice = () => {
    const vs = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
    for (const re of PREFS) { const v = vs.find((x) => re.test(x.name)); if (v) return (voice = v); }
    return (voice = vs[0] || null);
  };
  speechSynthesis.onvoiceschanged = pickVoice;
  pickVoice();
  function say(text, { then } = {}) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    if (voice || pickVoice()) u.voice = voice;
    u.rate = 0.95; u.pitch = 1.05;
    if (then) { let done = false; const go = () => { if (!done) { done = true; then(); } }; u.onend = go; setTimeout(go, 400 + text.length * 70); }
    speechSynthesis.speak(u);
    $("listenLine").textContent = text;
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
  function placeLine() {
    const p = place();
    if (!p) return G.mode === "family" ? "Nothing has been shared with you yet." : "There are no memories yet.";
    return `Place ${G.placeIdx + 1} of ${G.places.length}. ${p.name}. ${count(p.memory_count, "memory", "memories")}.`;
  }
  function memLine() {
    const p = place(), m = mem();
    if (!m) return "No memory here.";
    const t = m.transcript || {};
    const words = t.text ? ` It begins: ${t.text.split(/\s+/).slice(0, 14).join(" ")}${t.text.split(/\s+/).length > 14 ? "…" : ""}` : "";
    const vis = G.mode === "author" ? ` ${gx.visOf(m).say[0].toUpperCase()}${gx.visOf(m).say.slice(1)}.` : "";
    return `Memory ${G.memIdx + 1} of ${p.memories.length}. Left ${gx.dateHuman(m.created_at, true)}.${vis}${words} Space to play.`;
  }
  const HELP = "Left and right move between places. Up and down move between memories in a place. " +
               "Space plays a memory, or stops it. D describes the place. M looks closer, which takes a few seconds. " +
               "T takes you there, in the 3D walk. " +
               "H repeats this. Escape leaves listen mode.";

  // ---------- actions (keyboard and hat share them) ----------
  const actions = {
    left() { if (G.placeIdx === 0) { EAR.edge(); return say(`This is the first place. ${placeLine()}`); }
             gx.stopMemory(); G.placeIdx--; G.memIdx = 0; EAR.move(); say(placeLine()); },
    right() { if (G.placeIdx >= G.places.length - 1) { EAR.edge(); return say(`That was the last place. ${placeLine()}`); }
              gx.stopMemory(); G.placeIdx++; G.memIdx = 0; EAR.move(); say(placeLine()); },
    up() { if (!mem()) { EAR.edge(); return say(placeLine()); }
           if (G.memIdx === 0) { EAR.edge(); return say(`This is the first memory here. ${memLine()}`); }
           gx.stopMemory(); G.memIdx--; EAR.move(); say(memLine()); },
    down() { const p = place(); if (!p) { EAR.edge(); return say(placeLine()); }
             if (G.memIdx >= p.memories.length - 1) { EAR.edge(); return say(`That was the last memory here. ${memLine()}`); }
             gx.stopMemory(); G.memIdx++; EAR.move(); say(memLine()); },
    async play() {
      const m = mem();
      if (!m) { EAR.edge(); return say("Nothing to play here."); }
      if (G.audio) { gx.stopMemory(); EAR.end(); return say("Stopped."); }
      speechSynthesis.cancel(); EAR.play();
      const ok = await gx.playMemory(m, { onend: () => { EAR.end(); } });
      if (!ok) { EAR.error(); say("Sorry, I couldn't play that memory."); }
    },
    async describe() {
      const m = mem();
      if (!m) { EAR.edge(); return say("Nothing to describe here."); }
      gx.stopMemory();
      // cached only: written once when the memory was saved. The live call is M, and only M.
      if (m.description) { EAR.ready(); return say(m.description.text); }
      EAR.edge();
      say(G.mode === "family" ? "This place hasn't been described yet." : "This place hasn't been described yet. M looks closer.");
    },
    async more() {
      const m = mem();
      if (!m) { EAR.edge(); return say("Nothing here to look at."); }
      if (G.mode === "family") { EAR.edge(); return say("Tell me more is not available on a family link."); }
      gx.stopMemory();
      say("Looking closer. This takes a few seconds.");
      const beat = setInterval(() => tone(740, 0.05, 0.035), 1600);
      try {
        const body = await gx.tellMeMore(m);
        clearInterval(beat); EAR.ready();
        if (L.on && mem() === m) say(`${body.bearings} ${body.text}`);   // moved on meanwhile: a tone only
      } catch (e) {
        clearInterval(beat); EAR.error();
        if (L.on && mem() === m) say("I couldn't look closer right now.");
      }
    },
    there() {
      const m = mem();
      if (!m) { EAR.edge(); return say("Nothing to go to here."); }
      if (G.mode === "family") { EAR.edge(); return say("Take me there is not available on a family link."); }
      gx.stopMemory(); EAR.play();
      say("Opening the 3D walk at that spot. When it opens, press any key to hear where the memory is.",
          { then: () => (location.href = gx.takeMeThere(m)) });
    },
    help() { EAR.move(); say(HELP); },
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
      const empty = !G.places.length;
      say(empty ? placeLine() : `Listen mode. ${placeLine()} Left and right for places, up and down for memories, space to play. H for help.`);
    } else {
      gx.stopMemory(); speechSynthesis.cancel(); EAR.off();
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
    else if (!["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) { EAR.edge(); say("Press H for help."); }
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
      if (b === "listen") return setListen(!L.on);
      if (!L.on) setListen(true);
      if (actions[b]) actions[b]();
    };
  }
})();
