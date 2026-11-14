"use strict";
// Meridies memory layer. Map frame: metres, +X right, +Y up on screen, origin at the house.
// Heading: degrees clockwise from +Y (map "up"). Not north-aligned.

const NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const svgEl = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
const norm360 = (d) => ((d % 360) + 360) % 360;

const state = {
  plan: null,
  walker: { x: 0, y: 0, heading: 0 },
  target: null,
  listeners: [],          // functions called after the walker moves or turns
};
const onWalkerChange = (fn) => state.listeners.push(fn);

function toast(msg, ms = 1600) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ---------- geometry ----------
function insidePoly(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
const walkable = (x, y) => insidePoly(x, y, state.plan.walkable);
function currentZone() {
  const { x, y } = state.walker;
  let best = null;
  for (const z of state.plan.zones) {
    const d = Math.hypot(x - z.center[0], y - z.center[1]);
    if (d <= z.radius_m && (!best || d < best.d)) best = { z, d };
  }
  return best && best.z;
}

// ---------- map ----------
const map = $("map");
let layers = {};
function drawMap() {
  const p = state.plan;
  const [x0, x1, y0, y1] = p.backdrop.extent;
  map.setAttribute("viewBox", `${x0} ${-y1} ${x1 - x0} ${y1 - y0}`); // SVG y = -map y
  svgEl("image", { href: `/data/${p.backdrop.image}`, x: x0, y: -y1, width: x1 - x0, height: y1 - y0,
    preserveAspectRatio: "none", opacity: 0.75 }, map);
  svgEl("polygon", { points: p.walkable.map(([x, y]) => `${x},${-y}`).join(" "),
    fill: "rgba(63,208,224,.14)", stroke: "var(--walk)", "stroke-width": 0.18, "stroke-linejoin": "round" }, map);
  const zg = svgEl("g", {}, map);
  for (const z of p.zones) {
    svgEl("circle", { cx: z.center[0], cy: -z.center[1], r: z.radius_m, fill: "none", stroke: "var(--zone)",
      "stroke-width": 0.12, "stroke-dasharray": "0.5 0.4", opacity: 0.8 }, zg);
    const t = svgEl("text", { x: z.center[0], y: -z.center[1] - z.radius_m - 0.4, fill: "var(--zone)", "font-size": 1.3,
      "font-weight": 700, "text-anchor": "middle", "paint-order": "stroke", stroke: "#000", "stroke-width": 0.3 }, zg);
    t.textContent = z.name.split(",")[0]; // "Yard A"; full name shows in the panel
  }
  // scale bar: 5 m
  const sb = svgEl("g", { transform: `translate(${x0 + 1.5},${-y0 - 1.5})` }, map);
  svgEl("rect", { x: 0, y: -0.25, width: 5, height: 0.5, fill: "#fff" }, sb);
  const st = svgEl("text", { x: 2.5, y: -0.8, fill: "#fff", "font-size": 1.1, "text-anchor": "middle" }, sb);
  st.textContent = "5 m";

  layers.anchors = svgEl("g", {}, map);
  layers.walker = svgEl("g", {}, map);
  // heading cone (60 deg) + body + nose
  svgEl("path", { d: coneD(5, 30), fill: "rgba(255,255,255,.22)", stroke: "rgba(255,255,255,.6)", "stroke-width": 0.08 }, layers.walker);
  svgEl("circle", { r: 0.8, fill: "var(--walker)", stroke: "#000", "stroke-width": 0.15 }, layers.walker);
  svgEl("path", { d: "M 0 -2.1 L 0.7 -0.65 L -0.7 -0.65 Z", fill: "var(--walker)", stroke: "#000", "stroke-width": 0.1 }, layers.walker);
}
function coneD(r, half) {
  const a = rad(half);
  return `M 0 0 L ${r * Math.sin(-a)} ${-r * Math.cos(a)} A ${r} ${r} 0 0 1 ${r * Math.sin(a)} ${-r * Math.cos(a)} Z`;
}
function svgPoint(evt) {
  const pt = map.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const p = pt.matrixTransform(map.getScreenCTM().inverse());
  return { x: p.x, y: -p.y };
}

function renderWalker() {
  const w = state.walker;
  layers.walker.setAttribute("transform", `translate(${w.x},${-w.y}) rotate(${w.heading})`);
  $("hdg").textContent = String(Math.round(norm360(w.heading))).padStart(3, "0");
  $("pos").textContent = `x ${w.x.toFixed(1)} m, y ${w.y.toFixed(1)} m`;
  const z = currentZone();
  $("zone").textContent = z ? z.name : "";
  for (const fn of state.listeners) fn();
}

// ---------- movement ----------
const SPEED = 1.4; // m/s, walking pace
map.addEventListener("click", (e) => {
  const p = svgPoint(e);
  if (!walkable(p.x, p.y)) return toast("Not walkable there");
  state.target = p;
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("button, select, input, textarea")) return;
  const w = state.walker;
  if (e.key === "ArrowLeft") w.heading = norm360(w.heading - 15);
  else if (e.key === "ArrowRight") w.heading = norm360(w.heading + 15);
  else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    const s = e.key === "ArrowUp" ? 0.5 : -0.5;
    const nx = w.x + s * Math.sin(rad(w.heading)), ny = w.y + s * Math.cos(rad(w.heading));
    if (walkable(nx, ny)) { w.x = nx; w.y = ny; } else toast("Edge of walkable area");
  } else return;
  e.preventDefault();
  state.target = null;
  renderWalker();
});
let lastT = performance.now();
function tick(now) {
  const dt = Math.min(0.1, (now - lastT) / 1000);
  lastT = now;
  const w = state.walker, t = state.target;
  if (t) {
    const dx = t.x - w.x, dy = t.y - w.y, d = Math.hypot(dx, dy);
    w.heading = norm360(deg(Math.atan2(dx, dy)));
    const step = SPEED * dt;
    if (d <= step) { w.x = t.x; w.y = t.y; state.target = null; }
    else { w.x += (dx / d) * step; w.y += (dy / d) * step; }
    renderWalker();
  }
  requestAnimationFrame(tick);
}

// ---------- boot ----------
async function boot() {
  state.plan = await (await fetch("/api/floorplan")).json();
  $("bname").textContent = state.plan.building.name;
  $("baddr").textContent = state.plan.building.address;
  drawMap();
  // start at the centre of the first zone (inside the walkable area)
  const z = state.plan.zones[0];
  Object.assign(state.walker, { x: z.center[0], y: z.center[1], heading: 0 });
  renderWalker();
  requestAnimationFrame(tick);
}
boot();
