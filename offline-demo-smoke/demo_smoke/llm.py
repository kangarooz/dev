"""OpenAI-compatible chat helpers (urllib only; ollama, llama.cpp, LM Studio, vLLM).

``base_url`` is the OpenAI-style root, e.g. ``http://localhost:11434/v1``.  A
missing ``/v1`` suffix is appended.  Every failure is an ``LLMError`` with a
one-line message; nothing here raises past the CLI boundary.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_step_status",
        "description": "Return the status of one smoke-test step.",
        "parameters": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string", "description": "The step id, e.g. 'open'."}
            },
            "required": ["step_id"],
        },
    },
}
PROBE_USER_MESSAGE = 'Call get_step_status for step_id "open".'


class LLMError(RuntimeError):
    """Endpoint unreachable, HTTP error, timeout, or malformed response."""


def normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise LLMError("empty --base-url; expected e.g. http://localhost:11434/v1")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    key = os.environ.get("DEMO_SMOKE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _request(method: str, url: str, body: dict | None, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        excerpt = ""
        try:
            excerpt = e.read().decode("utf-8", errors="replace")[:300].replace("\n", " ")
        except OSError:
            pass
        raise LLMError(f"HTTP {e.code} from {url}: {excerpt or e.reason}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"cannot reach {url}: {e.reason}") from None
    except TimeoutError:
        raise LLMError(f"timeout after {timeout}s waiting for {url}") from None
    except OSError as e:
        raise LLMError(f"network error talking to {url}: {e}") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise LLMError(f"non-JSON response from {url}: {raw[:200]!r}") from None
    if not isinstance(parsed, dict):
        raise LLMError(f"unexpected response shape from {url}: {type(parsed).__name__}")
    return parsed


def reachable(base_url: str, timeout: int = 5) -> bool:
    """GET {base_url}/models; True when the server answers (any status < 500)."""
    try:
        url = normalize_base_url(base_url) + "/models"
    except LLMError:
        return False
    req = urllib.request.Request(url, method="GET", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def chat(base_url: str, model: str, messages: list, tools: list | None = None,
         timeout: int = 120, temperature: float = 0.1, response_json: bool = False) -> dict:
    """POST /chat/completions and return the raw response dict.  Raises LLMError."""
    if not model:
        raise LLMError("no --model given (e.g. --model qwen2.5:7b)")
    url = normalize_base_url(base_url) + "/chat/completions"
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_json:
        body["response_format"] = {"type": "json_object"}
    resp = _request("POST", url, body, timeout)
    if "error" in resp and not resp.get("choices"):
        err = resp["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise LLMError(f"server error from {url}: {msg}")
    if not resp.get("choices"):
        raise LLMError(f"response from {url} has no choices: {json.dumps(resp)[:200]}")
    return resp


def message_of(resp: dict) -> dict:
    """First choice's message (``{}`` if absent)."""
    try:
        return resp["choices"][0].get("message") or {}
    except (KeyError, IndexError, AttributeError, TypeError):
        return {}


def content_text(resp: dict) -> str:
    """Assistant text of the first choice; list-of-parts content is joined."""
    content = message_of(resp).get("content")
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def tool_calls_of(resp: dict) -> list[dict]:
    msg = message_of(resp)
    calls = msg.get("tool_calls") or []
    out = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or {}
        out.append({"name": fn.get("name") or c.get("name"), "arguments": fn.get("arguments")})
    # Some servers put a legacy single `function_call` on the message.
    fc = msg.get("function_call")
    if isinstance(fc, dict) and fc.get("name"):
        out.append({"name": fc["name"], "arguments": fc.get("arguments")})
    return out


def probe_tool_call(base_url: str, model: str, timeout: int = 120) -> dict:
    """Ask the model to call ``get_step_status``; PASS iff it returns that tool call."""
    messages = [
        {"role": "system",
         "content": "You are a test harness. When a tool fits the request, call it "
                    "instead of answering in prose."},
        {"role": "user", "content": PROBE_USER_MESSAGE},
    ]
    try:
        resp = chat(base_url, model, messages, tools=[PROBE_TOOL], timeout=timeout, temperature=0.0)
    except LLMError as e:
        return {"pass": False, "detail": str(e)}
    calls = tool_calls_of(resp)
    for c in calls:
        if c["name"] == PROBE_TOOL["function"]["name"]:
            args = c.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            step_id = args.get("step_id") if isinstance(args, dict) else None
            detail = f"model returned tool call get_step_status(step_id={step_id!r})"
            if step_id != "open":
                detail += " (expected step_id 'open', still counts as tool-capable)"
            return {"pass": True, "detail": detail}
    if calls:
        names = ", ".join(str(c["name"]) for c in calls)
        return {"pass": False, "detail": f"model called other tool(s): {names}"}
    text = content_text(resp).strip().replace("\n", " ")
    return {"pass": False,
            "detail": "no tool_calls in response; model answered in prose: "
                      f"{text[:160]!r}"}
