import json

import pytest

from demo_smoke import narration, scenario


@pytest.fixture
def scen(simple_scenario_path):
    return scenario.load(simple_scenario_path)


def test_words():
    assert narration.words("") == 0
    assert narration.words("Hello, world!  It's 3 pm — ok") == 6
    assert narration.words("... -- ") == 0


def test_template_uses_fields_and_defaults(scen):
    n = narration.template(scen)
    assert n["intro"] == "This is a tiny app walkthrough."
    assert n["outro"] == "And that is the tiny app."
    assert [s["id"] for s in n["steps"]] == ["open", "upload", "ask"]
    assert n["steps"][0]["text"] == "I open the app."
    assert n["steps"][1]["text"] == "Now I upload a manual."     # no narration field
    assert narration.validate(n, scen) == []


def test_template_default_intro_outro():
    s = {"name": "Foo", "steps": [{"id": "a", "title": "Do things"}]}
    n = narration.template(s)
    assert n["intro"] == "Here is a quick walkthrough of Foo."
    assert n["outro"] == "That completes the Foo smoke test."
    assert n["steps"] == [{"id": "a", "text": "Now I do things."}]


def test_validate_errors(scen):
    good = narration.template(scen)
    bad = json.loads(json.dumps(good))
    bad["intro"] = ""
    bad["steps"][0]["text"] = " ".join(["word"] * 46)
    bad["steps"][1]["id"] = "wrong"
    bad["extra"] = 1
    errs = narration.validate(bad, scen)
    assert any("intro must be" in e for e in errs)
    assert any("46 words" in e for e in errs)
    assert any("step ids must be exactly" in e for e in errs)
    assert any("unknown key 'extra'" in e for e in errs)
    assert narration.validate([], scen) == ["narration must be a JSON object with intro, outro, steps"]
    assert any("steps must be a list" in e for e in narration.validate({"intro": "a", "outro": "b"}, scen))


def test_validate_total_budget(scen):
    scen["max_length_seconds"] = 10   # 26 words
    n = narration.template(scen)
    n["steps"][2]["text"] = " ".join(["long"] * 30)
    errs = narration.validate(n, scen)
    assert any("total narration is" in e and "max 26" in e for e in errs)


def test_extract_json_is_tolerant():
    obj = {"intro": "a", "outro": "b", "steps": []}
    assert narration.extract_json(json.dumps(obj)) == obj
    assert narration.extract_json("```json\n" + json.dumps(obj) + "\n```") == obj
    assert narration.extract_json("Sure! Here you go:\n" + json.dumps(obj) + "\nHope it helps") == obj
    assert narration.extract_json('[1, 2] then {"intro": "x"} and {"other": 1}') == {"intro": "x"}
    with pytest.raises(ValueError, match="no JSON object"):
        narration.extract_json("nothing here")
    with pytest.raises(ValueError, match="empty"):
        narration.extract_json("   ")


def _llm_narr(scen, **over):
    n = {"intro": "Intro from the model.", "outro": "Outro from the model.",
         "steps": [{"id": s["id"], "text": f"Model text for {s['id']}."} for s in scen["steps"]]}
    n.update(over)
    return n


def test_from_llm_success(fake_llm, scen):
    fake_llm.queue.append({"content": json.dumps(_llm_narr(scen))})
    narr, source, note = narration.from_llm(scen, fake_llm.base_url, "m")
    assert source == "llm"
    assert narr["intro"] == "Intro from the model."
    assert "model" in note
    body = fake_llm.last_body
    assert body["temperature"] == 0.1
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "JSON" in body["messages"][0]["content"]
    assert '"steps"' in body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert '["open", "upload", "ask"]' in user
    assert "manual.pdf" in user and "Why?" in user
    assert len(fake_llm.requests) == 1


def test_from_llm_repair_round(fake_llm, scen):
    broken = _llm_narr(scen)
    broken["steps"][1]["id"] = "wrong-id"
    fake_llm.queue.append({"content": "```json\n" + json.dumps(broken) + "\n```"})
    fake_llm.queue.append({"content": json.dumps(_llm_narr(scen))})
    _narr, source, note = narration.from_llm(scen, fake_llm.base_url, "m")
    assert source == "llm"
    assert "repair" in note
    assert len(fake_llm.requests) == 2
    msgs = fake_llm.requests[1]["body"]["messages"]
    assert msgs[-2]["role"] == "assistant"
    assert msgs[-1]["role"] == "user"
    assert "step ids must be exactly" in msgs[-1]["content"]


def test_from_llm_falls_back_after_two_bad(fake_llm, scen):
    fake_llm.queue.append({"content": "I would rather not."})
    fake_llm.queue.append({"content": json.dumps({"intro": "x"})})
    narr, source, note = narration.from_llm(scen, fake_llm.base_url, "m")
    assert source == "template"
    assert narr == narration.template(scen)
    assert note.startswith("fell back to template")
    assert "attempt 1" in note and "attempt 2" in note
    assert len(fake_llm.requests) == 2


def test_from_llm_request_failure(fake_llm, scen, unreachable_url):
    narr, source, note = narration.from_llm(scen, unreachable_url, "m", timeout=2)
    assert source == "template" and "request failed" in note
    fake_llm.queue.append({"status": 500})
    narr, source, note = narration.from_llm(scen, fake_llm.base_url, "m")
    assert source == "template" and "HTTP 500" in note
    assert narration.validate(narr, scen) == []
