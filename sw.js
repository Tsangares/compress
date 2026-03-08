const CACHE_NAME = 'compress-v6';

// Large files that rarely change — cache-first (avoid re-downloading 31MB WASM)
const CACHE_FIRST = [
    '/lib/ffmpeg.js',
    '/lib/814.ffmpeg.js',
    '/lib/util.js',
    '/lib/ffmpeg-core.js',
    '/lib/ffmpeg-core.wasm',
];

// App files — network-first (always get fresh, fall back to cache offline)
const NETWORK_FIRST = [
    '/',
    '/index.html',
    '/style.css',
    '/app.js',
    '/manifest.json',
];

// Install: pre-cache everything
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) =>
            cache.addAll([...CACHE_FIRST, ...NETWORK_FIRST])
        )
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

// Fetch: network-first for app files, cache-first for lib files
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    const path = url.pathname;
    const isCacheFirst = CACHE_FIRST.some((p) => path === p || path.startsWith('/lib/'));

    if (isCacheFirst) {
        // Cache-first: use cached WASM/lib, only fetch if not cached
        event.respondWith(
            caches.match(event.request).then((cached) =>
                cached || fetch(event.request).then((response) => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                })
            )
        );
    } else {
        // Network-first: always try fresh, fall back to cache
        event.respondWith(
            fetch(event.request).then((response) => {
                if (response.ok) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                }
                return response;
            }).catch(() => caches.match(event.request))
        );
    }
});
