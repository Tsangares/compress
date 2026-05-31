const CACHE_NAME = 'compress-v27';
const SHARE_PROBE_CACHE = 'share-probe-v1';

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
    '/icon-192.png',
    '/icon-512.png',
    '/icon-maskable-192.png',
    '/icon-maskable-512.png',
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

// Activate: clean old caches (but keep the share-probe stash so an in-flight
// share-target handoff survives an SW update mid-flight)
self.addEventListener('activate', (event) => {
    const KEEP = new Set([CACHE_NAME, SHARE_PROBE_CACHE]);
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((k) => !KEEP.has(k)).map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

// Fetch: handle share target POST, then normal caching
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    // Handle share target: POST to / with shared media file
    if (event.request.method === 'POST' && url.pathname === '/') {
        event.respondWith(
            (async () => {
                const formData = await event.request.formData();
                // Manifest now uses a single 'media' field, but old WebAPKs
                // minted from the previous manifest version still POST 'video'
                // or 'image' — accept all three so an unrefreshed WebAPK keeps
                // working until the next WebAPK rebuild lands.
                const file = formData.get('media') || formData.get('image') || formData.get('video');
                const isImage = !!file && (file.type || '').startsWith('image/');
                const videoFile = !isImage ? file : null;
                const imageFile = isImage ? file : null;

                // Image branch: stash in cache, redirect to probe page
                // (probe phase — we want to inspect what Google Photos actually
                // hands us before committing to the wasm-vips pipeline)
                if (imageFile && imageFile.size > 0) {
                    const id = (self.crypto && self.crypto.randomUUID)
                        ? self.crypto.randomUUID().replace(/-/g, '').slice(0, 16)
                        : Math.random().toString(36).slice(2, 18);
                    const cache = await caches.open(SHARE_PROBE_CACHE);
                    const headers = new Headers({
                        'Content-Type': imageFile.type || 'application/octet-stream',
                        'X-Original-Name': encodeURIComponent(imageFile.name || ''),
                        'X-Original-Type': imageFile.type || '',
                        'X-Original-Size': String(imageFile.size),
                        'X-Original-LastModified': String(imageFile.lastModified || 0),
                        'X-Form-Field': 'image',
                        'X-Captured-At': String(Date.now()),
                    });
                    await cache.put(
                        new Request('/__share-probe/' + id),
                        new Response(imageFile, { headers })
                    );
                    return Response.redirect('/photos/?id=' + id, 303);
                }

                // Video branch: stash in cache + redirect with an id, so the
                // page PULLS the file on load. (The old postMessage-then-redirect
                // raced the page's listener — the message often fired before
                // app.js attached it, dropping large shares onto a blank screen.)
                if (videoFile && videoFile.size > 0) {
                    const id = (self.crypto && self.crypto.randomUUID)
                        ? self.crypto.randomUUID().replace(/-/g, '').slice(0, 16)
                        : Math.random().toString(36).slice(2, 18);
                    const cache = await caches.open(SHARE_PROBE_CACHE);
                    const headers = new Headers({
                        'Content-Type': videoFile.type || 'video/mp4',
                        'X-Original-Name': encodeURIComponent(videoFile.name || ''),
                        'X-Original-Type': videoFile.type || '',
                        'X-Original-Size': String(videoFile.size),
                        'X-Captured-At': String(Date.now()),
                    });
                    await cache.put(
                        new Request('/__share-probe/' + id),
                        new Response(videoFile, { headers })
                    );
                    return Response.redirect('/?shared=' + id, 303);
                }

                // Link branch: no file, but TikTok/YouTube/IG/etc. share-sheet
                // sent a URL via title/text/url params. Pull the first URL out
                // and bounce home with ?url= so the page auto-fetches.
                const title = formData.get('title') || '';
                const text = formData.get('text') || '';
                const urlParam = formData.get('url') || '';
                const blob = [urlParam, text, title].filter(Boolean).join(' ');
                const linkMatch = blob.match(/https?:\/\/[^\s<>"']+/);
                if (linkMatch) {
                    return Response.redirect('/?url=' + encodeURIComponent(linkMatch[0]), 303);
                }
                return Response.redirect('/', 303);
            })()
        );
        return;
    }

    // Serve stashed probe payload (only fetchable via /__share-probe/<id>)
    if (url.pathname.startsWith('/__share-probe/')) {
        event.respondWith(
            caches.open(SHARE_PROBE_CACHE).then((cache) =>
                cache.match(event.request).then((r) =>
                    r || new Response('Not found', { status: 404 })
                )
            )
        );
        return;
    }

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
