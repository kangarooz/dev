"""Shared fixtures: tmp output dir, a fake OpenAI-compatible server, sample scenarios."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

# Chrome discovery is env.find_chrome() (DEMO_SMOKE_CHROME, install paths, PATH, Playwright caches
# such as /opt/pw-browsers); nothing machine-specific is hard-coded here.

EXAMPLE_SCENARIO = KIT / "scenarios" / "example-chat-with-manuals.json"


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path / "out"


@pytest.fixture
def example_scenario_path() -> Path:
    return EXAMPLE_SCENARIO


@pytest.fixture
def simple_scenario_path(tmp_path: Path) -> Path:
    """A small valid scenario whose upload fixture exists (so check_files passes)."""
    fixture = tmp_path / "fixtures" / "manual.pdf"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes(b"%PDF-1.4\n%fake\n")
    data = {
        "name": "Tiny App",
        "slug": "tiny-app",
        "app_url": "http://localhost:3000",
        "viewport": {"width": 1280, "height": 720},
        "login": {"type": "none"},
        "max_length_seconds": 60,
        "intro": "This is a tiny app walkthrough.",
        "outro": "And that is the tiny app.",
        "steps": [
            {"id": "open", "title": "Open the app", "narration": "I open the app.",
             "actions": [{"goto": "/"}], "expect": [{"text": "Tiny"}], "timeout_s": 10},
            {"id": "upload", "title": "Upload a manual",
             "actions": [{"upload": {"selector": "input[type=file]", "files": ["fixtures/manual.pdf"]}}],
             "expect": [{"selector": ".doc-chip", "count_min": 1}]},
            {"id": "ask", "title": "Ask a question", "narration": "I ask a question and read the answer.",
             "actions": [{"fill": {"selector": "textarea", "text": "Why?"}}, {"click": "button"}],
             "expect": [{"selector": ".answer", "contains": "because"}]},
        ],
    }
    p = tmp_path / "tiny.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- fake LLM server


class FakeLLM:
    """OpenAI-compatible stub.  Push response specs onto ``queue``; each POST to
    /v1/chat/completions pops one (or uses ``default``).  Specs:

    * ``{"content": "..."}``                       plain assistant text
    * ``{"tool_calls": [{"name": n, "arguments": {...}}]}``
    * ``{"status": 500, "body": {...}}``            HTTP error
    * ``{"raw": {...}}``                           returned verbatim
    * ``{"raw_text": "not json"}``                 body that is not JSON
    * any spec may add ``"delay": seconds``
    """

    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.requests: list[dict] = []
        self.gets: list[dict] = []        # {"path", "headers"} for every GET (e.g. /v1/models)
        self.default: dict = {"content": "Hello from the fake model."}
        self.models_status = 200
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):  # silence
                pass

            def _send(self, status: int, body, raw: bool = False):
                data = body.encode("utf-8") if raw else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.gets.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                if self.path.rstrip("/").endswith("/v1/models"):
                    self._send(fake.models_status, {"object": "list", "data": [{"id": "fake-model"}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                fake.requests.append({"path": self.path, "body": body,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}})
                if not self.path.rstrip("/").endswith("/v1/chat/completions"):
                    self._send(404, {"error": {"message": "not found"}})
                    return
                spec = fake.queue.pop(0) if fake.queue else dict(fake.default)
                if spec.get("delay"):
                    time.sleep(spec["delay"])
                if "raw_text" in spec:
                    self._send(200, spec["raw_text"], raw=True)
                    return
                if "status" in spec:
                    self._send(spec["status"], spec.get("body", {"error": {"message": "boom"}}))
                    return
                if "raw" in spec:
                    self._send(200, spec["raw"])
                    return
                message: dict = {"role": "assistant", "content": spec.get("content")}
                if spec.get("tool_calls"):
                    message["content"] = spec.get("content")
                    message["tool_calls"] = [
                        {"id": f"call_{i}", "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(tc.get("arguments", {}))}}
                        for i, tc in enumerate(spec["tool_calls"])
                    ]
                self._send(200, {
                    "id": "chatcmpl-fake", "object": "chat.completion", "model": body.get("model"),
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": "tool_calls" if spec.get("tool_calls") else "stop"}],
                })

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def last_body(self) -> dict:
        return self.requests[-1]["body"]


@pytest.fixture
def fake_llm():
    srv = FakeLLM()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def unreachable_url() -> str:
    """A URL nothing listens on (port 1 is reserved and closed on all platforms)."""
    return "http://127.0.0.1:1/v1"
