# Compress

**Shrink your video. FFmpeg runs in your browser, compresses it locally, hands it back.**

A video compression PWA built on FFmpeg compiled to WebAssembly. The compression itself happens in your browser — no upload, no server-side encoding. The ~31 MB FFmpeg engine downloads once on first visit, then it's cached and works offline.

<p align="center">
  <img src="screenshot.png" alt="Compress app screenshot" width="320">
</p>

<p align="center">
  <a href="https://compress.applesauce.chat">compress.applesauce.chat</a>
</p>

---

## What it does

| | |
|---|---|
| **Compress** | H.264 / AAC encoding via libx264 (`ultrafast` preset). Output is a standard `.mp4`. |
| **Trim** | Cut a clip to a start/end range before encoding. Stream copy when possible (fast, no quality loss). |
| **Audio-only** | Strip the video, save the audio as MP3 (128 kbps). Useful for podcasts, voice memos. |
| **Paste a link** | Drop in a TikTok / YouTube / Instagram / X (Twitter) URL. Server fetches with yt-dlp, browser handles compression. |
| **Share for 7 days** | Optionally save the result to a temporary share URL. Auto-previews in iMessage / Discord / Slack via OG tags. Expires after 7 days. |
| **Resume** | If you navigate away mid-session, the loaded video is held in memory — `Continue` button reopens it. |

## Quality presets

The presets target different codec settings via FFmpeg's CRF (constant rate factor) mode. Lower CRF = higher quality + larger file:

| Preset | CRF | Scale | x264 preset | Typical size reduction |
|---|---|---|---|---|
| **High** | 23 | original | ultrafast | ~40–60% |
| **Medium** | 30 | 720p max | ultrafast | ~70–85% |
| **Low** | 34 | 480p max | ultrafast | ~85–95% |
| **&lt; 10 MB** | (computed) | adaptive | ultrafast | targets 9.5 MB |

The `< 10 MB` preset runs a sample encode on a short slice, projects the bitrate needed to hit 9.5 MB, then encodes the full clip. It's slower but more accurate than a fixed CRF for size-targeted output (Discord 10 MB limit, etc.).

## Privacy model

Be specific about what hits the network and what doesn't:

| Action | Network? |
|---|---|
| Compress/trim a file under 50 MB | **No** (usually). The video is processed by FFmpeg.wasm in the page. If the wasm engine can't handle the input (odd codec, memory pressure), it retries on the server — the file is uploaded, processed with native FFmpeg, and swept within ~30 minutes. |
| Compress/trim a file over 50 MB | **Yes.** Too big for the wasm heap — uploaded to the server (`/api/upload` → `/api/compress` or `/api/trim`), processed with native FFmpeg, downloaded back, swept within ~30 minutes. |
| Paste a link | **Yes.** A small backend (yt-dlp on `mat`) downloads the video and serves it back to the browser. The browser then compresses locally. The server keeps the file ~30 minutes. |
| Click "Share" on a result | **Yes.** Uploads the compressed output to a 7-day share URL with OG-tagged thumbnail. Optional — never automatic. |
| Idle on the page | **No.** No analytics, no tracking, no telemetry, no cookies (only the service-worker cache). |

The FFmpeg `.wasm` bundle is the only resource pulled at first load; after that the service worker serves it cache-first.

## Browser support

- **Chrome / Edge** (desktop and mobile): full support
- **Firefox** (desktop): full support
- **Safari** (15+ desktop, 16+ iOS): full support, slower encode (no SIMD threading)
- **In-app browsers** (Instagram, TikTok webview, etc.): often missing `SharedArrayBuffer` — falls back to single-threaded mode (still works, just slower)

The bundled FFmpeg core is the single-threaded build, which works without `SharedArrayBuffer` — production currently serves no COOP/COEP headers and runs fine. Adding those headers (`Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`) is only needed if the multi-threaded core is ever adopted; the local test server (`tests/serve.py`) already sends them.

## Performance notes

- Encoding speed is roughly **0.5–2× realtime** on modern phones, **2–5× realtime** on laptops, depending on input resolution and the preset. The `ultrafast` x264 preset trades 10–15% efficiency for a 3–5× speedup over `medium` — this matters a lot in WebAssembly without SIMD.
- A wake lock keeps the device awake during long encodes. The app fires a notification when done if you've granted permission.
- Memory ceiling is the WASM 4 GB limit. Practical max input is ~1.5 GB for a 1080p source; bigger clips will fail with an out-of-memory.

## Tech stack

| Component | Technology |
|---|---|
| Compression | [FFmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm) v0.12 — H.264 via libx264 |
| URL fetch | [yt-dlp](https://github.com/yt-dlp/yt-dlp) (separate service, runs server-side) |
| Frontend | Vanilla JS, no frameworks, no build step |
| Styling | Custom CSS — DM Serif Display + Space Mono + Space Grotesk |
| PWA | Service worker (cache-first for WASM, network-first for HTML/JS) |
| Hosting | Static files behind Caddy at [compress.applesauce.chat](https://compress.applesauce.chat) |

## Run locally

```bash
git clone https://github.com/Tsangares/compress.git
cd compress
```

Download FFmpeg.wasm UMD files into `lib/`:

```bash
mkdir -p lib
curl -o lib/ffmpeg.js "https://unpkg.com/@ffmpeg/ffmpeg@0.12.10/dist/umd/ffmpeg.js"
curl -o lib/814.ffmpeg.js "https://unpkg.com/@ffmpeg/ffmpeg@0.12.10/dist/umd/814.ffmpeg.js"
curl -o lib/util.js "https://unpkg.com/@ffmpeg/util@0.12.1/dist/umd/util.js"
curl -o lib/ffmpeg-core.js "https://unpkg.com/@ffmpeg/core@0.12.6/dist/umd/ffmpeg-core.js"
curl -o lib/ffmpeg-core.wasm "https://unpkg.com/@ffmpeg/core@0.12.6/dist/umd/ffmpeg-core.wasm"
```

Serve with COOP/COEP headers (required for `SharedArrayBuffer`):

```bash
# Use any server that can set the headers, e.g. with Caddy:
caddy file-server --listen :8000 --root . \
  --header "Cross-Origin-Opener-Policy: same-origin" \
  --header "Cross-Origin-Embedder-Policy: require-corp"
```

Or for quick local testing without URL paste / share features, vanilla `python3 -m http.server` works for file uploads (browser will fall back to single-threaded mode).

## License

MIT
