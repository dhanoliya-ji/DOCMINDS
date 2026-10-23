/**
 * sw.js -- the DocMinds service worker (registered by components/PwaSupport.tsx
 * in production builds only).
 *
 * WHAT IT CACHES, AND WHAT IT NEVER DOES
 * --------------------------------------
 *   * Page navigations: network first. A successful page is kept, so a page
 *     you opened recently still renders offline; with no network and no copy,
 *     /offline.html is shown instead of the browser's error.
 *   * Next's hashed build assets (/_next/static/...) and the icon: cache first.
 *     Their URLs change whenever their content does, so a cached copy is never
 *     stale.
 *   * Everything else -- above all the API, which lives on another origin and
 *     carries private documents and tokens -- is not touched: those requests
 *     go straight to the network as if there were no worker.
 *
 * Bump VERSION to drop every old cache on the next visit.
 */

const VERSION = "v1";
const PAGES = `docminds-pages-${VERSION}`;
const ASSETS = `docminds-assets-${VERSION}`;
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(PAGES)
      .then((cache) => cache.addAll([OFFLINE_URL, "/icon.svg"]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== PAGES && k !== ASSETS).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // the API and anything third-party

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(PAGES).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || caches.match(OFFLINE_URL)))
    );
    return;
  }

  if (url.pathname.startsWith("/_next/static/") || url.pathname === "/icon.svg") {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(ASSETS).then((cache) => cache.put(request, copy));
            }
            return response;
          })
      )
    );
  }
});
