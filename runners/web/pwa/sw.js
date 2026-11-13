// Frame keeps its interface available offline. Media, playlists, API responses
// and signed source URLs always go directly to the network.
const VERSION = "2026-11-13-frame-7";
// Keep the original cache lineage so existing installations migrate safely.
const PAGE_CACHE = "fleet-shell-" + VERSION;
const ASSET_CACHE = "fleet-assets-" + VERSION;
const PAGE = "/watch", ASSET_LIMIT = 12;
const ICONS = ["/pwa/apple-touch-icon.png", "/pwa/icon-192.png", "/pwa/icon-512.png", "/pwa/icon-512-maskable.png"];
const ownsCache = (key) => key === "fleet-1" || key.startsWith("fleet-shell-") || key.startsWith("fleet-assets-");
const isAsset = (url) => url.pathname === "/manifest.webmanifest" || ICONS.includes(url.pathname) ||
  /^\/gw\/cdn\.jsdelivr\.net\/npm\/hls\.js@\d+\.\d+\.\d+\/dist\/hls\.min\.js$/.test(url.pathname);

async function keep(cacheName, key, response) {
  if (!response || !response.ok || response.status !== 200 || response.type === "opaque" || response.headers.has("Content-Range")) return;
  const copy = response.clone();
  const cache = await caches.open(cacheName);
  await cache.put(key, copy);
  if (cacheName === ASSET_CACHE) {
    const keys = await cache.keys();
    await Promise.all(keys.slice(0, Math.max(0, keys.length - ASSET_LIMIT)).map((old) => cache.delete(old)));
  }
}
self.addEventListener("install", (event) => {
  // A new worker waits for an explicit update action; it never reloads playback.
  event.waitUntil(Promise.all([PAGE, "/manifest.webmanifest", ...ICONS.map((path) => path + "?v=4")].map(async (path) => {
    try {
      const response = await fetch(path, {cache: "reload"});
      await keep(path === PAGE ? PAGE_CACHE : ASSET_CACHE, path, response);
    } catch (_) { /* An existing installation remains usable if one fetch fails. */ }
  })));
});
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys(), current = await caches.open(PAGE_CACHE);
    // A failed install-time fetch must not throw away the last usable offline shell.
    if (!await current.match(PAGE)) {
      for (const key of keys.slice().reverse().filter((key) => key !== PAGE_CACHE && (key === "fleet-1" || key.startsWith("fleet-shell-")))) {
        const previous = await (await caches.open(key)).match(PAGE);
        if (previous) { await keep(PAGE_CACHE, PAGE, previous); break; }
      }
    }
    await Promise.all(keys.filter((key) => ownsCache(key) && key !== PAGE_CACHE && key !== ASSET_CACHE).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "FLEET_ACTIVATE_UPDATE") event.waitUntil(self.skipWaiting());
});
const offlinePage = () => new Response('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b0d10"><title>Frame · Offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d10;color:#f3f5f7;font:16px/1.6 system-ui,sans-serif}main{max-width:28rem;padding:32px}p{color:#b3bbc5}a{display:inline-block;margin-top:16px;padding:12px 20px;border-radius:12px;background:#a79bcc;color:#0b0d10;text-decoration:none;font-weight:650}a:focus-visible{outline:2px solid #b8acd9;outline-offset:4px}</style><main><h1>You’re offline</h1><p>Reconnect to browse and play. Your saved library and watch progress stay on this device.</p><a href="/watch">Try again</a></main></html>', {status: 200, headers: {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}});
async function pageResponse(event) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(event.request, {cache: "no-store", signal: controller.signal});
    if (response.ok) {
      event.waitUntil(keep(PAGE_CACHE, PAGE, response).catch(() => {}));
      return response;
    }
    const cached = await (await caches.open(PAGE_CACHE)).match(PAGE);
    return cached || response;
  } catch (_) {
    return await (await caches.open(PAGE_CACHE)).match(PAGE) || offlinePage();
  } finally { clearTimeout(timer); }
}
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== "GET" || event.request.headers.has("Range")) return;
  if (url.pathname === PAGE || url.pathname === PAGE + "/") event.respondWith(pageResponse(event));
  else if (isAsset(url)) event.respondWith((async () => {
    const cached = await (await caches.open(ASSET_CACHE)).match(event.request);
    if (cached) return cached;
    const response = await fetch(event.request);
    event.waitUntil(keep(ASSET_CACHE, event.request, response).catch(() => {}));
    return response;
  })());
});
