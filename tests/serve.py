#!/usr/bin/env python3
"""
Dev server for the compress PWA test harness.

Serves the repo root (parent of tests/) statically with the COOP/COEP headers
that ffmpeg.wasm needs for SharedArrayBuffer, and proxies /api/* to the local
dl-service.py backend (default http://127.0.0.1:8090/*), mirroring the prod
Caddy config which strips the /api prefix.

Usage:
    python tests/serve.py --port 8899
    python tests/serve.py --port 8899 --backend http://127.0.0.1:8090 --root /path/to/repo
"""
import argparse
import http.server
import json
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BACKEND = "http://127.0.0.1:8090"


def make_handler(root: Path, backend: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        # Quiet down default logging noise a bit but keep it useful.
        def log_message(self, fmt, *args):
            sys.stderr.write("[serve.py] %s - %s\n" % (self.address_string(), fmt % args))

        def end_headers(self):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._proxy("GET", strip_prefix=True)
            if self.path.startswith("/v/"):
                return self._proxy("GET", strip_prefix=False)
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/api/"):
                return self._proxy("POST", strip_prefix=True)
            self.send_error(405, "POST not supported outside /api/")

        def _proxy(self, method: str, strip_prefix: bool = True):
            # /api/* mirrors prod Caddy's reverse_proxy, which strips the /api
            # prefix before forwarding. /v/* mirrors prod Caddy's *other* route,
            # which forwards to the backend with the path unchanged (backend
            # route: @app.get("/v/{share_id}") in dl-service.py).
            upstream_path = (self.path[len("/api") :] or "/") if strip_prefix else self.path
            upstream_url = backend.rstrip("/") + upstream_path

            content_length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(content_length) if content_length else None

            req_headers = {}
            for key in ("Content-Type", "Accept"):
                if key in self.headers:
                    req_headers[key] = self.headers[key]

            req = urllib.request.Request(
                upstream_url, data=body, headers=req_headers, method=method
            )

            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    self.send_response(resp.status)
                    for key, value in resp.getheaders():
                        if key.lower() in ("transfer-encoding", "connection"):
                            continue
                        if key.lower() == "content-type":
                            self.send_header(key, value)
                        elif key.lower() == "content-length":
                            self.send_header(key, value)
                    self.end_headers()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            return
            except urllib.error.HTTPError as e:
                # Upstream returned a non-2xx status; relay body + status verbatim.
                self.send_response(e.code)
                ctype = e.headers.get("Content-Type", "application/json") if e.headers else "application/json"
                self.send_header("Content-Type", ctype)
                payload = e.read()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    return
            except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError) as e:
                payload = json.dumps(
                    {"detail": f"Backend unreachable at {upstream_url}: {e}"}
                ).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    return

    return Handler


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--root", default=str(REPO_ROOT), help="Static root to serve (default: repo root)")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="Upstream backend base URL for /api/* proxying")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    handler = make_handler(root, args.backend)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[serve.py] serving {root} on http://{args.host}:{args.port} (proxy /api/* -> {args.backend})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
