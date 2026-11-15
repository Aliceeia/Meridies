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

  // Delivery per sentence: a hazard or warning sounds different from a description.
  //   alert: warnings, and hazard lines (the only sentences that carry a height in metres)
  //   plain: short status lines and help;  calm: everything else
  function styleOf(sentence, kind) {
    if (kind === "warning" || /^(mind the|careful|stop|low )/i.test(sentence) || /\bmetres?\b/i.test(sentence)) return "alert";
    return kind === "short" || kind === "help" ? "plain" : "calm";
  }

  // Delivery that doesn't depend on the TTS model: warnings brisker, descriptions unhurried. Audio playbackRate keeps
  // the pitch (preservesPitch), so this changes pace, not voice.
  // (kept small: time-stretching a natural voice by much makes it sound processed)
  const PACE = { alert: 1.05, plain: 1.0, calm: 1.0, mixed: 1.0 };
  const TTS_RATE = { alert: 1.08, plain: 0.98, calm: 0.9, mixed: 1.0 };
  // one delivery for a whole line: a hazard first and a description after is "mixed"
  function lineStyle(sents, kind) {
    const st = sents.map((s) => styleOf(s, kind));
    if (st.every((x) => x === st[0])) return st[0];
    return st[0] === "alert" ? "mixed" : st.includes("alert") ? "mixed" : st[0];
  }

  const S = {
    LIMITS, clean, shape, vary, seconds, styleOf, lineStyle, PACE,
    useVoice: false,                                   // pages turn on the cached human voice (/api/voice)
    muted: () => false,                                // a page can mute speech (e.g. while the hat warns)
    tone: null,                                        // a page can give oops() its error tone
    lastText: "", lastDone: 0, current: null,
    say(text, { kind = "any", force = false, onend = null, prepared = false } = {}) {
      if (!force && S.muted()) return "";
      const t = shape(text, kind);
      if (!t) return "";
      if (!force && t === S.lastText && S.lastDone && Date.now() - S.lastDone < 20000) return "";   // already heard it
      S.cancel();
      S.seq = (S.seq || 0) + 1;                        // lets a late, slower line know something newer was said
      const tok = { cut: false, audio: null, done: false };
      S.lastText = t; S.lastDone = 0; S.current = tok;
      tok.finish = () => {
        if (tok.done) return; tok.done = true;
        if (!tok.cut) S.lastDone = Date.now();
        if (S.current === tok) S.current = null;
        onend && onend(tok.cut);
      };
      const endTimer = !S.useVoice && setTimeout(tok.finish, 2500 + seconds(t) * 1000 * 1.5);   // some engines never fire "end"
      endTimer?.unref?.();
      if(S.useVoice && typeof Audio !== "undefined") {
        S._sentences(t,kind,tok,prepared);
      } else S._tts(t,tok,kind);
      return t;
    },
    _audioCache: new Map(),
    _audioQueue: [],
    _audioPending: 0,
    _quotaUntil: 0,
    _parts(text,kind){return shape(text,kind).split(/(?<=[.!?])\s+/).filter(Boolean);},
    _audioFor(text,cacheOnly=false){
      if(S._audioCache.has(text))return S._audioCache.get(text);
      const promise=new Promise((resolve,reject)=>{
        S._audioQueue.push({text,resolve,reject,cacheOnly});S._drainAudio();
      });
      S._audioCache.set(text,promise);
      promise.catch(()=>{S._audioCache.delete(text);});
      while(S._audioCache.size>48){S._audioCache.delete(S._audioCache.keys().next().value);}
      return promise;
    },
    _drainAudio(){
      while(S._audioPending<2 && S._audioQueue.length){
        const item=S._audioQueue.shift();S._audioPending++;
        const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),30000);
        fetch(`/api/voice?text=${encodeURIComponent(item.text)}&style=calm&wait=${item.cacheOnly||Date.now()<S._quotaUntil?0:1}&provider=gemini`,{signal:abort.signal})
          .then(async r=>{
            if(r.status===429){
              S._quotaUntil=Date.now()+3600000;
              const error=Error('Gemini speech quota reached. Cached audio still works; new speech is temporarily unavailable.');
              for(const waiting of S._audioQueue.splice(0))waiting.reject(error);
              throw error;
            }
            if(!r.ok)throw Error('Gemini voice is temporarily unavailable.');return r.blob();
          })
          .then(blob=>item.resolve(blob)).catch(item.reject)
          .finally(()=>{clearTimeout(timer);S._audioPending--;S._drainAudio();});
      }
    },
    prefetch(text,kind='any',cacheOnly=false){
      // Warm the first sentence only: bounded cloud work; subsequent speech is fetched on demand.
      const first=S._parts(text,kind)[0];if(first)S._audioFor(first,cacheOnly).catch(()=>{});
    },
    async _sentences(text,kind,tok,prepared=false){
      const parts=S._parts(text,kind);
      const clips=parts.map(part=>S._audioFor(part,prepared));
      clips.forEach(p=>p.catch(()=>{}));
      try{
        for(let i=0;i<clips.length;i++){
          const blob=await clips[i];if(tok.cut)return;
          const url=URL.createObjectURL(blob),a=tok.audio=new Audio(url);
          a.preservesPitch=true;
          await new Promise((resolve,reject)=>{
            a.onplaying=()=>{if(tok.cut){a.pause();resolve();return;}tok.startedAt ||= performance.now();};
            a.onended=resolve;a.onerror=reject;
            tok.releaseClip=()=>{a.pause();resolve();};
            a.play().catch(reject);
          }).finally(()=>{URL.revokeObjectURL(url);tok.releaseClip=null;});
          if(tok.cut)return;
        }
        tok.finish();
      }catch(error){
        if(tok.cut)return;
        tok.cut=true;tok.finish();
        document.dispatchEvent(new CustomEvent('speech-unavailable',{detail:prepared?'This location’s sample voice is not generated yet. The text is available.':error.message || 'Gemini voice is temporarily unavailable.'}));
      }
    },

    // the human voice: one cached clip per sentence, played in order. If any is missing, the whole line uses the
    // browser voice (never two voices in one line); the server makes the missing clips for next time.
    _clips(t, kind, tok) {
      const sents = t.split(/(?<=[.!?…])\s+/).filter(Boolean);
      const whole = lineStyle(sents, kind);
      // asked-for lines (where am I, tell me more, describe, help) wait for their clip to be made (a few seconds);
      // automatic ones (arrivals, warnings) never wait: a miss is queued and the browser voice speaks this once
      // everything except one-clause warnings (all pre-made) streams: first sound in ~1 s even for a brand-new line
      const asked = kind !== "warning";
      const get = (text, style, queue, wait) => {
        const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
        if (ctl) setTimeout(() => ctl.abort(), wait ? 12000 : 3000);
        return fetch(`/api/voice?text=${encodeURIComponent(text)}&style=${style}${queue ? "" : "&queue=0"}${wait ? "&wait=1" : ""}`,
                     ctl ? { signal: ctl.signal } : {})
          .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
      };
      if (asked) return S._stream(`/api/voice?text=${encodeURIComponent(t)}&style=${whole}&wait=1`, t, kind, tok, whole);
      // best: the whole line as ONE clip (natural intonation across sentences); a miss queues it for next time
      get(t, whole, true, false).then((blob) => {
        if (tok.cut) return;
        if (blob) return S._play([blob], [whole], t, kind, tok);
        if (sents.length < 2) return S._tts(t, tok, kind);
        const styles = sents.map((s) => styleOf(s, kind));       // second best: every sentence already cached
        Promise.all(sents.map((s, i) => get(s, styles[i], false, false))).then((blobs) => {
          if (tok.cut) return;
          if (blobs.some((b) => !b)) return S._tts(t, tok, kind);  // never two voices in one line
          S._play(blobs, styles, t, kind, tok);
        });
      });
    },
    // an asked-for line: the audio element plays the server's stream as it arrives (cached clip or made now)
    _stream(url, t, kind, tok, style) {
      const a = (tok.audio = new Audio(url));
      a.preservesPitch = true;
      a.playbackRate = PACE[style] || 1;
      let started = false, fell = false;
      // the error event AND the failed play() both land here: speak the fallback once, not twice
      const fallback = () => { if (!started && !fell && !tok.cut) {
        fell=true;a.pause();a.removeAttribute("src");a.load();tok.cut=true;tok.finish();
        document.dispatchEvent(new CustomEvent("speech-unavailable",{detail:"Gemini voice is unavailable. Please try again shortly."}));
      } };
      a.onplaying = () => { if (fell || tok.cut) return a.pause(); started = true; tok.startedAt = performance.now(); };
      a.onended = () => tok.finish();
      a.onerror = fallback;
      setTimeout(() => { if (!started && !tok.cut) { a.pause(); fallback(); } }, 30000);
      a.play().catch(fallback);
    },
    _play(blobs, styles, t, kind, tok) {
      const sents = blobs.length === 1 ? [t] : t.split(/(?<=[.!?…])\s+/).filter(Boolean);
      let i = 0;
      const next = () => {
        if (tok.cut) return;
        if (i >= blobs.length) return tok.finish();
        const a = (tok.audio = new Audio(URL.createObjectURL(blobs[i])));
        a.preservesPitch = true;
        a.playbackRate = PACE[styles[i]] || 1;
        i++;
        a.onended = () => setTimeout(next, styles[i - 1] === "alert" ? 80 : 160);   // a breath between sentences
        a.onerror = () => S._tts(sents.slice(i - 1).join(" "), tok, kind);
        a.play().catch(() => S._tts(sents.slice(i - 1).join(" "), tok, kind));   // no gesture yet: browser voice
      };
      next();
    },
    _tts(t, tok, kind = "any") {                       // fallback: the browser's own voice
      if (tok.cut) return;
      if (typeof speechSynthesis === "undefined") return tok.finish();
      const u = new SpeechSynthesisUtterance(t);
      u.lang = "en-US";
      const v = S.voice || S.pickVoice();
      if (!v) { tok.cut = true; tok.finish(); return; }
      u.voice = v;
      const first = styleOf(t.split(/(?<=[.!?…])\s+/)[0] || t, kind);
      u.rate = first === "alert" ? 1.08 : 1.0; u.pitch = 1.0; u.volume = 1.0;
      u.onstart = () => { tok.startedAt = performance.now(); };
      u.onend = tok.finish; u.onerror = () => { tok.cut = true; tok.finish(); };
      speechSynthesis.speak(u);
    },
    cancel() {                                         // cut off mid-sentence; it does not count as heard
      const tok = S.current;
      if (tok) { tok.cut = true; if (tok.audio) tok.audio.pause(); tok.releaseClip?.(); }
      if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
      S.current = null;
      S.lastDone = 0;
    },
    oops(why) {
      console.warn("said 'I couldn't get that':", why || new Error().stack.split("\n")[2]);   // where it came from
      if (S.tone) S.tone();
      return S.say("I couldn't get that — try again.", { kind: "short", force: true });
    },
    voice: null,
    pickVoice() {
      if (typeof speechSynthesis === "undefined") return null;
      if(S.voice) return S.voice; // lock the voice for the whole page session
      const vs=speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang));
      const preferences=[/Zira/i,/Samantha/i,/Microsoft (?:Aria|Jenny|Emma|Ava)/i,/Google UK English Female/i];
      for(const pref of preferences){const v=vs.find(v=>pref.test(v.name));if(v)return S.voice=v;}
      const localFemale=speechSynthesis.getVoices().find(v=>/Huihui|Yaoyao/i.test(v.name));
      if(localFemale)return S.voice=localFemale;
      return null; // never silently select a male/default voice

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
