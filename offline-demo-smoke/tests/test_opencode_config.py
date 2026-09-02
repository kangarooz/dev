"""OpenCode wiring: opencode.json, the demo-smoke agent, slash commands, README, setup scripts.

No YAML library is available here, so the agent/command frontmatter is parsed
with a tiny YAML-subset parser (nested maps, quoted keys, scalars) below.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
CONFIG = KIT / "opencode.json"
AGENT = KIT / ".opencode" / "agents" / "demo-smoke.md"
COMMANDS = KIT / ".opencode" / "commands"
README = KIT / "README.md"
AGENTS_MD = KIT / "AGENTS.md"
SETUP_SH = KIT / "scripts" / "setup.sh"
SETUP_PS1 = KIT / "scripts" / "setup.ps1"

KIT_BASH_ALLOW = [
    "python -m demo_smoke *",
    "python3 -m demo_smoke *",
    ".venv/bin/python -m demo_smoke *",
    ".venv\\Scripts\\python.exe -m demo_smoke *",
    "cat *",
    "ls *",
    "dir *",
    "type *",
    "git status*",
    "git diff*",
]
KIT_BASH_DENY = ["rm -rf *", "del *", "git push*",
                 # .env is denied to the read tool; these keep bash from reopening it
                 "cat *.env*", "type *.env*", "* .env*", "printenv*", "env", "set", "export",
                 # the two kit commands that reach the network are denied even under --auto
                 "python -m demo_smoke prefetch*", "python3 -m demo_smoke prefetch*",
                 ".venv/bin/python -m demo_smoke prefetch*", ".venv\\Scripts\\python.exe -m demo_smoke prefetch*",
                 "* --online*"]
KIT_EDIT_ALLOW = ["demo-output/**", "scenarios/*.json", "**/audio/narration.json"]


# ----------------------------------------------------------------- helpers


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _split_kv(line: str) -> tuple[str, str]:
    if line[0] in "\"'":
        end = line.index(line[0], 1)
        key = line[1:end]
        rest = line[end + 1:].lstrip()
        assert rest.startswith(":"), f"bad mapping line: {line!r}"
        return key, rest[1:].strip()
    key, _, value = line.partition(":")
    return key.strip(), value.strip()


def parse_yaml_subset(text: str) -> dict:
    """Nested maps of scalars, indentation based; enough for OpenCode frontmatter."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, value = _split_kv(raw.strip())
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(value)
    return root


def split_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: no frontmatter"
    end = text.index("\n---", 4)
    return parse_yaml_subset(text[4:end]), text[end + 4:]


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agent() -> tuple[dict, str]:
    return split_frontmatter(AGENT)


# ----------------------------------------------------------------- opencode.json


def test_config_parses_and_has_required_keys(config):
    assert config["$schema"] == "https://opencode.ai/config.json"
    assert config["autoupdate"] is False
    assert config["share"] == "disabled"
    for key in ("provider", "model", "small_model", "permission"):
        assert key in config, key


def test_local_providers(config):
    prov = config["provider"]
    expected = {
        "ollama": "http://localhost:11434/v1",
        "llama.cpp": "http://127.0.0.1:8080/v1",
        "lmstudio": "http://127.0.0.1:1234/v1",
    }
    for pid, url in expected.items():
        p = prov[pid]
        assert p["npm"] == "@ai-sdk/openai-compatible", pid
        assert p["options"]["baseURL"] == url, pid
        assert p["models"], f"{pid} has no models"
        for mid, m in p["models"].items():
            assert m.get("name"), f"{pid}/{mid} has no display name"
    ollama = prov["ollama"]["models"]
    for mid in ("qwen3-coder:30b", "qwen3:14b", "devstral:24b", "gpt-oss:20b"):
        assert mid in ollama, mid
    assert ollama["qwen3-coder:30b"]["name"] == "Qwen3 Coder 30B-A3B (local)"
    # README tells people to run Ollama with OLLAMA_CONTEXT_LENGTH=32768; the budget must not exceed it
    assert ollama["qwen3-coder:30b"]["limit"] == {"context": 32768, "output": 8192}
    for mid, m in ollama.items():
        assert m["limit"]["context"] <= 32768, mid
    assert list(prov["llama.cpp"]["models"]) == ["local"]
    # "your-model-id" is the placeholder the README tells people to replace with a `check-model --list` id
    assert list(prov["lmstudio"]["models"]) == ["local", "your-model-id"]


def test_model_ids_exist_under_providers(config):
    for key in ("model", "small_model"):
        pid, _, mid = config[key].partition("/")
        assert pid in config["provider"], f"{key}: unknown provider {pid}"
        assert mid in config["provider"][pid]["models"], f"{key}: unknown model {mid}"
    assert config["model"] == "ollama/qwen3-coder:30b"
    assert config["small_model"] == config["model"]


def test_permissions(config):
    perm = config["permission"]
    assert perm["webfetch"] == "deny"
    assert perm["websearch"] == "deny"
    assert perm["doom_loop"] == "deny", "--auto would approve the default `ask`; a 3rd identical call must be blocked"
    bash = perm["bash"]
    assert next(iter(bash)) == "*" and bash["*"] == "ask", "catch-all must come first (last match wins)"
    for pat in KIT_BASH_ALLOW:
        assert bash.get(pat) == "allow", pat
    for pat in KIT_BASH_DENY:
        assert bash.get(pat) == "deny", pat
    keys = list(bash)
    assert max(keys.index(p) for p in KIT_BASH_ALLOW) < min(keys.index(p) for p in KIT_BASH_DENY), \
        "deny rules must follow the allow rules they override (last match wins)"
    edit = perm["edit"]
    assert next(iter(edit)) == "*" and edit["*"] == "deny"
    for pat in KIT_EDIT_ALLOW:
        assert edit.get(pat) == "allow", pat


def test_single_source_of_truth_for_agent(config):
    """The markdown file defines demo-smoke; opencode.json must not redefine it inline."""
    inline = (config.get("agent") or {}).get("demo-smoke")
    assert inline is None, "demo-smoke is defined twice (opencode.json and .opencode/agents)"
    assert config.get("default_agent") == "demo-smoke"
    assert AGENT.is_file()
    assert "command" not in config, "commands live in .opencode/commands/*.md; do not duplicate"


# ----------------------------------------------------------------- agent markdown


def test_agent_frontmatter(agent):
    fm, _body = agent
    assert fm["mode"] == "primary"
    assert fm["steps"] == 60          # /onboard with its single retries needs more than /smoke's 16-20 calls
    assert fm["temperature"] == pytest.approx(0.1)
    assert fm["description"]
    perm = fm["permission"]
    assert perm["webfetch"] == "deny" and perm["websearch"] == "deny"
    assert perm["doom_loop"] == "deny"
    bash = perm["bash"]
    assert next(iter(bash)) == "*" and bash["*"] == "ask"
    for pat in KIT_BASH_ALLOW:
        assert bash.get(pat) == "allow", pat
    for pat in KIT_BASH_DENY:
        assert bash.get(pat) == "deny", pat
    keys = list(bash)
    assert max(keys.index(p) for p in KIT_BASH_ALLOW) < min(keys.index(p) for p in KIT_BASH_DENY)
    edit = perm["edit"]
    assert next(iter(edit)) == "*" and edit["*"] == "deny"
    for pat in KIT_EDIT_ALLOW:
        assert edit.get(pat) == "allow", pat


def test_agent_playbook_content(agent):
    _, body = agent
    assert len(AGENT.read_text(encoding="utf-8").splitlines()) < 120
    for needle in (
        "python -m demo_smoke doctor",
        "python -m demo_smoke dryrun <scenario> --out <out>",
        "narrate-validate",
        "python -m demo_smoke synth --out <out>",
        "python -m demo_smoke record <scenario> --out <out> --capture screencast",
        "python -m demo_smoke edit --out <out>",
        "python -m demo_smoke verify --out <out>",
        "--narration template",
        "narration.json",
        "45 words",
        "result.json",
        "Report",
    ):
        assert needle in body, needle
    assert "exit code" in body.lower() or "Exit codes" in body
    assert "never write code" in body.lower() or "never write code" in body.replace("\n", " ").lower()


# ----------------------------------------------------------------- commands


@pytest.mark.parametrize("name", ["setup", "smoke", "narrate", "voice-check"])
def test_command_files(name):
    path = COMMANDS / f"{name}.md"
    assert path.is_file(), path
    fm, body = split_frontmatter(path)
    assert fm.get("description"), f"{name}.md: description missing"
    assert fm.get("agent") == "demo-smoke", f"{name}.md must run under the demo-smoke agent"
    assert body.strip(), f"{name}.md has no template body"


def test_command_templates():
    setup = (COMMANDS / "setup.md").read_text(encoding="utf-8")
    # the injected doctor prefers the kit's venv (bare `python` is the Store stub on Windows / absent on Debian)
    injection = re.search(r"^!`(.*)`$", setup, re.MULTILINE)
    assert injection, "setup.md has no !`...` injection"
    assert ".venv/bin/python -m demo_smoke doctor" in injection.group(1)
    assert "python3 -m demo_smoke doctor" in injection.group(1) and "python -m demo_smoke doctor" in injection.group(1)
    assert "exit code 3 from doctor" in setup and "chrome=MISSING" in setup
    assert "scripts/setup.sh" in setup and "setup.ps1" in setup
    for flag in ("--python PATH", "--base-url URL", "--no-doctor", "--torch-index URL",
                 "-Python PATH", "-BaseUrl URL", "-NoDoctor", "-TorchIndex URL"):
        assert flag in setup, flag
    smoke = (COMMANDS / "smoke.md").read_text(encoding="utf-8")
    assert "$1" in smoke and "$2" in smoke and "demo-output/<slug>" in smoke
    assert "@$1" in smoke
    assert "$ARGUMENTS" in smoke and "`headless`" in smoke and "`.wav`" in smoke    # opencode run --command passes one message
    narrate = (COMMANDS / "narrate.md").read_text(encoding="utf-8")
    assert "@$1" in narrate and "logs/dryrun.json" in narrate and "narrate-validate" in narrate
    voice = (COMMANDS / "voice-check.md").read_text(encoding="utf-8")
    assert "voice-check --ref $1" in voice
    assert "ask the user for the WAV path" in voice
    assert "as `backend`" not in voice and "tts_auto" in voice     # exit-3 log has no `backend` key


# ----------------------------------------------------------------- docs


def test_readme_mentions_key_paths():
    text = README.read_text(encoding="utf-8")
    for needle in (
        "--narration template",
        "prefetch",
        "OLLAMA_CONTEXT_LENGTH",
        "opencode run --agent demo-smoke --auto",
        "--command smoke",
        "prefetch --tts auto",
        "DEMO_USER",
        "Try it on the bundled mock app",
        "requirements-dev.txt",
        "DEMO_SMOKE_CHROME",
        "qwen3-coder:30b",
        "gpt-oss:20b",
        "Perth",
        "ARCHITECTURE.md",
        "rocm",
        "opencode --version",                     # OpenCode is installed while online
        "OPENCODE_DISABLE_MODELS_FETCH=1",
        "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS",
        "HUGGINGFACE_HUB_CACHE",
        "chrome-profile/",
        "Wayland",
        "doom_loop",
        "export DEMO_USER=alice DEMO_PASS=secret",
    ):
        assert needle in text, needle
    assert "DEMO_USER=alice DEMO_PASS=secret python" not in text     # no inline env prefix (unmatched by allow rules)
    assert "about a\ndozen tool calls" not in text


def test_ignore_file_reincludes_outputs():
    text = (KIT / ".ignore").read_text(encoding="utf-8")
    assert "!demo-output/" in text.splitlines()
    assert (KIT / "scenarios" / "fixtures" / "osha-1910.pdf").is_file()
    assert (KIT / "requirements-dev.txt").is_file()


def test_playbook_guards_for_small_models(agent):
    _, body = agent
    assert ".venv/bin/python" in body and "exists" in body            # venv rule keyed on existence
    assert "`ls .venv/bin/python`" in body                            # an exact, allow-listed command for the check
    assert "tts_ready" in body and "--tts tone" in body               # no-chatterbox fallback
    assert "--narration template`\n" in body and "--narration template --ref <ref>" not in body   # one-shot: no literal --ref
    assert "timed out" in body                                        # bash tool timeout is a named stop condition
    assert "Never copy `expect` values" in body                       # deterministic narration source
    assert "write tool" in body and "heredoc" in body                 # narration written whole
    assert "observed" in body and "quotes" in body                    # no selectors in narration
    assert "--headless" in body
    assert "do not edit the scenario" in body
    setup = (COMMANDS / "setup.md").read_text(encoding="utf-8")
    assert "do NOT run the setup script" in setup and "--base-url http://localhost:11434/v1" in setup
    narrate = (COMMANDS / "narrate.md").read_text(encoding="utf-8")
    assert "if empty" not in narrate and "@$2/logs/dryrun.json" in narrate


def test_agents_md_rules():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "python -m demo_smoke" in text
    assert "Never" in text
    assert "narration.json" in text
    assert "demo-output" in text
    # no second, differently numbered copy of the playbook (the agent file is the single source)
    assert not re.search(r"^\d+\. `python -m demo_smoke (dryrun|synth|record)", text, re.MULTILINE)
    assert ".opencode/agents/demo-smoke.md" in text


# ----------------------------------------------------------------- setup scripts


def test_setup_scripts_exist_and_mention_requirements():
    sh = SETUP_SH.read_text(encoding="utf-8")
    ps = SETUP_PS1.read_text(encoding="utf-8")
    for text, name in ((sh, "setup.sh"), (ps, "setup.ps1")):
        assert "requirements.txt" in text, name
        assert "requirements-tts.txt" in text, name
        assert "demo_smoke doctor" in text, name
        assert "ollama pull" in text, name
        assert "download.pytorch.org/whl/cu" in text, name
        assert "sudo" not in text.replace("never uses sudo", ""), name
    assert sh.startswith("#!/usr/bin/env bash")
    assert "--tts" in sh and "--torch" in sh and "--model" in sh
    assert "[switch]$Tts" in ps and "$Torch" in ps and "$Model" in ps
    for text, name in ((sh, "setup.sh"), (ps, "setup.ps1")):
        assert "demo_smoke prefetch --tts" in text, name             # the scripts fill the HF cache
        assert "--command smoke" in text, name
        assert "/smoke scenarios" not in text, name
        assert "OPENCODE_DISABLE_MODELS_FETCH=1" in text, name
        assert "opencode --version" in text, name
    assert 'cuda) [ "$OS" != Darwin ]' in sh                          # no CUDA wheels for macOS: clear message
    req = (KIT / "requirements-tts.txt").read_text(encoding="utf-8")
    assert "chatterbox-tts==0.1.7" in req and "gradio" in req


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_setup_sh_syntax_and_help():
    subprocess.run(["bash", "-n", str(SETUP_SH)], check=True)
    res = subprocess.run(["bash", str(SETUP_SH), "--help"], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    assert "--tts" in res.stdout
    res = subprocess.run(["bash", str(SETUP_SH), "--bogus"], capture_output=True, text=True, check=False)
    assert res.returncode == 4
    assert "unknown option" in res.stderr
    res = subprocess.run(["bash", str(SETUP_SH), "--torch", "tpu"], capture_output=True, text=True, check=False)
    assert res.returncode == 3
    assert "--torch must be" in res.stderr
    res = subprocess.run(["bash", str(SETUP_SH), "--prefetch", "all"], capture_output=True, text=True, check=False)
    assert res.returncode == 3
    assert "--prefetch must be" in res.stderr
