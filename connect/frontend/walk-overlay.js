"use strict";
// Keep the existing recorder, spatial audio and server actions; only move their controls.
(() => {
  const stage = $("stage");
  const hud = document.createElement("div");
  hud.id = "walkHud";
  hud.innerHTML = `<header class="walk-top"><div id="walkBrand"></div><div id="walkLocation"></div><div id="walkTools"></div></header>
    <div id="recordDock" aria-label="Recording controls"></div>
    <nav id="askDock" aria-label="Explore by sound"></nav>
    <details id="spokenCaption"><summary>Show spoken response</summary><div id="captionBody"></div></details>
    <section id="memoryPopover" role="region" aria-label="Selected memory" hidden>
      <div class="memory-heading"><span id="memoryLabel">A memory here</span><button id="closeMemory" aria-label="Close memory">×</button></div>
      <div id="memoryActions"></div><p id="memoryFeedback" role="status"></p>
    </section>`;
  stage.appendChild(hud);
  $("walkBrand").append($("toGallery"));
  $("walkBrand").setAttribute("aria-label","Return navigation");
  const homeLink=document.createElement("a");homeLink.href="/";homeLink.textContent="Meri assistant";homeLink.id="toMeri";
  $("walkBrand").append(homeLink);
  $("walkLocation").append($("whereLine"));
  $("walkTools").append($("blindBtn"), $("debug"));
  document.addEventListener("speech-unavailable", e => { _toast(e.detail, 6000); });
  $("blindBtn").textContent = "Meri · Enable microphone";
  $("blindBtn").setAttribute("aria-label", "Enable microphone for Meri. Then say Hi Meri.");
  $("blindBtn").setAttribute("aria-pressed", "false");
  $("blind").setAttribute("role", "dialog");
  $("blind").setAttribute("aria-label", "Audio-only navigation");
  $("blind").setAttribute("aria-modal", "true");
  $("blind").firstElementChild.textContent = "Say go forward, turn left, where am I, tell me more, or leave a memory. Say help for all commands.";
  const listeningStatus = document.createElement("p");
  listeningStatus.id = "audioListeningStatus"; listeningStatus.setAttribute("role", "status");
  const exitAudio = document.createElement("button");
  exitAudio.id = "exitAudioMode"; exitAudio.textContent = "Exit audio-only mode";
  exitAudio.onclick = () => setBlind(false);
  $("blind").append(listeningStatus, exitAudio);
  document.addEventListener("voice-control-status", e => {
    const status = e.detail;
    listeningStatus.textContent = status.error || (status.processing ? "Meri is hearing your words…" : status.listening ? status.awake ? `Meri is listening${status.lastCommand ? ' · Heard: '+status.lastCommand : ''}` : "Microphone on · Say Hi Meri" : status.active ? "Connecting to your microphone…" : "Microphone off");
    $("blindBtn").textContent = status.active ? status.awake ? "Meri · Listening" : "Meri · Say Hi Meri" : "Meri · Enable microphone";
    $("blindBtn").setAttribute("aria-pressed", String(status.active));
    micLevel.value = status.level || 0;
  });
  $("blind").removeAttribute("role"); $("blind").removeAttribute("aria-modal");
  $("blind").hidden = true;
  const meriStatus = document.createElement("div"); meriStatus.id="meriStatus";
  const micLevel = document.createElement("meter"); micLevel.min=0;micLevel.max=1;micLevel.value=0;micLevel.setAttribute("aria-label","Microphone input level");
  meriStatus.append(micLevel, listeningStatus); $("walkTools").append(meriStatus);
  const retrySave = document.createElement("button"); retrySave.id="retrySave";retrySave.textContent="Retry save";retrySave.hidden=true;retrySave.onclick=()=>retryPendingMemory();
  $("recordDock").append(retrySave);
  $("recordDock").append($("rec"), $("recStop"), $("recState"));
  $("askDock").append($("whereami"), $("tellmore"));
  $("captionBody").append($("whereText"), $("tellText"));
  $("markers").removeAttribute("aria-hidden");
  let selected = null, returnTo = null;
  const close = () => { $("memoryPopover").hidden = true; selected = null; returnTo?.focus(); };
  $("closeMemory").onclick = close;
  document.addEventListener("keydown", e => { if (e.key === "Escape" && selected) close(); });

  function openMemory(id, focus = true) {
    const m = memById(id);
    if (!m) return close();
    selected = id;
    if (focus) returnTo = document.activeElement;
    const panel = $("memoryPopover"), actions = $("memoryActions");
    panel.hidden = false;
    $("memoryLabel").textContent = `${describeDir(relative(m))} · ${m.visibility === 'private' ? 'Only you' : m.visibility === 'family' ? 'Family' : 'Community'}`;
    $("memoryFeedback").textContent = "";
    actions.replaceChildren();
    const button = (label, action) => {
      const b = document.createElement("button"); b.textContent = label;
      b.onclick = async () => {
        b.disabled = true;
        try { await action(b); }
        catch (_) { $("memoryFeedback").textContent = "Couldn't do that. Please try again."; Speech.oops(); }
        finally { b.disabled = false; }
      };
      actions.append(b); return b;
    };
    button("▶ Listen", async b => {
      if (play.playingId === id) { stopVoice(); b.textContent = "▶ Listen"; }
      else { Speech.cancel(); await playVoice(memById(id)); b.textContent = play.playingId === id ? "■ Stop" : "▶ Listen"; }
    });
    button("Say it again", () => {
      const current = memById(id), text = current.description?.text || current.transcript?.text;
      stopVoice();
      if (text) speak(text, { kind: "detail", force: true });
      else return playVoice(current);
    });
    if (m.author_id === state.user) {
      const label = document.createElement("label"); label.textContent = "Share with ";
      const select = document.createElement("select"); select.id = "memoryVisibility";
      for (const [value, name] of [["private", "Only me"], ["family", "My circle"], ...(state.plan.building.public ? [["community", "Community (reviewed)"]] : [])]) {
        const opt = document.createElement("option"); opt.value = value; opt.textContent = name; select.append(opt);
      }
      select.value = m.visibility; label.append(select); actions.append(label);
      button("Save sharing", async () => {
        const response = await fetch(`/api/memories/${id}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({viewer:state.user, visibility:select.value})});
        if (!response.ok) throw new Error("sharing failed");
        await loadMemories();
        $("memoryFeedback").textContent = select.value === "community" ? "Requested. Private until reviewed." : "Sharing updated.";
        speak($("memoryFeedback").textContent, {kind:"short", force:true});
      });
    }
    if (focus) actions.querySelector("button")?.focus();
  }

  const originalMarkers = placeMarkers;
  placeMarkers = function () {
    originalMarkers();
    const placed = [];
    for (const dot of $("markers").querySelectorAll(".memdot")) {
      const id = dot.dataset.id, m = memById(id);
      dot.style.width = dot.style.height = `${Math.min(70, parseFloat(dot.style.width))}px`;
      let x = parseFloat(dot.style.left), y = parseFloat(dot.style.top);
      for (let i = 0; i < placed.length; i++) {
        if (Math.hypot(x - placed[i][0], y - placed[i][1]) < 65) x += 76;
      }
      x = Math.max(40, Math.min($("markers").clientWidth - 40, x));
      dot.style.left = `${x}px`;
      placed.push([x, y]);
      dot.setAttribute("role", "button"); dot.tabIndex = 0;
      dot.setAttribute("aria-label", `Open memory ${describeDir(relative(m))}`);
      dot.setAttribute("aria-expanded", String(selected === id));
      dot.onclick = () => { if (stale) return toast("Re-sync the view to align the memories"); openMemory(id); };
      dot.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); dot.click(); } };
    }
  };
  memHooks.push(() => { if (selected) openMemory(selected, false); });
  const cancelDescription = () => {
    if (moreController) moreController.abort();
    moreController = null;
    stopThinking();
  };
  document.addEventListener("recording-started", close);
  document.addEventListener("recording-started", () => {
    Speech.cancel();
    cancelDescription();
    $("whereami").disabled = $("tellmore").disabled = true;
  });
  const endRecording = () => { $("whereami").disabled = $("tellmore").disabled = false; };
  document.addEventListener("recording-finished", endRecording);
  document.addEventListener("recording-failed", endRecording);
  $("building").addEventListener("change", close);
  $("building").addEventListener("change", cancelDescription);
  $("user").addEventListener("change", cancelDescription);
  $("whereami").addEventListener("click", cancelDescription);
  window.addEventListener("resize", () => placeMarkers());
  if (state.ready) placeMarkers();
})();
