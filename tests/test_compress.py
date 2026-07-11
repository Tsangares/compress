"""Compress flow: pick a file, hit each quality preset, verify the download."""
import pytest

from conftest import wait_engine_ready, ffprobe, FIXTURES_DIR

QUALITIES = ["high", "medium", "low", "target"]


def _load_file_and_wait_options_screen(page, app_url, path):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(path))
    page.wait_for_selector("#screen-options.active", timeout=15000)


@pytest.mark.parametrize("quality", QUALITIES)
def test_compress_quality_preset(page, app_url, quality, tmp_path):
    small = FIXTURES_DIR / "small.mp4"
    assert small.exists(), "run tests/make_fixtures.sh first"

    _load_file_and_wait_options_screen(page, app_url, small)

    # Select the quality pill.
    page.click(f'.pill[data-quality="{quality}"]')

    # Kick off compression.
    page.click("#compressBtn")

    # wasm compression is slow in headless chromium — be generous.
    page.wait_for_selector("#screen-done.active", timeout=180000)

    # Download via the Save button.
    with page.expect_download(timeout=30000) as dl_info:
        page.click("#saveBtn")
    download = dl_info.value

    out_path = tmp_path / f"{quality}_compressed.mp4"
    download.save_as(str(out_path))

    assert out_path.stat().st_size > 0, "downloaded file is empty"

    probe = ffprobe(out_path)
    fmt = probe["format"]
    assert "mp4" in fmt["format_name"], f"unexpected container: {fmt['format_name']}"

    streams = probe["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]

    assert video_streams, "no video stream in output"
    assert video_streams[0]["codec_name"] == "h264", (
        f"expected h264 video, got {video_streams[0]['codec_name']}"
    )

    assert audio_streams, "no audio stream in output (source had audio)"
    assert audio_streams[0]["codec_name"] == "aac", (
        f"expected aac audio, got {audio_streams[0]['codec_name']}"
    )

    duration = float(fmt["duration"])
    assert abs(duration - 12.0) <= 0.5, f"duration {duration} not within 12±0.5s"
