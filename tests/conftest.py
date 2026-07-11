"""
Shared pytest fixtures for the compress PWA test harness.

Local mode (default --base-url http://127.0.0.1:8899):
    - generates tests/fixtures/*.mp4 (idempotent)
    - starts tests/serve.py as a subprocess (static + COOP/COEP + /api/* proxy)
    - starts the local dl-service.py backend with COMPRESS_DL_DIR/COMPRESS_SHARE_DIR
      pointed at a scratch dir, and COMPRESS_PORT=8092 (dl-service.py defaults
      to 8090 to match prod, but this dev machine has an unrelated service
      permanently bound to 0.0.0.0:8090, so an alternate port is used here —
      distinct from test_api.py's own self-contained backend on 8091). If it
      still fails to boot (missing deps, chosen port also taken, etc.),
      backend-dependent tests are skipped with a clear reason instead of
      erroring.

Remote mode (--base-url https://compress.applesauce.chat):
    - starts nothing locally; tests run against the live site.

tests/test_server_path.py covers the large-file (>50MB) server-side compress
and /trim paths (requires the local backend; skips cleanly otherwise).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SCREENSHOTS_DIR = TESTS_DIR / "screenshots"

DEFAULT_BASE_URL = "http://127.0.0.1:8899"
SCRATCH_DIR = Path(
    "/tmp/claude-1000/-home-wil/b5b23348-1569-4e57-9b21-2636b4882746/scratchpad/compress-dl-test"
)

# Backend readiness is tracked here so tests can introspect *why* they were
# skipped rather than just that they were.
_backend_state = {"available": False, "reason": "not started"}


def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        action="store",
        default=DEFAULT_BASE_URL,
        help="Base URL to test against. Default starts a local server + backend. "
        "Point at https://compress.applesauce.chat to test the live site (starts nothing local).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_console_errors: this test expects console errors/pageerrors and opts out of the zero-error assertion"
    )
    config.addinivalue_line(
        "markers", "network: hits the live network (e.g. real URL downloads via /api). Deselected by default."
    )


def pytest_collection_modifyitems(config, items):
    # `network` tests are opt-in only: run with `pytest -m network` explicitly,
    # or `pytest -m "" ` to include everything. By default we deselect them.
    if config.getoption("-m", default=""):
        return  # user already specified a marker expression; don't override
    skip_network = pytest.mark.skip(reason="network-marked test; run explicitly with -m network to opt in")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)


def _is_local(base_url: str) -> bool:
    return "127.0.0.1" in base_url or "localhost" in base_url


def _port_open(host: str, port: int, timeout=0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _free_port() -> int:
    """Ask the OS for an unused TCP port. This dev machine runs a lot of
    unrelated background services (observed: 8090, 8092, 8095, 8099 all
    occupied at various points), so hardcoding a "spare" port number is
    fragile — bind-to-0-then-release is the robust way to find one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http_ok(url: str, timeout: float) -> tuple:
    """Poll a URL until it returns any HTTP response (not necessarily 200).
    Returns (ok: bool, last_error: str)."""
    deadline = time.time() + timeout
    last_err = "never attempted"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            # Any HTTP response (even 4xx/5xx from the app) means the server is up.
            return True, f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(0.3)
    return False, last_err


@pytest.fixture(scope="session")
def base_url(pytestconfig):
    return pytestconfig.getoption("--base-url")


@pytest.fixture(scope="session")
def fixtures_dir():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURES_DIR


@pytest.fixture(scope="session", autouse=True)
def _generate_fixtures_if_local(base_url):
    """Fixtures are only needed for tests that upload files, but they're cheap
    to generate once per session and every suite here uses at least one."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    script = TESTS_DIR / "make_fixtures.sh"
    subprocess.run(["bash", str(script)], check=True, cwd=str(TESTS_DIR))
    yield


@pytest.fixture(scope="session")
def backend_info(base_url):
    """Starts the local dl-service.py backend (if base_url is local). Returns
    a dict describing availability so dependent fixtures/tests can skip
    cleanly instead of erroring when the backend can't boot in this env."""
    if not _is_local(base_url):
        _backend_state.update(available=False, reason="remote base-url; not starting anything local")
        yield _backend_state
        return

    dl_dir = SCRATCH_DIR / "dl"
    share_dir = SCRATCH_DIR / "shares"
    dl_dir.mkdir(parents=True, exist_ok=True)
    share_dir.mkdir(parents=True, exist_ok=True)

    # This dev machine runs a lot of unrelated background services that
    # squat on ports in the 8090s (observed: 8090, 8092, 8095, 8099 all
    # occupied at various points, including dl-service.py's own prod default
    # of 8090). Ask the OS for a genuinely free one instead of guessing.
    backend_port = _free_port()

    env = os.environ.copy()
    env["COMPRESS_DL_DIR"] = str(dl_dir)
    env["COMPRESS_SHARE_DIR"] = str(share_dir)
    env["COMPRESS_PORT"] = str(backend_port)

    proc = None
    try:
        # Quick pre-flight: make sure the module even imports with these env
        # vars before spending the full poll timeout on it. If fastapi/uvicorn
        # aren't importable, try to install them once.
        for mod in ("fastapi", "uvicorn"):
            try:
                __import__(mod)
            except ImportError:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "fastapi", "uvicorn"],
                    check=False,
                )

        proc = subprocess.Popen(
            [sys.executable, "dl-service.py"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        ok, detail = _wait_http_ok(f"http://127.0.0.1:{backend_port}/health", timeout=20)

        if not ok:
            # Drain any output for diagnostics before giving up.
            proc.poll()
            out = ""
            if proc.stdout:
                try:
                    out = proc.stdout.read(4000)
                except Exception:  # noqa: BLE001
                    pass
            _backend_state.update(
                available=False,
                reason=f"backend did not become healthy within timeout ({detail}); output: {out[:2000]}",
            )
        else:
            _backend_state.update(available=True, reason=f"healthy ({detail})", port=backend_port)

        yield _backend_state
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def server_process(base_url, backend_info):
    """Starts tests/serve.py locally. No-op for remote base_url."""
    if not _is_local(base_url):
        yield None
        return

    port = int(base_url.rsplit(":", 1)[-1].split("/")[0])

    if _port_open("127.0.0.1", port):
        # Something (maybe a previous leaked run) already serves this port —
        # reuse it rather than erroring, but warn loudly.
        print(f"[conftest] WARNING: port {port} already in use; assuming a compatible server.py is already serving it")
        yield None
        return

    # If the backend didn't come up, point the proxy at *some* port anyway —
    # /api/* requests will just 502 cleanly (see serve.py's _proxy error
    # handling) rather than the dev server failing to start at all.
    backend_port = backend_info.get("port", 8090)

    proc = subprocess.Popen(
        [sys.executable, str(TESTS_DIR / "serve.py"), "--port", str(port),
         "--backend", f"http://127.0.0.1:{backend_port}"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    ok, detail = _wait_http_ok(f"http://127.0.0.1:{port}/index.html", timeout=15)
    if not ok:
        proc.terminate()
        raise RuntimeError(f"tests/serve.py did not come up on port {port}: {detail}")

    yield proc

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def app_url(base_url, server_process):
    """The URL to load the app from (ensures local server is up first)."""
    return base_url.rstrip("/")


@pytest.fixture(scope="session")
def backend_available(backend_info, base_url):
    if not _is_local(base_url):
        # Remote: assume the live site's backend is up; we don't control it
        # and won't try to health-check it here (network tests handle that).
        return True
    return backend_info["available"]


@pytest.fixture(scope="session")
def require_backend(backend_available, backend_info):
    """Use in a test/fixture to skip cleanly when the backend isn't up."""
    if not backend_available:
        pytest.skip(f"backend unavailable: {backend_info['reason']}")


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    b = playwright_instance.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture
def context(browser):
    ctx = browser.new_context()
    yield ctx
    ctx.close()


@pytest.fixture
def page(context, request):
    pg = context.new_page()
    errors = []

    pg.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    def _on_console(msg):
        if msg.type == "error":
            errors.append(f"console.error: {msg.text}")

    pg.on("console", _on_console)
    pg.on("crash", lambda: errors.append("page crashed"))

    yield pg

    allow = request.node.get_closest_marker("allow_console_errors") is not None
    pg.close()
    if errors and not allow:
        joined = "\n  - ".join(errors)
        pytest.fail(f"Console/page errors detected during test:\n  - {joined}")


@pytest.fixture
def mobile_context(browser, playwright_instance):
    device_ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=3,
    )
    yield device_ctx
    device_ctx.close()


@pytest.fixture
def mobile_page(mobile_context, request):
    pg = mobile_context.new_page()
    errors = []
    pg.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    def _on_console(msg):
        if msg.type == "error":
            errors.append(f"console.error: {msg.text}")

    pg.on("console", _on_console)
    pg.on("crash", lambda: errors.append("page crashed"))

    yield pg

    allow = request.node.get_closest_marker("allow_console_errors") is not None
    pg.close()
    if errors and not allow:
        joined = "\n  - ".join(errors)
        pytest.fail(f"Console/page errors detected during test:\n  - {joined}")


# ============================================
# ffmpeg/ffprobe helpers
# ============================================
def ffprobe(path) -> dict:
    """Run ffprobe -show_format -show_streams on `path`, return parsed JSON."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def decode_clean(path) -> bool:
    """Decode the whole file with ffmpeg -f null -; True if exit 0 and no
    stderr output (warnings/errors)."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stderr.strip() == ""


# Make these importable as `from conftest import ffprobe, decode_clean` won't
# work directly in pytest's rootdir-relative collection, but tests in the
# same directory can `from conftest import ffprobe, decode_clean, wait_engine_ready`.
def wait_engine_ready(page, timeout_ms=60000):
    """Wait for ffmpeg.wasm to finish loading. app.js sets
    `#engineStatus` to have class `ready` and its `.engine-label` text to
    'Engine ready' once state.ffmpegLoaded flips true (see loadFFmpeg() in
    app.js, called unconditionally on page load)."""
    page.wait_for_selector("#engineStatus.ready", timeout=timeout_ms, state="attached")


@pytest.fixture(autouse=True)
def _screenshots_dir():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    yield
