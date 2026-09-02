import json

import pytest

from demo_smoke import llm


def test_normalize_base_url():
    assert llm.normalize_base_url("http://localhost:11434") == "http://localhost:11434/v1"
    assert llm.normalize_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    assert llm.normalize_base_url("localhost:8080/v1") == "http://localhost:8080/v1"
    with pytest.raises(llm.LLMError, match="empty"):
        llm.normalize_base_url("  ")


def test_reachable(fake_llm, unreachable_url):
    assert llm.reachable(fake_llm.base_url) is True
    assert llm.reachable(fake_llm.base_url.removesuffix("/v1")) is True
    assert llm.reachable(unreachable_url, timeout=2) is False
    fake_llm.models_status = 401          # server there, auth wanted: still "reachable"
    assert llm.reachable(fake_llm.base_url) is True
    fake_llm.models_status = 503
    assert llm.reachable(fake_llm.base_url) is False


def test_chat_sends_expected_body(fake_llm):
    fake_llm.queue.append({"content": "hi"})
    resp = llm.chat(fake_llm.base_url, "m1", [{"role": "user", "content": "x"}],
                    tools=[llm.PROBE_TOOL], temperature=0.3, response_json=True)
    assert llm.content_text(resp) == "hi"
    body = fake_llm.last_body
    assert body["model"] == "m1"
    assert body["temperature"] == 0.3
    assert body["stream"] is False
    assert body["tools"][0]["function"]["name"] == "get_step_status"
    assert body["tool_choice"] == "auto"
    assert body["response_format"] == {"type": "json_object"}
    assert fake_llm.requests[-1]["path"].endswith("/v1/chat/completions")


def test_chat_errors_are_one_line(fake_llm, unreachable_url):
    with pytest.raises(llm.LLMError, match="cannot reach"):
        llm.chat(unreachable_url, "m", [], timeout=2)
    fake_llm.queue.append({"status": 500, "body": {"error": {"message": "kaboom"}}})
    with pytest.raises(llm.LLMError, match="HTTP 500.*kaboom"):
        llm.chat(fake_llm.base_url, "m", [])
    fake_llm.queue.append({"raw_text": "<html>oops</html>"})
    with pytest.raises(llm.LLMError, match="non-JSON"):
        llm.chat(fake_llm.base_url, "m", [])
    fake_llm.queue.append({"raw": {"error": {"message": "model not found"}}})
    with pytest.raises(llm.LLMError, match="model not found"):
        llm.chat(fake_llm.base_url, "m", [])
    fake_llm.queue.append({"raw": {"choices": []}})
    with pytest.raises(llm.LLMError, match="no choices"):
        llm.chat(fake_llm.base_url, "m", [])
    with pytest.raises(llm.LLMError, match="no --model"):
        llm.chat(fake_llm.base_url, "", [])
    for e in [llm.LLMError("x")]:
        assert "\n" not in str(e)


def test_chat_timeout(fake_llm):
    fake_llm.queue.append({"content": "slow", "delay": 2.5})
    with pytest.raises(llm.LLMError, match="timeout"):
        llm.chat(fake_llm.base_url, "m", [], timeout=1)


def test_probe_pass(fake_llm):
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "open"}}]})
    res = llm.probe_tool_call(fake_llm.base_url, "m")
    assert res["pass"] is True
    assert "step_id='open'" in res["detail"]
    body = fake_llm.last_body
    assert body["messages"][-1]["content"] == 'Call get_step_status for step_id "open".'
    assert body["tools"][0]["function"]["parameters"]["required"] == ["step_id"]


def test_probe_pass_with_odd_argument_still_passes(fake_llm):
    fake_llm.queue.append({"tool_calls": [{"name": "get_step_status", "arguments": {"step_id": "step1"}}]})
    res = llm.probe_tool_call(fake_llm.base_url, "m")
    assert res["pass"] is True
    assert "expected step_id 'open'" in res["detail"]


def test_probe_fail_cases(fake_llm, unreachable_url):
    fake_llm.queue.append({"content": "I cannot call tools, but the step is fine."})
    res = llm.probe_tool_call(fake_llm.base_url, "m")
    assert res["pass"] is False and "prose" in res["detail"]
    fake_llm.queue.append({"tool_calls": [{"name": "other_tool", "arguments": {}}]})
    res = llm.probe_tool_call(fake_llm.base_url, "m")
    assert res["pass"] is False and "other_tool" in res["detail"]
    res = llm.probe_tool_call(unreachable_url, "m", timeout=2)
    assert res["pass"] is False and "cannot reach" in res["detail"]


def test_content_text_handles_parts_and_missing():
    assert llm.content_text({"choices": [{"message": {"content": [{"type": "text", "text": "a"}, "b"]}}]}) == "ab"
    assert llm.content_text({"choices": [{"message": {"content": None}}]}) == ""
    assert llm.content_text({}) == ""
    calls = llm.tool_calls_of({"choices": [{"message": {"function_call": {"name": "f", "arguments": "{}"}}}]})
    assert calls == [{"name": "f", "arguments": "{}"}]
    assert json.loads(calls[0]["arguments"]) == {}
