"""Server-side processing path for large (>50MB) files.

big.mp4 exceeds SERVER_COMPRESS_THRESHOLD (50MB, app.js), so:
  - Compress routes through startServerCompression(): POST /api/upload ->
    POST /api/compress -> GET /api/file/{id}/{name}.
  - Trim/export routes through startServerTrim(): POST /api/upload ->
    POST /api/trim -> GET /api/file/{id}/{name}. (Before the Round-1 fix,
    exportEdit() loaded the whole file into the wasm FS regardless of size,
    which OOM-crashed the tab on real phones.)

Both require the local dl-service.py backend (skips cleanly if it isn't up).
"""
import pytest

from conftest import wait_engine_ready, ffprobe, decode_clean, FIXTURES_DIR

from test_trim import (
    _enter_editor,
    _duration,
    _split_at,
    _select_segment,
    _delete_selected,
)

# Native ffmpeg on loopback is fast, but a 70s 720p re-encode plus a ~72MB
# upload deserves generous headroom.
SERVER_TIMEOUT_MS = 300_000


def _fixture_duration(path) -> float:
    return float(ffprobe(path)["format"]["duration"])


def test_big_file_server_compress(page, app_url, tmp_path, require_backend):
    """>50MB file -> Compress must complete via the server path and produce a
    valid, smaller h264/aac mp4 of the same duration."""
    big = FIXTURES_DIR / "big.mp4"
    in_duration = _fixture_duration(big)

    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(big))
    page.wait_for_selector("#screen-options.active", timeout=15000)

    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=SERVER_TIMEOUT_MS)

    with page.expect_download(timeout=60000) as dl_info:
        page.click("#saveBtn")
    out_path = tmp_path / "big_server_compressed.mp4"
    dl_info.value.save_as(str(out_path))

    assert out_path.stat().st_size > 0
    assert out_path.stat().st_size < big.stat().st_size, "server compress did not shrink the file"

    probe = ffprobe(out_path)
    assert float(probe["format"]["duration"]) == pytest.approx(in_duration, abs=0.5)
    codecs = {s["codec_type"]: s["codec_name"] for s in probe["streams"]}
    assert codecs.get("video") == "h264"
    assert codecs.get("audio") == "aac"


def test_big_file_trim_routes_to_server(page, app_url, tmp_path, require_backend):
    """>50MB file -> editor -> keep only the first ~20s -> export. Must route
    to POST /api/trim (never the wasm FS) and return a frame-accurate cut."""
    big = FIXTURES_DIR / "big.mp4"

    # Observe which API calls the page makes so we can assert the routing.
    api_calls = []
    page.on("request", lambda req: api_calls.append(req.url) if "/api/" in req.url else None)

    _enter_editor(page, app_url, big)
    duration = _duration(page)
    assert duration > 50, f"big.mp4 should be >50s for a meaningful trim, got {duration}"

    _split_at(page, 20, duration)
    _select_segment(page, 1)  # tail segment [20s, end)
    _delete_selected(page)

    page.click("#editExportBtn")
    page.wait_for_selector("#screen-done.active", timeout=SERVER_TIMEOUT_MS)

    assert any("/api/trim" in u for u in api_calls), (
        f"expected the export to POST /api/trim (server path), saw: "
        f"{[u for u in api_calls if '/api/' in u]}"
    )

    with page.expect_download(timeout=60000) as dl_info:
        page.click("#saveBtn")
    out_path = tmp_path / "big_server_trimmed.mp4"
    dl_info.value.save_as(str(out_path))

    assert out_path.stat().st_size > 0
    probe = ffprobe(out_path)
    out_duration = float(probe["format"]["duration"])
    assert out_duration == pytest.approx(20.0, abs=0.5), (
        f"expected ~20s server-trimmed output, got {out_duration}s"
    )
    assert decode_clean(out_path), "server trim output does not decode cleanly"
