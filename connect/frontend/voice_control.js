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
    if (has(/ (take off|remove|lift) (the )?blindfold | i want to see | let me see | exit blindfold | exit audio only( mode)? | (turn off|disable) audio only( mode)? | show (the )?screen /)) return { act: "unblind" };
    if (has(/ (?:go |back |return )?(?:back )?(?:to )?(?:the )?(?:home|assistant|meri assistant) /)) return {act:"home"};
    if (has(/ (go back to |open )?(the )?gallery /)) return { act: "gallery" };
    if (has(/ (?:play |listen to |hear |revisit |view |show )?(?:my|previous|old) memor(?:y|ies) /)) return {act:"suggestion", kind:"own"};
    if (has(/ (?:play |listen to |hear )?(?:shared|friends|friend's|a friend's) memor(?:y|ies|y audio) /)) return {act:"suggestion", kind:"shared"};
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
  let lastTarget = null, motionSeq = 0;

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
    const ticket = motionSeq;
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
      if (!window.voiceRoute || ticket !== motionSeq) return;                  // "stop" ends the route
      goToSweep(i);
      await arrived();
      await sleep(250);
    }
    if (ticket !== motionSeq) return;
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
    const ticket = motionSeq;
    for (let k = 0; k < Math.min(n, 10); k++) {
      if (ticket !== motionSeq) return;
      if (state.target) await arrived();
      if (ticket !== motionSeq) return;
      const before = state.walker.sweep;
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: dir > 0 ? "ArrowUp" : "ArrowDown", bubbles: true }));
      await sleep(60);
      await arrived();
      if (state.walker.sweep === before) break;          // couldn't go on (the toast already said why)
    }
  }
  const HELP = "You can say: go forward or back, turn left, right, or around. Take me to the kitchen, or to the " +
               "nearest memory. Where's the bedroom? Where am I, or tell me more. Play, stop, record, or repeat. " +
               "To record, say leave a memory, then stop recording when finished. Say keep it private or decide later when asked. Say exit audio-only mode to show the screen.";

  async function handle(text) {
    const c = parse(text);
    if (!c) {
      if (text.split(" ").length <= 6) say(Speech.vary("huh", ["Didn't catch that. Say help for what I can do.", "Sorry? Say help to hear the options."]));
      return null;
    }
    if (["quiet", "turn", "step", "go-to", "go-last", "suggestion", "unblind"].includes(c.act)) {
      motionSeq++; window.voiceRoute = false; state.target = null;
    }
    earcon("tap");
    switch (c.act) {
      case "quiet":
        window.voiceRoute = false;
        if (typeof moreController !== "undefined" && moreController) { moreController.abort(); moreController = null; stopThinking(); }
        Speech.cancel(); stopVoice();
        if (window.afterRecordState?.phase === "reviewing") document.body.dispatchEvent(new KeyboardEvent("keydown", {key:"Enter", bubbles:true}));
        break;
      case "help": say(HELP, { kind: "help" }); break;
      case "repeat": if (Speech.lastText) say(Speech.lastText, { kind: "detail" }); break;
      case "unblind": setBlind(false); break;
      case "home": location.href="/"; break;
      case "gallery": location.href = "/gallery"; break;
      case "record": if (!rec.mr) $("rec").click(); break;
      case "stopRecording": if (rec.mr) rec.stop("user"); break;
      case "answer": {
        const b = document.querySelector(`#decide:not([hidden]) .opt[data-choice="${c.choice}"]:not([hidden])`);
        if (b) b.click(); else say("There's nothing to answer right now.");
        break;
      }
      case "where": $("whereami").click(); break;
      case "more": /look closer|fresh description/.test(text.toLowerCase()) ? freshSceneDetail() : tellMeMore(); break;
      case "suggestion": {
        const m=MeriContext.choose(c.kind);
        if(!m){say(c.kind==="own"?"You have no saved memories in this place yet.":"No recordings from your circle are available here.");break;}
        if(relative(m).dist<=NEAR_M){Speech.cancel();await playVoice(m);break;}
        const sweep=sweepAt(m.sweep);
        if(!sweep){say("That memory is farther away. Open the gallery to revisit it.");break;}
        await goTo({sweep,label:c.kind==="own"?"your previous memory":"the shared memory",memory:m});
        break;
      }
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

  // One session owns its microphone, recognizer and pending requests.
  const busy = () => !!(Speech.current || play.src || speechSynthesis.speaking);
  let active = false, mode = null, generation = 0, mic = null, processor = null, source = null, sink = null;
  let recognizer = null, recognizing = false, restarting = false, serverPending = false;
  let lastBrowserWords = 0, mutedUntil = 0, controller = null, queued = null;
  VC.status = { active:false, listening:false, awake:false, level:0, processing:false, error:null, lastCommand:null };
  VC.setAwake = on => status({awake:on});
  VC.recordingStream = () => mic && mic.getAudioTracks().some(t=>t.readyState === "live") ? mic.clone() : null;
  const status = (patch) => {
    Object.assign(VC.status, patch);
    document.dispatchEvent(new CustomEvent('voice-control-status', {detail:{...VC.status}}));
  };
  function heard(text, restricted = false) {
    if (!active || !text?.trim()) return;
    const wake = /\b(?:hi|hey|hello)\s+(?:meri|mary|merry|marie)\b/i;
    const addressed = wake.test(text);
    if (addressed) {
      text = text.replace(wake, "").replace(/^[ ,.!:]+/, "");
      setBlind(true);
      if (!text.trim()) return;
      clearTimeout(blind.welcomeTimer); Speech.cancel(); stopVoice();
    } else if (!VC.status.awake) return;
    if (/^(?:goodbye|bye)\s+(meri|mary|merry|marie)[.!]?$/i.test(text.trim())) { setBlind(false); return; }
    if (/^(?:turn off|disable|stop) (?:the )?(?:microphone|mic)[.!]?$/i.test(text.trim())) { setBlind(false); setActive(false); return; }
    if (/^retry (?:save|saving)[.!]?$/i.test(text.trim())) { retryPendingMemory(); return; }
    let command = parse(text);
    // While recording, narration belongs to the memory, never to navigation.
    const simple = text.toLowerCase().replace(/[^a-z ]/g,'').trim();
    if (rec.mr) {
      const stop = simple.match(/(stop recording|finish recording|done recording|im done|i am done)$/);
      if (!stop) return;
      text = stop[1]; command = {act:"stopRecording"};
    }
    // During playback only an exact stop phrase can interrupt; other speech may be echo.
    if (!rec.mr && !addressed && (restricted || busy()) && !/^(stop|pause|be quiet|stop speaking)$/.test(simple) && command?.act !== "answer" && command?.act !== "suggestion") return;
    if (!command) return;
    status({lastCommand:text.trim(), error:null});
    $('blind').firstElementChild.textContent = `Heard: ${text.trim()}`;
    handle(text.trim()).catch(() => say("I couldn't do that. Please try again."));
  }
  function ensureBrowser() {
    if (!active || mode !== 'browser' || recognizing || restarting || !recognizer) return;
    try { recognizing = true; recognizer.start(); }
    catch (_) { recognizing = false; }
  }
  function startBrowser(session) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { mode = 'server'; return; }
    mode = 'browser';
    const r = recognizer = new SR();
    r.lang='en-US'; r.continuous=true; r.interimResults=false; r.maxAlternatives=1;
    r.onresult = e => {
      if (!active || generation !== session) return;
      for (let i=e.resultIndex;i<e.results.length;i++) if (e.results[i].isFinal) {
        lastBrowserWords=performance.now(); heard(e.results[i][0].transcript);
      }
    };
    r.onerror = e => {
      if (generation !== session || !active) return;
      if (['network','service-not-allowed','language-not-supported','not-allowed','audio-capture'].includes(e.error)) {
        mode='server'; recognizing=false;
        try { r.abort(); } catch (_) {}
        status({error:e.error === 'not-allowed' ? 'Browser recognition unavailable; using microphone transcription.' : null});
      }
    };
    r.onend = () => {
      if (generation !== session) return;
      recognizing=false;
      restarting=true;
      setTimeout(() => { if (generation === session) { restarting=false; ensureBrowser(); } },350);
    };
    ensureBrowser();
  }
  function wav(chunks, rate) {
    const n=chunks.reduce((sum,c)=>sum+c.length,0), all=new Float32Array(n);
    let offset=0; for(const c of chunks){all.set(c,offset);offset+=c.length;}
    const count=Math.floor(n*16000/rate), b=new DataView(new ArrayBuffer(44+count*2));
    const word=(o,s)=>[...s].forEach((c,i)=>b.setUint8(o+i,c.charCodeAt(0)));
    word(0,'RIFF'); b.setUint32(4,36+count*2,true); word(8,'WAVE'); word(12,'fmt '); b.setUint32(16,16,true);
    b.setUint16(20,1,true); b.setUint16(22,1,true); b.setUint32(24,16000,true); b.setUint32(28,32000,true);
    b.setUint16(32,2,true);b.setUint16(34,16,true);word(36,'data');b.setUint32(40,count*2,true);
    for(let i=0;i<count;i++)b.setInt16(44+i*2,Math.max(-1,Math.min(1,all[Math.floor(i*rate/16000)]))*32767,true);
    return new Blob([b],{type:'audio/wav'});
  }
  async function send(chunks, rate, session, restricted, saidAt) {
    if (!active || session !== generation || lastBrowserWords >= saidAt) return;
    if (serverPending) { queued = [chunks,rate,session,restricted,saidAt]; return; }
    serverPending=true; status({processing:true});
    const abort=controller=new AbortController();
    const timeout=setTimeout(()=>abort.abort(),10000);
    try {
      const response=await fetch('/api/hear',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav(chunks,rate),signal:abort.signal});
      if (!response.ok) throw Error('transcription unavailable');
      const body=await response.json();
      if(active && session===generation && lastBrowserWords<saidAt) heard(body.text,restricted);
    } catch (_) {
      if(active && session===generation) { status({error:'Could not hear that. Please try again.'}); if (!busy() && !rec.mr) say('I could not hear that. Please try again.'); }
    } finally { clearTimeout(timeout); if(session===generation){serverPending=false;controller=null;status({processing:false});const next=queued;queued=null;if(next)send(...next);} }
  }
  async function setActive(on) {
    if (on === active) return;
    active=on;
    const session=++generation;
    status({active:on,listening:false,error:null,awake:false,level:0,processing:false});
    if(!on){
      motionSeq++; window.voiceRoute=false; state.target=null;
      controller?.abort(); controller=null; serverPending=false; queued=null;
      try { recognizer?.abort(); } catch (_) {}
      recognizer=null;recognizing=false;restarting=false;mode=null;
      if(processor){processor.onaudioprocess=null;processor.disconnect();}
      source?.disconnect();sink?.disconnect();processor=source=sink=null;
      mic?.getTracks().forEach(track=>track.stop());mic=null;
      return;
    }
    let stream;
    try { stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}}); }
    catch (_) {
      if(session!==generation)return;
      active=false;status({active:false,listening:false,error:'Microphone permission is required. Allow access and activate audio-only mode again.'});
      $('blind').firstElementChild.textContent=VC.status.error;
      say('Microphone access is blocked. Allow microphone access, then turn audio-only mode on again.');
      return;
    }
    if(!active || generation!==session){stream.getTracks().forEach(t=>t.stop());return;}
    mic=stream;
    const ctx=audio.ctx();
    source=ctx.createMediaStreamSource(mic);processor=ctx.createScriptProcessor(4096,1,1);sink=ctx.createGain();sink.gain.value=0;
    source.connect(processor);processor.connect(sink);sink.connect(ctx.destination);
    const pre=[], segment=[]; let speaking=false, silent=0, restricted=false, lastMeter=0, noise=0.002;
    processor.onaudioprocess=event=>{
      if(!active || generation!==session)return;
      const samples=new Float32Array(event.inputBuffer.getChannelData(0));
      const rms=Math.sqrt(samples.reduce((sum,x)=>sum+x*x,0)/samples.length);
      if(performance.now()-lastMeter>150){lastMeter=performance.now();status({level:Math.min(1,rms*15)});}
      if(!speaking && rms<0.02)noise=noise*0.98+rms*0.02;
      pre.push(samples);if(pre.length>3)pre.shift();
      if(performance.now()<mutedUntil)return;
      if(!speaking){if(rms<Math.max(0.008,noise*2.5))return;speaking=true;restricted=busy();segment.push(...pre);silent=0;}
      else {segment.push(samples);restricted ||= busy();silent=rms<Math.max(0.005,noise*1.8)?silent+1:0;}
      const secs=segment.length*4096/ctx.sampleRate;
      if(silent*4096/ctx.sampleRate>0.7 || secs>6){
        speaking=false;const clip=segment.splice(0),saidAt=performance.now()-secs*1000,limited=restricted;
        mutedUntil=performance.now()+250;
        if(secs>0.4)setTimeout(()=>send(clip,ctx.sampleRate,session,limited,saidAt),mode==='browser'?1000:0);
      }
    };
    lastBrowserWords=0;mutedUntil=performance.now()+500;
    startBrowser(session);status({listening:true,error:null});
  }
  // Browsers can end recognition spontaneously; resume while the session is enabled.
  setInterval(ensureBrowser,750);
  // Listening is explicitly armed by the microphone button; waking never replaces the scene.
  window.addEventListener('pagehide',()=>setActive(false));
  VC.setActive=setActive;
  VC.mode=()=>mode;

})(typeof window !== "undefined" ? window : globalThis);
