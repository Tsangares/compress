"""One-off visual sweep: screenshot every screen at desktop + mobile sizes.

Not auto-collected (filename lacks test_ prefix). Run explicitly:
    python3 -m pytest tests/visual_sweep.py -q
Screenshots land in $SWEEP_OUT (default: tests/.sweep).
"""
import os
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
OUT = Path(os.environ.get("SWEEP_OUT", TESTS_DIR / ".sweep"))

VIEWPORTS = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "mobile": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 2,
        "is_mobile": True,
        "has_touch": True,
    },
}


def _shot(page, label, name):
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"{label}-{name}.png"))


@pytest.mark.parametrize("label", list(VIEWPORTS.keys()))
def test_sweep(browser, app_url, label):
    ctx = browser.new_context(**VIEWPORTS[label])
    page = ctx.new_page()
    small = FIXTURES_DIR / "small.mp4"

    page.goto(app_url, wait_until="load")
    page.wait_for_timeout(800)
    _shot(page, label, "01-home")

    # URL-active home state
    page.click("#urlInput")
    page.fill("#urlInput", "https://www.tiktok.com/@example/video/123")
    page.wait_for_timeout(400)
    _shot(page, label, "02-home-url-active")
    page.fill("#urlInput", "")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Options
    page.set_input_files("#fileInput", str(small))
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.wait_for_timeout(600)
    _shot(page, label, "03-options")

    # Home with resume card
    page.click("[data-back]")
    page.wait_for_selector("#screen-select.active", timeout=10000)
    page.wait_for_timeout(500)
    _shot(page, label, "04-home-resume-card")

    # About
    page.click("#aboutBtn")
    page.wait_for_selector("#screen-about.active", timeout=10000)
    page.wait_for_timeout(400)
    _shot(page, label, "05-about")
    page.go_back()
    page.wait_for_selector("#screen-select.active", timeout=10000)

    # Edit (trim) screen
    page.click("#resumeBtn")
    page.wait_for_selector("#screen-options.active", timeout=10000)
    page.click("#editBtn")
    page.wait_for_selector("#screen-edit.active", timeout=15000)
    page.wait_for_timeout(2500)  # let thumbnails render
    _shot(page, label, "06-edit")
    page.click("#editBack")
    page.wait_for_selector("#screen-options.active", timeout=10000)

    # Progress + done (wait for engine first so compress starts immediately)
    page.wait_for_selector("#engineStatus.ready", timeout=60000, state="attached")
    page.click("#compressBtn")
    page.wait_for_selector("#screen-progress.active", timeout=10000)
    page.wait_for_timeout(1500)
    _shot(page, label, "07-progress")
    page.wait_for_selector("#screen-done.active", timeout=120000)
    page.wait_for_timeout(600)
    _shot(page, label, "08-done")

    ctx.close()
