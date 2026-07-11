"""URL-paste flow: fetches a real video via the /api/info + /api/download
endpoints, which call out to yt-dlp against the live internet. These hit real
third-party services (YouTube etc.), so they're marked `network` and
deselected by default (see conftest.py's pytest_collection_modifyitems).

Run explicitly with:
    pytest tests/test_url.py -m network
or, to run the whole suite including network tests:
    pytest -m network
"""
import pytest

from conftest import wait_engine_ready

pytestmark = pytest.mark.network

# A short, stable, unlisted-friendly public domain / CC clip is ideal here,
# but any small, long-lived video works. Keeping this centralized so it's
# easy to swap if the URL rots.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" — first YouTube video, 19s


def test_url_paste_fetches_and_actions_appear(page, app_url, require_backend):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)

    page.fill("#urlInput", TEST_VIDEO_URL)
    page.click("#urlGoBtn")

    # yt-dlp info+download round trip against the live network — generous timeout.
    page.wait_for_selector("#urlActions:not(.hidden)", timeout=60000)

    # All the action buttons should be present and enabled once ready.
    for sel in ("#urlSaveBtn", "#urlShareBtn", "#urlCompressBtn", "#urlEditBtn", "#urlAudioBtn"):
        assert page.is_visible(sel), f"{sel} not visible after URL download completed"


def test_url_paste_then_compress(page, app_url, require_backend):
    page.goto(app_url, wait_until="load")
    wait_engine_ready(page)

    page.fill("#urlInput", TEST_VIDEO_URL)
    page.click("#urlGoBtn")
    page.wait_for_selector("#urlActions:not(.hidden)", timeout=60000)

    page.click("#urlCompressBtn")
    page.wait_for_selector("#screen-options.active", timeout=15000)
    page.click("#compressBtn")
    page.wait_for_selector("#screen-done.active", timeout=180000)
