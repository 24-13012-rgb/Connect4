// Connect Four service worker.
//
// This is a live, real-time multiplayer game, so we deliberately do NOT try
// to cache pages or game state — that would risk showing stale boards or
// letting someone "play" against a frozen snapshot while offline. All this
// worker does is: (1) cache the static shell (CSS/JS/icons) so repeat loads
// are instant, and (2) show a friendly offline page instead of the browser's
// default error if there's truly no connection when opening the app.

const CACHE_NAME = "connect4-shell-v1";

const STATIC_ASSETS = [
  "/static/style.css",
  "/static/script.js",
  "/static/offline.html",
  "/static/icons/icon-192.png",
  "/static/icons/icon-192-maskable.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-512-maskable.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never intercept moves/joins/etc.

  const url = new URL(req.url);

  // Static assets: cache-first, refreshing the cache in the background.
  if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req)
          .then((res) => {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Page loads: always prefer a fresh copy from the network (game state
  // lives in these pages), only falling back to an offline notice if the
  // network is genuinely unreachable.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }

  // Everything else (e.g. /state/<room>, /move/<room>) is left alone and
  // always goes straight to the network.
});
