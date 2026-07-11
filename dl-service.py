"""
compress-dl: yt-dlp download service for Compress app.
Runs on fabian at port 8090, behind Caddy.
"""
import asyncio
import json
import os
import re
import secrets
import uuid
import time
from pathlib import Path
import urllib.parse
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
import httpx

DL_DIR = Path(os.environ.get("COMPRESS_DL_DIR", "/opt/compress-dl/downloads"))
DL_DIR.mkdir(parents=True, exist_ok=True)

SHARE_DIR = Path(os.environ.get("COMPRESS_SHARE_DIR", "/opt/compress-dl/shares"))
SHARE_DIR.mkdir(parents=True, exist_ok=True)

CLEANUP_AGE = 1800  # 30 minutes for /downloads
DL_SWEEP_INTERVAL = 600  # sweep every 10 minutes
SHARE_TTL = 7 * 24 * 3600  # 7 days sliding window (resets on extend)
SHARE_MAX_AGE = 30 * 24 * 3600  # absolute 30-day cap from share creation
SHARE_MAX_BYTES = 200 * 1024 * 1024  # 200 MB per share
SHARE_TOTAL_CAP = 5 * 1024 * 1024 * 1024  # 5 GB rolling cap across shares
SHARE_SWEEP_INTERVAL = 600  # sweep every 10 minutes
UPLOAD_MAX_BYTES = int(os.environ.get("COMPRESS_UPLOAD_MAX_BYTES", 500 * 1024 * 1024))

PUBLIC_BASE = os.environ.get("COMPRESS_PUBLIC_BASE", "https://compress.applesauce.chat")
VIEWER_TEMPLATE_PATH = Path(__file__).parent / "v" / "index.html"
# Static template path on mat (deployed location of the static viewer)
if not VIEWER_TEMPLATE_PATH.exists():
    VIEWER_TEMPLATE_PATH = Path("/opt/compress/v/index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    share_task = asyncio.create_task(_share_sweeper())
    dl_task = asyncio.create_task(_dl_sweeper())
    try:
        yield
    finally:
        share_task.cancel()
        dl_task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://compress.applesauce.chat"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

TIKTOK_PATTERNS = re.compile(
    r"(tiktok\.com|vm\.tiktok\.com)", re.IGNORECASE
)

INSTAGRAM_PATTERNS = re.compile(
    r"(instagram\.com|instagr\.am)", re.IGNORECASE
)


class DownloadRequest(BaseModel):
    url: str


class InfoRequest(BaseModel):
    url: str


# Common yt-dlp args for impersonation and anti-bot
YTDLP_BASE = [
    "yt-dlp",
    "--impersonate", "chrome",
    "--no-check-certificates",
    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def is_tiktok(url: str) -> bool:
    return bool(TIKTOK_PATTERNS.search(url))


def is_instagram(url: str) -> bool:
    return bool(INSTAGRAM_PATTERNS.search(url))


async def _has_audio_stream(path: Path) -> bool:
    """ffprobe a file and report whether it contains at least one audio stream."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-select_streams", "a", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        return False
    return b"audio" in stdout


async def _ffprobe_duration(path: Path) -> float:
    """ffprobe a file's container duration in seconds. Returns 0.0 if it can't
    be determined (missing/corrupt file, timeout, etc.)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


# ============================================
# TikTok fallback via tikwm.com API
# ============================================
async def tiktok_info(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": 1},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        raise HTTPException(400, f"TikTok fetch failed: {data.get('msg', 'unknown error')}")

    vid = data["data"]
    return {
        "title": vid.get("title", "TikTok video")[:80],
        "duration": vid.get("duration"),
        "thumbnail": vid.get("cover") or vid.get("origin_cover"),
        "formats": 1,
        "ext": "mp4",
        "filesize_approx": vid.get("size"),
        "_tiktok_data": vid,
    }


async def tiktok_download(url: str, out_dir: Path) -> Path:
    info = await tiktok_info(url)
    vid = info["_tiktok_data"]

    # Prefer `play` (watermark-free h.264) over `hdplay`.
    # Why: tikwm's `hdplay` often returns ByteDance's proprietary bvc2 codec,
    # which browsers/players can't decode — the file looks audio-only.
    video_url = vid.get("play") or vid.get("hdplay")
    if not video_url:
        raise HTTPException(400, "No video URL found in TikTok response")

    title = re.sub(r'[^\w\s-]', '', info["title"])[:60].strip() or "tiktok_video"
    out_path = out_dir / f"{title}.mp4"

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(
            video_url,
            headers={
                "Referer": "https://www.tiktok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            },
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)

    return out_path


async def tiktok_audio_download(url: str, out_dir: Path) -> Path:
    info = await tiktok_info(url)
    vid = info["_tiktok_data"]

    music_url = vid.get("music")
    if not music_url:
        raise HTTPException(400, "No audio URL found in TikTok response")

    title = re.sub(r'[^\w\s-]', '', info["title"])[:60].strip() or "tiktok_audio"
    out_path = out_dir / f"{title}.mp3"

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(
            music_url,
            headers={
                "Referer": "https://www.tiktok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            },
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)

    return out_path


# ============================================
# Instagram: residential-IP proxy + audio-presence retries
# ============================================
# Why this exists: Instagram serves only a single video-only mp4 to mat's
# datacenter IP. To recover audio (which lives in DASH manifests Instagram
# only exposes to "trusted" IPs), we route the request through a small HTTP
# CONNECT proxy on `juno`, a residential-IP machine on the same Tailscale net.
# When juno is online (the common case), Instagram returns the full DASH
# manifest and we get a proper video+audio merge. When juno is unreachable we
# fall back to a direct request, which produces the silent-but-still-usable
# mp4 — better than failing the whole download.
INSTAGRAM_PROXY = os.environ.get("INSTAGRAM_PROXY", "http://100.107.209.60:8888")
INSTAGRAM_PROXY_PROBE_TIMEOUT = 3
INSTAGRAM_FORMAT_ATTEMPTS = [
    "bv*[ext=mp4]+ba/bv*+ba/b[ext=mp4]/b",
    "bv*+ba[ext=m4a]/bv+ba/best",
    "best[acodec!=none]/best",
    "best",
]


async def _proxy_alive(proxy_url: str) -> bool:
    """Cheap pre-flight TCP check so we don't burn a yt-dlp timeout when
    juno is offline."""
    if not proxy_url:
        return False
    m = re.match(r"^https?://([^:/]+):(\d+)$", proxy_url)
    if not m:
        return False
    host, port = m.group(1), int(m.group(2))
    try:
        fut = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(fut, timeout=INSTAGRAM_PROXY_PROBE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def instagram_download(url: str, out_dir: Path) -> Path:
    """Download an Instagram video, preferring the residential-IP proxy."""
    out_template = str(out_dir / "%(title).80s.%(ext)s")
    use_proxy = await _proxy_alive(INSTAGRAM_PROXY)
    proxy_args = ["--proxy", INSTAGRAM_PROXY] if use_proxy else []
    last_file: Path | None = None
    last_err: str | None = None

    for fmt in INSTAGRAM_FORMAT_ATTEMPTS:
        for prev in out_dir.iterdir():
            try:
                prev.unlink()
            except OSError:
                pass

        proc = await asyncio.create_subprocess_exec(
            *YTDLP_BASE,
            *proxy_args,
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--max-filesize", "500m",
            "-o", out_template,
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            last_err = f"timeout on format {fmt!r}"
            continue

        if proc.returncode != 0:
            last_err = stderr.decode()[:300]
            continue

        files = list(out_dir.glob("*"))
        if not files:
            last_err = f"no file produced for format {fmt!r}"
            continue

        candidate = files[0]
        last_file = candidate
        if await _has_audio_stream(candidate):
            return candidate
        # Silent output — try the next format selector.

    if last_file is not None:
        # All formats exhausted but we have *something*. Likely a genuinely
        # silent reel; surface it rather than failing the whole request.
        return last_file

    raise HTTPException(400, f"Instagram download failed: {last_err or 'no candidate produced'}")


# ============================================
# Routes
# ============================================
@app.post("/info")
async def get_info(req: InfoRequest):
    """Get video info without downloading."""
    # TikTok fallback
    if is_tiktok(req.url):
        try:
            info = await tiktok_info(req.url)
            info.pop("_tiktok_data", None)
            return info
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"TikTok info failed: {str(e)[:200]}")

    # Standard yt-dlp path
    proc = await asyncio.create_subprocess_exec(
        *YTDLP_BASE, "--dump-json", "--no-download", req.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Info fetch timed out")

    if proc.returncode != 0:
        raise HTTPException(400, f"Could not fetch info: {stderr.decode()[:200]}")

    data = json.loads(stdout)
    return {
        "title": data.get("title", "video"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "formats": len(data.get("formats", [])),
        "ext": data.get("ext", "mp4"),
        "filesize_approx": data.get("filesize_approx"),
    }


@app.post("/download")
async def download(req: DownloadRequest):
    """Download video and return a file ID for retrieval."""
    cleanup_old_files()

    file_id = str(uuid.uuid4())[:8]
    out_dir = DL_DIR / file_id
    out_dir.mkdir(exist_ok=True)

    # TikTok fallback
    if is_tiktok(req.url):
        try:
            out_file = await tiktok_download(req.url, out_dir)
            return {
                "id": file_id,
                "filename": out_file.name,
                "size": out_file.stat().st_size,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"TikTok download failed: {str(e)[:200]}")

    # Instagram: dedicated path with audio-presence retries (Reels regularly
    # land on a video-only DASH stream when format selection is too strict).
    if is_instagram(req.url):
        try:
            out_file = await instagram_download(req.url, out_dir)
            return {
                "id": file_id,
                "filename": out_file.name,
                "size": out_file.stat().st_size,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Instagram download failed: {str(e)[:200]}")

    # Standard yt-dlp path
    out_template = str(out_dir / "%(title).80s.%(ext)s")

    proc = await asyncio.create_subprocess_exec(
        *YTDLP_BASE,
        # `bv*+ba` allows any audio codec (Instagram often serves non-m4a audio
        # — the older `bestaudio[ext=m4a]` constraint silently fell through to
        # the video-only `best[ext=mp4]` branch, producing soundless mp4s).
        "-f", "bv*[ext=mp4]+ba/bv*+ba/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--max-filesize", "500m",
        "-o", out_template,
        req.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Download timed out")

    if proc.returncode != 0:
        raise HTTPException(400, f"Download failed: {stderr.decode()[:300]}")

    files = list(out_dir.glob("*"))
    if not files:
        raise HTTPException(500, "Download completed but no file found")

    out_file = files[0]
    return {
        "id": file_id,
        "filename": out_file.name,
        "size": out_file.stat().st_size,
    }


@app.post("/download-audio")
async def download_audio(req: DownloadRequest):
    """Download audio-only track and return a file ID for retrieval."""
    cleanup_old_files()

    file_id = str(uuid.uuid4())[:8]
    out_dir = DL_DIR / file_id
    out_dir.mkdir(exist_ok=True)

    if is_tiktok(req.url):
        try:
            out_file = await tiktok_audio_download(req.url, out_dir)
            return {
                "id": file_id,
                "filename": out_file.name,
                "size": out_file.stat().st_size,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"TikTok audio download failed: {str(e)[:200]}")

    out_template = str(out_dir / "%(title).80s.%(ext)s")

    print(f"[audio] start url={req.url!r}", flush=True)
    proc = await asyncio.create_subprocess_exec(
        *YTDLP_BASE,
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "--max-filesize", "100m",
        "-o", out_template,
        req.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        print(f"[audio] TIMEOUT url={req.url!r}", flush=True)
        raise HTTPException(504, "Audio download timed out")

    if proc.returncode != 0:
        err = stderr.decode()[-600:]
        print(f"[audio] FAIL url={req.url!r} rc={proc.returncode}\n{err}", flush=True)
        raise HTTPException(400, f"Audio download failed: {err[:300]}")

    files = list(out_dir.glob("*"))
    if not files:
        print(f"[audio] FAIL url={req.url!r} no-file-produced", flush=True)
        raise HTTPException(500, "Audio download completed but no file found")

    out_file = files[0]
    return {
        "id": file_id,
        "filename": out_file.name,
        "size": out_file.stat().st_size,
    }


MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".opus": "audio/opus",
}


@app.get("/file/{file_id}/{filename}")
async def get_file(file_id: str, filename: str):
    """Serve a downloaded file."""
    filepath = DL_DIR / file_id / filename
    try:
        if not filepath.is_file() or not filepath.resolve().is_relative_to(DL_DIR.resolve()):
            raise HTTPException(404, "File not found")
    except (OSError, ValueError):
        raise HTTPException(404, "File not found")
    ext = filepath.suffix.lower()
    media_type = MIME_BY_EXT.get(ext, "application/octet-stream")
    return FileResponse(
        filepath,
        media_type=media_type,
        filename=filename,
    )


# ============================================
# Public programmatic API  (GET, single-call, CORS-open)
# For external programs: pass a URL, get back the MP3 (or JSON with a link).
# Caddy strips the "/api" prefix, so these routes are reached publicly at
#   https://compress.applesauce.chat/api/<route>
# ============================================
API_CORS = {"Access-Control-Allow-Origin": "*"}


@app.get("/help")
async def api_docs():
    """Self-describing index so the API is discoverable at /api/help."""
    return JSONResponse({
        "service": "compress-dl",
        "base": f"{PUBLIC_BASE}/api",
        "endpoints": {
            "GET /api/mp3?url=<URL>": "Download audio and stream it back as audio/mpeg (one call).",
            "GET /api/mp3?url=<URL>&format=json": "Same, but return JSON {title,size,url} with a ~30-min link instead of bytes.",
            "GET /api/info?url=<URL>": "Metadata only (title, duration, thumbnail) — no download.",
        },
        "notes": "Link-form files live ~30 min then are swept. Max 100 MB audio.",
    }, headers=API_CORS)


@app.get("/info")
async def api_info(url: str):
    """Metadata-only convenience (GET wrapper around POST /info)."""
    info = await get_info(InfoRequest(url=url))
    return JSONResponse(info, headers=API_CORS)


@app.get("/mp3")
async def api_mp3(url: str, format: str = "file"):
    """URL in, MP3 out. The endpoint other programs call.

    GET /api/mp3?url=https://music.youtube.com/...   -> audio/mpeg bytes
    GET /api/mp3?url=...&format=json                 -> {"title","size","url"}
    """
    result = await download_audio(DownloadRequest(url=url))
    filepath = DL_DIR / result["id"] / result["filename"]
    if not filepath.exists():
        raise HTTPException(500, "MP3 produced but file is missing")

    if format == "json":
        quoted = urllib.parse.quote(result["filename"])
        return JSONResponse({
            "title": Path(result["filename"]).stem,
            "filename": result["filename"],
            "size": result["size"],
            "url": f"{PUBLIC_BASE}/api/file/{result['id']}/{quoted}",
        }, headers=API_CORS)

    return FileResponse(
        filepath,
        media_type="audio/mpeg",
        filename=result["filename"],
        headers=API_CORS,
    )


QUALITY_MAP = {
    # CRF values aligned with the client's wasm-path QUALITY_PRESETS (app.js)
    # so server- and client-side compression produce comparable output sizes.
    "high":   {"crf": "23", "preset": "fast", "audio": "128k", "scale": None},
    "medium": {"crf": "30", "preset": "medium", "audio": "96k", "scale": None},
    "low":    {"crf": "34", "preset": "medium", "audio": "64k", "scale": "480"},
}


class CompressRequest(BaseModel):
    file_id: str
    filename: str
    quality: str = "medium"
    target_mb: float | None = None


class TrimSegment(BaseModel):
    start: float
    end: float


class TrimRequest(BaseModel):
    file_id: str
    filename: str
    segments: list[TrimSegment]


@app.post("/compress")
async def compress_video(req: CompressRequest):
    """Compress a video server-side using native FFmpeg."""
    input_path = DL_DIR / req.file_id / req.filename
    if not input_path.exists() or not input_path.is_relative_to(DL_DIR):
        raise HTTPException(404, "Input file not found")

    out_name = input_path.stem + "_compressed.mp4"
    out_path = DL_DIR / req.file_id / out_name

    args = ["ffmpeg", "-i", str(input_path)]

    if req.target_mb:
        # Target size mode — calculate bitrate
        duration = await _ffprobe_duration(input_path)
        if duration <= 0:
            raise HTTPException(400, "could not determine video duration")

        target_bytes = req.target_mb * 1024 * 1024
        total_kbps = int((target_bytes * 8) / duration / 1000)
        video_kbps = max(total_kbps - 64, 100)

        if video_kbps < 200:
            args += ["-vf", "scale=-2:480"]
        elif video_kbps < 500:
            args += ["-vf", "scale=-2:720"]

        args += [
            "-c:v", "libx264", "-preset", "medium",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.5)}k",
            "-bufsize", f"{int(video_kbps * 2)}k",
        ]
    else:
        preset = QUALITY_MAP.get(req.quality, QUALITY_MAP["medium"])
        if preset["scale"]:
            args += ["-vf", f"scale=-2:{preset['scale']}"]
        args += [
            "-c:v", "libx264", "-preset", preset["preset"],
            "-crf", preset["crf"],
        ]

    audio_br = QUALITY_MAP.get(req.quality, QUALITY_MAP["medium"])["audio"]
    args += [
        "-c:a", "aac", "-b:a", audio_br,
        "-movflags", "+faststart",
        "-y", str(out_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Compression timed out")

    if proc.returncode != 0:
        raise HTTPException(500, f"Compression failed: {stderr.decode()[-300:]}")

    if not out_path.exists():
        raise HTTPException(500, "Compression produced no output")

    return {
        "id": req.file_id,
        "filename": out_name,
        "size": out_path.stat().st_size,
        "original_size": input_path.stat().st_size,
    }


@app.post("/trim")
async def trim_video(req: TrimRequest):
    """Server-side frame-accurate trim via native FFmpeg.

    For files too large to trim in-browser (wasm heap OOMs above ~50MB), the
    frontend routes here instead. Mirrors /compress's input validation and
    response shape so the frontend can fetch the result the same way.
    """
    input_path = DL_DIR / req.file_id / req.filename
    try:
        if not input_path.is_file() or not input_path.resolve().is_relative_to(DL_DIR.resolve()):
            raise HTTPException(404, "Input file not found")
    except (OSError, ValueError):
        raise HTTPException(404, "Input file not found")

    if not req.segments:
        raise HTTPException(400, "At least one segment is required")

    segments = [(s.start, s.end) for s in req.segments]
    prev_end = None
    for start, end in segments:
        if start < 0 or end <= start:
            raise HTTPException(400, "Each segment must satisfy 0 <= start < end")
        if prev_end is not None and start < prev_end:
            raise HTTPException(400, "Segments must be sorted and non-overlapping")
        prev_end = end

    out_name = input_path.stem + "_trimmed.mp4"
    out_path = DL_DIR / req.file_id / out_name

    has_audio = await _has_audio_stream(input_path)

    if len(segments) == 1 and segments[0][0] < 0.05:
        # Fast path: single segment from (near) the start — stream copy, no re-encode.
        end = segments[0][1]
        args = [
            "ffmpeg", "-i", str(input_path),
            "-t", f"{end:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero",
            "-y", str(out_path),
        ]
    else:
        filter_parts = []
        v_labels = []
        a_labels = []
        for i, (start, end) in enumerate(segments):
            filter_parts.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]"
            )
            v_labels.append(f"[v{i}]")
            if has_audio:
                filter_parts.append(
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
                )
                a_labels.append(f"[a{i}]")

        n = len(segments)
        if has_audio:
            concat_inputs = "".join(v + a for v, a in zip(v_labels, a_labels))
            filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[v][a]")
        else:
            concat_inputs = "".join(v_labels)
            filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[v]")

        filter_complex = ";".join(filter_parts)

        args = ["ffmpeg", "-i", str(input_path), "-filter_complex", filter_complex, "-map", "[v]"]
        if has_audio:
            args += ["-map", "[a]"]
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
        if has_audio:
            args += ["-c:a", "aac", "-b:a", "128k"]
        args += ["-movflags", "+faststart", "-y", str(out_path)]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Trim timed out")

    if proc.returncode != 0:
        raise HTTPException(500, f"Trim failed: {stderr.decode()[-300:]}")

    if not out_path.exists():
        raise HTTPException(500, "Trim produced no output")

    return {
        "id": req.file_id,
        "filename": out_name,
        "size": out_path.stat().st_size,
    }


from fastapi import UploadFile


@app.post("/upload")
async def upload_video(file: UploadFile):
    """Upload a video file for server-side compression."""
    cleanup_old_files()

    file_id = str(uuid.uuid4())[:8]
    out_dir = DL_DIR / file_id
    out_dir.mkdir(exist_ok=True)

    # Sanitize filename
    safe_name = re.sub(r'[^\w\s\-.]', '', file.filename or "video.mp4")[:80]
    if not safe_name.endswith(('.mp4', '.mov', '.mkv', '.avi', '.webm')):
        safe_name += '.mp4'

    out_path = out_dir / safe_name

    # Stream to disk to avoid loading entire file in memory
    written = 0
    try:
        with open(out_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                written += len(chunk)
                if written > UPLOAD_MAX_BYTES:
                    f.close()
                    out_path.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        f"File too large (max {UPLOAD_MAX_BYTES // (1024*1024)} MB)",
                    )
                f.write(chunk)
    except HTTPException:
        if out_dir.exists() and not any(out_dir.iterdir()):
            out_dir.rmdir()
        raise

    return {
        "id": file_id,
        "filename": safe_name,
        "size": out_path.stat().st_size,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================
# Share: 7-day ephemeral hosting with rolling cap
# ============================================
SHARE_ID_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"  # no 0/1/l confusables


def _new_share_id() -> str:
    return "".join(secrets.choice(SHARE_ID_ALPHABET) for _ in range(8))


def _safe_share_name(name: str | None) -> str:
    base = re.sub(r"[^\w\s\-.]", "", (name or "compressed.mp4"))[:80].strip()
    if not base:
        base = "compressed.mp4"
    if not re.search(r"\.(mp4|mov|mkv|webm|avi|m4a|mp3|aac|ogg|wav|opus)$", base, re.IGNORECASE):
        base += ".mp4"
    return base


class PromoteShareRequest(BaseModel):
    file_id: str
    filename: str


@app.post("/share/promote")
async def promote_to_share(req: PromoteShareRequest):
    """Take an existing /downloads file and copy it into /shares with a 7-day TTL."""
    if not re.fullmatch(r"[a-fA-F0-9\-]{4,40}", req.file_id):
        raise HTTPException(404, "Not found")
    src = DL_DIR / req.file_id / req.filename
    try:
        if not src.is_file() or not src.resolve().is_relative_to(DL_DIR.resolve()):
            raise HTTPException(404, "File not found")
    except (OSError, ValueError):
        raise HTTPException(404, "File not found")

    if src.stat().st_size > SHARE_MAX_BYTES:
        raise HTTPException(
            413, f"File too large (max {SHARE_MAX_BYTES // (1024*1024)} MB per share)"
        )

    safe_name = _safe_share_name(req.filename)

    for _ in range(8):
        sid = _new_share_id()
        out_dir = SHARE_DIR / sid
        if not out_dir.exists():
            break
    else:
        raise HTTPException(500, "Could not allocate share id")

    out_dir.mkdir()
    _write_share_created(out_dir)
    out_path = out_dir / safe_name
    # Hardlink when possible (same filesystem), copy otherwise
    try:
        os.link(src, out_path)
    except OSError:
        import shutil
        shutil.copy2(src, out_path)

    _enforce_share_cap()

    return {
        "id": sid,
        "filename": safe_name,
        "size": out_path.stat().st_size,
        "url": f"/api/share/{sid}/{safe_name}",
        "share_url": f"/v/{sid}",
        "expires_in": SHARE_TTL,
    }


@app.post("/share")
async def create_share(file: UploadFile):
    """Store a file under /shares/<id>/ for 7 days. Returns short-id + URL."""
    safe_name = _safe_share_name(file.filename)

    # Pick a fresh id (avoid the rare collision)
    for _ in range(8):
        sid = _new_share_id()
        out_dir = SHARE_DIR / sid
        if not out_dir.exists():
            break
    else:
        raise HTTPException(500, "Could not allocate share id")

    out_dir.mkdir()
    _write_share_created(out_dir)
    out_path = out_dir / safe_name

    written = 0
    try:
        with open(out_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > SHARE_MAX_BYTES:
                    f.close()
                    out_path.unlink(missing_ok=True)
                    out_dir.rmdir()
                    raise HTTPException(
                        413,
                        f"File too large (max {SHARE_MAX_BYTES // (1024*1024)} MB per share)",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception:
        # Clean up partial write
        out_path.unlink(missing_ok=True)
        if out_dir.exists() and not any(out_dir.iterdir()):
            out_dir.rmdir()
        raise

    # Best-effort enforce rolling cap right after a write
    _enforce_share_cap()

    return {
        "id": sid,
        "filename": safe_name,
        "size": written,
        "url": f"/api/share/{sid}/{safe_name}",
        "share_url": f"/v/{sid}",
        "expires_in": SHARE_TTL,
    }


def _share_created_at(d: Path) -> float:
    """Read the share's recorded creation timestamp. Fall back to the share dir's
    own mtime for shares created before this metadata existed."""
    f = d / "_created"
    try:
        return float(f.read_text().strip())
    except (OSError, ValueError):
        try:
            return d.stat().st_mtime
        except OSError:
            return time.time()


def _write_share_created(d: Path):
    try:
        (d / "_created").write_text(str(int(time.time())))
    except OSError:
        pass


def _share_first_file(share_id: str) -> Path:
    """Return the (single) primary file in a share dir, or raise 404/410."""
    if not re.fullmatch(r"[a-z0-9]{4,16}", share_id):
        raise HTTPException(404, "Not found")
    d = SHARE_DIR / share_id
    if not d.is_dir():
        raise HTTPException(404, "Not found")
    files = sorted(
        [
            f for f in d.iterdir()
            if f.is_file()
            and not f.name.endswith("_audio.mp3")
            and not f.name.startswith("_")
        ],
        key=lambda f: f.stat().st_mtime,
    )
    if not files:
        raise HTTPException(404, "Empty share")
    f = files[0]
    now = time.time()
    sliding_expired = (now - f.stat().st_mtime) > SHARE_TTL
    max_age_expired = (now - _share_created_at(d)) > SHARE_MAX_AGE
    if sliding_expired or max_age_expired:
        for g in d.iterdir():
            g.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass
        raise HTTPException(410, "Share expired")
    return f


async def _probe_share_dims(share_id: str, src: Path) -> tuple[int, int, float]:
    """Return (width, height, duration). Cached as _meta.json next to the file."""
    cache = src.parent / "_meta.json"
    if cache.exists():
        try:
            d = json.loads(cache.read_text())
            return int(d["w"]), int(d["h"]), float(d["dur"])
        except Exception:
            pass

    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(src),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        return 0, 0, 0.0
    if proc.returncode != 0:
        return 0, 0, 0.0

    try:
        info = json.loads(stdout)
        w = int(info.get("streams", [{}])[0].get("width", 0))
        h = int(info.get("streams", [{}])[0].get("height", 0))
        dur = float(info.get("format", {}).get("duration", 0) or 0)
    except Exception:
        w = h = 0
        dur = 0.0

    try:
        cache.write_text(json.dumps({"w": w, "h": h, "dur": dur}))
    except OSError:
        pass
    return w, h, dur


@app.get("/share/{share_id}")
async def get_share_meta(share_id: str):
    """Return metadata for a share (used by the viewer page)."""
    f = _share_first_file(share_id)
    ext = f.suffix.lower()
    w, h, dur = await _probe_share_dims(share_id, f)
    created = _share_created_at(f.parent)
    age = time.time() - created
    return {
        "id": share_id,
        "filename": f.name,
        "size": f.stat().st_size,
        "mime": MIME_BY_EXT.get(ext, "application/octet-stream"),
        "url": f"/api/share/{share_id}/{f.name}",
        "width": w,
        "height": h,
        "duration": dur,
        "created_at": int(created),
        "expires_at": int(f.stat().st_mtime + SHARE_TTL),
        "max_age_remaining": max(0, int(SHARE_MAX_AGE - age)),
        "extendable": age < SHARE_MAX_AGE,
    }


@app.post("/share/{share_id}/extend")
async def extend_share(share_id: str):
    """Reset the 7-day sliding TTL. Capped at 30 days from share creation."""
    f = _share_first_file(share_id)
    d = f.parent
    created = _share_created_at(d)
    now = time.time()
    age = now - created
    if age >= SHARE_MAX_AGE:
        raise HTTPException(
            403,
            "This share has reached its maximum lifetime of 30 days and can no longer be extended.",
        )

    # Touch every real file so the sweeper (which uses mtime) sees a fresh window.
    for entry in d.iterdir():
        if entry.is_file():
            try:
                os.utime(entry, (now, now))
            except OSError:
                pass

    return {
        "id": share_id,
        "created_at": int(created),
        "expires_at": int(now + SHARE_TTL),
        "max_age_remaining": max(0, int(SHARE_MAX_AGE - age)),
        "extendable": True,
    }


@app.get("/share/{share_id}/thumb.jpg")
async def share_thumb(share_id: str):
    """Generate (and cache) a JPG thumbnail of the shared video for OG previews."""
    src = _share_first_file(share_id)
    out_path = src.parent / "_thumb.jpg"

    if not out_path.exists():
        # Grab a frame ~1s in (or 10% of duration, whichever is later — avoids
        # black intro frames on long videos).
        _, _, dur = await _probe_share_dims(share_id, src)
        seek = max(1.0, dur * 0.10) if dur > 0 else 1.0

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", f"{seek:.2f}", "-i", str(src),
            "-vframes", "1", "-vf", "scale='min(1280,iw)':-2:flags=lanczos",
            "-q:v", "4",
            str(out_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, "Thumbnail timed out")

        if proc.returncode != 0 or not out_path.exists():
            raise HTTPException(500, f"Thumbnail failed: {stderr.decode()[-200:]}")

    return FileResponse(out_path, media_type="image/jpeg", filename="thumb.jpg")


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@app.get("/v/{share_id}", response_class=HTMLResponse)
async def viewer_page(share_id: str):
    """Server-render the viewer with per-share Open Graph + Twitter Card metadata
    so iMessage / Discord / Twitter / Slack get a thumbnail + inline player."""
    if not re.fullmatch(r"[a-z0-9]{4,16}", share_id):
        raise HTTPException(404, "Not found")

    try:
        f = _share_first_file(share_id)
    except HTTPException as e:
        # Still serve the viewer shell for graceful client-side error rendering;
        # crawlers will see no OG video tags and just get the basic title.
        if e.status_code in (404, 410):
            try:
                tpl = VIEWER_TEMPLATE_PATH.read_text()
                title = "Expired share" if e.status_code == 410 else "Share not found"
                og = f'<meta property="og:title" content="{title}"><meta property="og:description" content="This share is no longer available.">'
                return HTMLResponse(tpl.replace("<!--OG_META-->", og))
            except Exception:
                pass
        raise

    w, h, dur = await _probe_share_dims(share_id, f)

    file_url = f"{PUBLIC_BASE}/api/share/{share_id}/{urllib.parse.quote(f.name)}"
    thumb_url = f"{PUBLIC_BASE}/api/share/{share_id}/thumb.jpg"
    page_url = f"{PUBLIC_BASE}/v/{share_id}"

    title = f"Video — {f.name}" if f.name else "Shared video"
    desc_parts = []
    if dur > 0:
        m, s = divmod(int(dur), 60)
        desc_parts.append(f"{m}:{s:02d}")
    if w and h:
        desc_parts.append(f"{w}×{h}")
    desc_parts.append(f"{f.stat().st_size // (1024 * 1024)} MB")
    desc_parts.append("expires in 7d")
    description = " · ".join(desc_parts)

    # Best-effort: ensure thumbnail exists so crawlers don't race.
    thumb_path = f.parent / "_thumb.jpg"
    if not thumb_path.exists():
        try:
            seek = max(1.0, dur * 0.10) if dur > 0 else 1.0
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-ss", f"{seek:.2f}", "-i", str(f),
                "-vframes", "1", "-vf", "scale='min(1280,iw)':-2:flags=lanczos",
                "-q:v", "4", str(thumb_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            pass  # Thumbnail will be generated lazily on first /thumb.jpg hit

    safe_title = _html_escape(title)
    safe_desc = _html_escape(description)

    og_lines = [
        f'<meta property="og:title" content="{safe_title}">',
        f'<meta property="og:description" content="{safe_desc}">',
        f'<meta property="og:type" content="video.other">',
        f'<meta property="og:url" content="{page_url}">',
        f'<meta property="og:site_name" content="compress.applesauce.chat">',
        f'<meta property="og:image" content="{thumb_url}">',
        f'<meta property="og:image:secure_url" content="{thumb_url}">',
        f'<meta property="og:image:type" content="image/jpeg">',
    ]
    if w and h:
        # For portrait video, scale thumb width is preserved; height becomes scaled.
        og_lines += [
            f'<meta property="og:image:width" content="{min(w, 1280)}">',
            f'<meta property="og:image:height" content="{int(h * (min(w, 1280) / w)) if w else 720}">',
        ]
    og_lines += [
        f'<meta property="og:video" content="{file_url}">',
        f'<meta property="og:video:secure_url" content="{file_url}">',
        f'<meta property="og:video:type" content="video/mp4">',
    ]
    if w and h:
        og_lines += [
            f'<meta property="og:video:width" content="{w}">',
            f'<meta property="og:video:height" content="{h}">',
        ]
    og_lines += [
        f'<meta name="twitter:card" content="player">',
        f'<meta name="twitter:title" content="{safe_title}">',
        f'<meta name="twitter:description" content="{safe_desc}">',
        f'<meta name="twitter:image" content="{thumb_url}">',
        f'<meta name="twitter:player" content="{page_url}">',
    ]
    if w and h:
        og_lines += [
            f'<meta name="twitter:player:width" content="{w}">',
            f'<meta name="twitter:player:height" content="{h}">',
        ]

    og_block = "\n    ".join(og_lines)

    try:
        tpl = VIEWER_TEMPLATE_PATH.read_text()
    except OSError:
        raise HTTPException(500, "Viewer template missing")

    html = tpl.replace("<!--OG_META-->", og_block)
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=60"})


# ============================================
# Speech-to-text via faster-whisper
# ============================================
import threading

_whisper_model = None
_whisper_load_lock = threading.Lock()
_whisper_run_lock = threading.Lock()  # serialize inference (single CPU pool)
_transcript_jobs: dict[str, dict] = {}


def _get_whisper_model():
    global _whisper_model
    with _whisper_load_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    return _whisper_model


def _transcribe_blocking(src: Path) -> dict:
    """Sync transcription on a single file. Serialized via _whisper_run_lock."""
    model = _get_whisper_model()
    with _whisper_run_lock:
        segments_iter, info = model.transcribe(
            str(src),
            language=None,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        segments = []
        for s in segments_iter:
            segments.append(
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            )
    text = " ".join(s["text"] for s in segments).strip()
    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 2),
        "segments": segments,
        "text": text,
        "model": "small",
    }


async def _run_transcription(share_id: str, src: Path):
    job = _transcript_jobs[share_id]
    job["state"] = "working"
    try:
        result = await asyncio.to_thread(_transcribe_blocking, src)
        out = src.parent / "_transcript.json"
        out.write_text(json.dumps(result))
        job["state"] = "done"
        job["finished_at"] = time.time()
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)[:300]
        job["finished_at"] = time.time()


@app.post("/share/{share_id}/transcript")
async def start_transcript(share_id: str):
    """Kick off transcription (idempotent: returns done if cached, queued if running)."""
    src = _share_first_file(share_id)
    out = src.parent / "_transcript.json"

    if out.exists():
        return {"id": share_id, "state": "done"}

    job = _transcript_jobs.get(share_id)
    if job and job["state"] in ("queued", "working"):
        return {"id": share_id, "state": job["state"], "started_at": int(job["started_at"])}

    _transcript_jobs[share_id] = {"state": "queued", "started_at": time.time()}
    asyncio.create_task(_run_transcription(share_id, src))
    return {"id": share_id, "state": "queued", "started_at": int(time.time())}


@app.get("/share/{share_id}/transcript")
async def get_transcript(share_id: str):
    """Return cached transcript JSON, or job status if still working."""
    src = _share_first_file(share_id)
    out = src.parent / "_transcript.json"
    if out.exists():
        try:
            return JSONResponse(json.loads(out.read_text()))
        except Exception:
            out.unlink(missing_ok=True)
            raise HTTPException(500, "Cached transcript was corrupt; please retry.")

    job = _transcript_jobs.get(share_id)
    if not job:
        raise HTTPException(404, "Not transcribed yet — POST /transcript to start.")

    if job["state"] == "error":
        raise HTTPException(500, job.get("error", "Transcription failed"))

    elapsed = int(time.time() - job["started_at"])
    return JSONResponse(
        {"id": share_id, "state": job["state"], "elapsed_seconds": elapsed},
        status_code=202,
    )


def _format_srt_time(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(t: float) -> str:
    return _format_srt_time(t).replace(",", ".")


@app.get("/share/{share_id}/transcript.srt")
async def get_transcript_srt(share_id: str):
    src = _share_first_file(share_id)
    out = src.parent / "_transcript.json"
    if not out.exists():
        raise HTTPException(404, "Transcript not generated yet")
    data = json.loads(out.read_text())
    lines = []
    for i, s in enumerate(data.get("segments", []), start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(s['start'])} --> {_format_srt_time(s['end'])}")
        lines.append(s["text"])
        lines.append("")
    body = "\n".join(lines)
    return Response(
        body,
        media_type="application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{share_id}.srt"'},
    )


@app.get("/share/{share_id}/transcript.vtt")
async def get_transcript_vtt(share_id: str):
    src = _share_first_file(share_id)
    out = src.parent / "_transcript.json"
    if not out.exists():
        raise HTTPException(404, "Transcript not generated yet")
    data = json.loads(out.read_text())
    lines = ["WEBVTT", ""]
    for s in data.get("segments", []):
        lines.append(f"{_format_vtt_time(s['start'])} --> {_format_vtt_time(s['end'])}")
        lines.append(s["text"])
        lines.append("")
    body = "\n".join(lines)
    return Response(
        body,
        media_type="text/vtt",
        headers={"Content-Disposition": f'attachment; filename="{share_id}.vtt"'},
    )


@app.get("/share/{share_id}/extract-audio")
async def share_extract_audio(share_id: str):
    """Extract audio from the shared video as MP3 (cached after first extract)."""
    src = _share_first_file(share_id)
    out_path = src.parent / (src.stem + "_audio.mp3")

    if not out_path.exists():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, "Audio extraction timed out")
        if proc.returncode != 0:
            out_path.unlink(missing_ok=True)
            raise HTTPException(500, f"Audio extraction failed: {stderr.decode()[-200:]}")

    return FileResponse(out_path, media_type="audio/mpeg", filename=out_path.name)


@app.get("/share/{share_id}/{filename}")
async def get_shared_file(share_id: str, filename: str):
    """Serve a shared file."""
    if not re.fullmatch(r"[a-z0-9]{4,16}", share_id):
        raise HTTPException(404, "Not found")
    filepath = SHARE_DIR / share_id / filename
    try:
        if not filepath.is_file() or not filepath.resolve().is_relative_to(SHARE_DIR.resolve()):
            raise HTTPException(404, "Not found")
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")

    # Lazy-expire: if the share is past TTL, remove and 404
    if (time.time() - filepath.stat().st_mtime) > SHARE_TTL:
        filepath.unlink(missing_ok=True)
        try:
            filepath.parent.rmdir()
        except OSError:
            pass
        raise HTTPException(410, "Share expired")

    ext = filepath.suffix.lower()
    media_type = MIME_BY_EXT.get(ext, "application/octet-stream")
    return FileResponse(filepath, media_type=media_type, filename=filename)


def _share_dirs_by_age():
    entries = []
    for d in SHARE_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            files = [f for f in d.iterdir() if f.is_file()]
            size = sum(f.stat().st_size for f in files)
            mtime = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
        except OSError:
            continue
        entries.append((mtime, size, d, files))
    return entries


def _delete_share_dir(d: Path, files):
    for f in files:
        f.unlink(missing_ok=True)
    try:
        d.rmdir()
    except OSError:
        pass


def _enforce_share_cap():
    entries = _share_dirs_by_age()
    total = sum(e[1] for e in entries)
    if total <= SHARE_TOTAL_CAP:
        return
    # Evict oldest first
    entries.sort(key=lambda e: e[0])
    for mtime, size, d, files in entries:
        if total <= SHARE_TOTAL_CAP:
            break
        _delete_share_dir(d, files)
        total -= size


def _expire_old_shares():
    now = time.time()
    for mtime, size, d, files in _share_dirs_by_age():
        # Sliding 7-day window expired
        if (now - mtime) > SHARE_TTL:
            _delete_share_dir(d, files)
            continue
        # Absolute 30-day cap from creation, even if recently extended
        if (now - _share_created_at(d)) > SHARE_MAX_AGE:
            _delete_share_dir(d, files)


async def _share_sweeper():
    """Background task: expire old shares + enforce rolling cap."""
    while True:
        try:
            _expire_old_shares()
            _enforce_share_cap()
        except Exception as e:
            print(f"[share-sweeper] error: {e}")
        await asyncio.sleep(SHARE_SWEEP_INTERVAL)


def cleanup_old_files():
    now = time.time()
    for d in DL_DIR.iterdir():
        if d.is_dir() and (now - d.stat().st_mtime) > CLEANUP_AGE:
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()


async def _dl_sweeper():
    """Background task: periodically purge stale /downloads dirs, mirroring
    _share_sweeper. The opportunistic cleanup_old_files() calls in the
    download/upload endpoints still run too — this just covers the case where
    no new requests come in to trigger them."""
    while True:
        try:
            cleanup_old_files()
        except Exception as e:
            print(f"[dl-sweeper] error: {e}")
        await asyncio.sleep(DL_SWEEP_INTERVAL)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("COMPRESS_PORT", 8090)))
