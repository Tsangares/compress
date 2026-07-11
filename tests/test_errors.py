"""Coverage for how the main app (index.html/app.js) handles bad/hostile
file input — content that decodes as garbage, and files whose type doesn't
even claim to be a video. `accept="video/*"` on #fileInput (index.html:43)
only advises the OS file picker; Playwright's `set_input_files()` (like a
user manually switching to "All files" in a native picker, or a
mis-extensioned upload) can hand the app anything, so app.js's own runtime
handling is what actually matters here.

Local-only (no backend/network dependency) — everything here is pure
client-side <input>/<video> behavior.
"""
import os

from conftest import FIXTURES_DIR, wait_engine_ready


def _goto_ready(page, app_url):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)


# ============================================
# 1. Corrupt-but-video-typed file
# ============================================
def test_corrupt_video_shows_error(page, app_url, tmp_path):
    """A file named *.mp4 full of random bytes gets `file.type ==
    'video/mp4'` from the browser (MIME is inferred from the extension, not
    content-sniffed), so it passes handleFile()'s `file.type.startsWith
    ('video/')` gate (app.js:195) and proceeds to set it as the <video>
    preview's src. The container is garbage, so the <video> element's
    `error` event fires; app.js's handler for that (app.js:202-213) is
    intentional graceful degradation for this exact situation (real-world
    equivalent: an HEVC .mov or oddly-muxed file the browser can't preview
    but ffmpeg might still handle) — it must show a toast, zero out
    width/height instead of leaving them at a stale or 0x0-looking state,
    and still land on the options screen rather than hanging."""
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(os.urandom(200 * 1024))

    _goto_ready(page, app_url)
    page.set_input_files("#fileInput", str(corrupt))

    page.wait_for_selector("#screen-options.active", timeout=15000)

    # Must not silently present a broken 0x0 dims screen: app.js's onerror
    # path (app.js:208) sets a friendly placeholder, not "0x0".
    res_text = (page.text_content("#infoRes") or "").strip()
    assert res_text != "0x0", f"options screen shows raw 0x0 dimensions: {res_text!r}"
    assert "unavailable" in res_text.lower(), (
        f"expected app.js's 'Preview unavailable' placeholder, got: {res_text!r}"
    )

    # A user-visible error/toast must have been surfaced (app.js's
    # showToast() call at app.js:211), not a silent dead end.
    toast_text = page.evaluate(
        "() => { const el = document.getElementById('appToast'); return el ? el.textContent : null; }"
    )
    assert toast_text and toast_text.strip(), "expected a visible toast explaining the preview failure"

    # Recovery: a real, decodable file loaded right after must still work.
    small = FIXTURES_DIR / "small.mp4"
    page.set_input_files("#fileInput", str(small))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    res_text_2 = (page.text_content("#infoRes") or "").strip()
    assert "unavailable" not in res_text_2.lower(), (
        f"stale 'Preview unavailable' state leaked into the next, valid file: {res_text_2!r}"
    )


# ============================================
# 2. Non-video file (accept filter bypassed)
# ============================================
def test_text_file_rejected(page, app_url, tmp_path):
    """APP BUG: handleFile() (app.js:194-195) starts with
    `if (!file || !file.type.startsWith('video/')) return;` — a bare, silent
    early return with no user feedback whatsoever. `accept="video/*"` on
    #fileInput (index.html:43) is only a picker hint; nothing stops a
    non-video file from reaching this code (a mis-named/mis-tagged file, or
    a user overriding the OS picker's file-type filter), and
    set_input_files() reproduces exactly that here with a plain .txt file
    (browser-inferred `file.type === 'text/plain'`).

    Confirmed by direct repro: selecting a .txt file via #fileInput leaves
    the app parked on #screen-select with zero DOM change, zero toast, and
    zero console output — a real, reachable dead end where the user gets no
    indication their selection did anything at all.

    This assertion is written to FAIL (documenting the bug) rather than
    being softened into an xfail, since it's a genuine UX dead end reachable
    by any user whose file manager doesn't perfectly enforce the accept
    filter (very common on Android/desktop 'All files' pickers), not a
    testing artifact.
    """
    bad = tmp_path / "notes.txt"
    bad.write_text("this is not a video\n" * 200)

    _goto_ready(page, app_url)
    page.set_input_files("#fileInput", str(bad))

    # Give the (nonexistent) handling a generous window in case something
    # async eventually kicks in.
    page.wait_for_timeout(3000)

    screen = page.eval_on_selector(".screen.active", "el => el.id")
    toast_text = page.evaluate(
        "() => { const el = document.getElementById('appToast'); return el ? el.textContent : null; }"
    )

    assert screen != "screen-select" or (toast_text and toast_text.strip()), (
        "BUG: selecting a non-video file (app.js:195's "
        "`file.type.startsWith('video/')` gate in handleFile()) silently "
        "no-ops with no screen change and no toast/error — the user gets "
        "zero feedback that anything happened."
    )

    # Recovery: the app must still be usable afterwards even though this
    # bug exists — a real file loaded next should reach the options screen.
    small = FIXTURES_DIR / "small.mp4"
    page.set_input_files("#fileInput", str(small))
    page.wait_for_selector("#screen-options.active", timeout=15000)
