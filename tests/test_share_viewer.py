"""Coverage for the server-rendered share viewer at /v/<id> (dl-service.py's
`viewer_page()`, dl-service.py:1162) and its static template (v/index.html).

Requires the local backend (skips cleanly via `require_backend` otherwise)
since every test here needs a real share to exist. Uses tests/serve.py's /v/*
proxy branch (added alongside this file) which forwards GET /v/* to the
backend with the path unchanged, mirroring prod Caddy.

Note on PUBLIC_BASE: dl-service.py builds absolute OG/file URLs from
COMPRESS_PUBLIC_BASE, which conftest.py's `backend_info` fixture does not set
when it spawns the local backend — so it falls back to dl-service.py's
hardcoded default, "https://compress.applesauce.chat" (dl-service.py:37),
*regardless* of the local app_url/port. Assertions below check the URL
*path* (e.g. it ends with "/api/share/<id>/<filename>") rather than the host,
so they hold in both local and remote (--base-url) runs.
"""
import urllib.parse

import httpx
import pytest

from conftest import FIXTURES_DIR, wait_engine_ready


def _create_share(app_url, fixture_path, filename=None):
    """POST a fixture to /api/share the same way the app's fetch() calls do
    (via tests/serve.py's /api/* proxy -> dl-service.py's create_share())."""
    with open(fixture_path, "rb") as f:
        resp = httpx.post(
            f"{app_url}/api/share",
            files={"file": (filename or fixture_path.name, f, "video/mp4")},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()


def _is_expected_transcript_probe_404(msg: str) -> bool:
    """v/index.html's init() eagerly does `fetch('/api/share/<id>/transcript')`
    on every viewer page load (v/index.html:560-568) to render a
    previously-generated transcript if one exists. For any share that has
    never had a transcript generated (the normal case for a freshly-created
    share, as used throughout this file), the backend 404s
    (dl-service.py's get_transcript(), :1349-1352) and the JS silently
    swallows it (`catch (_) { /* no-op */ }`) — but Chromium's network
    monitor still logs 'Failed to load resource: ... 404' to the console
    regardless of the catch, since that logging happens at the network
    layer, not from an uncaught JS exception. This fires on effectively
    every real-world share view without a transcript, so it's treated as
    expected noise here rather than a bug."""
    return "failed to load resource" in msg.lower() and "404" in msg


def _xfail_if_vibrate_blocked(console_msgs):
    """KNOWN APP BUG (see tests/test_flows.py's identically-named helper for
    the full writeup): navigator.vibrate(10) in goToScreen() (app.js:154)
    fires unconditionally on every screen transition, including the
    trim-handoff path exercised here (viewer -> app.js's consumeShareParam()
    -> enterEditMode() -> goToScreen(5)), which reaches it with no preceding
    user gesture in this test. Chromium blocks vibrate() without "transient
    user activation" and logs a console.error; that's the browser correctly
    enforcing a real API constraint against real app code, not a test
    artifact, so it's reported via xfail(strict=False) instead of silently
    swallowed or hard-failed."""
    msgs = [m for m in console_msgs if not _is_expected_transcript_probe_404(m)]
    hits = [m for m in msgs if "vibrate" in m.lower()]
    if hits:
        pytest.xfail(
            "app bug: navigator.vibrate(10) in goToScreen() (app.js:154) fires "
            "without user activation on this programmatic-navigation handoff "
            f"path and Chromium blocked it: {hits[0]!r}"
        )
    other = [m for m in msgs if "vibrate" not in m.lower()]
    assert not other, f"unexpected console error(s) beyond the known vibrate issue: {other}"


# ============================================
# 1. OG/Twitter metadata (crawler-facing, plain HTTP)
# ============================================
def test_viewer_og_meta(app_url, require_backend):
    small = FIXTURES_DIR / "small.mp4"
    share = _create_share(app_url, small, filename="small.mp4")
    share_id = share["id"]

    r = httpx.get(f"{app_url}/v/{share_id}", timeout=30)
    assert r.status_code == 200, r.text
    html = r.text

    assert "<!--OG_META-->" not in html, "OG placeholder was not substituted"

    assert 'property="og:title"' in html
    assert "small.mp4" in html, "og:title should reference the shared filename"

    # og:video (dl-service.py has no og:video:url variant, just og:video /
    # og:video:secure_url) must point at the share file, quoted per
    # _safe_share_name()'s output.
    quoted_name = urllib.parse.quote("small.mp4")
    expected_suffix = f"/api/share/{share_id}/{quoted_name}"
    assert 'property="og:video"' in html
    assert expected_suffix in html, (
        f"expected an og:video URL ending in {expected_suffix!r}; got html snippet with no match"
    )

    assert 'name="twitter:card" content="player"' in html
    assert 'name="twitter:player"' in html
    assert f"/v/{share_id}" in html, "twitter:player should point back at the viewer page"


# ============================================
# 2. Real playback + button hrefs (browser)
# ============================================
@pytest.mark.allow_console_errors
def test_viewer_playback_and_buttons(page, app_url, require_backend):
    """Marked allow_console_errors: v/index.html's init() eagerly probes
    GET /api/share/<id>/transcript on every load to render a previously
    generated transcript if one exists (v/index.html:560-568); for a
    freshly-created share with no transcript yet, that 404s and Chromium
    logs a benign 'Failed to load resource' console.error even though the
    JS catches it silently. See _is_expected_transcript_probe_404()'s
    docstring above for the full writeup; asserted explicitly below rather
    than just relying on the blanket marker."""
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    small = FIXTURES_DIR / "small.mp4"
    share = _create_share(app_url, small, filename="small.mp4")
    share_id = share["id"]

    page.goto(f"{app_url}/v/{share_id}", wait_until="load")

    # Video element is created client-side by v/index.html's inline script
    # once /api/share/<id> metadata resolves.
    page.wait_for_selector(".player video", timeout=15000)
    page.wait_for_function(
        "document.querySelector('.player video').readyState >= 2", timeout=15000
    )

    quoted_name = urllib.parse.quote("small.mp4")
    download_href = page.eval_on_selector("#downloadBtn", "el => el.href")
    assert download_href.endswith(f"/api/share/{share_id}/{quoted_name}"), download_href

    trim_href = page.eval_on_selector("#trimBtn", "el => el.getAttribute('href')")
    assert trim_href == f"/?share={share_id}&action=trim", trim_href

    compress_href = page.eval_on_selector("#compressBtn", "el => el.getAttribute('href')")
    assert compress_href == f"/?share={share_id}&action=compress", compress_href

    other = [m for m in console_msgs if not _is_expected_transcript_probe_404(m)]
    assert not other, f"unexpected console error(s): {other}"


# ============================================
# 3. Trim handoff end-to-end: viewer -> app -> editor
# ============================================
@pytest.mark.allow_console_errors
def test_viewer_trim_handoff_end_to_end(page, app_url, require_backend):
    """Clicking Trim on the viewer must land the user in the main app's
    editor with the shared file actually loaded — exercises the full
    viewer -> app.js consumeShareParam() -> enterEditMode() chain."""
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    small = FIXTURES_DIR / "small.mp4"
    share = _create_share(app_url, small, filename="small.mp4")
    share_id = share["id"]

    page.goto(f"{app_url}/v/{share_id}", wait_until="load")
    page.wait_for_selector(".player video", timeout=15000)

    page.click("#trimBtn")
    page.wait_for_selector("#screen-edit.active", timeout=30000)

    page.wait_for_function(
        "document.getElementById('editTotalTime').textContent !== '0:00'", timeout=15000
    )
    duration = page.evaluate("document.querySelector('#editPreview').duration")
    assert duration > 0, f"expected a positive duration in the editor, got {duration}"

    _xfail_if_vibrate_blocked(console_msgs)


# ============================================
# 4. Missing/nonexistent share: graceful degradation
# ============================================
@pytest.mark.allow_console_errors
def test_viewer_missing_share_graceful(page, app_url, require_backend):
    """A syntactically-valid but nonexistent share id (8 lowercase-alnum
    chars, matching dl-service.py's r"[a-z0-9]{4,16}" check) must render the
    viewer shell with a 'Share not found'-ish OG title for crawlers, and in
    a real browser must show a user-visible error state rather than hanging
    on the 'Loading…' spinner forever.

    Marked allow_console_errors: v/index.html's init() does
    `fetch('/api/share/<id>')` first thing (v/index.html:303); for a
    nonexistent id this legitimately 404s and is handled gracefully
    (showError('Not found', ...)), but Chromium still logs the network-level
    'Failed to load resource' message regardless of the app's own handling
    (same mechanism as the transcript-probe 404 documented on
    _is_expected_transcript_probe_404() above). Asserted explicitly below."""
    console_msgs = []
    page.on("console", lambda m: console_msgs.append(m.text) if m.type == "error" else None)

    missing_id = "aaaabbbb"

    r = httpx.get(f"{app_url}/v/{missing_id}", timeout=30)
    assert r.status_code == 200, r.text
    html = r.text
    assert "<!--OG_META-->" not in html
    assert 'property="og:title"' in html
    assert "not found" in html.lower(), "expected a 'Share not found' style OG title"

    page.goto(f"{app_url}/v/{missing_id}", wait_until="load")

    # v/index.html's init() fetches /api/share/<id>; a 404 triggers
    # showError('Not found', ...), which replaces #content's innerHTML with
    # a `.error` block (see v/index.html:275-277, :305). It must NOT get
    # stuck on the initial "Loading…" placeholder.
    page.wait_for_selector(".error", timeout=15000)
    assert "Loading" not in (page.text_content("#content") or ""), (
        "viewer got stuck on the loading spinner instead of showing an error"
    )
    error_text = page.text_content(".error") or ""
    assert "not found" in error_text.lower() or "couldn't find" in error_text.lower(), (
        f"expected a user-visible 'not found' style message, got: {error_text!r}"
    )

    other = [m for m in console_msgs if not _is_expected_transcript_probe_404(m)]
    assert not other, f"unexpected console error(s) beyond the expected share-metadata 404: {other}"
