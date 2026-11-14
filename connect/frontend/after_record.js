"use strict";
// After-recording flow as an explicit state machine (pure: no DOM, no audio, no network).
//
//   idle ──START──▶ recording ──STOPPED──▶ reviewing ──PLAYBACK_ENDED──▶ deciding ──CHOOSE/TIMEOUT/CANCEL──▶ saved
//                       │                      │                             │
//                       └──── INTERRUPT ───────┴──────────── INTERRUPT ──────┴──▶ saved (private draft, silent)
//
// HARD RULE: private by default. Silence, timeout, cancel, close, back or interrupt NEVER shares.
// "share" is only offered (and only accepted) in public spaces, and even then it only REQUESTS sharing:
// the server puts the clip in pending_review; nothing is visible to the community before review.
//
// transition(state, event, ctx) -> { state, effects }. Effects are plain objects the page executes:
//   { type: "stopRecording" }                       stop the microphone now (keep what was recorded)
//   { type: "playReview", memoryId }                play back the take once
//   { type: "stopAudio" }                           stop any memory audio / speech immediately
//   { type: "ask", options, prompt }                speak the question once and show the big options
//   { type: "closeAsk" }                            hide the options
//   { type: "startTimer", ms } / { type: "clearTimer" }
//   { type: "commit", memoryId, choice, reason }    tell the server: choice = "private" | "share" | "later"
//   { type: "announce", text }                      short spoken/visible confirmation (never after an interrupt)

const CHOICES = ["private", "share", "later"];
const DECIDE_TIMEOUT_MS = 20000;

function optionsFor(ctx) {
  // Fixed order, always. "share" exists only in public spaces (missing flag = not public).
  return ctx && ctx.isPublicSpace === true ? ["private", "share", "later"] : ["private", "later"];
}

function promptFor(options) {
  return options.includes("share")
    ? "Saved. Keep it private, or share with the community?"
    : "Saved, and private. Keep it private, or decide later?";
}

const initial = Object.freeze({ phase: "idle", memoryId: null, result: null });

function transition(s, ev, ctx = {}) {
  const fx = [];
  const done = (result, extra = {}) => ({ state: { ...s, phase: "saved", result, ...extra }, effects: fx });
  switch (s.phase) {
    case "idle":
      if (ev.type === "START") return { state: { ...initial, phase: "recording" }, effects: fx };
      break;

    case "recording":
      if (ev.type === "STOPPED") {
        // the take is already stored server-side as private; now let the person hear it
        fx.push({ type: "playReview", memoryId: ev.memoryId });
        return { state: { ...s, phase: "reviewing", memoryId: ev.memoryId }, effects: fx };
      }
      if (ev.type === "INTERRUPT") {
        // stop the mic at once; what was recorded is uploaded as a PRIVATE DRAFT by the page
        fx.push({ type: "stopRecording" }, { type: "stopAudio" });
        return { state: { ...s, phase: "saved", result: "interrupted-draft", draft: true, interrupted: true }, effects: fx };
      }
      if (ev.type === "AUTO_STOPPED") {
        // the person forgot to stop: the mic is closed, the take is kept PRIVATE, nothing played or asked
        fx.push({ type: "commit", memoryId: ev.memoryId, choice: "private", reason: "auto-stopped" },
                { type: "announce", text: "Recording stopped. It's saved as private." });
        return { state: { ...s, phase: "saved", memoryId: ev.memoryId, result: "private" }, effects: fx };
      }
      if (ev.type === "FAILED") return { state: { ...initial }, effects: fx };
      break;

    case "reviewing":
      if (ev.type === "SKIP_REVIEW" || ev.type === "PLAYBACK_ENDED") {
        // skipping a long playback goes to the SAME question; it never implies a choice
        if (ev.type === "SKIP_REVIEW") fx.push({ type: "stopAudio" });
        const options = optionsFor(ctx);
        fx.push({ type: "ask", options, prompt: promptFor(options) },
                { type: "startTimer", ms: ctx.timeoutMs || DECIDE_TIMEOUT_MS });
        return { state: { ...s, phase: "deciding", options }, effects: fx };
      }
      if (ev.type === "INTERRUPT") {
        fx.push({ type: "stopAudio" }, { type: "commit", memoryId: s.memoryId, choice: "later", reason: "interrupted" });
        return done("interrupted-draft", { draft: true, interrupted: true });
      }
      if (ev.type === "CANCEL") {   // user left during playback: stays private, nothing asked
        fx.push({ type: "stopAudio" }, { type: "commit", memoryId: s.memoryId, choice: "private", reason: "cancelled" });
        return done("private");
      }
      break;

    case "deciding": {
      const close = () => fx.push({ type: "clearTimer" }, { type: "closeAsk" });
      if (ev.type === "CHOOSE") {
        let choice = CHOICES.includes(ev.choice) ? ev.choice : "private";
        if (choice === "share" && !(s.options || []).includes("share")) choice = "private";   // never share where it isn't offered
        close();
        fx.push({ type: "commit", memoryId: s.memoryId, choice, reason: "chosen" },
                { type: "announce", text: choice === "share" ? "Sent for review. It stays private until it's approved."
                  : choice === "later" ? "Kept as a private draft. You can decide later." : "Kept private." });
        return done(choice === "share" ? "pending_review" : choice === "later" ? "draft" : "private",
                    { draft: choice === "later" });
      }
      if (ev.type === "TIMEOUT" || ev.type === "CANCEL") {   // silence is NOT consent
        close();
        fx.push({ type: "commit", memoryId: s.memoryId, choice: "private", reason: ev.type === "TIMEOUT" ? "timeout" : "cancelled" },
                { type: "announce", text: "Kept private." });
        return done("private");
      }
      if (ev.type === "INTERRUPT") {   // stop everything, choose nothing on the user's behalf
        close();
        fx.push({ type: "stopAudio" }, { type: "commit", memoryId: s.memoryId, choice: "later", reason: "interrupted" });
        return done("interrupted-draft", { draft: true, interrupted: true });
      }
      break;
    }

    case "saved":
      if (ev.type === "START") return { state: { ...initial, phase: "recording" }, effects: fx };   // only a fresh, manual start
      break;
  }
  return { state: s, effects: fx };   // anything else is ignored (no auto-resume, no repeat questions)
}

const AfterRecord = { initial, transition, optionsFor, promptFor, DECIDE_TIMEOUT_MS };
if (typeof module !== "undefined" && module.exports) module.exports = AfterRecord;
else window.AfterRecord = AfterRecord;
