// Keeps /live loadable with no network: network first, cached copy when offline. API calls pass straight through.
const CACHE = "meridies-live-v1";
const SHELL = ["/live", "/live.js"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !SHELL.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(u.pathname, copy));
    return r;
  }).catch(() => caches.match(u.pathname)));
});
