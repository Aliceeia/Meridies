"use strict";
// Voice control for blindfold mode: say it instead of pressing it. Everything the keys do, plus "take me to …".
//
// Hearing: Chrome's speech recognition when it works; if it can't reach its service, the page detects speech itself
// (microphone level) and sends the short clip to /api/hear, where Gemini writes down the words (the clip is not kept).
// It never listens while the system or a memory is speaking, so it can't hear itself.
// Understanding: a fixed phrase grammar (parse()), no AI: instant and predictable.
(function (root) {
  // ---------- understanding: words -> one action ----------
  const NUMS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, a: 1, an: 1 };
  const num = (s) => (s == null ? null : /^\d+$/.test(s) ? +s : NUMS[s] || null);
  function parse(raw) {
    const t = ` ${String(raw || "").toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\b(please|now|thanks|thank you|okay|ok|um|uh)\b/g, " ")
      .replace(/\s+/g, " ").trim()} `;
    const has = (re) => re.test(t);
    let m;
    if (has(/ (stop|quiet|be quiet|shut up|enough|pause|cancel|hush) /) && !has(/ stop recording /)) return { act: "quiet" };
    if (has(/ (stop recording|finish recording|i'm done|i am done|done recording|that's it|that is it) /)) return { act: "stopRecording" };
    if (has(/ (keep it private|private|only me|just me) /)) return { act: "answer", choice: "private" };
    if (has(/ (decide later|later|not now) /)) return { act: "answer", choice: "later" };
    if (has(/ (share it|share with|share) /)) return { act: "answer", choice: "share" };
    if (has(/ (help|what can i say|commands|what can you do) /)) return { act: "help" };
    if (has(/ (repeat|say (it|that) again|again|what did you say) /)) return { act: "repeat" };
    if (has(/ (take off|remove|lift) (the )?blindfold | i want to see | let me see | exit blindfold /)) return { act: "unblind" };
    if (has(/ (go back to |open )?(the )?gallery /)) return { act: "gallery" };
    if (has(/ (record|leave a (memory|note)|remember this|start recording|new memory) /)) return { act: "record" };
    if (has(/ (where am i|where are we|what's around|what is around|look around|what's here|what is here) /)) return { act: "where" };
    if (has(/ (tell me more|more about|describe|what do you see|what's this place|what is this place|look closer) /)) return { act: "more" };
    if (has(/ (play|listen|hear it|what did \w+ say) /)) return { act: "play" };
    if ((m = t.match(/ (?:where is|where's|how do i get to|how far is) (?:the |my )?([a-z' ]+?) $/))) return { act: "where-is", target: m[1].trim() };
    if (has(/ (take me there|go there|let's go|lets go|yes go) /)) return { act: "go-last" };
    if ((m = t.match(/ (?:take me to|go to|walk to|bring me to|head to|lead me to) (?:the |my |a )?([a-z' ]+?) $/))) return { act: "go-to", target: m[1].trim() };
    if (has(/ turn(ed)? around | about face | behind me /)) return { act: "turn", deg: 180 };
    if ((m = t.match(/ (a (little|bit)|slightly|little) (left|right) /))) return { act: "turn", deg: m[3] === "left" ? -30 : 30 };
    if ((m = t.match(/ (turn |face |go )?(left|right) /)) && !has(/ on (my|your) (left|right) /)) return { act: "turn", deg: m[2] === "left" ? -90 : 90 };
    if ((m = t.match(/ (?:go |walk |step |move )?(back|backward|backwards)(?: (\w+) steps?)? /))) return { act: "step", dir: -1, n: num(m[2]) || 1 };
    if ((m = t.match(/ (?:go |walk |step |move |keep )?(?:forward|forwards|ahead|straight|going)(?: (\w+) steps?)? /)) ||
        (m = t.match(/ (?:walk|go|step|next|move)(?: (\w+) steps?)? /))) return { act: "step", dir: 1, n: num(m[1]) || 1 };
    return null;
  }
  const VC = { parse };
  if (typeof module !== "undefined" && module.exports) { module.exports = VC; return; }
  root.voiceControl = VC;

  // ---------- acting (walk page only) ----------
  const say = (text, opts = {}) => speak(text, { kind: "short", force: true, ...opts });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const arrived = async () => { while (state.target) await sleep(80); };
  let lastTarget = null;

  function roomOfSweep(s) {
    const r = (state.plan.rooms || []).find((r) => (r.floor ?? 0) === (s.floor ?? 0) && insidePoly(s.position.x, s.position.y, r.polygon));
    return (r ? r.label : s.room || "").toLowerCase();
  }
  // a place by name: a room, a hazard, "memory"/"note", someone's memory, or the place a memory was left
  function findTarget(words) {
    const w = words.replace(/^(the|my|a) /, "").replace(/'s$/, "");
    const sweeps = state.plan.sweeps || [];
    const here = sweepAt(state.walker.sweep);
    const near = (pred) => sweeps.filter(pred).sort((a, b) => Math.hypot(a.position.x - here.position.x, a.position.y - here.position.y)
                                                         - Math.hypot(b.position.x - here.position.x, b.position.y - here.position.y))[0];
    const room = near((s) => roomOfSweep(s) && (roomOfSweep(s).includes(w) || w.includes(roomOfSweep(s))));
    if (room) return { sweep: room, label: `the ${roomOfSweep(room)}` };
    const closest = (x, y) => sweeps.reduce((b, s) => (Math.hypot(s.position.x - x, s.position.y - y)
                                                      < Math.hypot(b.position.x - x, b.position.y - y) ? s : b));
    const hz = (state.plan.hazards || []).find((h) => w.includes(h.kind.toLowerCase()) || h.kind.toLowerCase().includes(w));
    if (hz) return { sweep: closest(hz.position[0], hz.position[1]), label: `the ${hz.kind}` };
    if (/memor|note|recording|message/.test(w) || /\w's /.test(`${words} `)) {
      const who = (words.match(/(\w+)'s/) || [])[1];           // "Theo's memory"
      const mems = state.memories.filter((m) => !who || (m.author_id || "").includes(who));
      const m = (mems.length ? mems : state.memories).map((m) => ({ m, d: relative(m).dist })).sort((a, b) => a.d - b.d)[0];
      if (m) {
        const s = sweepAt(m.m.sweep) || closest(m.m.position.x, m.m.position.y);
        return { sweep: s, label: who ? `${who}'s memory` : "the nearest memory", memory: m.m };
      }
    }
    return null;
  }
  // shortest route over the scan's own walkable links (sweep neighbours), breadth first
  function route(from, to) {
    const prev = new Map([[from, null]]), q = [from];
    while (q.length) {
      const i = q.shift();
      if (i === to) break;
      for (const n of (sweepAt(i) || {}).neighbours || []) if (!prev.has(n)) { prev.set(n, i); q.push(n); }
    }
    if (!prev.has(to)) return null;
    const path = [];
    for (let i = to; i !== from; i = prev.get(i)) path.unshift(i);
    return path;
  }
  async function goTo(target) {
    if (!isInterior()) return say("I can only lead the way indoors.");
    const path = route(state.walker.sweep, target.sweep.index);
    if (!path) return say(`I can't find a way to ${target.label} from here.`);
    if (!path.length) return say(`You're already at ${target.label}.`);
    const dist = path.reduce((d, i, k) => { const a = sweepAt(k ? path[k - 1] : state.walker.sweep), b = sweepAt(i);
      return d + Math.hypot(b.position.x - a.position.x, b.position.y - a.position.y); }, 0);
    say(Speech.vary("route", [`Heading to ${target.label}, about ${Math.max(1, Math.round(dist / 0.7))} steps.`,
                              `Okay, ${target.label}. About ${Math.max(1, Math.round(dist / 0.7))} steps. Follow me.`]));
    window.voiceRoute = true;                          // on the way: only hazards are spoken
    for (const i of path) {
      if (!window.voiceRoute) return;                  // "stop" ends the route
      goToSweep(i);
      await arrived();
      await sleep(250);
    }
    window.voiceRoute = false;
    if (target.memory) { play.near = target.memory.id; updateNearUI(); }
    if (typeof window.announceArrival === "function") window.announceArrival(target.memory ? "Here. The memory is close; say play." : "");
  }
  function turn(deg) {
    state.walker.heading = norm360(state.walker.heading + deg);
    state.target = null;
    renderWalker();
    earcon("step");
    if (Math.abs(deg) >= 180) say("Turned around.");
  }
  async function step(dir, n) {
    for (let k = 0; k < Math.min(n, 10); k++) {
      if (state.target) await arrived();
      const before = state.walker.sweep;
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: dir > 0 ? "ArrowUp" : "ArrowDown", bubbles: true }));
      await sleep(60);
      await arrived();
      if (state.walker.sweep === before) break;          // couldn't go on (the toast already said why)
    }
  }
  const HELP = "You can say: go forward or back, turn left, right, or around. Take me to the kitchen, or to the " +
               "nearest memory. Where's the bedroom? Where am I, or tell me more. Play, stop, record, or repeat. " +
               "Say take off the blindfold to see.";

  async function handle(text) {
    const c = parse(text);
    if (!c) {
      if (text.split(" ").length <= 6) say(Speech.vary("huh", ["Didn't catch that. Say help for what I can do.", "Sorry? Say help to hear the options."]));
      return null;
    }
    earcon("tap");
    switch (c.act) {
      case "quiet": window.voiceRoute = false; Speech.cancel(); stopVoice(); break;
      case "help": say(HELP, { kind: "help" }); break;
      case "repeat": if (Speech.lastText) say(Speech.lastText, { kind: "detail" }); break;
      case "unblind": setBlind(false); break;
      case "gallery": location.href = "/"; break;
      case "record": if (!rec.mr) $("rec").click(); break;
      case "stopRecording": if (rec.mr) rec.stop("user"); break;
      case "answer": {
        const b = document.querySelector(`#decide:not([hidden]) .opt[data-choice="${c.choice}"]:not([hidden])`);
        if (b) b.click(); else say("There's nothing to answer right now.");
        break;
      }
      case "where": $("whereami").click(); break;
      case "more": tellMeMore(); break;
      case "play": {
        const m = (play.near && memById(play.near)) || state.memories.map((m) => ({ m, d: relative(m).dist }))
          .filter((x) => x.d <= NEAR_M).sort((a, b) => a.d - b.d).map((x) => x.m)[0];
        if (m) playVoice(m); else say(Speech.vary("noplay", ["Nothing here to play.", "No memory right here."]));
        break;
      }
      case "where-is": {
        const t = findTarget(c.target);
        if (!t) { say(`I don't know where ${c.target} is.`); break; }
        lastTarget = t;
        const r = relative({ position: { x: t.sweep.position.x, y: t.sweep.position.y } });
        say(`${t.label.charAt(0).toUpperCase() + t.label.slice(1)} is ${describeDir(r)}. Say take me there.`);
        break;
      }
      case "go-last": if (lastTarget) await goTo(lastTarget); else say("Where to? Say, take me to the kitchen."); break;
      case "go-to": {
        const t = findTarget(c.target);
        if (!t) { say(`I don't know where ${c.target} is.`); break; }
        lastTarget = t;
        await goTo(t);
        break;
      }
      case "turn": turn(c.deg); break;
      case "step": await step(c.dir, c.n); break;
    }
    return c;
  }
  VC.handle = handle;
  VC.route = route;
  VC.findTarget = findTarget;

  // ---------- hearing ----------
  const busy = () => !!(Speech.current || (typeof play !== "undefined" && play.src) || (window.speechSynthesis && speechSynthesis.speaking));
  let mode = null, active = false, rec_ = null, quietSince = 0, lastBrowserWords = 0;
  const heard = (text, via) => {
    if (!text || !text.trim()) return;
    if (via === "browser") lastBrowserWords = performance.now();
    $("blind").firstElementChild.textContent = `“${text.trim()}”`;
    handle(text.trim());
  };

  function startBrowser() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return false;
    rec_ = new SR();
    rec_.lang = "en-US"; rec_.continuous = true; rec_.interimResults = false; rec_.maxAlternatives = 1;
    rec_.onresult = (e) => { for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) heard(e.results[i][0].transcript, "browser"); };
    rec_.onerror = (e) => {
      if (["network", "service-not-allowed", "language-not-supported"].includes(e.error)) {
        console.warn("speech recognition unavailable (", e.error, "): the page's own listening carries on alone");
        mode = "server"; try { rec_.abort(); } catch (_) {} rec_ = null;
      }
    };
    rec_.onend = () => { if (active && mode === "browser") setTimeout(() => { if (active && !busy()) try { rec_.start(); } catch (_) {} }, 250); };
    mode = "browser";
    try { rec_.start(); } catch (_) {}
    return true;
  }

  // fallback: our own voice-activity detection on the microphone, then Gemini writes down the words
  let mic = null;
  async function startServer() {
    if (mic) return;
    try { mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (e) { say("I can't use the microphone. Keys still work."); return; }
    const ctx = audio.ctx(), src = ctx.createMediaStreamSource(mic), proc = ctx.createScriptProcessor(4096, 1, 1);
    const sink = ctx.createGain(); sink.gain.value = 0;
    src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
    const rate = ctx.sampleRate, pre = [], seg = [];
    let floor = 0.01, speaking = false, loud = 0, silent = 0, t0 = performance.now();
    proc.onaudioprocess = (ev) => {
      if (!active) return;
      const x = new Float32Array(ev.inputBuffer.getChannelData(0));
      const rms = Math.sqrt(x.reduce((s, v) => s + v * v, 0) / x.length);
      if (busy()) { quietSince = performance.now(); speaking = false; seg.length = 0; return; }   // never hear ourselves
      if (performance.now() - quietSince < 400) return;
      if (!speaking) {
        if (performance.now() - t0 < 1500) floor = Math.max(floor, rms);                         // learn the room
        else floor = floor * 0.995 + rms * 0.005;
        pre.push(x); if (pre.length > 3) pre.shift();
        loud = rms > Math.max(0.02, floor * 3) ? loud + 1 : 0;
        if (loud >= 2) { speaking = true; seg.push(...pre); silent = 0; }
      } else {
        seg.push(x);
        silent = rms < Math.max(0.015, floor * 2) ? silent + 1 : 0;
        const secs = seg.length * 4096 / rate;
        if (silent * 4096 / rate > 0.7 || secs > 6) {
          speaking = false;
          const clip = seg.splice(0), saidAt = performance.now() - secs * 1000;
          // Chrome's recogniser gets first go; if it produced no words for this speech, the server hears it
          if (secs > 0.4) setTimeout(() => { if (lastBrowserWords < saidAt) send(clip, rate); }, mode === "browser" ? 1500 : 0);
        }
      }
    };
    if (!mode) mode = "server";
  }
  function wav(chunks, rate) {
    const n = chunks.reduce((s, c) => s + c.length, 0), step = rate / 16000, out = new Int16Array(Math.floor(n / step));
    let i = 0, k = 0;
    const all = new Float32Array(n); for (const c of chunks) { all.set(c, k); k += c.length; }
    for (let j = 0; j < out.length; j++, i += step) out[j] = Math.max(-1, Math.min(1, all[Math.floor(i)])) * 32767;
    const b = new DataView(new ArrayBuffer(44 + out.length * 2));
    const w = (o, s) => [...s].forEach((ch, q) => b.setUint8(o + q, ch.charCodeAt(0)));
    w(0, "RIFF"); b.setUint32(4, 36 + out.length * 2, true); w(8, "WAVE"); w(12, "fmt "); b.setUint32(16, 16, true);
    b.setUint16(20, 1, true); b.setUint16(22, 1, true); b.setUint32(24, 16000, true); b.setUint32(28, 32000, true);
    b.setUint16(32, 2, true); b.setUint16(34, 16, true); w(36, "data"); b.setUint32(40, out.length * 2, true);
    out.forEach((v, q) => b.setInt16(44 + q * 2, v, true));
    return new Blob([b], { type: "audio/wav" });
  }
  async function send(chunks, rate) {
    audio.tone(990, 0.04, 0.04);                                      // a tick: heard you, working on it
    try {
      const r = await fetch("/api/hear", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav(chunks, rate) });
      const { text } = await r.json();
      if (text) heard(text, "server");
    } catch (_) { /* nothing: the person can say it again */ }
  }

  function setActive(on) {
    active = on;
    if (on) {
      // both: Chrome's recogniser (fast, when it works) and the page's own speech detection (always works)
      if (!mode) startBrowser();
      else if (mode === "browser" && rec_) try { rec_.start(); } catch (_) {}
      startServer();
    } else {
      window.voiceRoute = false;
      if (rec_) try { rec_.abort(); } catch (_) {}
    }
  }
  // pause the browser recogniser while anything is speaking, resume when quiet (it can't hear itself)
  setInterval(() => {
    if (!active || mode !== "browser" || !rec_) return;
    if (busy()) { try { rec_.abort(); } catch (_) {} }
  }, 200);
  // listening follows the blindfold
  new MutationObserver(() => setActive(!$("blind").hidden)).observe($("blind"), { attributes: true, attributeFilter: ["hidden"] });
  VC.setActive = setActive;
  VC.mode = () => mode;
})(typeof window !== "undefined" ? window : globalThis);
