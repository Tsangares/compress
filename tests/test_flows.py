"""Page-flow / navigation coverage for the compress PWA.

Screens (index : DOM id): 0 #screen-select (home), 1 #screen-options,
2 #screen-progress, 3 #screen-done, 4 #screen-about, 5 #screen-edit. The
`active` class marks whichever one is current (see goToScreen() in app.js).

This file drives the screen graph itself — home/options/progress/done/
about/edit transitions, the browser back gesture (popstate -> SCREEN_BACK
map), job cancellation, the "compress another" reset, and the two inbound
handoff paths (?share=<id>&action=... and the Android share-target ?shared=
<id> stash pulled from CacheStorage). It does NOT re-cover the editor's
trim/split/export correctness (see test_trim.py) or the yt-dlp URL-paste
path against the live network (see test_url.py, marked `network`).

Per the harness contract, `page`/`mobile_page` fail the test on any
console error/pageerror unless marked `allow_console_errors` — several
tests here (cancel, back-during-progress) lean on that as a real assertion:
if cancelling a job left something throwing in the background, the fixture
teardown will catch it even though the test body doesn't check for it
explicitly.
"""
import base64
import subprocess

import httpx
import pytest
from playwright.sync_api import TimeoutError as PWTimeoutError

from conftest import wait_engine_ready, FIXTURES_DIR


# ============================================
# Helpers
# ============================================
def _goto_ready(page, app_url):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)


def _load_file_to_options(page, app_url, fixture_path):
    _goto_ready(page, app_url)
    page.set_input_files("#fileInput", str(fixture_path))
    page.wait_for_selector("#screen-options.active", timeout=15000)


def _active_screen_id(page):
    return page.eval_on_selector(".screen.active", "el => el.id")


def _assert_selector_never_appears(page, selector, seconds):
    """Poll for up to `seconds` and fail if `selector` shows up. Used to prove
    a cancelled job's background work never reaches a later screen."""
    try:
        page.wait_for_selector(selector, timeout=seconds * 1000)
    except PWTimeoutError:
        return
    pytest.fail(f"{selector} appeared unexpectedly within {seconds}s of cancelling")


def _create_share(app_url, fixture_path, filename=None):
    """POST a fixture to /api/share (dl-service.py's create_share()), which
    proxies through tests/serve.py the same way the live app's fetch() calls
    do. Returns the JSON body: {id, filename, size, url, share_url,
    expires_in}."""
    with open(fixture_path, "rb") as f:
        resp = httpx.post(
            f"{app_url}/api/share",
            files={"file": (filename or fixture_path.name, f, "video/mp4")},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def _xfail_if_vibrate_blocked(console_msgs):
    """KNOWN APP BUG: goToScreen() (app.js line 154, `if (navigator.vibrate)
    navigator.vibrate(10);`) fires unconditionally on every screen
    transition, including ones triggered by a fully programmatic navigation
    with no preceding user gesture — the three inbound-handoff paths in
    this file (?share=...&action=compress, ?share=...&action=trim, and the
    ?shared=<id> stash pull) all reach goToScreen() this way, straight out
    of handleFile()/enterEditMode() with zero clicks first. Chromium
    requires "transient user activation" for navigator.vibrate() and blocks
    it otherwise, logging: "Blocked call to navigator.vibrate because user
    hasn't tapped on the frame or any embedded frame yet." `navigator.
    vibrate` only feature-detects support (`if (navigator.vibrate)`), it
    never checks/handles the activation requirement, so this fires for real
    whenever one of these flows completes without a prior tap — which, for
    an actual Android share-target handoff or a tapped share link, is the
    *normal* case, not an edge case.

    Confirmed via direct repro (see the report for this task): running each
    of the three handoff tests solo reproduces this reliably; Chromium
    appears to throttle/dedup the identical repeated console message across
    sequential pages sharing one browser process, so within a single pytest
    session only *some* of the three fire on a given run (observed 1-2 of 3
    per run across repeated trials) — hence xfail(strict=False) via
    pytest.xfail() here instead of a flat assertion: report it clearly
    when Chromium does emit it, without flaking the suite on runs where the
    dedup happens to swallow it.
    """
    hits = [m for m in console_msgs if "vibrate" in m.lower()]
    if hits:
        pytest.xfail(
            "app bug: navigator.vibrate(10) in goToScreen() (app.js:154) fires "
            "without user activation on this programmatic-navigation handoff "
            f"path and Chromium blocked it: {hits[0]!r}"
        )
    other = [m for m in console_msgs if "vibrate" not in m.lower()]
    assert not other, f"unexpected console error(s) beyond the known vibrate issue: {other}"


def _make_tiny_clip(tmp_path):
    """A ~2s, tiny-resolution clip generated on the fly — small enough to
    base64-embed into a page.evaluate() call without the encode/transfer
    being the bottleneck (small.mp4 at 1.3MB would work but is slower to
    round-trip through base64 for no benefit in this test)."""
    path = tmp_path / "tiny_stash.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=green:s=160x90:d=2:r=15",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True, capture_output=True, timeout=30,
    )
    return path


# ============================================
# 1. Full click-driven nav matrix
# ============================================
def test_nav_matrix_basic(page, app_url):
    """Walk every UI-button-driven transition once: home -> load -> options
    -> back -> home(resume visible) -> resume -> options -> edit -> editor
    -> editBack -> options -> back -> home -> about -> back -> home.
    Asserts the correct `.screen.active` at every hop.

    KNOWN APP BUG (see test_resume_card_blocks_about_link_click below for the
    isolated repro): while the resume card is showing on the home screen
    (i.e. state.file is set, which it still is at the "navigate home first"
    step below), it visually overlaps and intercepts pointer events on the
    "About this app" link underneath it (.resume-card is
    `position:absolute; bottom:60px` in style.css:636-649 with no collision
    avoidance against .about-link in index.html:116 / style.css:1542-1557).
    A real click there times out (Playwright's `force=True` doesn't help —
    it skips Playwright's own actionability checks but still performs a real
    hit-tested mouse click, which the browser then delivers to whichever
    element is topmost, i.e. the resume card, not the link underneath it).
    We dispatch a synthetic `.click()` via JS here purely so this test can
    still exercise the rest of the matrix (about -> back -> home); the
    dedicated test below is the one that drives a *real* click and asserts
    the bug.
    """
    small = FIXTURES_DIR / "small.mp4"
    _goto_ready(page, app_url)
    assert _active_screen_id(page) == "screen-select"

    page.set_input_files("#fileInput", str(small))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    assert _active_screen_id(page) == "screen-options"

    page.click("[data-back]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"
    assert not page.eval_on_selector(
        "#resumeCard", "el => el.classList.contains('hidden')"
    ), "resume card should be visible on home once a file is loaded"

    page.click("#resumeBtn")
    page.wait_for_selector("#screen-options.active", timeout=10000)
    assert _active_screen_id(page) == "screen-options"

    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)
    assert _active_screen_id(page) == "screen-edit"

    page.click("#editBack")
    page.wait_for_selector("#screen-options.active", timeout=10000)
    assert _active_screen_id(page) == "screen-options"

    # aboutBtn only exists on the home screen — go home first via the
    # options screen's own back button.
    page.click("[data-back]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"

    # Synthetic JS click: see the KNOWN APP BUG note in this test's
    # docstring — the still-visible resume card intercepts a real click here.
    page.eval_on_selector("#aboutBtn", "el => el.click()")
    page.wait_for_selector("#screen-about.active", timeout=10000)
    assert _active_screen_id(page) == "screen-about"

    page.click("[data-back-home]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"


def test_resume_card_blocks_about_link_click(page, app_url):
    """APP BUG: once a file is loaded (state.file set) and the user backs
    out to the home screen, #resumeCard renders (style.css:636-649,
    `.resume-card { position: absolute; bottom: 60px; left: 24px;
    right: 24px; }`) with no collision handling against `#aboutBtn`
    ("About this app", index.html:116 / .about-link in style.css:1542-1557),
    which sits in-flow near the bottom of the same screen. In a normal
    (non-forced) click, the resume card physically intercepts pointer events
    meant for the About link and the click never lands — reproduced here
    with Playwright's default actionability check, which is exactly the
    check a real click/tap goes through.

    This assertion is written to FAIL (documenting the bug) rather than
    xfail, since it's a real, currently-reachable interaction dead end for
    users (load a file, go back, try to read About) and shouldn't be
    silently swallowed by the suite.
    """
    small = FIXTURES_DIR / "small.mp4"
    _load_file_to_options(page, app_url, small)
    page.click("[data-back]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert not page.eval_on_selector(
        "#resumeCard", "el => el.classList.contains('hidden')"
    ), "precondition: resume card must be visible for this bug to reproduce"

    try:
        page.click("#aboutBtn", timeout=3000)
        reached_about = True
    except PWTimeoutError:
        reached_about = False

    assert reached_about, (
        "BUG: #aboutBtn is unclickable via a real (non-forced) click while "
        "#resumeCard is visible on the home screen — the resume card's "
        "absolute-positioned box (style.css:636-649) overlaps and "
        "intercepts pointer events meant for the About link underneath it "
        "(index.html:116, style.css:1542-1557)."
    )


# ============================================
# 2. Browser back gesture (popstate -> SCREEN_BACK map)
# ============================================
def test_browser_back_gesture(page, app_url):
    """System back gesture (page.go_back(), which fires popstate) must follow
    app.js's SCREEN_BACK map rather than a plain history stack walk:
    edit(5) -> options(1), and done(3) -> home(0) directly (skipping
    progress/options) in a single back tap."""
    small = FIXTURES_DIR / "small.mp4"
    _load_file_to_options(page, app_url, small)

    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)

    page.go_back()
    page.wait_for_selector("#screen-options.active", timeout=10000)
    assert _active_screen_id(page) == "screen-options"

    page.go_back()
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"

    # Now drive a real (default-quality) compress through to the done screen,
    # then confirm a single back gesture from `done` lands on home per
    # SCREEN_BACK[3] = 0 — not on progress or options.
    #
    # Note: reusing *the exact same* file path in set_input_files() a second
    # time on the same <input> does not fire a native 'change' event in
    # Chromium (confirmed by direct repro: a fresh listener attached right
    # before the call never sees it fire) — that's a browser/Playwright
    # quirk around re-selecting an identical file, not an app bug, so a
    # different fixture is used here to get a real 'change' event.
    noaudio = FIXTURES_DIR / "noaudio.mp4"
    page.set_input_files("#fileInput", str(noaudio))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=60000)

    page.go_back()
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"


# ============================================
# 3. Cancel button actually stops the job
# ============================================
def test_cancel_actually_cancels(page, app_url):
    """Regression coverage for the recently-added cancelActiveJob(): hitting
    Cancel mid-compress must not just switch screens while the orphaned wasm
    job keeps running in the background and later hijacks the UI onto the
    done screen. Also verifies the engine comes back (terminate + reload)
    so a subsequent compress from the same options screen still works."""
    small = FIXTURES_DIR / "small.mp4"
    _load_file_to_options(page, app_url, small)

    page.click("#compressBtn")
    page.wait_for_selector("#screen-progress.active", timeout=10000)

    # Cancel shortly after the job starts (within ~1s), well before a 12s
    # clip could finish encoding.
    page.wait_for_timeout(800)
    page.click("#cancelBtn")
    page.wait_for_selector("#screen-options.active", timeout=10000)
    assert _active_screen_id(page) == "screen-options"

    # The old bug: the terminated job's promise chain still resolved and
    # called showDone() -> goToScreen(3), yanking the user away from
    # wherever they'd navigated to. Give it a generous window to prove it
    # doesn't happen.
    _assert_selector_never_appears(page, "#screen-done.active", 45)

    # Engine should have reloaded in the background after terminate(); once
    # ready, a fresh compress from this same options screen must succeed.
    wait_engine_ready(page, timeout_ms=90000)
    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=90000)
    assert _active_screen_id(page) == "screen-done"


# ============================================
# 4. Back gesture during progress also cancels
# ============================================
def test_back_during_progress_cancels(page, app_url):
    """Same as test_cancel_actually_cancels, but triggered via the system
    back gesture instead of the Cancel button — popstate's handler calls
    cancelActiveJob() itself when leaving screen index 2 (progress)."""
    small = FIXTURES_DIR / "small.mp4"
    _load_file_to_options(page, app_url, small)

    page.click("#compressBtn")
    page.wait_for_selector("#screen-progress.active", timeout=10000)

    page.wait_for_timeout(800)
    page.go_back()
    page.wait_for_selector("#screen-options.active", timeout=10000)
    assert _active_screen_id(page) == "screen-options"

    _assert_selector_never_appears(page, "#screen-done.active", 45)


# ============================================
# 5. "Compress another" resets everything
# ============================================
def test_another_resets_everything(page, app_url):
    """anotherBtn must reset state.file, the output blob, and the entire URL
    paste section (status/actions/input), and leave the app ready for a
    completely fresh flow."""
    small = FIXTURES_DIR / "small.mp4"
    _load_file_to_options(page, app_url, small)

    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=60000)

    page.click("#anotherBtn")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"

    assert page.eval_on_selector(
        "#resumeCard", "el => el.classList.contains('hidden')"
    ), "resume card should be hidden after 'compress another' resets state.file"
    assert page.eval_on_selector(
        "#urlActions", "el => el.classList.contains('hidden')"
    ), "url actions row should be hidden after reset"
    assert page.eval_on_selector(
        "#urlStatus", "el => el.classList.contains('hidden')"
    ), "url status row should be hidden after reset"
    assert page.input_value("#urlInput") == "", "url input should be cleared after reset"
    assert page.eval_on_selector("#fileInput", "el => el.value") == "", (
        "file input should be cleared after reset"
    )

    # Fresh flow still works immediately after the reset.
    page.set_input_files("#fileInput", str(small))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    assert _active_screen_id(page) == "screen-options"


# ============================================
# 6/7. Inbound share handoff: ?share=<id>&action=compress|trim
# ============================================
@pytest.mark.allow_console_errors
def test_share_param_compress_handoff(page, app_url, require_backend):
    """A viewer-page share link with ?share=<id>&action=compress should pull
    the shared file down (GET /api/share/<id> for metadata, then fetch the
    file itself) and hand it to the normal compress flow — landing on the
    options screen with real file metadata, and stripping the share/action
    params from the URL bar (consumeShareParam's history.replaceState).

    Marked allow_console_errors: this handoff completes with no preceding
    user gesture, which can trip the navigator.vibrate() bug documented on
    _xfail_if_vibrate_blocked() above — that helper (not this marker) is
    what actually turns a real occurrence into a reported, non-flaky xfail.
    """
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    small = FIXTURES_DIR / "small.mp4"
    share = _create_share(app_url, small)
    share_id = share["id"]
    assert share_id and share["size"] > 0

    page.goto(f"{app_url}/?share={share_id}&action=compress", wait_until="load")
    page.wait_for_selector("#screen-options.active", timeout=30000)
    assert _active_screen_id(page) == "screen-options"

    assert "share=" not in page.url and "action=" not in page.url, (
        f"share/action params were not stripped from the URL bar: {page.url}"
    )

    size_text = page.text_content("#infoSize")
    assert size_text and size_text.strip() != "--", (
        "options screen should show real file metadata for the handed-off share"
    )

    _xfail_if_vibrate_blocked(console_msgs)


@pytest.mark.allow_console_errors
def test_share_param_trim_handoff(page, app_url, require_backend):
    """Same handoff, but action=trim should land in the editor (screen-edit)
    with a real, positive duration loaded — consumeShareParam clicks the URL
    section's hidden 'edit' button once the shared file is downloaded.

    Marked allow_console_errors: see test_share_param_compress_handoff's
    docstring — same navigator.vibrate()-without-activation bug can trip on
    this path too (enterEditMode() -> goToScreen(5) with no prior click).
    """
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    small = FIXTURES_DIR / "small.mp4"
    share = _create_share(app_url, small)
    share_id = share["id"]

    page.goto(f"{app_url}/?share={share_id}&action=trim", wait_until="load")
    page.wait_for_selector("#screen-edit.active", timeout=30000)
    assert _active_screen_id(page) == "screen-edit"

    page.wait_for_function(
        "document.getElementById('editTotalTime').textContent !== '0:00'", timeout=15000
    )
    duration = page.evaluate("document.querySelector('#editPreview').duration")
    assert duration > 0, f"expected a positive duration in the editor, got {duration}"

    assert "share=" not in page.url and "action=" not in page.url, (
        f"share/action params were not stripped from the URL bar: {page.url}"
    )

    _xfail_if_vibrate_blocked(console_msgs)


# ============================================
# 8. Android share-target stash handoff: ?shared=<id>
# ============================================
@pytest.mark.allow_console_errors
def test_shared_stash_flow(page, app_url, tmp_path):
    """Simulates the share_target handoff the service worker performs for a
    native Android "Share to Compress": sw.js stashes the shared file in the
    'share-probe-v1' CacheStorage bucket under /__share-probe/<id> and
    redirects to /?shared=<id>; app.js's consumeSharedVideo() then fetches
    that path (intercepted by the SW's own fetch handler), builds a File,
    and calls handleFile() -> options screen. It also frees the stash
    immediately after reading it.

    We can't drive an actual OS share sheet here, so this test reproduces
    the SW's half directly: register the SW, wait for it to be ready (so it
    will actually intercept the subsequent navigation's fetch), put a
    Response into the cache ourselves, then navigate with ?shared=<id> and
    let the real app code do the rest.

    Marked allow_console_errors: see test_share_param_compress_handoff's
    docstring — this is the third of the three programmatic-navigation
    paths that can trip the navigator.vibrate()-without-activation bug.
    """
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    clip = _make_tiny_clip(tmp_path)
    b64 = base64.b64encode(clip.read_bytes()).decode("ascii")
    share_id = "testid12"

    page.goto(app_url, wait_until="load")
    # Ensure the SW is actually active before we navigate again — otherwise
    # the next navigation might not be intercepted by its fetch handler.
    page.evaluate("() => navigator.serviceWorker.ready")

    page.evaluate(
        """
        async ({ b64, id, name, mime }) => {
            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const blob = new Blob([bytes], { type: mime });
            const headers = new Headers({
                'Content-Type': mime,
                'X-Original-Name': encodeURIComponent(name),
                'X-Original-Type': mime,
            });
            const resp = new Response(blob, { headers });
            const cache = await caches.open('share-probe-v1');
            await cache.put(new Request('/__share-probe/' + id), resp);
        }
        """,
        {"b64": b64, "id": share_id, "name": "stash.mp4", "mime": "video/mp4"},
    )

    page.goto(f"{app_url}/?shared={share_id}", wait_until="load")
    page.wait_for_selector("#screen-options.active", timeout=20000)
    assert _active_screen_id(page) == "screen-options"

    assert "shared=" not in page.url, f"shared param not stripped from URL: {page.url}"

    # The stash should be freed right after being consumed (app.js deletes it
    # via a fire-and-forget caches.open().then(c => c.delete(...))) — poll
    # briefly since that delete isn't awaited by handleFile().
    def _stash_gone():
        return not page.evaluate(
            """
            async (id) => {
                const cache = await caches.open('share-probe-v1');
                const keys = await cache.keys();
                return keys.some((r) => r.url.includes(id));
            }
            """,
            share_id,
        )

    for _ in range(20):
        if _stash_gone():
            break
        page.wait_for_timeout(150)
    assert _stash_gone(), "stashed share-probe cache entry was not freed after being consumed"

    _xfail_if_vibrate_blocked(console_msgs)


# ============================================
# 10. About screen roundtrip + no stray-history crash
# ============================================
def test_about_roundtrip_and_history(page, app_url):
    """home -> about -> [data-back-home] -> home, then a further back
    gesture must not throw or strand the app: goToScreen(0) never pushes a
    history entry, so popstate's guard (`state.currentScreen > 0`) is
    already false and the handler no-ops instead of touching the DOM."""
    _goto_ready(page, app_url)
    assert _active_screen_id(page) == "screen-select"

    page.click("#aboutBtn")
    page.wait_for_selector("#screen-about.active", timeout=10000)

    page.click("[data-back-home]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    assert _active_screen_id(page) == "screen-select"

    # Should not crash/throw (the `page` fixture would fail the test on any
    # pageerror/console.error), and home should still be showing.
    page.go_back()
    page.wait_for_timeout(300)
    assert _active_screen_id(page) == "screen-select"
