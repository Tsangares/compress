const CACHE_NAME = 'compress-v1';
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/style.css',
    '/app.js',
    '/manifest.json',
];

// Install: cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

// Fetch: network-first for CDN (ffmpeg), cache-first for local assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // CDN resources (ffmpeg wasm files) - cache after first fetch
    if (url.hostname === 'unpkg.com') {
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(event.request).then((cached) => {
                    if (cached) return cached;
                    return fetch(event.request).then((response) => {
                        if (response.ok) {
                            cache.put(event.request, response.clone());
                        }
                        return response;
                    });
                })
            )
        );
        return;
    }

    // Local assets - cache-first
    if (url.origin === self.location.origin) {
        event.respondWith(
            caches.match(event.request).then((cached) =>
                cached || fetch(event.request)
            )
        );
    }
});
