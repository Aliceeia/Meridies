"use strict";
// One voice for every page, with the spoken-output rules built in (acceptance criteria):
//   INTERRUPTIBLE  any key, any tap, any hat button cancels speech at once (capture phase, before the action)
//   LENGTH         per kind: arrival <= 4 sentences / 20 s, warning 1 clause / 2 s, more <= 6 sentences, never > 30 s
//   NO FILLER      "I can see", "it appears", "there seems to be", "in this image", apologies, AI talk: removed
//   SILENCE        the same words, already heard in full in the last 20 s, are not said again (unless forced)
//   VARY           vary(key, forms) never picks the same form twice in a row
//   ERRORS         oops(): "I couldn't get that — try again." Nothing more.
(function (root) {
  const WPS = 2.5;                                       // words per second at rate 0.95
  const LIMITS = {
    arrival: { sentences: 4, seconds: 20 },
    warning: { sentences: 1, seconds: 2 },
    short:   { sentences: 2, seconds: 10 },
    detail:  { sentences: 5, seconds: 30 },
    more:    { sentences: 6, seconds: 30 },
    help:    { sentences: 8, seconds: 30 },
    any:     { sentences: 6, seconds: 30 },
  };
  const FILLER = [
    [/\b(?:I can see|I see|we can see|you can see)\s+(?:that\s+)?/gi, ""],
    [/\bit (?:appears|seems|looks like)(?: that)?\s+/gi, ""],
    [/\bthere (?:seems|appears) to be\b/gi, "there's"],
    [/\b(?:in|from) (?:this|the) (?:first |second |third )?(?:image|photo|picture|scan|render|view)s?,?\s*/gi, ""],
    [/\bbased on (?:the|this) [^,.]*,?\s*/gi, ""],
    [/\blet me (?:describe|explain)[^.]*\.\s*/gi, ""],
    [/\b(?:I'm|I am) (?:sorry|afraid)[^.]*\.\s*/gi, ""],
    [/\bsorry,?\s*/gi, ""],
    [/\bas an AI[^,.]*[,.]\s*/gi, ""],
    [/\b(?:loading|analy[sz]ing|fetching)\b[^.]*\.\s*/gi, ""],
    [/\b[\d.]+ ?degrees\b/gi, ""],
  ];
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const seconds = (t) => (t.trim() ? t.trim().split(/\s+/).length / WPS : 0);
  function clean(text) {
    let t = ` ${text || ""} `;
    for (const [re, rep] of FILLER) t = t.replace(re, rep);
    return t.replace(/\s{2,}/g, " ").replace(/\s+([,.;])/g, "$1").trim()
      .split(/(?<=[.!?])\s+/).filter(Boolean).map(cap).join(" ");
  }
  function shape(text, kind = "any") {
    const lim = LIMITS[kind] || LIMITS.any;
    const out = [];
    for (const s of clean(text).split(/(?<=[.!?…])\s+/).filter(Boolean)) {
      if (out.length >= lim.sentences || seconds([...out, s].join(" ")) > lim.seconds) break;
      out.push(s);
    }
    if (!out.length && text) {                         // one long sentence: cut it at the time limit
      const w = clean(text).split(/\s+/).slice(0, Math.max(3, Math.floor(lim.seconds * WPS)));
      out.push(w.join(" ").replace(/[,;:]$/, "") + ".");
    }
    return out.join(" ");
  }
  const lastForm = {};
  function vary(key, forms) {
    const idx = forms.map((_, i) => i).filter((i) => i !== lastForm[key]);
    const i = idx[Math.floor(Math.random() * idx.length)] ?? 0;
    lastForm[key] = i;
    return forms[i];
  }

  const S = {
    LIMITS, clean, shape, vary, seconds,
    muted: () => false,                                // a page can mute speech (e.g. while the hat warns)
    tone: null,                                        // a page can give oops() its error tone
    lastText: "", lastDone: 0, current: null,
    say(text, { kind = "any", force = false, onend = null } = {}) {
      if (!force && S.muted()) return "";
      const t = shape(text, kind);
      if (!t) return "";
      if (!force && t === S.lastText && S.lastDone && Date.now() - S.lastDone < 20000) return "";   // already heard it
      if (typeof speechSynthesis === "undefined") return t;
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(t);
      u.lang = "en-US";
      const v = S.voice || S.pickVoice();
      if (v) u.voice = v;
      u.rate = 0.95; u.pitch = 1.03; u.volume = 0.95;
      S.lastText = t; S.lastDone = 0; S.current = u;
      let done = false;
      const finish = () => { if (done) return; done = true; if (!u._cut) S.lastDone = Date.now(); if (S.current === u) S.current = null; onend && onend(u._cut); };
      u.onend = finish; u.onerror = () => { u._cut = true; finish(); };
      if (onend) setTimeout(finish, 1500 + seconds(t) * 1000 * 1.3);   // some engines never fire "end"
      speechSynthesis.speak(u);
      return t;
    },
    cancel() {                                         // cut off mid-sentence; it does not count as heard
      if (S.current) S.current._cut = true;
      if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
      S.lastDone = 0;
    },
    oops() {
      if (S.tone) S.tone();
      return S.say("I couldn't get that — try again.", { kind: "short", force: true });
    },
    voice: null,
    pickVoice() {
      if (typeof speechSynthesis === "undefined") return null;
      const PREFS = [/(Ava|Aria|Jenny|Emma|Michelle|Sonia|Libby|Natasha|Clara).*(Natural|Online)/i, /(Natural|Neural)/i,
                     /Google US English/i, /Google UK English Female/i, /Samantha/i, /Zira/i];
      const vs = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
      for (const re of PREFS) { const v = vs.find((x) => re.test(x.name)); if (v) return (S.voice = v); }
      return (S.voice = vs[0] || null);
    },
  };

  if (typeof document !== "undefined") {
    if (typeof speechSynthesis !== "undefined") speechSynthesis.addEventListener?.("voiceschanged", () => S.pickVoice());
    // INTERRUPTIBLE: someone who knows the room cuts it off. Runs before any page handler.
    const cut = (e) => { if (e.repeat) return; if (S.current || (typeof speechSynthesis !== "undefined" && speechSynthesis.speaking)) S.cancel(); };
    document.addEventListener("keydown", cut, true);
    document.addEventListener("pointerdown", cut, true);
  }
  root.Speech = S;
  if (typeof module !== "undefined" && module.exports) module.exports = S;
})(typeof window !== "undefined" ? window : globalThis);
