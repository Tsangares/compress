"""Mobile-viewport smoke tests: 390x844, is_mobile, has_touch, dsf=3.

Screenshots every reachable screen and asserts the primary action button on
each is fully within the viewport. Also checks touch scrubbing on the
editor's timeline works without throwing.

Note: .screen elements slide in via a CSS transform transition (~0.45s, see
style.css .screen.active) — bounding-box reads taken immediately after the
`.active` class lands can catch mid-transition geometry, so every screen
transition here is followed by a short settle wait before measuring/
screenshotting.
"""
from conftest import wait_engine_ready, FIXTURES_DIR, SCREENSHOTS_DIR

SETTLE_MS = 600
VIEWPORT = {"width": 390, "height": 844}


def _in_viewport(box, viewport=VIEWPORT, tol=1):
    return (
        box["x"] >= -tol
        and box["y"] >= -tol
        and box["x"] + box["width"] <= viewport["width"] + tol
        and box["y"] + box["height"] <= viewport["height"] + tol
    )


def _box(page, selector):
    return page.eval_on_selector(
        selector,
        "el => { const r = el.getBoundingClientRect(); "
        "return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
    )


def test_home_screen_primary_action_in_viewport(mobile_page, app_url):
    page = mobile_page
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.wait_for_timeout(SETTLE_MS)

    page.screenshot(path=str(SCREENSHOTS_DIR / "mobile_home.png"))

    box = _box(page, "#dropZone")
    assert _in_viewport(box), f"file picker (#dropZone) out of viewport: {box}"


def test_preview_quality_screen_primary_action_in_viewport(mobile_page, app_url):
    page = mobile_page
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(FIXTURES_DIR / "small.mp4"))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.wait_for_timeout(SETTLE_MS)

    page.screenshot(path=str(SCREENSHOTS_DIR / "mobile_preview_quality.png"))

    box = _box(page, "#compressBtn")
    assert _in_viewport(box), f"#compressBtn out of viewport: {box}"


def test_editor_screen_primary_action_in_viewport_and_touch_interactions(mobile_page, app_url):
    page = mobile_page
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(FIXTURES_DIR / "small.mp4"))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.wait_for_timeout(SETTLE_MS)

    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)
    page.wait_for_function(
        "document.getElementById('editTotalTime').textContent !== '0:00'", timeout=10000
    )
    page.wait_for_timeout(SETTLE_MS)

    page.screenshot(path=str(SCREENSHOTS_DIR / "mobile_editor.png"))

    box = _box(page, "#editExportBtn")
    assert _in_viewport(box), f"#editExportBtn out of viewport: {box}"

    duration = page.evaluate("document.querySelector('#editPreview').duration")
    track_box = _box(page, "#timelineTrack")

    # Touchscreen tap on the timeline seeks the playhead (scrubTimeline()
    # reads touchstart via dom.timelineScroll's 'touchstart' listener).
    mid_x = track_box["x"] + track_box["width"] / 2
    mid_y = track_box["y"] + track_box["height"] / 2
    page.touchscreen.tap(mid_x, mid_y)
    page.wait_for_timeout(200)
    current_time = page.evaluate("document.querySelector('#editPreview').currentTime")
    expected = duration / 2
    assert abs(current_time - expected) < 1.0, (
        f"touch tap on timeline did not seek near mid-point: "
        f"currentTime={current_time}, expected~={expected}"
    )

    # A horizontal drag across the timeline (simulated via touchstart/move/end
    # dispatch, since Playwright's high-level touchscreen API has no native
    # drag helper) must not throw any errors — this exercises the same
    # 'touchmove' scrubbing path plus (if it registers as 2 touches) the
    # pinch-zoom path.
    page.evaluate(
        """
        () => {
            const el = document.getElementById('timelineScroll');
            const rect = el.getBoundingClientRect();
            const y = rect.y + rect.height / 2;
            const startX = rect.x + rect.width * 0.2;
            const endX = rect.x + rect.width * 0.8;
            function fire(type, x, y) {
                const touch = new Touch({
                    identifier: 1, target: el, clientX: x, clientY: y,
                });
                const ev = new TouchEvent(type, {
                    cancelable: true, bubbles: true,
                    touches: type === 'touchend' ? [] : [touch],
                    changedTouches: [touch],
                    targetTouches: type === 'touchend' ? [] : [touch],
                });
                el.dispatchEvent(ev);
            }
            fire('touchstart', startX, y);
            fire('touchmove', (startX + endX) / 2, y);
            fire('touchmove', endX, y);
            fire('touchend', endX, y);
        }
        """
    )
    page.wait_for_timeout(200)
    # If we got here without the `page` fixture's teardown assertion firing
    # on a pageerror/console.error, the drag didn't throw.


def test_done_screen_primary_action_in_viewport(mobile_page, app_url):
    page = mobile_page
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)
    page.set_input_files("#fileInput", str(FIXTURES_DIR / "small.mp4"))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.wait_for_timeout(SETTLE_MS)

    page.click('.pill[data-quality="low"]')  # fastest preset for a quick mobile smoke test
    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=180000)
    page.wait_for_timeout(SETTLE_MS)

    page.screenshot(path=str(SCREENSHOTS_DIR / "mobile_done.png"))

    box = _box(page, "#saveBtn")
    assert _in_viewport(box), f"#saveBtn out of viewport: {box}"
