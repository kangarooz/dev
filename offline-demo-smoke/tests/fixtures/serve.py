"""Serve a directory over HTTP on a free localhost port, in a background thread.

Used by the browser tests (and importable by other test modules):

    from tests.fixtures.serve import serve_dir
    with serve_dir(APP_DIR) as base_url:
        ...

``basic_auth=("user", "pw")`` makes the server demand HTTP basic auth, which
lets the ``login.type == "basic"`` path be exercised offline.
"""
from __future__ import annotations

import base64
import contextlib
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    basic_auth: tuple[str, str] | None = None

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _authorized(self) -> bool:
        if self.basic_auth is None:
            return True
        expected = "Basic " + base64.b64encode(
            f"{self.basic_auth[0]}:{self.basic_auth[1]}".encode()
        ).decode("ascii")
        return self.headers.get("Authorization", "") == expected

    def _deny(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = b"<h1>Sign in required</h1>"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._deny()
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._authorized():
            self._deny()
            return
        super().do_HEAD()


class FixtureServer:
    """Threaded static file server bound to 127.0.0.1 on a free port."""

    def __init__(self, directory: Path | str, basic_auth: tuple[str, str] | None = None):
        self.directory = str(Path(directory).resolve())
        handler = partial(_QuietHandler, directory=self.directory)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        _QuietHandler.basic_auth = basic_auth  # class attribute; one server per process at a time
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    def start(self) -> str:
        self._thread.start()
        return self.url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        _QuietHandler.basic_auth = None


@contextlib.contextmanager
def serve_dir(directory: Path | str, basic_auth: tuple[str, str] | None = None):
    """Context manager yielding the base URL (``http://127.0.0.1:PORT``)."""
    server = FixtureServer(directory, basic_auth=basic_auth)
    server.start()
    try:
        yield server.url
    finally:
        server.stop()
