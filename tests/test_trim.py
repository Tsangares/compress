"""Editor (trim/split) flow.

PRIORITY: these tests are expected to FAIL today. That's the baseline that
proves the known bugs in app.js's stream-copy export path (exportEdit(),
app.js ~1805-1912):

  - Segments are cut with `-ss <t> -i input -c copy` (input seeking). With
    `-c copy` ffmpeg cannot re-encode to land exactly on the requested cut
    point, so it snaps to the nearest preceding keyframe. small.mp4 is
    generated with `-g 90` (3s GOP @ 30fps) specifically so a split at ~4s or
    ~8s falls mid-GOP, guaranteeing the snap-back is visible in the output
    (wrong duration, non-zero start PTS, audio/video start drift).
  - The single-segment export path (segments.length === 1, app.js ~1837-1849)
    is missing `-avoid_negative_ts make_zero`, which the multi-segment path
    does have (~1869) — so even a simple single end-trim can produce
    negative/misaligned starting timestamps.

Drives the real UI: click-drag/click on the timeline to scrub (mirrors
scrubTimeline() in app.js, wired to mousedown/touchstart on #timelineScroll,
reading position against #timelineTrack's bounding box), then click
#editSplitBtn (reads state from the video element's currentTime).
"""
import pytest

from conftest import wait_engine_ready, ffprobe, decode_clean, FIXTURES_DIR


def _enter_editor(page, app_url, fixture_path):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(fixture_path))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)
    # Wait for loadedmetadata -> editTotalTime to be populated (enterEditMode
    # sets it inside video.onloadedmetadata, right before goToScreen(5) and
    # kicking off thumbnail generation).
    page.wait_for_function(
        "document.getElementById('editTotalTime').textContent !== '0:00'", timeout=10000
    )
    # Let requestAnimationFrame-triggered thumbnail/segment rendering settle.
    page.wait_for_timeout(500)


def _duration(page):
    return page.evaluate("document.querySelector('#editPreview').duration")


def _scrub_to(page, t, duration):
    box = page.eval_on_selector(
        "#timelineTrack",
        "el => { const r = el.getBoundingClientRect(); "
        "return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
    )
    x = box["x"] + max(1, min(box["width"] - 1, (t / duration) * box["width"]))
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.up()


def _split_at(page, t, duration):
    _scrub_to(page, t, duration)
    page.click("#editSplitBtn")
    page.wait_for_timeout(100)


def _select_segment(page, index):
    """Select the Nth (0-based) segment via its timeline overlay.

    Note: the timeline-segment overlay (app.js renderSegments()) has its own
    'click' handler that toggles selection with e.stopPropagation(). Since
    #timelineScroll's scrubbing is wired to 'mousedown' (not 'click'), a
    plain click on the timeline for scrubbing purposes *also* fires a click
    on whatever segment happens to sit under the cursor — so _scrub_to()/
    _split_at() calls can leave a segment already selected as a side effect.
    Selection is a single toggle (editState.selectedSegment), so clicking an
    already-selected target would deselect it. Check first and only click if
    it isn't already the selected one.
    """
    loc = page.locator(".timeline-segment").nth(index)
    cls = loc.get_attribute("class") or ""
    if "selected" not in cls.split():
        loc.click()
        page.wait_for_timeout(100)
    cls = loc.get_attribute("class") or ""
    assert "selected" in cls.split(), f"segment {index} did not end up selected (class={cls!r})"


def _delete_selected(page):
    page.click("#editDeleteBtn")
    page.wait_for_timeout(100)


def _export_and_download(page, tmp_path, name):
    page.click("#editExportBtn")
    page.wait_for_selector("#screen-done.active", timeout=180000)
    with page.expect_download(timeout=30000) as dl_info:
        page.click("#saveBtn")
    out_path = tmp_path / name
    dl_info.value.save_as(str(out_path))
    return out_path


def test_split_middle_delete_export(page, app_url, tmp_path):
    """Split small.mp4 (12s) at ~4s and ~8s, delete the middle segment,
    export. Expect ~8s of output (0-4s + 8-12s stitched via concat)."""
    small = FIXTURES_DIR / "small.mp4"
    _enter_editor(page, app_url, small)
    duration = _duration(page)
    assert duration == pytest.approx(12.0, abs=0.5)

    _split_at(page, 4, duration)
    _split_at(page, 8, duration)

    seg_texts = page.locator(".segment-item").all_inner_texts()
    assert len(seg_texts) == 3, f"expected 3 segments after 2 splits, got: {seg_texts}"

    # Middle segment is index 1 ([4s, 8s)).
    _select_segment(page, 1)
    _delete_selected(page)

    out_path = _export_and_download(page, tmp_path, "trim_middle_deleted.mp4")
    assert out_path.stat().st_size > 0

    probe = ffprobe(out_path)
    out_duration = float(probe["format"]["duration"])

    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    video_start = float(video_stream.get("start_time", 0))
    audio_start = float(audio_stream.get("start_time", 0))

    clean = decode_clean(out_path)

    # Expected (correct) behavior — kept segments are 0-4s + 8-12s = 8s total.
    assert out_duration == pytest.approx(8.0, abs=0.5), (
        f"expected ~8s output, got {out_duration}s "
        "(stream-copy split snapped to nearest keyframe instead of the exact cut point)"
    )
    assert clean, "output does not decode cleanly (ffmpeg -f null - reported errors/warnings)"
    assert video_start >= 0, f"video stream start_time is negative: {video_start}"
    assert abs(video_start - audio_start) <= 0.15, (
        f"audio/video start_time drift too large: video={video_start} audio={audio_start}"
    )


def test_single_segment_end_trim(page, app_url, tmp_path):
    """Split at ~8s, delete the tail segment -> single kept segment [0, 8s).
    Exercises the segments.length === 1 export path, which is missing
    -avoid_negative_ts make_zero (present on the multi-segment path)."""
    small = FIXTURES_DIR / "small.mp4"
    _enter_editor(page, app_url, small)
    duration = _duration(page)

    _split_at(page, 8, duration)
    seg_texts = page.locator(".segment-item").all_inner_texts()
    assert len(seg_texts) == 2, f"expected 2 segments after 1 split, got: {seg_texts}"

    _select_segment(page, 1)  # tail segment [8s, 12s)
    _delete_selected(page)

    out_path = _export_and_download(page, tmp_path, "trim_end.mp4")
    assert out_path.stat().st_size > 0

    probe = ffprobe(out_path)
    out_duration = float(probe["format"]["duration"])
    assert out_duration == pytest.approx(8.0, abs=0.5), (
        f"expected ~8s output for single-segment end-trim, got {out_duration}s"
    )


def test_single_segment_start_trim_mid_gop(page, app_url, tmp_path):
    """Single kept segment that does NOT start at 0: split at ~4s (mid-GOP,
    since small.mp4 uses -g 90 = 3s GOPs), delete the head, keep the tail
    [4s, 12s) as the lone remaining segment. This is the case that actually
    exercises the missing `-avoid_negative_ts make_zero` on the
    segments.length === 1 export path (app.js ~1837-1849) — the end-trim
    variant above keeps segment [0, 8s), which starts at an already-aligned
    keyframe (0), so it never touches this bug."""
    small = FIXTURES_DIR / "small.mp4"
    _enter_editor(page, app_url, small)
    duration = _duration(page)

    _split_at(page, 4, duration)
    seg_texts = page.locator(".segment-item").all_inner_texts()
    assert len(seg_texts) == 2, f"expected 2 segments after 1 split, got: {seg_texts}"

    _select_segment(page, 0)  # head segment [0s, 4s)
    _delete_selected(page)

    out_path = _export_and_download(page, tmp_path, "trim_start_mid_gop.mp4")
    assert out_path.stat().st_size > 0

    probe = ffprobe(out_path)
    out_duration = float(probe["format"]["duration"])
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    video_start = float(video_stream.get("start_time", 0))

    assert out_duration == pytest.approx(8.0, abs=0.5), (
        f"expected ~8s output for a mid-GOP single-segment start-trim, got {out_duration}s "
        "(missing -avoid_negative_ts make_zero on the single-segment export path, or "
        "keyframe-snap on the -ss input seek)"
    )
    assert video_start >= -0.05, (
        f"video stream start_time is negative ({video_start}) — the single-segment "
        "export path lacks -avoid_negative_ts make_zero that the multi-segment path has"
    )


def test_export_noaudio_does_not_error(page, app_url, tmp_path):
    """Trimming a video with no audio track must not throw (the multi-segment
    concat path assumes nothing audio-specific, but worth covering explicitly
    since AAC-audio assumptions elsewhere in the app are common failure
    points)."""
    noaudio = FIXTURES_DIR / "noaudio.mp4"
    _enter_editor(page, app_url, noaudio)
    duration = _duration(page)

    _split_at(page, 4, duration)
    _split_at(page, 8, duration)
    _select_segment(page, 1)
    _delete_selected(page)

    out_path = _export_and_download(page, tmp_path, "trim_noaudio.mp4")
    assert out_path.stat().st_size > 0
    probe = ffprobe(out_path)
    assert not any(s["codec_type"] == "audio" for s in probe["streams"]), (
        "expected no audio stream in noaudio.mp4 trim output"
    )


def test_enter_exit_editor_twice_then_export(page, app_url, tmp_path):
    """Regression coverage for the URL-leak path: enter the editor, back out
    to options, re-enter, then perform a real edit and export. enterEditMode()
    creates a fresh URL.createObjectURL(state.file) every call without
    revoking the previous one — this test checks that re-entering doesn't
    break the export flow (it does not assert anything about actual memory
    leak, which isn't observable from outside the page)."""
    small = FIXTURES_DIR / "small.mp4"
    _enter_editor(page, app_url, small)

    # Back out to options screen.
    page.click("#editBack")
    page.wait_for_selector("#screen-options.active", timeout=10000)

    # Re-enter the editor.
    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)
    page.wait_for_function(
        "document.getElementById('editTotalTime').textContent !== '0:00'", timeout=10000
    )
    page.wait_for_timeout(500)

    duration = _duration(page)
    _split_at(page, 4, duration)
    _select_segment(page, 0)
    _delete_selected(page)

    out_path = _export_and_download(page, tmp_path, "trim_reenter.mp4")
    assert out_path.stat().st_size > 0
