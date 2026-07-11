"""Baseline sanity checks: the app loads cleanly, isolation is actually on,
and the ffmpeg.wasm engine reaches ready."""
from conftest import wait_engine_ready


def test_app_loads_with_zero_console_errors(page, app_url):
    # The `page` fixture itself asserts zero console/page errors at teardown;
    # this test just needs to visit the app and let that assertion run.
    page.goto(app_url, wait_until="load")
    page.wait_for_selector("#screen-select.active", timeout=10000)


def test_cross_origin_isolated(page, app_url):
    page.goto(app_url, wait_until="load")
    isolated = page.evaluate("crossOriginIsolated")
    assert isolated is True, (
        "crossOriginIsolated is False — COOP/COEP headers are missing or "
        "wrong; ffmpeg.wasm's SharedArrayBuffer usage requires this."
    )


def test_engine_reaches_ready(page, app_url):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page, timeout_ms=60000)
    label = page.text_content("#engineStatus .engine-label")
    assert label == "Engine ready"
