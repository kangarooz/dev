"""A scripted OpenAI-compatible LLM for driving the real OpenCode binary in tests.

``FakeOpenCodeLLM(commands)`` serves ``GET /v1/models`` and ``POST
/v1/chat/completions`` (streaming SSE and plain JSON) on a loopback port.  It
never "thinks": on every completion request it looks for the ``bash`` tool in
the request's ``tools`` list, counts the ``role: "tool"`` messages already in
the conversation (one per finished tool call) and answers with a tool call for
the next command in ``commands``.  Once every command has a result it answers
with a plain assistant message (``SMOKE DONE``) so the session ends.  Requests
without a ``bash`` tool (OpenCode's title / summary agents) get a short plain
text answer.

Every request body and the reply are appended to ``log_path`` as JSON lines so
a failing run can be debugged from the file alone.  The server is a
``ThreadingHTTPServer`` on a daemon thread; use it as a context manager or call
``start()``/``stop()``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DONE_TEXT = "SMOKE DONE"
TITLE_TEXT = "Demo smoke pipeline"
BASH_TOOL = "bash"
MAX_REPEATS = 3   # same command index requested this many times -> give up (a loop, not progress)


def shell_join(argv: list[str]) -> str:
    """One command line for OpenCode's bash tool (POSIX quoting; Windows list2cmdline)."""
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def kit_command(*args: str, python: str | None = None) -> str:
    """``<python> -m demo_smoke <args...>`` as one shell command string."""
    return shell_join([python or sys.executable, "-m", "demo_smoke", *args])


def _tool_schema(tools, name: str) -> dict | None:
    """The ``function`` dict of the tool called ``name`` in an OpenAI ``tools`` list."""
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" or "function" in t else t
        if isinstance(fn, dict) and fn.get("name") == name:
            return fn
    return None


def _count_tool_results(messages) -> int:
    n = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":
            n += 1
        elif m.get("role") == "user" and isinstance(m.get("content"), list):
            # Anthropic-style tool results inside a user message (not what the AI SDK
            # sends for openai-compatible, but harmless to count)
            n += sum(1 for part in m["content"]
                     if isinstance(part, dict) and part.get("type") == "tool_result")
    return n


class FakeOpenCodeLLM:
    """Scripted OpenAI-compatible server; see the module docstring.

    ``commands``: shell command strings, run in order through OpenCode's bash tool.
    ``log_path``: JSON-lines log of every request/reply (default ``<tmp>/fake-llm.jsonl``).
    ``tool_timeout_ms``: value for the bash tool's ``timeout`` parameter when its schema has one.
    """

    def __init__(self, commands: list[str], log_path: str | Path | None = None,
                 model_id: str = "scripted", done_text: str = DONE_TEXT,
                 tool_timeout_ms: int | None = 600_000) -> None:
        self.commands = list(commands)
        self.model_id = model_id
        self.done_text = done_text
        self.tool_timeout_ms = tool_timeout_ms
        self.log_path = Path(log_path) if log_path else None
        self.requests: list[dict] = []          # every parsed request body, in order
        self.replies: list[dict] = []           # {"kind": "tool_call"|"text", "index", "command"|"text"}
        self.max_tool_results = 0               # most role=tool messages seen in one request
        self.served: dict[int, int] = {}        # command index -> times served
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # ----------------------------------------------------------------- lifecycle

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> "FakeOpenCodeLLM":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a, **k):  # quiet
                pass

            def _send(self, status: int, data: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

            def do_GET(self):
                path = self.path.split("?", 1)[0].rstrip("/")
                if path.endswith("/models"):
                    body = {"object": "list", "data": [
                        {"id": fake.model_id, "object": "model", "created": 0, "owned_by": "fake"}]}
                    self._send(200, json.dumps(body).encode("utf-8"), "application/json")
                    return
                self._send(404, b'{"error":{"message":"not found"}}', "application/json")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    body = {"_unparsable": raw[:2000].decode("utf-8", "replace")}
                path = self.path.split("?", 1)[0].rstrip("/")
                if not path.endswith("/chat/completions"):
                    fake._log({"path": self.path, "body": body, "reply": {"status": 404}})
                    self._send(404, b'{"error":{"message":"not found"}}', "application/json")
                    return
                reply = fake._decide(body)
                fake._log({"path": self.path, "body": body, "reply": reply})
                if body.get("stream"):
                    data = fake._sse(body, reply)
                    self._send(200, data, "text/event-stream; charset=utf-8")
                else:
                    data = json.dumps(fake._completion(body, reply)).encode("utf-8")
                    self._send(200, data, "application/json")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        if self.log_path is None:
            self.log_path = Path(os.environ.get("TMPDIR") or ".") / f"fake-llm-{self.port}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeOpenCodeLLM":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----------------------------------------------------------------- scripting

    @property
    def tool_calls_issued(self) -> list[str]:
        return [r["command"] for r in self.replies if r["kind"] == "tool_call"]

    def _log(self, rec: dict) -> None:
        rec = {"t": round(time.time(), 3), **rec}
        with self._lock:
            self.requests.append(rec["body"])
            if self.log_path is not None:
                try:
                    with self.log_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec, default=str) + "\n")
                except OSError:
                    pass

    def _decide(self, body: dict) -> dict:
        tools = body.get("tools")
        bash = _tool_schema(tools, BASH_TOOL)
        if bash is None:
            return {"kind": "text", "text": TITLE_TEXT, "reason": "no bash tool in request"}
        idx = _count_tool_results(body.get("messages"))
        with self._lock:
            self.max_tool_results = max(self.max_tool_results, idx)
            if idx >= len(self.commands):
                return {"kind": "text", "text": self.done_text, "index": idx}
            times = self.served.get(idx, 0) + 1
            self.served[idx] = times
            if times > MAX_REPEATS:
                self.errors.append(f"command {idx} requested {times} times without a tool result")
                return {"kind": "text", "text": f"{self.done_text} (aborted: loop at command {idx})",
                        "index": idx}
        props = ((bash.get("parameters") or {}).get("properties") or {})
        args: dict = {"command": self.commands[idx]}
        if "timeout" in props and self.tool_timeout_ms:
            args["timeout"] = int(self.tool_timeout_ms)
        if "description" in props:
            args["description"] = f"kit command {idx + 1} of {len(self.commands)}"
        with self._lock:
            reply = {"kind": "tool_call", "index": idx, "command": self.commands[idx],
                     "arguments": args, "call_id": f"call_{idx}_{len(self.replies)}"}
            self.replies.append(reply)
        return reply

    # ----------------------------------------------------------------- wire formats

    @staticmethod
    def _usage() -> dict:
        return {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}

    def _message(self, reply: dict) -> tuple[dict, str]:
        if reply["kind"] == "tool_call":
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": reply["call_id"], "type": "function",
                "function": {"name": BASH_TOOL, "arguments": json.dumps(reply["arguments"])}}]}
            return msg, "tool_calls"
        return {"role": "assistant", "content": reply["text"]}, "stop"

    def _completion(self, body: dict, reply: dict) -> dict:
        msg, finish = self._message(reply)
        return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                "model": body.get("model") or self.model_id,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish, "logprobs": None}],
                "usage": self._usage()}

    def _sse(self, body: dict, reply: dict) -> bytes:
        created = int(time.time())
        model = body.get("model") or self.model_id
        cid = f"chatcmpl-fake-{created}"

        def chunk(delta: dict, finish=None, usage=None) -> str:
            c: dict = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}]}
            if usage is not None:
                c["usage"] = usage
            return "data: " + json.dumps(c) + "\n\n"

        parts = [chunk({"role": "assistant", "content": ""})]
        if reply["kind"] == "tool_call":
            arguments = json.dumps(reply["arguments"])
            parts.append(chunk({"tool_calls": [{"index": 0, "id": reply["call_id"], "type": "function",
                                                "function": {"name": BASH_TOOL, "arguments": ""}}]}))
            # arguments streamed in two pieces, like a real server
            cut = max(1, len(arguments) // 2)
            for piece in (arguments[:cut], arguments[cut:]):
                parts.append(chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}))
            parts.append(chunk({}, finish="tool_calls"))
        else:
            words = reply["text"].split(" ")
            for i, w in enumerate(words):
                parts.append(chunk({"content": (" " if i else "") + w}))
            parts.append(chunk({}, finish="stop"))
        usage = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                 "choices": [], "usage": self._usage()}
        parts.append("data: " + json.dumps(usage) + "\n\n")
        parts.append("data: [DONE]\n\n")
        return "".join(parts).encode("utf-8")
