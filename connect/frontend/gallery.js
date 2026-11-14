"use strict";
// The gallery: places (derived by the server from saved memories) and a timeline of memories in each.
//   /            author view: your own memories, all visibilities, with controls
//   /f/<token>   family view: ONLY the owner's family memories, read-only (the server enforces both)
// Nothing here records where anyone went: places and positions come only from saved memories.

const $ = (id) => document.getElementById(id);
const FAMILY = (location.pathname.match(/^\/f\/([\w-]+)/) || [])[1] || null;
const G = {
  mode: FAMILY ? "family" : "author",
  user: (() => { try { return localStorage.getItem("meridies.user") || "user-a"; } catch (_) { return "user-a"; } })(),
  places: [],
  placeIdx: 0,          // the place in focus (also used by listen mode)
  memIdx: 0,            // the memory in focus within that place
  audio: null,          // the one <audio> element playing
  listeners: [],        // called after every load (listen mode keeps its cursor valid)
};
const auth = () => (FAMILY ? `family=${encodeURIComponent(FAMILY)}` : `viewer=${encodeURIComponent(G.user)}`);
const withAuth = (url) => `${url}${url.includes("?") ? "&" : "?"}${auth()}`;

const VIS = {
  private:   { label: "Private", icon: "🔒", say: "private, only you" },
  family:    { label: "Family", icon: "👪", say: "shared with family" },
  community: { label: "Community", icon: "👥", say: "shared with the community" },
};
function visOf(m) {
  if (m.draft) return { cls: "private", text: "🔒 Draft · private", say: "a private draft" };
  if (m.visibility === "private" && m.share_request && m.share_request.status === "pending_review")
    return { cls: "pending", text: "⏳ Waiting for review · private", say: "private, waiting for review" };
  const v = VIS[m.visibility] || VIS.private;
  return { cls: m.visibility, text: `${v.icon} ${v.label}`, say: v.say };
}

// ---------- words ----------
const plural = (n, one, many) => `${n === 1 ? "One" : n} ${n === 1 ? one : many}`;
function dateHuman(iso, long = false) {
  const d = new Date(iso), day = 864e5;
  const days = Math.floor((new Date(new Date().toDateString()) - new Date(d.toDateString())) / day);
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (days <= 0) return long ? `today at ${time}` : `Today, ${time}`;
  if (days === 1) return long ? `yesterday at ${time}` : `Yesterday, ${time}`;
  const date = d.toLocaleDateString("en-US", { month: "long", day: "numeric", ...(days > 300 ? { year: "numeric" } : {}) });
  return long ? `on ${date}` : date;
}
function transcriptLine(m) {
  const t = m.transcript;
  if (!t || t.status === "pending") return { text: "Transcribing…", quiet: true };
  if (t.status === "failed") return { text: "No transcript yet", quiet: true };
  if (t.no_speech || !t.text) return { text: "No words, just the sound of the place", quiet: true };
  return { text: `“${t.text}”`, quiet: false };
}

// ---------- data ----------
async function load() {
  const r = await fetch(`/api/gallery?${auth()}`);
  const g = await r.json();
  G.places = g.places || [];
  G.placeIdx = Math.min(G.placeIdx, Math.max(0, G.places.length - 1));
  render();
  for (const fn of G.listeners) fn();
  // keep transcripts fresh while the server is still working on them
  clearTimeout(load._t);
  if (G.places.some((p) => p.memories.some((m) => (m.transcript || {}).status === "pending" || !m.description)))
    load._t = setTimeout(load, 6000);
}

// ---------- rendering ----------
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e[k] = v; else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
function coverImg(m, alt) {
  const img = el("img", { class: "cover", loading: "lazy", alt });
  img.src = withAuth(m.cover);
  img.onerror = () => { img.removeAttribute("src"); img.alt = ""; };
  return img;
}

function render() {
  const route = decodeURIComponent((location.hash.match(/^#place=(.+)$/) || [])[1] || "");
  const p = route && G.places.find((x) => x.id === route);
  $("home").hidden = !!p;
  $("place").hidden = !p;
  if (p) { G.placeIdx = G.places.indexOf(p); renderPlace(p); } else renderHome();
}

function renderHome() {
  const n = G.places.length, total = G.places.reduce((s, p) => s + p.memory_count, 0);
  $("homeTitle").textContent = !n ? "" : G.mode === "family"
    ? `${plural(n, "place", "places")} with memories shared with you.`
    : `${plural(n, "place", "places")}, ${total} ${total === 1 ? "memory" : "memories"}. A place appears only where a memory was saved.`;
  const grid = $("places");
  grid.replaceChildren();
  if (!n) {
    grid.append(el("p", { class: "empty" }, G.mode === "family"
      ? "Nothing has been shared with family yet."
      : "No memories yet. Leave one with the hat, or in the 3D walk."));
    return;
  }
  G.places.forEach((p, i) => {
    const latest = p.memories[0];
    const a = el("a", { class: `place${i === G.placeIdx ? " current" : ""}`, href: `#place=${encodeURIComponent(p.id)}`, role: "listitem",
                        "aria-label": `${p.name}. ${plural(p.memory_count, "memory", "memories")}. Most recent ${dateHuman(p.latest_at, true)}.` },
      coverImg(latest, ""),
      el("div", { class: "body" },
        el("div", { class: "name" }, p.name),
        el("div", { class: "bld" }, p.building.name),
        el("div", { class: "meta" }, `${plural(p.memory_count, "memory", "memories")} · ${dateHuman(p.latest_at)}`)));
    grid.append(a);
  });
}

function renderPlace(p) {
  $("placeName").textContent = p.name;
  $("placeBld").textContent = `${p.building.name} · ${plural(p.memory_count, "memory", "memories")}`;
  const tl = $("timeline");
  tl.replaceChildren();
  p.memories.forEach((m, i) => tl.append(memCard(m, i === G.memIdx)));
}

function memCard(m, current) {
  const line = transcriptLine(m), v = visOf(m);
  const card = el("article", { class: `mem${current ? " current" : ""}`, role: "listitem", id: `m-${m.id}` });
  const play = el("button", { class: "play", onclick: () => playMemory(m) }, "▶ Play");
  const toggle = el("button", { class: "showtext", "aria-expanded": "false" }, "▸ show text");
  toggle.onclick = () => {
    const o = card.classList.toggle("open");
    toggle.textContent = o ? "▾ hide text" : "▸ show text";
    toggle.setAttribute("aria-expanded", String(o));
  };
  const text = el("div", { class: "text" });
  if (m.transcript && m.transcript.text) text.append(el("h4", {}, "What was said"), el("p", {}, m.transcript.text));
  text.append(el("h4", {}, "The place"),
    el("p", {}, m.description ? m.description.text : "Not described yet."));
  if (m.description) text.append(el("div", { style: "font-size:13px;color:var(--faint)" }, `Described by ${m.description.model}`));

  const controls = el("div", { class: "row" }, play);
  if (G.mode === "author") {
    controls.append(el("a", { href: takeMeThere(m), class: "who", style: "text-decoration:none" }, "🧭 Take me there"));
    controls.append(visSelect(m));
  }
  card.append(
    coverImg(m, m.cover_source === "camera" ? "Photo from the hat camera" : "View of the spot from the 3D scan"),
    el("div", {},
      el("div", { class: "when" }, dateHuman(m.created_at), el("span", { class: `badge ${v.cls}` }, v.text)),
      el("div", { class: `line${line.quiet ? " quiet" : ""}`, title: line.text }, line.text),
      controls, toggle, text));
  return card;
}

function visSelect(m) {
  const s = el("select", { class: "vis", "aria-label": "Who can hear this memory" });
  for (const [k, v] of Object.entries(VIS)) {
    const o = el("option", { value: k }, `${v.icon} ${k === "community" ? "Ask to share with community" : v.label}`);
    s.append(o);
  }
  s.value = m.visibility;
  s.onchange = async () => {
    s.disabled = true;
    const r = await fetch(`/api/memories/${m.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ viewer: G.user, visibility: s.value }) });
    const body = await r.json();
    if (!r.ok) alert(body.error);
    await load();
  };
  return s;
}

// "Take me there": the existing 3D walk, at that spot, with this memory chiming from its own direction
function takeMeThere(m) {
  const q = new URLSearchParams({ building: m.building_id, memory: m.id, viewer: G.user });
  return `/walk?${q}`;
}

// ---------- playback ----------
function stopMemory() {
  if (G.audio) { G.audio.pause(); G.audio = null; }
}
function playMemory(m, { onend } = {}) {
  stopMemory();
  const a = (G.audio = new Audio(withAuth(m.audio)));
  a.onended = () => { if (G.audio === a) G.audio = null; onend && onend(); };
  return a.play().then(() => true, () => { G.audio = null; return false; });
}

// ---------- controls ----------
window.addEventListener("hashchange", () => { G.memIdx = 0; render(); window.scrollTo(0, 0); });
$("backLink").onclick = (e) => { e.preventDefault(); history.pushState(null, "", location.pathname); render(); };
window.addEventListener("popstate", render);
if (FAMILY) {
  $("familyPill").hidden = false;
  document.title = "Meridies Gallery · family";
} else {
  $("authorControls").hidden = false;
  $("user").value = G.user;
  $("user").onchange = (e) => {
    G.user = e.target.value;
    try { localStorage.setItem("meridies.user", G.user); } catch (_) {}
    stopMemory(); load();
  };
}

// ---------- family link: one read-only link per author; it can be turned off at any time ----------
if (!FAMILY) {
  const dlg = $("famDlg");
  const show = (tok) => {
    $("famUrlRow").hidden = !tok;
    $("famUrl").value = tok ? `${location.origin}/f/${tok}` : "";
    $("famMake").hidden = !!tok;
    $("famOff").hidden = !tok;
    const n = G.places.reduce((s, p) => s + p.memories.filter((m) => m.visibility === "family").length, 0);
    $("famNote").lastChild.textContent = `, and nothing else. They can't change anything, record, or open the 3D walk. ` +
      `${n === 1 ? "One memory is" : `${n} memories are`} marked Family right now.`;
  };
  const famCall = async (body) => (await (await fetch("/api/family-link", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ viewer: G.user, ...body }) })).json()).token;
  $("familyBtn").hidden = false;
  $("familyBtn").onclick = async () => {
    show((await (await fetch(`/api/family-link?viewer=${encodeURIComponent(G.user)}`)).json()).token);
    dlg.showModal();
  };
  $("famMake").onclick = async () => show(await famCall({}));
  $("famOff").onclick = async () => show(await famCall({ revoke: true }));
  $("famCopy").onclick = async () => {
    try { await navigator.clipboard.writeText($("famUrl").value); $("famCopy").textContent = "Copied"; }
    catch (_) { $("famUrl").select(); }
    setTimeout(() => ($("famCopy").textContent = "Copy"), 1500);
  };
  $("famClose").onclick = () => dlg.close();
}

window.gallery = { G, load, render, playMemory, stopMemory, takeMeThere, visOf, transcriptLine, dateHuman, plural, auth, withAuth };
load();
