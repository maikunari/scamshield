/* ScamShield service worker: keeps the app shell available offline so the installed PWA opens
 * instantly (and so Android lets it be a share target). API calls are never cached. */
const VERSION = "scamshield-shell-v1";
const SHELL = ["/", "/check/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    // Pages: network first, fall back to the cached shell (ignore share-target query params).
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((cache) => cache.put(url.pathname, copy));
          return res;
        })
        .catch(() => caches.match(url.pathname).then((hit) => hit || caches.match("/check/"))),
    );
    return;
  }

  // Hashed build assets and icons: cache first.
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((res) => {
          if (res.ok && (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/"))) {
            const copy = res.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return res;
        }),
    ),
  );
});
