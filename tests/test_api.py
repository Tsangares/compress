"""
Self-contained pytest suite for dl-service.py's HTTP API.

Deliberately does NOT use tests/conftest.py's fixtures (those exist for the
Playwright end-to-end harness against port 8090). This file boots its own
dl-service.py subprocess on port 8091 (via COMPRESS_PORT) so it can run
independently and in parallel with that harness, with its own scratch
COMPRESS_DL_DIR/COMPRESS_SHARE_DIR.

Run with:
    python -m pytest tests/test_api.py -x -q
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
SERVICE_PATH = REPO_DIR / "dl-service.py"

SCRATCH_ROOT = Path(
    "/tmp/claude-1000/-home-wil/b5b23348-1569-4e57-9b21-2636b4882746/scratchpad/compress-api-test"
)
DL_DIR = SCRATCH_ROOT / "downloads"
SHARE_DIR = SCRATCH_ROOT / "shares"
MEDIA_DIR = SCRATCH_ROOT / "media"

PORT = 8091  # distinct from conftest.py's hardcoded 8090, avoids collisions
BASE_URL = f"http://127.0.0.1:{PORT}"
UPLOAD_MAX_BYTES = 1 * 1024 * 1024  # tight 1MB cap just for this test session


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _ffprobe_codecs(path: Path) -> set:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


@pytest.fixture(scope="module")
def media_dir():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return MEDIA_DIR


@pytest.fixture(scope="module")
def small_video(media_dir):
    """A tiny (well under the 1MB test cap) mp4 with video+audio, 6s long."""
    path = media_dir / "small.mp4"
    if not path.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=6:r=24",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                "-c:a", "aac", "-shortest",
                str(path),
            ],
            check=True, capture_output=True, timeout=60,
        )
    assert path.stat().st_size < UPLOAD_MAX_BYTES
    return path


@pytest.fixture(scope="module")
def noaudio_video(media_dir):
    """Same idea, no audio track — exercises the video-only trim filtergraph."""
    path = media_dir / "noaudio.mp4"
    if not path.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=320x240:d=4:r=24",
                "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                str(path),
            ],
            check=True, capture_output=True, timeout=60,
        )
    assert path.stat().st_size < UPLOAD_MAX_BYTES
    return path


@pytest.fixture(scope="module")
def fake_mp4(media_dir):
    """A plain-text file with a .mp4 extension. ffprobe can't get a duration
    from it, exercising the /compress duration<=0 guard."""
    path = media_dir / "notreally.mp4"
    if not path.exists():
        path.write_text("this is not a video file\n" * 200)
    return path


@pytest.fixture(scope="module")
def oversized_file(media_dir):
    """~2MB of random bytes with a .mp4 extension — bigger than the test
    session's 1MB COMPRESS_UPLOAD_MAX_BYTES cap."""
    path = media_dir / "oversized.mp4"
    if not path.exists() or path.stat().st_size < 2 * 1024 * 1024:
        with open(path, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))
    return path


@pytest.fixture(scope="module")
def server():
    for d in (DL_DIR, SHARE_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["COMPRESS_DL_DIR"] = str(DL_DIR)
    env["COMPRESS_SHARE_DIR"] = str(SHARE_DIR)
    env["COMPRESS_PORT"] = str(PORT)
    env["COMPRESS_UPLOAD_MAX_BYTES"] = str(UPLOAD_MAX_BYTES)

    proc = subprocess.Popen(
        [sys.executable, str(SERVICE_PATH)],
        cwd=str(REPO_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.time() + 30
    healthy = False
    last_err = None
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"dl-service.py exited early (rc={proc.returncode}):\n{out}")
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=1)
            if r.status_code == 200:
                healthy = True
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.2)

    if not healthy:
        proc.kill()
        raise RuntimeError(f"dl-service.py did not become healthy in time: {last_err}")

    yield BASE_URL

    proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()


@pytest.fixture()
def client(server):
    with httpx.Client(base_url=server, timeout=60) as c:
        yield c


def _upload(client, path, content_type="video/mp4"):
    with open(path, "rb") as f:
        return client.post("/upload", files={"file": (path.name, f, content_type)})


# ============================================
# Tests
# ============================================
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_then_trim_two_segments(client, small_video):
    r = _upload(client, small_video)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["size"] > 0
    assert data["filename"].endswith(".mp4")

    segments = [{"start": 0.5, "end": 2.0}, {"start": 3.0, "end": 4.5}]
    r = client.post(
        "/trim",
        json={"file_id": data["id"], "filename": data["filename"], "segments": segments},
    )
    assert r.status_code == 200, r.text
    trimmed = r.json()
    assert trimmed["id"] == data["id"]
    assert trimmed["size"] > 0

    r = client.get(f"/file/{trimmed['id']}/{trimmed['filename']}")
    assert r.status_code == 200
    out_path = MEDIA_DIR / "trim_out_2seg.mp4"
    out_path.write_bytes(r.content)

    expected_duration = sum(s["end"] - s["start"] for s in segments)
    actual_duration = _ffprobe_duration(out_path)
    assert abs(actual_duration - expected_duration) <= 0.3, (
        f"expected ~{expected_duration}s, got {actual_duration}s"
    )

    codecs = _ffprobe_codecs(out_path)
    assert "h264" in codecs
    assert "aac" in codecs


def test_trim_noaudio_input(client, noaudio_video):
    r = _upload(client, noaudio_video)
    assert r.status_code == 200, r.text
    data = r.json()

    r = client.post(
        "/trim",
        json={
            "file_id": data["id"],
            "filename": data["filename"],
            "segments": [{"start": 1.0, "end": 2.5}],
        },
    )
    assert r.status_code == 200, r.text
    trimmed = r.json()

    r = client.get(f"/file/{trimmed['id']}/{trimmed['filename']}")
    assert r.status_code == 200
    out_path = MEDIA_DIR / "trim_out_noaudio.mp4"
    out_path.write_bytes(r.content)

    codecs = _ffprobe_codecs(out_path)
    assert "h264" in codecs
    assert "aac" not in codecs


def test_trim_empty_segments_rejected(client, small_video):
    r = _upload(client, small_video)
    data = r.json()
    r = client.post(
        "/trim",
        json={"file_id": data["id"], "filename": data["filename"], "segments": []},
    )
    assert r.status_code == 400


def test_trim_overlapping_segments_rejected(client, small_video):
    r = _upload(client, small_video)
    data = r.json()
    r = client.post(
        "/trim",
        json={
            "file_id": data["id"],
            "filename": data["filename"],
            "segments": [{"start": 0.0, "end": 2.0}, {"start": 1.0, "end": 3.0}],
        },
    )
    assert r.status_code == 400


def test_trim_bad_start_end_rejected(client, small_video):
    r = _upload(client, small_video)
    data = r.json()
    r = client.post(
        "/trim",
        json={
            "file_id": data["id"],
            "filename": data["filename"],
            "segments": [{"start": 2.0, "end": 1.0}],
        },
    )
    assert r.status_code == 400


def test_trim_bad_file_id_404(client):
    r = client.post(
        "/trim",
        json={
            "file_id": "doesnotexist",
            "filename": "x.mp4",
            "segments": [{"start": 0.0, "end": 1.0}],
        },
    )
    assert r.status_code == 404


def test_compress_duration_zero_guard(client, fake_mp4):
    r = _upload(client, fake_mp4)
    assert r.status_code == 200, r.text
    data = r.json()

    r = client.post(
        "/compress",
        json={"file_id": data["id"], "filename": data["filename"], "target_mb": 5.0},
    )
    assert 400 <= r.status_code < 500, r.text
    assert r.status_code != 500


def test_upload_size_cap_413(client, oversized_file):
    r = _upload(client, oversized_file)
    assert r.status_code == 413, r.text


@pytest.mark.parametrize(
    "path",
    [
        "/file/x/..%2F..%2F..%2Fetc%2Fpasswd",
        "/file/..%2F..%2Fetc%2Fpasswd/x",
        "/file/../../etc/passwd",
    ],
)
def test_get_file_traversal_blocked(client, path):
    r = client.get(path)
    assert r.status_code in (400, 404)
    assert "root:" not in r.text
