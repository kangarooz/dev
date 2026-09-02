"""Minimal ``.env`` reader/writer and credential resolver (no third-party lib).

Format: ``NAME=value`` per line, optional ``export`` prefix, ``#`` comments (a
whole line, or after an unquoted value), single or double quotes stripped
(``\\n``, ``\\t``, ``\\"`` and ``\\\\`` are unescaped inside double quotes).
Names must match ``[A-Z_][A-Z0-9_]*``; other lines are ignored.

A value may be a 1Password reference (``op://vault/item/field``); it is
resolved at run time with ``op read REF`` when the ``op`` CLI is on PATH.

``load_env(env_file)`` is what ``cli.main`` calls at start-up: it exports every
name from the file that is not already set in ``os.environ`` (the environment
always wins) and resolves ``op://`` values while doing so.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
OP_PREFIX = "op://"
OP_TIMEOUT_S = 60
KIT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = KIT_DIR / ".env"

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_DQ_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class OpError(RuntimeError):
    """``op read`` could not resolve a reference (one-line message)."""


# --------------------------------------------------------------------------- parsing


def env_path(env_file: str | Path | None = None) -> Path:
    """``--env-file`` if given, else ``<kit>/.env``."""
    return Path(env_file) if env_file else DEFAULT_ENV_FILE


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                out.append(_DQ_ESCAPES.get(raw[i + 1], "\\" + raw[i + 1]))
                i += 2
                continue
            if ch == '"':
                break
            out.append(ch)
            i += 1
        return "".join(out)
    if len(raw) >= 2 and raw[0] == "'":
        end = raw.find("'", 1)
        return raw[1:end] if end != -1 else raw[1:]
    # unquoted: a trailing comment starts at " #"
    m = re.search(r"\s#", raw)
    if m:
        raw = raw[: m.start()]
    return raw.strip()


def parse(text: str) -> dict[str, str]:
    """``{NAME: value}`` for every well-formed line; order preserved, last wins."""
    values: dict[str, str] = {}
    for line in text.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if not NAME_RE.match(name):
            continue
        values[name] = _unquote(m.group(2))
    return values


def read(env_file: str | Path | None = None) -> dict[str, str]:
    """Parse the file; a missing file is an empty dict."""
    p = env_path(env_file)
    try:
        return parse(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def names(env_file: str | Path | None = None) -> list[str]:
    return list(read(env_file))


# --------------------------------------------------------------------------- writing


def format_value(value: str) -> str:
    """Quote a value when it would not survive the unquoted parser."""
    if value == "" or re.search(r"[\s#\"'\\]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") \
            .replace("\r", "\\r").replace("\t", "\\t")
        return f'"{escaped}"'
    return value


def _secure_open_write(p: Path):
    """Open for writing, creating the file with mode 0600 (POSIX; a no-op mask on Windows)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(p), flags, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n")


def write_value(env_file: str | Path | None, name: str, value: str) -> Path:
    """Set ``NAME=value``: update the existing line in place, else append.

    Creates the file (and parent directory) with mode 0600 on POSIX; an
    existing file is re-chmodded to 0600 as well, since it holds secrets.
    Raises ``ValueError`` for a bad name.
    """
    if not NAME_RE.match(name or ""):
        raise ValueError(f"invalid name {name!r}: must match [A-Z_][A-Z0-9_]*")
    p = env_path(env_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if p.is_file():
        lines = p.read_text(encoding="utf-8").lstrip("\ufeff").splitlines()
    new_line = f"{name}={format_value(value)}"
    replaced = False
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m and m.group(1) == name:
            if not replaced:
                lines[i] = new_line
                replaced = True
            else:
                lines[i] = ""  # drop duplicates so the file has one definition
    if not replaced:
        lines.append(new_line)
    text = "\n".join(line for line in lines if line is not None)
    with _secure_open_write(p) as fh:
        fh.write(text.rstrip("\n") + "\n")
    if os.name == "posix":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


# --------------------------------------------------------------------------- op://


def is_op_ref(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(OP_PREFIX)


def op_path() -> str | None:
    """The 1Password CLI on PATH (``op``, or ``op.exe``/``op.cmd`` on Windows), else None."""
    return shutil.which("op")


def resolve_op(ref: str, timeout: int = OP_TIMEOUT_S) -> str:
    """``op read REF`` -> the secret (one trailing newline stripped).  Raises ``OpError``."""
    op = op_path()
    if not op:
        raise OpError("1Password CLI 'op' is not on PATH (install it, or set the value directly)")
    try:
        cp = subprocess.run([op, "read", ref], capture_output=True, text=True, timeout=timeout,
                            check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise OpError(f"op read failed: {e}") from None
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip().splitlines()
        raise OpError(f"op read {ref} failed (exit {cp.returncode})"
                      + (f": {detail[-1][:200]}" if detail else ""))
    out = cp.stdout
    if out.endswith("\r\n"):
        out = out[:-2]
    elif out.endswith("\n"):
        out = out[:-1]
    return out


# --------------------------------------------------------------------------- resolving


def lookup(name: str, env_file: str | Path | None = None) -> tuple[str | None, str]:
    """Raw value without touching ``op``: ``(value, "environ" | ".env" | "missing")``."""
    if name in os.environ:
        return os.environ[name], "environ"
    vals = read(env_file)
    if name in vals:
        return vals[name], ".env"
    return None, "missing"


def resolve(name: str, env_file: str | Path | None = None) -> tuple[str | None, str]:
    """Resolved value: ``os.environ`` -> ``.env`` -> ``op://`` via the 1Password CLI.

    Returns ``(value, source)`` where source is ``"environ"``, ``".env"``,
    ``"op://"`` (resolved through ``op``), ``"missing"`` or ``"op-error: <why>"``
    (value None in the last two).
    """
    raw, source = lookup(name, env_file)
    if raw is None:
        return None, "missing"
    if not is_op_ref(raw):
        return raw, source
    try:
        return resolve_op(raw), "op://"
    except OpError as e:
        return None, f"op-error: {e}"


def load_env(env_file: str | Path | None = None, resolve_refs: bool = True) -> dict[str, str]:
    """Export ``.env`` names that are not already in ``os.environ``; return what was set.

    ``op://`` values are resolved through the 1Password CLI when it is on PATH;
    when it is missing or fails the name is **not** exported (a raw ``op://...``
    string in ``os.environ`` would be typed into a login form as the password)
    and ``load_env.unresolved`` maps that name to the reason after the call.
    """
    loaded: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    for name, value in read(env_file).items():
        if name in os.environ:
            continue
        if resolve_refs and is_op_ref(value):
            try:
                value = resolve_op(value)
            except OpError as e:
                unresolved[name] = str(e)
                continue
        os.environ[name] = value
        loaded[name] = value
    load_env.unresolved = unresolved  # type: ignore[attr-defined]
    return loaded


load_env.unresolved = {}  # type: ignore[attr-defined]
