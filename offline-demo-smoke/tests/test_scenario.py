import json
from pathlib import Path

import pytest

from demo_smoke import scenario


def _valid():
    return {
        "name": "X", "app_url": "http://localhost:3000",
        "steps": [{"id": "open", "title": "Open", "actions": [{"goto": "/"}],
                   "expect": [{"text": "hi"}]}],
    }


def test_example_scenario_loads(example_scenario_path):
    s = scenario.load(example_scenario_path)
    assert s["slug"] == "chat-with-manuals"
    assert s["login"]["type"] == "form"
    assert s["_dir"] == example_scenario_path.resolve().parent
    assert [st["id"] for st in s["steps"]] == ["open", "upload", "ask", "citation"]
    files = s["steps"][1]["actions"][0]["upload"]["files"]
    assert files == [str(example_scenario_path.resolve().parent / "fixtures" / "osha-1910.pdf")]
    for st in s["steps"]:
        assert st["timeout_s"] > 0
        assert st["narration"]


def test_example_ships_its_fixture(example_scenario_path):
    s = scenario.load(example_scenario_path, check_files=True)      # must not raise
    assert Path(s["steps"][1]["actions"][0]["upload"]["files"][0]).is_file()


def test_check_files_reports_missing_fixture(example_scenario_path, tmp_path):
    data = json.loads(example_scenario_path.read_text())
    data["steps"][1]["actions"][0]["upload"]["files"] = ["fixtures/missing-manual.pdf"]
    p = tmp_path / "copy.json"
    p.write_text(json.dumps(data))
    scenario.load(p)                                                 # fine without check_files
    with pytest.raises(scenario.ScenarioError, match="upload file.*missing-manual.pdf"):
        scenario.load(p, check_files=True)


def test_defaults_applied(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_valid()))
    s = scenario.load(p)
    assert s["viewport"] == {"width": 1920, "height": 1080}
    assert s["login"] == {"type": "none"}
    assert s["max_length_seconds"] == 90
    assert s["slug"] == "x"
    assert s["steps"][0]["timeout_s"] == 60
    assert s["intro"] == "" and s["outro"] == ""


def test_relative_and_absolute_upload_paths(tmp_path):
    d = _valid()
    absolute = str(tmp_path / "abs.pdf")
    d["steps"][0]["actions"].append({"upload": {"selector": "input", "files": ["rel/a.pdf", absolute]}})
    p = tmp_path / "sub" / "s.json"
    p.parent.mkdir()
    p.write_text(json.dumps(d))
    s = scenario.load(p)
    files = s["steps"][0]["actions"][1]["upload"]["files"]
    assert files[0] == str(p.resolve().parent / "rel" / "a.pdf")
    assert files[1] == absolute


def test_missing_file_and_bad_json(tmp_path):
    with pytest.raises(scenario.ScenarioError, match="not found"):
        scenario.load(tmp_path / "nope.json")
    p = tmp_path / "bad.json"
    p.write_text('{"name": "x",')
    with pytest.raises(scenario.ScenarioError, match="invalid JSON at line"):
        scenario.load(p)


def test_load_raises_joined_messages(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"name": "", "app_url": "ftp://x", "steps": []}))
    with pytest.raises(scenario.ScenarioError) as ei:
        scenario.load(p)
    msg = str(ei.value)
    assert "name" in msg and "app_url" in msg and "steps" in msg and "\n" not in msg


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.pop("name"), "name"),
        (lambda d: d.update(app_url="localhost:3000"), "app_url"),
        (lambda d: d.update(slug="Bad Slug"), "slug"),
        (lambda d: d.update(viewport={"width": 0, "height": 10}), "viewport"),
        (lambda d: d.update(max_length_seconds=-1), "max_length_seconds"),
        (lambda d: d.update(intro=5), "intro"),
        (lambda d: d.update(login={"type": "oauth"}), "login.type"),
        (lambda d: d.update(login={"type": "form"}), "login.username_selector"),
        (lambda d: d.update(login={"type": "basic", "username_env": "U"}), "login.password_env"),
        (lambda d: d.update(steps=[]), "steps must be a non-empty list"),
        (lambda d: d["steps"][0].update(id="Open!"), "steps[0].id"),
        (lambda d: d["steps"].append(dict(d["steps"][0])), "duplicated"),
        (lambda d: d["steps"][0].update(title=""), "title"),
        (lambda d: d["steps"][0].update(timeout_s=0), "timeout_s"),
        (lambda d: d["steps"][0].update(bogus=1), "unknown key 'bogus'"),
        (lambda d: d["steps"][0].update(actions=[{"jump": "/"}]), "unknown action 'jump'"),
        (lambda d: d["steps"][0].update(actions=[{"goto": "/", "click": "a"}]), "exactly one key"),
        (lambda d: d["steps"][0].update(actions=[{"fill": {"text": "x"}}]), "fill.selector"),
        (lambda d: d["steps"][0].update(actions=[{"type": {"selector": "a", "text": "x", "delay_ms": -1}}]),
         "delay_ms"),
        (lambda d: d["steps"][0].update(actions=[{"upload": {"selector": "a", "files": []}}]), "upload.files"),
        (lambda d: d["steps"][0].update(actions=[{"scroll": {"selector": "a", "y": 1}}]), "scroll"),
        (lambda d: d["steps"][0].update(actions=[{"wait": {"ms": -5}}]), "wait"),
        (lambda d: d["steps"][0].update(actions=[{"wait_for": {"timeout_s": 3}}]), "wait_for"),
        (lambda d: d["steps"][0].update(actions=[{"screenshot": "../x"}]), "screenshot"),
        (lambda d: d["steps"][0].update(expect=[{"text": "a", "selector": "b"}]), "exactly one of"),
        (lambda d: d["steps"][0].update(expect=[{"selector": "b", "count_min": "many"}]), "count_min"),
        (lambda d: d["steps"][0].update(expect=[{"selector": "b", "count_min": 1.5}]), "count_min"),
        (lambda d: d.update(foo=1), "unknown top-level key 'foo'"),
        (lambda d: d.update(login={"type": "form", "url": "", "username_selector": "#u",
                                   "password_selector": "#p", "submit_selector": "#s",
                                   "username_env": "U", "password_env": "P"}), "login.url"),
        (lambda d: d["steps"][0].update(expect=[{"text": "a", "contains": "b"}]), "unknown key 'contains'"),
        (lambda d: d["steps"][0].update(expect=[{"nope": "a"}]), "exactly one of"),
        (lambda d: d["steps"][0].update(expect="x"), "expect must be a list"),
        # unknown keys are rejected like scenarios/schema.json (additionalProperties: false)
        (lambda d: d.update(login={"type": "none", "password": "x"}), "login has unknown key 'password'"),
        (lambda d: d.update(login={"type": "form", "url": "/l", "username_selector": "#u",
                                   "password_selector": "#p", "submit_selector": "#s",
                                   "username_env": "U", "password_env": "P", "password": "hunter2"}),
         "login has unknown key 'password'"),
        (lambda d: d.update(login={"type": "basic", "username_env": "U", "password_env": "P", "url": "/x"}),
         "login has unknown key 'url'"),
        (lambda d: d.update(viewport={"width": 10, "height": 10, "scale": 2}), "viewport has unknown key 'scale'"),
        (lambda d: d["steps"][0].update(actions=[{"fill": {"selector": "a", "text": "x", "delay_ms": 1}}]),
         "fill has unknown key 'delay_ms'"),
        (lambda d: d["steps"][0].update(actions=[{"type": {"selector": "a", "text": "x", "speed": 1}}]),
         "type has unknown key 'speed'"),
        (lambda d: d["steps"][0].update(actions=[{"upload": {"selector": "a", "files": ["f"], "name": "n"}}]),
         "upload has unknown key 'name'"),
        (lambda d: d["steps"][0].update(actions=[{"wait": {"ms": 5, "seconds": 1}}]), "wait has unknown key 'seconds'"),
        (lambda d: d.update(_private=1), "unknown top-level key '_private'"),
    ],
)
def test_validate_messages(mutate, needle):
    d = _valid()
    mutate(d)
    errors = scenario.validate(d)
    assert errors, "expected a validation error"
    assert any(needle in e for e in errors), errors


def test_validate_accepts_every_action_and_expect():
    d = _valid()
    d["steps"][0]["actions"] = [
        {"goto": "/"}, {"click": "a"}, {"fill": {"selector": "i", "text": ""}},
        {"type": {"selector": "i", "text": "hi", "delay_ms": 20}}, {"press": "Enter"},
        {"upload": {"selector": "input", "files": ["a.pdf"]}}, {"hover": "a"},
        {"scroll": {"y": 200}}, {"scroll": {"selector": "footer"}}, {"wait": {"ms": 100}},
        {"wait_for": {"selector": ".x", "timeout_s": 5}}, {"wait_for": {"text": "ok"}},
        {"screenshot": "shot-1"},
    ]
    d["steps"][0]["expect"] = [
        {"text": "a"}, {"selector": ".a"}, {"selector": ".a", "contains": "b", "count_min": 2},
        {"url_contains": "/x"}, {"not_text": "Error"},
    ]
    d["login"] = {"type": "form", "url": "/login", "username_selector": "#u", "password_selector": "#p",
                  "submit_selector": "#s", "username_env": "U", "password_env": "P",
                  "success_selector": "nav"}
    d["$schema"] = "./schema.json"          # editor hint, allowed
    d["_dir"] = "/tmp"                       # added by load(), allowed on re-validation
    d["_path"] = "/tmp/s.json"
    assert scenario.validate(d) == []
    assert scenario.validate("not a dict") == ["scenario must be a JSON object"]


def test_slugify_and_step_ids():
    assert scenario.slugify("Chat with Manuals!") == "chat-with-manuals"
    assert scenario.slugify("***") == "demo"
    assert scenario.step_ids(_valid()) == ["open"]
