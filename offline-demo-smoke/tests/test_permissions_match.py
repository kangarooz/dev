"""Do the kit's OpenCode permission rules allow exactly the commands the playbooks use?

Re-implements OpenCode's rule evaluation (``packages/core/src/util/wildcard.ts``
+ ``Permission.evaluate`` in v1.18.x): a pattern is matched against the whole
input with ``*`` = any characters, ``?`` = one character, everything else
literal (backslashes normalised to ``/`` on both sides, a trailing ``" *"``
also matching the bare command, case-insensitive on Windows); rules are
flattened in object order and the **last matching rule wins**, ``ask`` when
nothing matches.  Rulesets are layered like OpenCode does for a markdown
agent: built-in defaults, then ``opencode.json``'s ``permission``, then the
agent frontmatter; ``opencode run`` appends ``question: deny``.

The golden-path command strings are pulled out of the command files and the
agent playbook, placeholders substituted, and every one must resolve to
``allow`` (``creds set`` to ``deny``) for all four interpreter spellings.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_opencode_config import split_frontmatter

KIT = Path(__file__).resolve().parents[1]
CONFIG = KIT / "opencode.json"
AGENT = KIT / ".opencode" / "agents" / "demo-smoke.md"
COMMANDS = KIT / ".opencode" / "commands"
COMMAND_FILES = ("setup", "smoke", "narrate", "voice-check", "onboard", "clone-voice")

INTERPRETERS = ("python", "python3", ".venv/bin/python", ".venv\\Scripts\\python.exe")

# OpenCode's built-in defaults for every agent (agent.ts, v1.18.26). ``read`` of
# ``.env`` is only ``ask`` there, which ``--auto`` would approve: the kit config
# turns it into ``deny``.
OPENCODE_DEFAULTS = {
    "*": "allow",
    "doom_loop": "ask",
    "external_directory": {"*": "ask"},
    "question": "deny",
    "plan_enter": "deny",
    "plan_exit": "deny",
    "read": {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"},
}
# ``opencode run`` (non-interactive) appends these to every session.
RUN_MODE_RULES = {"question": "deny", "plan_enter": "deny", "plan_exit": "deny"}

# Placeholders the playbooks use -> concrete values for matching.
PLACEHOLDERS = {
    "<scenario>": "scenarios/chat-with-manuals.json",
    "<out>": "demo-output/chat-with-manuals",
    "<ref>": "voices/nick.wav",
    "<name>": "nick",
    "<slug>": "chat-with-manuals",
    "<url>": "http://localhost:3000",
    "<index>": "1",
    "<n>": "1",
    "<backend>": "turbo",
    "<cmd>": "dryrun",
    "$1": "scenarios/chat-with-manuals.json",
    "$2": "demo-output/chat-with-manuals",
    "$ARGUMENTS": "",
    "NAME...": "DEMO_USER DEMO_PASS",
    "NAME": "DEMO_USER",
    "USER_ENV PASS_ENV": "DEMO_USER DEMO_PASS",
    "USER_ENV": "DEMO_USER",
    "PASS_ENV": "DEMO_PASS",
}


# ----------------------------------------------------------------- OpenCode semantics


def wildcard_match(text: str, pattern: str, windows: bool = False) -> bool:
    """``Wildcard.match`` from OpenCode core: ``*`` any run, ``?`` one char, else literal."""
    text = text.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        elif ch in ".+^${}()|[]\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    escaped = "".join(out)
    if escaped.endswith(" .*"):          # "ls *" also matches a bare "ls"
        escaped = escaped[:-3] + "( .*)?"
    flags = re.DOTALL | (re.IGNORECASE if windows else 0)
    return re.fullmatch(escaped, text, flags) is not None


def from_config(permission: dict) -> list[dict]:
    """``Permission.fromConfig``: object order preserved, shorthand -> pattern ``*``."""
    rules = []
    for key, value in permission.items():
        if isinstance(value, str):
            rules.append({"permission": key, "pattern": "*", "action": value})
        else:
            for pattern, action in value.items():
                rules.append({"permission": key, "pattern": pattern, "action": action})
    return rules


def evaluate(permission: str, pattern: str, *rulesets: list[dict], windows: bool = False) -> str:
    """``Permission.evaluate``: last matching rule wins over the flattened rulesets; else ``ask``."""
    result = "ask"
    for rule in [r for rs in rulesets for r in rs]:
        if wildcard_match(permission, rule["permission"], windows) and \
                wildcard_match(pattern, rule["pattern"], windows):
            result = rule["action"]
    return result


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def config_perm() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["permission"]


@pytest.fixture(scope="module")
def agent_perm() -> dict:
    fm, _ = split_frontmatter(AGENT)
    return fm["permission"]


@pytest.fixture(scope="module")
def rulesets(config_perm, agent_perm) -> tuple[list[dict], ...]:
    """(defaults, opencode.json, agent frontmatter) in OpenCode's merge order for a markdown agent."""
    return from_config(OPENCODE_DEFAULTS), from_config(config_perm), from_config(agent_perm)


def bash_action(rulesets, command: str, windows: bool = False) -> str:
    return evaluate("bash", command, *rulesets, windows=windows)


def _substitute(cmd: str) -> str:
    for key, value in PLACEHOLDERS.items():
        cmd = cmd.replace(key, value)
    cmd = re.sub(r"<[a-z_ -]+>", "x", cmd)        # any placeholder left: a plain token
    return re.sub(r"\s+", " ", cmd).strip()


def golden_commands() -> list[tuple[str, str]]:
    """Every ``python -m demo_smoke ...`` string in backticks in the playbooks, placeholders filled."""
    found: list[tuple[str, str]] = []
    sources = [(AGENT.name, AGENT)] + [(f"{n}.md", COMMANDS / f"{n}.md") for n in COMMAND_FILES]
    for label, path in sources:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"`(python -m demo_smoke [^`\n]*)`", text):
            cmd = _substitute(m.group(1))
            # a playbook may show "--flag <value>" alternatives with "|": take the first
            cmd = re.sub(r"(\S+)\|\S+", r"\1", cmd)
            found.append((label, cmd))
    assert len(found) >= 25, "playbooks lost their command strings?"
    return found


def with_interpreter(cmd: str, interpreter: str) -> str:
    assert cmd.startswith("python -m demo_smoke")
    return interpreter + cmd[len("python"):]


# ----------------------------------------------------------------- wildcard semantics


@pytest.mark.parametrize("text,pattern,expected", [
    ("ls", "ls *", True),                              # trailing " *" makes the rest optional
    ("ls -la", "ls *", True),
    ("lsx", "ls *", False),
    ("python -m demo_smoke doctor", "python -m demo_smoke *", True),
    ("python -m demo_smoke", "python -m demo_smoke *", True),
    ("python3 -m demo_smoke doctor", "python -m demo_smoke *", False),
    ("git status --porcelain", "git status*", True),
    ("git push origin main", "git push*", True),
    ("python -m demo_smoke synth --out x --online", "* --online*", True),
    ("python -m demo_smoke synth --out x --tts tone", "* --online*", False),
    ("rm -rf demo-output", "rm -rf *", True),
    ("rm -r demo-output", "rm -rf *", False),
    ("a?c", "a?c", True),                              # ? = exactly one character (also matches itself)
    ("abc", "a?c", True),
    ("abbc", "a?c", False),
    ("a.c", "a.c", True),                              # regex specials are literal
    ("abc", "a.c", False),
    ("x (1)", "x (1)", True),
    (".venv\\Scripts\\python.exe -m demo_smoke doctor", ".venv\\Scripts\\python.exe -m demo_smoke *", True),
    (".venv/Scripts/python.exe -m demo_smoke doctor", ".venv\\Scripts\\python.exe -m demo_smoke *", True),
    ("scenarios/x.json", "scenarios/*.json", True),
    ("scenarios/sub/x.json", "scenarios/*.json", True),   # * crosses "/" (it is not a glob)
    ("demo-output/a/audio/narration.json", "**/audio/narration.json", True),
    (".env", "*.env", True),
    ("sub/.env", "*.env", True),
    (".env.local", "*.env.*", True),
    (".env.example", "*.env.example", True),
    ("env", "*.env", False),
])
def test_wildcard_match(text, pattern, expected):
    assert wildcard_match(text, pattern) is expected


def test_wildcard_windows_is_case_insensitive():
    assert wildcard_match("PYTHON -m demo_smoke doctor", "python -m demo_smoke *", windows=True)
    assert not wildcard_match("PYTHON -m demo_smoke doctor", "python -m demo_smoke *", windows=False)


def test_last_matching_rule_wins_in_object_order():
    rules = from_config({"bash": {"*": "ask", "git *": "allow", "git push*": "deny"}})
    assert evaluate("bash", "git status", rules) == "allow"
    assert evaluate("bash", "git push origin", rules) == "deny"
    assert evaluate("bash", "make", rules) == "ask"
    # the same rules in the opposite order flip the answer: order matters, not specificity
    rules = from_config({"bash": {"git push*": "deny", "git *": "allow", "*": "ask"}})
    assert evaluate("bash", "git push origin", rules) == "ask"
    # nothing configured at all -> ask
    assert evaluate("bash", "anything", []) == "ask"
    # later rulesets override earlier ones
    assert evaluate("bash", "x", from_config({"bash": "deny"}), from_config({"bash": {"x": "allow"}})) == "allow"


# ----------------------------------------------------------------- the kit's rules


def test_config_and_agent_rules_are_identical(config_perm, agent_perm):
    """One source of truth twice: the markdown agent must not drift from opencode.json."""
    for key in ("bash", "edit", "read"):
        assert list(config_perm[key].items()) == list(agent_perm[key].items()), key
    for key in ("webfetch", "websearch", "doom_loop"):
        assert config_perm[key] == agent_perm[key] == "deny"
    assert agent_perm["task"] == "deny"
    assert agent_perm["question"] == "allow"
    assert "question" not in config_perm          # only the agent opens it (the TUI onboarding)
    assert "model" not in split_frontmatter(AGENT)[0], "the agent must not pin a model (--model / /models)"


@pytest.mark.parametrize("interpreter", INTERPRETERS)
def test_golden_path_commands_are_allowed(rulesets, interpreter):
    windows = "\\" in interpreter
    for label, cmd in golden_commands():
        full = with_interpreter(cmd, interpreter)
        # the playbooks quote two denied commands for the *user* to run: creds set and prefetch
        expected = "deny" if (" creds set " in cmd + " " or " prefetch" in cmd) else "allow"
        assert bash_action(rulesets, full, windows=windows) == expected, f"{label}: {full}"
        # the agent frontmatter alone (no opencode.json) gives the same answer
        assert evaluate("bash", full, rulesets[0], rulesets[2], windows=windows) == expected, f"{label}: {full}"
        # and so does opencode.json alone (any other agent, e.g. build)
        assert evaluate("bash", full, rulesets[0], rulesets[1], windows=windows) == expected, f"{label}: {full}"


def test_golden_path_covers_every_new_command():
    cmds = " ".join(c for _, c in golden_commands())
    for sub in ("record-ref", "devices", "creds check", "creds set", "init-scenario",
                "validate", "inspect", "doctor", "dryrun", "narrate-validate",
                "narrate-template", "synth", "record", "edit", "verify", "voice-check", "run"):
        assert f"demo_smoke {sub}" in cmds, sub


@pytest.mark.parametrize("interpreter", INTERPRETERS)
@pytest.mark.parametrize("tail", [
    "creds set DEMO_PASS",
    "creds set DEMO_USER --env-file .env",
    "creds set X --value-from-stdin",
])
def test_creds_set_is_denied(rulesets, interpreter, tail):
    cmd = f"{interpreter} -m demo_smoke {tail}"
    assert bash_action(rulesets, cmd, windows="\\" in interpreter) == "deny"


@pytest.mark.parametrize("cmd", [
    "python -m demo_smoke prefetch --tts turbo",
    "python3 -m demo_smoke prefetch",
    ".venv/bin/python -m demo_smoke prefetch --tts nano",
    "python -m demo_smoke synth --out demo-output/x --tts auto --online",
    "python -m demo_smoke voice-check --ref voices/a.wav --online",
    "rm -rf demo-output",
    "del demo-output\\x.mp4",
    "git push origin main",
    "git push",
    # .env is denied to the read tool; the bash allow rules must not reopen it
    "cat .env",
    "cat .env.local",
    "cat sub/.env",
    "type .env",
    "type .env.production",
    "head .env",
    "printenv DEMO_PASS",
    "printenv",
    "env",
    "set",
    "export",
    "cp .env demo-output/x.txt",
])
def test_denied_commands(rulesets, cmd):
    assert bash_action(rulesets, cmd, windows="\\" in cmd) == "deny"


@pytest.mark.parametrize("cmd,expected", [
    ("ls .venv/bin/python", "allow"),
    ("ls", "allow"),
    ("dir .venv\\Scripts\\python.exe", "allow"),
    ("cat demo-output/x/logs/doctor.json", "allow"),
    ("type demo-output\\x\\logs\\doctor.json", "allow"),
    ("git status", "allow"),
    ("git diff --stat", "allow"),
    ("python -m demo_smoke record-ref --out voices/nick.wav --device 1", "allow"),
    ("python -m demo_smoke devices", "allow"),
    ("python -m demo_smoke devices --out demo-output/voice", "allow"),
    ("python -m demo_smoke creds list", "allow"),
    ("python -m demo_smoke creds check DEMO_USER DEMO_PASS", "allow"),
    ("python -m demo_smoke check-model --base-url http://127.0.0.1:1234/v1 --list", "allow"),
    ("python -m demo_smoke inspect http://localhost:3000 --out demo-output/x --headless", "allow"),
    ("pip install torch", "ask"),
    ("curl https://example.com", "ask"),
    ("bash scripts/setup.sh --tts", "ask"),
    ("python demo_smoke/cli.py doctor", "ask"),
    ("python -m pip install x", "ask"),
])
def test_other_commands(rulesets, cmd, expected):
    assert bash_action(rulesets, cmd, windows="\\" in cmd) == expected


def test_compound_commands_are_judged_per_command(rulesets):
    """OpenCode parses ``a && b`` with tree-sitter and evaluates each command separately, so a
    denied ``rm -rf`` cannot hide behind an allowed prefix (the deny rule stops the whole call)."""
    parts = ["python -m demo_smoke doctor", "rm -rf demo-output"]
    assert [bash_action(rulesets, p) for p in parts] == ["allow", "deny"]


@pytest.mark.parametrize("path,expected", [
    (".env", "deny"),
    (".env.local", "deny"),
    ("sub/.env", "deny"),
    (".env.example", "allow"),
    (".envrc", "deny"),
    ("scenarios/chat.json", "allow"),
    ("voices/nick.json", "allow"),
    ("demo-output/x/logs/doctor.json", "allow"),
    ("README.md", "allow"),
])
def test_read_rules(rulesets, path, expected):
    assert evaluate("read", path, *rulesets) == expected


def test_read_env_would_only_ask_without_the_kit_config():
    """OpenCode's own default is ``ask`` for .env, which ``--auto`` approves; the kit denies it."""
    assert evaluate("read", ".env", from_config(OPENCODE_DEFAULTS)) == "ask"


@pytest.mark.parametrize("path,expected", [
    ("scenarios/chat-with-manuals.json", "allow"),
    ("scenarios/fixtures/osha-1910.pdf", "deny"),
    ("demo-output/chat/audio/narration.json", "allow"),
    ("demo-output/chat/logs/anything.txt", "allow"),
    ("voices/nick.json", "deny"),           # the CLI writes it
    ("voices/nick.wav", "deny"),
    ("demo_smoke/cli.py", "deny"),
    ("opencode.json", "deny"),
    (".env", "deny"),
    ("AGENTS.md", "deny"),
])
def test_edit_rules(rulesets, path, expected):
    assert evaluate("edit", path, *rulesets) == expected


def test_question_tui_allowed_run_denied(rulesets):
    assert evaluate("question", "*", *rulesets) == "allow"
    assert evaluate("question", "*", *rulesets, from_config(RUN_MODE_RULES)) == "deny"
    # opencode.json alone (e.g. the built-in build agent) keeps OpenCode's default: TUI allows, run denies
    assert evaluate("question", "*", rulesets[0], rulesets[1]) == "deny"


def test_web_and_loops_denied(rulesets):
    for perm in ("webfetch", "websearch", "doom_loop", "task"):
        assert evaluate(perm, "*", *rulesets) == "deny", perm
    assert evaluate("external_directory", "/tmp/somewhere", *rulesets) == "ask"   # --auto approves; TUI asks


def test_deny_rules_follow_the_allow_rules_they_override(config_perm):
    """Object order is the precedence: every deny must come after the wildcard allow it narrows."""
    keys = list(config_perm["bash"])
    assert keys[0] == "*"
    allow_idx = [i for i, k in enumerate(keys) if config_perm["bash"][k] == "allow"]
    deny_idx = [i for i, k in enumerate(keys) if config_perm["bash"][k] == "deny"]
    assert max(allow_idx) < min(deny_idx)
    assert next(iter(config_perm["read"])) == "*" and next(iter(config_perm["edit"])) == "*"
