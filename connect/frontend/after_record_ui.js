"use strict";
// Page wiring for the after-recording state machine (after_record.js).
// Runs the effects the machine asks for, using the existing audio engine (playVoice/stopVoice), the
// existing speech (speak) and the server's /decision endpoint. No new dependencies.
//
// INTERRUPT ENTRY POINT (for the obstacle / hat system):
//   window.meridies.interrupt(reason)          or   document.dispatchEvent(new CustomEvent("meridies:interrupt", {detail: {reason}}))
// Effects: recording stops at once (kept as a private draft); playback/question stop at once, nothing is
// chosen for the user, result = private draft. Nothing resumes by itself afterwards.
// window.setHatWarning(true) (the existing hardware hook) calls it.  STUB: no obstacle logic lives here.
(() => {
  const AR = window.AfterRecord;
  let s = AR.initial, timer = null, returnFocus = null;
  const ctx = () => ({ isPublicSpace: !!(state.plan && state.plan.building && state.plan.building.public === true) });
  const LABELS = {
    private: { text: "Keep private", note: "Only you will hear it. This is the default.",
               aria: "Keep private. Only you will hear it. This is the default." },
    share:   { text: "Share with the community", note: "Checked by a person first. Private until approved.",
               aria: "Share with the community. It is checked by a person first and stays private until approved." },
    later:   { text: "Decide later", note: "Saved as a private draft.",
               aria: "Decide later. Saved as a private draft." },
  };

  function dispatch(ev) {
    const r = AR.transition(s, ev, ctx());
    s = r.state;
    window.afterRecordState = s;                                   // read by tests / debug
    window.afterRecordBusy = ["recording", "reviewing", "deciding"].includes(s.phase);
    for (const fx of r.effects) run(fx);
    status();
  }

  function run(fx) {
    switch (fx.type) {
      case "stopRecording": rec.stop("interrupt"); break;
      case "stopAudio": stopVoice(); speechSynthesis.cancel(); break;
      case "playReview": review(fx.memoryId); break;
      case "ask": ask(fx.options, fx.prompt); break;
      case "closeAsk": closeAsk(); break;
      case "startTimer": clearTimeout(timer); timer = setTimeout(() => dispatch({ type: "TIMEOUT" }), fx.ms); break;
      case "clearTimer": clearTimeout(timer); timer = null; break;
      case "commit": commit(fx.memoryId, fx.choice, fx.reason); break;
      case "announce":
        $("recState").textContent = fx.text;
        if (!state.hatWarning) speak(fx.text);
        break;
    }
  }

  // reviewing: play the whole take once, from where it was left; tell the machine when it ends
  async function review(id) {
    const m = memById(id);
    if (!m) return dispatch({ type: "PLAYBACK_ENDED" });
    try { await playVoice(m); } catch (_) { /* can't play: still ask, the take is saved */ }
    if (s.phase !== "reviewing" || s.memoryId !== id) return;      // interrupted / skipped meanwhile
    if (!play.src) return dispatch({ type: "PLAYBACK_ENDED" });
    play.src.addEventListener("ended", () => {
      if (s.phase === "reviewing" && s.memoryId === id) dispatch({ type: "PLAYBACK_ENDED" });
    });
  }

  // deciding: one spoken question, big fixed-order choices, focus managed
  function ask(options, prompt) {
    const box = $("decide");
    $("decideTitle").textContent = prompt;
    for (const b of box.querySelectorAll(".opt")) {
      const on = options.includes(b.dataset.choice);
      b.hidden = !on;
      if (on) {
        const L = LABELS[b.dataset.choice];
        b.querySelector(".t").textContent = L.text;
        b.querySelector(".n").textContent = L.note;
        b.setAttribute("aria-label", L.aria);
      }
    }
    box.dataset.count = options.length;
    returnFocus = document.activeElement;
    box.hidden = false;
    box.querySelector(".opt:not([hidden])").focus();               // first option = Keep private
    if (!state.hatWarning) speak(prompt);                           // asked ONCE, never repeated
  }
  function closeAsk() {
    $("decide").hidden = true;
    const back = returnFocus && document.contains(returnFocus) ? returnFocus : $("rec");
    back.focus();
  }
  for (const b of document.querySelectorAll("#decide .opt")) {
    b.addEventListener("click", () => dispatch({ type: "CHOOSE", choice: b.dataset.choice }));
  }

  async function commit(id, choice, reason) {
    try {
      const r = await fetch(`/api/memories/${id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ viewer: state.user, choice, reason }) });
      if (!r.ok) console.warn("decision not stored:", (await r.json()).error, "- the memory stays private");
    } catch (e) {
      console.warn("decision not stored:", e, "- the memory stays private");
    }
    await loadMemories();
  }

  function status() {
    if (s.phase === "reviewing") $("recState").textContent = "Playing back what you recorded. Press Enter to skip.";
    document.body.classList.toggle("deciding", s.phase === "deciding");
  }

  // ---------- recording events from app.js ----------
  document.addEventListener("recording-started", () => dispatch({ type: "START" }));
  document.addEventListener("recording-failed", () => dispatch({ type: "FAILED" }));
  document.addEventListener("recording-finished", (e) => {
    const d = e.detail;
    if (d.interrupted) return;                                       // already settled as a private draft
    dispatch({ type: d.autoStopped ? "AUTO_STOPPED" : "STOPPED", memoryId: d.id });
  });

  // ---------- keys: R / Enter stop a recording; Enter skips playback; Escape = no answer (private) ----------
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("select, input, textarea")) return;
    const k = e.key;
    if (s.phase === "recording" && (k === "r" || k === "R" || k === "Enter")) {
      e.preventDefault(); e.stopImmediatePropagation();
      rec.stop("user");
    } else if (s.phase === "reviewing" && k === "Enter") {
      e.preventDefault(); e.stopImmediatePropagation();
      dispatch({ type: "SKIP_REVIEW" });
    } else if (s.phase === "deciding" && k === "Escape") {
      e.preventDefault(); e.stopImmediatePropagation();
      dispatch({ type: "CANCEL" });
    } else if (s.phase === "deciding" && k === "Tab") {                 // keep focus inside the question
      const opts = [...document.querySelectorAll("#decide .opt:not([hidden])")];
      const i = opts.indexOf(document.activeElement);
      e.preventDefault();
      opts[(i + (e.shiftKey ? -1 : 1) + opts.length) % opts.length].focus();
    }
  }, true);
  // leaving the place mid-flow counts as no answer: private
  $("building").addEventListener("change", () => { if (s.phase === "reviewing" || s.phase === "deciding") dispatch({ type: "CANCEL" }); }, true);

  // ---------- the interrupt entry point ----------
  window.meridies = window.meridies || {};
  window.meridies.interrupt = (reason = "obstacle") => {
    if (window.afterRecordBusy) dispatch({ type: "INTERRUPT", reason });
    stopVoice(); speechSynthesis.cancel();                           // warning sounds outrank every memory sound
    document.dispatchEvent(new CustomEvent("meridies:interrupted", { detail: { reason, phase: s.phase } }));
  };
  window.meridies.interruptEnd = () => { /* deliberately nothing: the user restarts things by hand */ };
  document.addEventListener("meridies:interrupt", (e) => window.meridies.interrupt((e.detail || {}).reason));
  const prevHat = window.setHatWarning;
  window.setHatWarning = (on) => { prevHat(on); if (on) window.meridies.interrupt("hat-warning"); };
})();
