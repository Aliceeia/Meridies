"use strict";
// MeriVerse: the gallery. A timeline of moments, grouped by day, filterable by place.
//   /            your memories (all visibilities) and "Shared with me" (family memories from circles you're in)
//   /f/<token>   the family link: only the owner's family memories, read-only (the server enforces both)
// Places come only from saved memories. Nothing here records where anyone went.

const $ = (id) => document.getElementById(id);
const FAMILY = (location.pathname.match(/^\/f\/([\w-]+)/) || [])[1] || null;
const USERS = { "user-a": "User A", "user-b": "User B" };          // demo accounts (identity is a switch, not a login)
const NAMES = { ...USERS, maya: "Maya", theo: "Theo" };           // people who may share with you (sample friends)
const nameOf = (id) => NAMES[id] || id;
const G = {
  tab: "mine",
  mode: FAMILY ? "family" : "author",   // author | shared | family  (listen.js reads this)
  user: (() => { try { return localStorage.getItem("meridies.user") || "user-a"; } catch (_) { return "user-a"; } })(),
  places: [], owner: null,
  placeIdx: 0, memIdx: 0,               // listen mode's cursor
  audio: null, playingId: null,
  listeners: [],
};
const auth = () => (FAMILY ? `family=${encodeURIComponent(FAMILY)}` : `viewer=${encodeURIComponent(G.user)}`);
const withAuth = (url) => `${url}${url.includes("?") ? "&" : "?"}${auth()}`;

// ---------- words ----------
const NUM = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
const plural = (n, one, many) => `${NUM[n] || n} ${n === 1 ? one : many}`;
const dayKey = (iso) => new Date(iso).toDateString();
function dateHuman(iso, long = false) {
  const d = new Date(iso), days = Math.round((new Date(new Date().toDateString()) - new Date(d.toDateString())) / 864e5);
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const date = d.toLocaleDateString("en-US", { month: "long", day: "numeric", ...(days > 300 ? { year: "numeric" } : {}) });
  if (!long) return days <= 0 ? "Today" : days === 1 ? "Yesterday" : date;
  return days <= 0 ? `today at ${time}` : days === 1 ? `yesterday at ${time}` : `on ${date}`;
}
const timeOf = (iso) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
function visOf(m) {
  if (m.draft) return { cls: "draft", label: "Draft, not decided yet", say: "a private draft, not decided yet" };
  if (m.visibility === "private" && m.share_request && m.share_request.status === "pending_review")
    return { cls: "review", label: "Awaiting review", say: "only you, awaiting review" };
  if (m.visibility === "family") return { cls: "family", label: "Shared with family", say: "shared with family" };
  if (m.visibility === "community") return { cls: "everyone", label: "Shared with everyone", say: "shared with everyone who visits" };
  return { cls: "private", label: "Only you", say: "only you", quiet: true };
}
function transcriptLine(m) {
  const t = m.transcript;
  if (!t || t.status === "pending") return { text: "Listening to what was said…", quiet: true };
  if (t.status === "failed") return { text: "A voice recording", quiet: true };
  if (t.no_speech || !t.text) return { text: "No words, just the sound of the place", quiet: true };
  return { text: t.text, quiet: false };
}
const announce = (t) => { const a = $("announcer"); a.textContent = ""; setTimeout(() => (a.textContent = t), 30); };

// ---------- dom ----------
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e[k] = v; else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids) if (k != null && k !== false) e.append(k);
  return e;
}
const ICON = {
  play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 2.8v10.4L13 8z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 3h3v10H4zM9 3h3v10H9z" fill="currentColor"/></svg>',
  there: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8 4.5 10 10 8 9 6 10z" fill="currentColor"/></svg>',
};
const placeOf = (m) => G.places.find((p) => p.memories.some((x) => x.id === m.id));
const memById = (id) => { for (const p of G.places) for (const m of p.memories) if (m.id === id) return m; return null; };
function coverAlt(m, p) {
  if (m.cover_source === "camera") return `Photo from the hat camera, ${p.name}`;
  return p.building.kind === "yard" ? `${p.name}, seen from above` : `${p.name}, seen from where the memory was left`;
}
const coverNote = (m, p) => m.cover_source === "camera" ? "Seen by the hat camera"
  : p.building.kind === "yard" ? "From above, by drone" : "From the 3D scan, facing where it was left";

// ---------- data ----------
let lastJson = "";
async function load() {
  const scope = G.tab === "shared" && !FAMILY ? "&scope=shared" : "";
  const r = await fetch(`/api/gallery?${auth()}${scope}`);
  const g = await r.json();
  const json = JSON.stringify(g);
  G.mode = g.mode; G.owner = g.owner || null;
  if (json !== lastJson) {
    lastJson = json;
    G.places = g.places || [];
    G.placeIdx = Math.min(G.placeIdx, Math.max(0, G.places.length - 1));
    render();
  }
  for (const fn of G.listeners) fn();
  clearTimeout(load._t);
  if (G.places.some((p) => p.memories.some((m) => (m.transcript || {}).status === "pending" || !m.description)))
    load._t = setTimeout(load, 6000);
}

// ---------- render ----------
const route = () => decodeURIComponent((location.hash.match(/^#place=(.+)$/) || [])[1] || "");
function render() {
  // a redraw must never drop keyboard / screen-reader focus: remember which control of which memory had it
  const a = document.activeElement, row = a && a.closest && a.closest(".entry");
  const keep = row ? { id: row.id, cls: a.classList[0] } : null;
  draw();
  if (keep) { const back = document.querySelector(`#${keep.id} .${keep.cls}`); if (back) back.focus(); }
}
function draw() {
  const sel = G.places.find((p) => p.id === route()) || null;
  if (sel) G.placeIdx = G.places.indexOf(sel);
  const total = G.places.reduce((s, p) => s + p.memory_count, 0);
  const others = [...new Set(G.places.flatMap((p) => p.memories.map((m) => m.author_id)))].map(nameOf);
  $("pageTitle").innerHTML = "";
  if (G.mode === "author") {
    $("pageTitle").append("Your ", el("em", {}, "memories"));
    $("lede").textContent = total ? `${plural(total, "moment", "moments")}, kept in ${plural(G.places.length, "place", "places").toLowerCase()}.` : "";
  } else {
    $("pageTitle").append("Shared ", el("em", {}, "with you"));
    $("lede").textContent = G.mode === "family"
      ? `Moments ${nameOf(G.owner)} chose to share with family.`
      : total ? `From ${others.join(" and ")}, who ${others.length === 1 ? "keeps" : "keep"} you in their circle.` : "";
  }
  renderPlaces(sel);
  renderTimeline(sel);
}

function renderPlaces(sel) {
  const nav = $("places");
  nav.replaceChildren();
  nav.hidden = G.places.length < 2;
  const chip = (label, p) => el("button", {
    class: `chip${p ? "" : " all"}`, "aria-pressed": String(p ? sel === p : !sel),
    onclick: () => { location.hash = p ? `place=${encodeURIComponent(p.id)}` : ""; if (!p) history.replaceState(null, "", location.pathname); render(); },
  }, p ? el("img", { src: withAuth(p.memories[0].cover), alt: "" }) : null, label,
     p ? el("span", { class: "n" }, String(p.memory_count)) : null);
  nav.append(chip("All places", null));
  for (const p of G.places) {
    const c = chip(p.name, p);
    c.setAttribute("aria-label", `${p.name}, ${p.building.name}. ${plural(p.memory_count, "memory", "memories")}.`);
    nav.append(c);
  }
}

function renderTimeline(sel) {
  const tl = $("timeline");
  tl.replaceChildren();
  const mems = (sel ? [sel] : G.places).flatMap((p) => p.memories).sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (!mems.length) {
    const [a, b] = G.mode === "author"
      ? ["Nothing kept here yet.", "Leave a memory with the hat, or in the 3D space, and it will appear here, in the place you left it."]
      : G.mode === "shared" ? ["Nothing shared with you yet.", "When someone keeps you in their circle and shares a memory with family, it appears here."]
      : ["Nothing shared yet.", "When something is shared with family, it will appear here."];
    tl.append(el("div", { class: "empty" }, el("p", {}, a), el("p", {}, b)));
    return;
  }
  const days = [];
  for (const m of mems) {
    const k = dayKey(m.created_at);
    if (!days.length || days[days.length - 1].k !== k) days.push({ k, mems: [] });
    days[days.length - 1].mems.push(m);
  }
  days.forEach((d, i) => {
    const iso = d.mems[0].created_at, rel = dateHuman(iso);
    const wd = new Date(iso).toLocaleDateString("en-US", { weekday: "long" });
    const hid = `day-${i}`;
    const h = el("h2", { class: "day-h", id: hid }, rel, rel === "Today" || rel === "Yesterday" ? null : el("span", { class: "wd" }, wd));
    const ol = el("ol", { class: "entries", "aria-labelledby": hid });
    for (const m of d.mems) ol.append(entry(m));
    tl.append(el("section", { class: "day", style: `--i:${i}` }, h, ol));
  });
}

function entry(m) {
  const p = placeOf(m), v = visOf(m), line = transcriptLine(m);
  const open = (e) => openDetail(m, e.currentTarget);
  const playBtn = el("button", { class: "play", "data-play": m.id, onclick: () => togglePlay(m) });
  setPlayIcon(playBtn, m);
  const there = G.mode !== "family"
    ? el("a", { class: "link", href: takeMeThere(m), html: `${ICON.there} Take me there`,
                "aria-label": `Take me there: open the 3D space at ${p.name}` }) : null;
  return el("li", { class: `entry${G.playingId === m.id ? " playing" : ""}`, id: `m-${m.id}` },
    el("span", { class: "node", "aria-hidden": "true" }),
    el("button", { class: "thumb-btn", onclick: open, "aria-label": `Open memory from ${p.name}, ${dateHuman(m.created_at, true)}` },
      el("img", { src: withAuth(m.cover), alt: coverAlt(m, p), loading: "lazy" })),
    el("div", { class: "body" },
      el("p", { class: "meta" },
        el("time", { datetime: m.created_at }, timeOf(m.created_at)), el("span", { class: "dot", "aria-hidden": "true" }),
        el("span", {}, `${p.name}, ${shortBuilding(p.building.name)}`),
        v.quiet ? el("span", { class: "sr" }, `. ${v.label}.`) : el("span", { class: `state ${v.cls}` }, v.label),
        G.mode === "shared" ? el("span", {}, `from ${nameOf(m.author_id)}`) : null),
      el("button", { class: `excerpt${line.quiet ? " quiet" : ""}`, onclick: open, "aria-label": `Open memory: ${line.text}` },
        line.quiet ? line.text : `“${line.text}”`),
      el("div", { class: "row" }, playBtn,
        el("div", { class: "progress live", "aria-hidden": "true" }, el("i", { "data-bar": m.id })),
        el("span", { class: "time live", "data-time": m.id }), there)));
}
const shortBuilding = (n) => n.replace(/ foyer, Princeton$/, ", Princeton");

// ---------- playback: one voice at a time ----------
function setPlayIcon(btn, m) {
  const on = G.playingId === m.id;
  btn.innerHTML = on ? ICON.pause : ICON.play;
  const p = placeOf(m);
  btn.setAttribute("aria-label", `${on ? "Pause" : "Play"} the memory from ${p ? p.name : "here"}, ${dateHuman(m.created_at, true)}`);
}
function syncPlaying() {
  for (const b of document.querySelectorAll("[data-play]")) { const m = memById(b.dataset.play); if (m) setPlayIcon(b, m); }
  for (const e of document.querySelectorAll(".entry")) e.classList.toggle("playing", e.id === `m-${G.playingId}`);
  const d = $("detail");
  if (d.open && d.mem) setPlayIcon($("dPlay"), d.mem);
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function progress(id, t, dur) {
  // recorded clips often report no duration (Infinity): then show the time elapsed and a gently filling line
  const known = Number.isFinite(dur) && dur > 0;
  const pct = known ? `${Math.min(100, (100 * t) / dur)}%` : t ? `${Math.min(92, 100 * (1 - Math.exp(-t / 8)))}%` : "0%";
  const txt = !t && !known ? "" : known ? `${mmss(t)} / ${mmss(dur)}` : mmss(t);
  for (const b of document.querySelectorAll(`[data-bar="${id}"]`)) b.style.width = pct;
  for (const s of document.querySelectorAll(`[data-time="${id}"]`)) s.textContent = txt;
  const d = $("detail");
  if (d.open && d.mem && d.mem.id === id) { $("dBar").style.width = pct; $("dTime").textContent = txt; }
}
function stopMemory() {
  if (!G.audio) return;
  const id = G.playingId;
  G.audio.pause(); G.audio = null; G.playingId = null;
  progress(id, 0, 0);
  syncPlaying();
}
function playMemory(m, { onend } = {}) {
  stopMemory();
  const a = (G.audio = new Audio(withAuth(m.audio)));
  G.playingId = m.id;
  a.ontimeupdate = () => progress(m.id, a.currentTime, a.duration);
  a.onended = () => {
    if (G.audio !== a) return;
    G.audio = null; G.playingId = null; progress(m.id, 0, 0); syncPlaying();
    onend && onend();
  };
  syncPlaying();
  return a.play().then(() => true, () => { if (G.audio === a) { G.audio = null; G.playingId = null; syncPlaying(); } return false; });
}
async function togglePlay(m) {
  if (G.playingId === m.id) { stopMemory(); announce("Paused."); return; }
  const p = placeOf(m);
  const ok = await playMemory(m, { onend: () => announce("Finished.") });
  announce(ok ? `${p.name}, playing.` : "I couldn't get that — try again.");
}

// "Tell me more": the one live AI call, at the spot where this memory was left. Explicit requests only.
async function tellMeMore(m) {
  const r = await fetch("/api/tell-me-more", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewer: G.user, building: m.building_id, x: m.position.x, y: m.position.y,
                           heading: m.heading_deg ?? null, sweep: m.sweep ?? null }) });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error);
  return body;
}

// "Take me there": the 3D space at that spot, with this memory chiming from its own direction
function takeMeThere(m) {
  return `/walk?${new URLSearchParams({ building: m.building_id, memory: m.id, viewer: G.user })}`;
}

// ---------- one memory, revisited ----------
function openDetail(m, opener) {
  const d = $("detail"), p = placeOf(m), v = visOf(m), line = transcriptLine(m);
  d.mem = m; d.opener = opener;
  $("dImg").src = withAuth(m.cover); $("dImg").alt = coverAlt(m, p);
  $("dCap").textContent = coverNote(m, p);
  $("dTitle").textContent = p.name;
  const when = new Date(m.created_at);
  $("dWhere").textContent = `${when.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })} at ${timeOf(m.created_at)} · ${p.building.name}`;
  $("dPlay").onclick = () => togglePlay(m);
  setPlayIcon($("dPlay"), m);
  progress(m.id, G.audio && G.playingId === m.id ? G.audio.currentTime : 0, G.audio && G.playingId === m.id ? G.audio.duration : 0);
  $("dSaid").textContent = line.quiet ? line.text : `“${line.text}”`;
  $("dSaid").className = line.quiet ? "quiet" : "";
  $("dAbout").textContent = m.description ? m.description.text : "Not described yet.";
  $("dCredit").textContent = [m.description ? `Described from the scan by ${m.description.model}.` : "",
    m.is_sample && G.mode === "author" ? "Sample memory: scripted, read by a computer voice." : ""].filter(Boolean).join(" ");
  $("dMore").hidden = true; $("dMoreText").textContent = "";
  const more = el("button", { class: "btn", onclick: async () => {
    more.disabled = true; more.textContent = "…";
    $("dMore").hidden = false; $("dMoreText").textContent = "…";
    try {
      const r = await tellMeMore(m);
      const text = Speech.shape(`${r.bearings} ${r.text}`, "more");
      $("dMoreText").textContent = text;
      announce(text);
    } catch (e) {
      console.warn("tell me more:", e.message);
      $("dMoreText").textContent = "I couldn't get that — try again.";
      announce("I couldn't get that — try again.");
    }
    more.disabled = false; more.textContent = "Tell me more";
  } }, "Tell me more");
  $("dActions").replaceChildren(...(G.mode === "family" ? [] : [
    el("a", { class: "btn", href: takeMeThere(m), html: `${ICON.there} Take me there` }), more]));
  const mine = G.mode === "author";
  $("dShare").hidden = !mine;
  $("dFrom").hidden = mine;
  if (!mine) { $("dFrom").innerHTML = ""; $("dFrom").append("Shared with you by ", el("b", {}, nameOf(m.author_id)), "."); }
  if (mine) {
    const pending = v.cls === "review";
    for (const r of d.querySelectorAll('input[name="vis"]')) {
      r.checked = pending ? r.value === "community" : r.value === m.visibility;
      r.onchange = () => changeVisibility(m, r.value);
    }
    const pub = !!p.building.public;
    const ev = d.querySelector('input[value="community"]');
    ev.disabled = !pub && m.visibility !== "community";
    $("everyoneNote").textContent = pub ? "A person listens first; until then it stays only yours."
      : "Only in public places, like a campus or a museum. This is a private home.";
    $("dShareNote").textContent = pending ? "Awaiting review. Until a person approves it, only you can hear it."
      : m.draft ? "This is a draft: you haven't decided yet, so it's only yours." : "";
  }
  d.showModal();
  $("dTitle").focus();
}
async function changeVisibility(m, value) {
  const r = await fetch(`/api/memories/${m.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewer: G.user, visibility: value }) });
  const body = await r.json();
  if (!r.ok) { $("dShareNote").textContent = body.error; announce(body.error); return; }
  Object.assign(m, body);
  const v = visOf(m);
  const msg = v.cls === "review" ? "Sent for review. It stays only yours until a person approves it." : `Now ${v.label.toLowerCase()}.`;
  $("dShareNote").textContent = msg;
  announce(msg);
  lastJson = "";
  await load();
  const d = $("detail");
  if (d.open) d.mem = memById(m.id) || m;
}
for (const d of document.querySelectorAll("dialog")) {
  d.addEventListener("click", (e) => { if (e.target === d || e.target.closest("[data-close]")) d.close(); });
  d.addEventListener("close", () => {
    // the list may have been redrawn while the dialog was open (a sharing change): find the same memory again
    let back = d.opener && document.contains(d.opener) ? d.opener : null;
    if (!back && d.mem) back = document.querySelector(`#m-${d.mem.id} .${d.opener && d.opener.classList.contains("thumb-btn") ? "thumb-btn" : "excerpt"}`);
    (back || $("main")).focus();
  });
}

// ---------- tabs ----------
for (const t of document.querySelectorAll("#tabs .tab")) {
  t.onclick = () => {
    if (G.tab === t.dataset.tab) return;
    G.tab = t.dataset.tab;
    for (const x of document.querySelectorAll("#tabs .tab")) x.removeAttribute("aria-current");
    t.setAttribute("aria-current", "page");
    stopMemory(); history.replaceState(null, "", location.pathname);
    G.placeIdx = 0; G.memIdx = 0; lastJson = "";
    load().then(() => announce(G.tab === "shared" ? "Shared with you." : "Your memories."));
  };
}
window.addEventListener("hashchange", () => { G.memIdx = 0; render(); });

// ---------- profile: account, circle, live view, demo data ----------
const menu = $("profileMenu"), pbtn = $("profileBtn");
function setMenu(open, focusBtn = false) {
  menu.hidden = !open; pbtn.setAttribute("aria-expanded", String(open));
  if (open) (menu.querySelector("button, a") || menu).focus();
  else if (focusBtn) pbtn.focus();
}
pbtn.onclick = () => setMenu(menu.hidden);
menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); setMenu(false, true); } });
document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest("#profile")) setMenu(false); });
$("profile").addEventListener("focusout", (e) => { if (!menu.hidden && !$("profile").contains(e.relatedTarget)) setMenu(false); });

function renderAccounts() {
  pbtn.textContent = nameOf(G.user).slice(-1);
  pbtn.setAttribute("aria-label", `Account and settings. Viewing as ${nameOf(G.user)}.`);
  $("accounts").replaceChildren(...Object.entries(USERS).map(([id, n]) =>
    el("button", { class: "item", "aria-pressed": String(id === G.user), onclick: () => switchUser(id) }, n)));
}
function switchUser(id) {
  if (id === G.user) return setMenu(false, true);
  G.user = id;
  try { localStorage.setItem("meridies.user", id); } catch (_) {}
  stopMemory(); lastJson = ""; renderAccounts(); setMenu(false, true); refreshCircle(); samplesSwitch();
  load().then(() => announce(`Viewing as ${nameOf(id)}.`));
}

// MeriCircle
let circleMembers = [];
async function refreshCircle() {
  try { circleMembers = (await (await fetch(`/api/circle?viewer=${encodeURIComponent(G.user)}`)).json()).members || []; } catch (_) {}
  $("circleCount").textContent = circleMembers.length ? plural(circleMembers.length, "person", "people").toLowerCase() : "just you";
}
async function saveCircle(members) {
  const r = await fetch("/api/circle", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewer: G.user, members }) });
  circleMembers = (await r.json()).members || [];
  refreshCircle();
}
async function openCircle(opener) {
  setMenu(false);
  await refreshCircle();
  $("members").replaceChildren(...Object.entries(USERS).filter(([id]) => id !== G.user).map(([id, n]) => {
    const cb = el("input", { type: "checkbox", id: `mem-${id}`, checked: circleMembers.includes(id) });
    cb.onchange = async () => {
      await saveCircle(cb.checked ? [...circleMembers, id] : circleMembers.filter((x) => x !== id));
      announce(cb.checked ? `${n} is in your circle. They'll hear what you share with family.` : `${n} is no longer in your circle.`);
    };
    return el("label", { class: "member", for: `mem-${id}` }, cb, el("span", {}, n));
  }));
  const tok = (await (await fetch(`/api/family-link?viewer=${encodeURIComponent(G.user)}`)).json()).token;
  showLink(tok);
  const d = $("circle");
  d.opener = opener || pbtn;
  d.showModal();
  $("cTitle").focus();
}
function showLink(tok) {
  $("famUrlRow").hidden = !tok;
  $("famUrl").value = tok ? `${location.origin}/f/${tok}` : "";
  $("famMake").hidden = !!tok; $("famOff").hidden = !tok;
  const n = G.mode === "author" ? G.places.reduce((s, p) => s + p.memories.filter((m) => m.visibility === "family").length, 0) : null;
  $("famNote").textContent = "A private link for family who don't use Meridies. It opens only what you've shared with family, and nothing can be changed from it." +
    (n != null ? ` ${n === 1 ? "One memory is" : `${NUM[n] || n} memories are`} shared with family right now.` : "");
}
const famCall = async (body) => (await (await fetch("/api/family-link", { method: "POST",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify({ viewer: G.user, ...body }) })).json()).token;
$("circleOpen").onclick = () => openCircle(pbtn);
$("famMake").onclick = async () => { showLink(await famCall({})); announce("Family link created."); $("famCopy").focus(); };
$("famOff").onclick = async () => { showLink(await famCall({ revoke: true })); announce("Family link turned off. It no longer opens anything."); $("famMake").focus(); };
$("famCopy").onclick = async () => {
  try { await navigator.clipboard.writeText($("famUrl").value); $("famCopy").textContent = "Copied"; announce("Link copied."); }
  catch (_) { $("famUrl").select(); }
  setTimeout(() => ($("famCopy").textContent = "Copy"), 1500);
};

// demo data: sample memories, one switch hides them from every view
async function samplesSwitch(set) {
  let s = await (await fetch("/api/settings")).json();
  if (set !== undefined) {
    if (set && !s.real) {   // never leave the gallery empty in front of someone
      $("samplesBtn").lastChild.textContent = "record a real one first";
      announce("Samples stay until there's a real recording.");
      return;
    }
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ viewer: G.user, hide_samples: set }) });
    s = { ...s, hide_samples: set };
    lastJson = ""; await load();
    announce(set ? "Sample memories hidden." : "Sample memories shown.");
  }
  $("devBox").hidden = !s.samples;
  $("samplesBtn").replaceChildren(s.hide_samples ? "Show sample memories" : "Hide sample memories",
    el("small", {}, s.hide_samples ? `${s.samples} hidden` : `${s.samples} shown`));
  $("samplesBtn").onclick = () => samplesSwitch(!s.hide_samples);
}

// ---------- boot ----------
if (FAMILY) {
  document.title = "Meridies · shared with you";
  $("tabs").hidden = true; $("profile").hidden = true;
  $("brand").href = location.pathname; $("brand").title = "Shared with you";
} else {
  renderAccounts(); refreshCircle(); samplesSwitch();
}
window.gallery = { G, load, render, playMemory, stopMemory, takeMeThere, visOf, transcriptLine, dateHuman, plural, auth, withAuth,
                   openDetail, memById, tellMeMore, nameOf };
load();
