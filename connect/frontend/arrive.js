"use strict";
// "Take me there": /walk?building=<id>&memory=<id>&viewer=<user> opens the existing 3D walk a few steps from
// the memory, facing it, and lets it chime from its own direction. Uses the walk as it is (loadBuilding,
// relative, chime, speak); changes none of it. Nothing about the visit is stored.
(() => {
  const params = new URLSearchParams(location.search);
  const wantId = params.get("memory");
  const viewer = params.get("viewer") || (() => { try { return localStorage.getItem("meridies.user"); } catch (_) { return null; } })();
  if (viewer) { state.user = viewer; const sel = $("user"); if (sel) sel.value = viewer; }
  let pending = null;           // the memory to announce on the first key or tap (browsers need a gesture for sound)
  let used = false;

  // a spot 1.2-3.8 m away, facing the memory, so its chime comes from ahead; else stand on it
  function standNear(m) {
    const target = m.position, w = state.walker;
    const face = (x, y) => norm360(deg(Math.atan2(target.x - x, target.y - y)));
    if (isInterior()) {
      const own = sweepAt(m.sweep) || state.plan.sweeps.reduce((b, s) =>
        Math.hypot(s.position.x - target.x, s.position.y - target.y) < Math.hypot(b.position.x - target.x, b.position.y - target.y) ? s : b);
      const cand = (own.neighbours || []).map(sweepAt).filter(Boolean)
        .filter((s) => (s.floor ?? 0) === (own.floor ?? 0))
        .map((s) => ({ s, d: Math.hypot(s.position.x - target.x, s.position.y - target.y) }))
        .filter(({ d }) => d >= 1.2 && d <= 3.8)
        .sort((a, b) => Math.abs(a.d - 2.5) - Math.abs(b.d - 2.5))[0];
      const s = cand ? cand.s : own;
      Object.assign(w, { x: s.position.x, y: s.position.y, sweep: s.index,
                         heading: cand ? face(s.position.x, s.position.y) : (m.heading_deg || 0) });
    } else {
      const h = rad(m.heading_deg || 0), bx = target.x - 2.5 * Math.sin(h), by = target.y - 2.5 * Math.cos(h);
      const ok = walkable(bx, by);
      Object.assign(w, { x: ok ? bx : target.x, y: ok ? by : target.y, heading: ok ? face(bx, by) : (m.heading_deg || 0) });
    }
    state.target = null;
    play.near = null; play.armed.clear();
    renderWalker();
  }

  function announce() {
    const m = pending && memById(pending);
    pending = null;
    $("arrive") && $("arrive").remove();
    if (!m) return;
    audio.ctx();
    play.near = m.id; play.armed.add(m.id);
    chime(m);
    updateNearUI();
    setTimeout(() => speak(`Here. The memory is ${describeDir(relative(m))}. Enter to listen. G for the gallery.`), 900);
  }

  const _load = loadBuilding;
  loadBuilding = async function (bid) {               // eslint-disable-line no-func-assign
    await _load(bid);
    if (!wantId || used) return;                       // only the first load; switching buildings later is normal
    used = true;
    const m = memById(wantId);
    if (!m) return toast("That memory isn't here, or isn't yours to hear");
    standNear(m);
    pending = m.id;
    const b = document.createElement("div");
    b.id = "arrive";
    b.setAttribute("role", "status");
    b.style.cssText = "position:fixed;left:50%;top:40%;transform:translate(-50%,-50%);z-index:1200;background:rgba(20,17,15,.92);" +
      "border:1px solid var(--glow);border-radius:18px;padding:22px 28px;font-size:22px;text-align:center";
    b.textContent = "Press any key, or tap, to hear where the memory is.";
    document.body.append(b);
  };

  const first = (e) => {
    if (!pending) return;
    e.preventDefault(); e.stopImmediatePropagation();
    announce();
  };
  document.addEventListener("keydown", first, true);
  document.addEventListener("pointerdown", first, true);

  // G: back to the gallery (not while recording or answering the after-recording question)
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("select, input, textarea") || (e.key !== "g" && e.key !== "G")) return;
    if ((typeof rec !== "undefined" && rec.mr) || window.afterRecordBusy) return;
    location.href = "/";
  });
})();
