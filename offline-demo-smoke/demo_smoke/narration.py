"""Narration text: template generation, validation, and LLM generation with fallback.

Narration JSON::

    {"intro": str, "outro": str, "steps": [{"id": str, "text": str}, ...]}

Step ids must equal the scenario's step ids, in order.  Each segment is at
most ``MAX_SEGMENT_WORDS`` words; the total is at most
``max_length_seconds * WORDS_PER_SECOND`` words.
"""

from __future__ import annotations

import json
import re

MAX_SEGMENT_WORDS = 45
WORDS_PER_SECOND = 2.6
FILENAME = "narration.json"

SYSTEM_PROMPT = (
    "You write short spoken narration for a screen-recorded product demo.\n"
    "Respond with ONE JSON object and nothing else: no markdown, no code fences, "
    "no commentary before or after.\n"
    "Exact schema:\n"
    '{"intro": "<string>", "outro": "<string>", '
    '"steps": [{"id": "<step id>", "text": "<string>"}, ...]}\n'
    "Rules:\n"
    "- steps must contain exactly the given step ids, in the given order, no extras.\n"
    "- Every string is plain spoken English, first person, present tense "
    "(\"I open the app and ...\"), one or two natural sentences.\n"
    f"- Each string is at most {MAX_SEGMENT_WORDS} words.\n"
    "- Say what the viewer sees on screen during that step; no marketing fluff, "
    "no bullet points, no emojis, no URLs, no selectors or code.\n"
    "- Stay within the total word budget given in the request."
)


def words(text: str) -> int:
    """Count spoken words (tokens containing at least one letter or digit)."""
    if not text:
        return 0
    return sum(1 for w in str(text).split() if re.search(r"[A-Za-z0-9]", w))


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _step_sentence(step: dict) -> str:
    title = " ".join(step.get("title", step["id"]).split())
    if not title:
        return f"Now the {step['id']} step."
    first, _, rest = title.partition(" ")
    return _sentence(f"Now I {first[:1].lower() + first[1:]}{(' ' + rest) if rest else ''}")


def template(scenario: dict) -> dict:
    """Deterministic narration from the scenario's own intro/outro/narration fields."""
    name = scenario.get("name", "the app")
    intro = scenario.get("intro") or f"Here is a quick walkthrough of {name}."
    outro = scenario.get("outro") or f"That completes the {name} smoke test."
    steps = []
    for step in scenario.get("steps", []):
        text = (step.get("narration") or "").strip() or _step_sentence(step)
        steps.append({"id": step["id"], "text": _sentence(text)})
    return {"intro": _sentence(intro), "outro": _sentence(outro), "steps": steps}


def word_budget(scenario: dict) -> int:
    return int(float(scenario.get("max_length_seconds", 90)) * WORDS_PER_SECOND)


def validate(narr: dict, scenario: dict) -> list[str]:
    """Problems with a narration dict against the scenario; ``[]`` if valid."""
    errors: list[str] = []
    if not isinstance(narr, dict):
        return ["narration must be a JSON object with intro, outro, steps"]
    total = 0
    for key in ("intro", "outro"):
        val = narr.get(key)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{key} must be a non-empty string")
            continue
        n = words(val)
        total += n
        if n > MAX_SEGMENT_WORDS:
            errors.append(f"{key} has {n} words (max {MAX_SEGMENT_WORDS})")
    steps = narr.get("steps")
    if not isinstance(steps, list):
        errors.append("steps must be a list of {id, text}")
        return errors
    want = [s["id"] for s in scenario.get("steps", [])]
    got = []
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            errors.append(f"steps[{i}] must be an object {{id, text}}")
            continue
        got.append(st.get("id"))
        text = st.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"steps[{i}] ({st.get('id')}) text must be a non-empty string")
            continue
        n = words(text)
        total += n
        if n > MAX_SEGMENT_WORDS:
            errors.append(f"steps[{i}] ({st.get('id')}) has {n} words (max {MAX_SEGMENT_WORDS})")
    if got != want:
        errors.append(f"step ids must be exactly {want} in order, got {got}")
    budget = word_budget(scenario)
    if total > budget:
        errors.append(
            f"total narration is {total} words; max {budget} for "
            f"max_length_seconds={scenario.get('max_length_seconds', 90)}"
        )
    for key in narr:
        if key not in ("intro", "outro", "steps"):
            errors.append(f"unknown key '{key}'")
    return errors


def extract_json(text: str) -> dict:
    """Tolerant JSON extraction: strips code fences, takes the first {...} object."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response")
    s = text.strip()
    s = re.sub(r"^\s*```[a-zA-Z0-9]*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    start = s.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(s[start:])
        except json.JSONDecodeError:
            start = s.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = s.find("{", start + 1)
    raise ValueError("no JSON object found in the model response")


def _summarize_action(action: dict) -> str:
    (name, val), = action.items()
    if isinstance(val, dict):
        if name == "fill" or name == "type":
            return f"{name} '{val.get('text', '')}' into {val.get('selector')}"
        if name == "upload":
            names = ", ".join(str(f).replace("\\", "/").rsplit("/", 1)[-1] for f in val.get("files", []))
            return f"upload {names}"
        return f"{name} {json.dumps(val)}"
    return f"{name} {val}"


def _summarize_expect(e: dict) -> str:
    parts = [f"{k}={v!r}" for k, v in e.items()]
    return ", ".join(parts)


def build_request(scenario: dict) -> str:
    lines = [
        f"App: {scenario.get('name')}",
        f"Total word budget: {word_budget(scenario)} words "
        f"(video max {scenario.get('max_length_seconds', 90)} s).",
        f"Max {MAX_SEGMENT_WORDS} words per segment.",
        "",
    ]
    if scenario.get("intro"):
        lines.append(f"Intro hint: {scenario['intro']}")
    if scenario.get("outro"):
        lines.append(f"Outro hint: {scenario['outro']}")
    lines.append("")
    lines.append("Steps (id | title | what happens on screen | what should appear):")
    for step in scenario.get("steps", []):
        acts = "; ".join(_summarize_action(a) for a in step.get("actions", [])) or "-"
        exp = "; ".join(_summarize_expect(e) for e in step.get("expect", [])) or "-"
        hint = f" | hint: {step['narration']}" if step.get("narration") else ""
        lines.append(f"- {step['id']} | {step.get('title', '')} | {acts} | {exp}{hint}")
    lines.append("")
    lines.append("Required step ids in order: " + json.dumps([s["id"] for s in scenario.get("steps", [])]))
    lines.append("Return only the JSON object.")
    return "\n".join(lines)


def from_llm(scenario: dict, base_url: str, model: str, timeout: int = 180) -> tuple[dict, str, str]:
    """(narration, source, note): source is "llm" or "template" (with the reason in note)."""
    from . import llm

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_request(scenario)},
    ]
    attempts: list[str] = []
    for attempt in range(2):
        try:
            resp = llm.chat(base_url, model, messages, timeout=timeout,
                            temperature=0.1, response_json=True)
        except llm.LLMError as e:
            attempts.append(f"request failed: {e}")
            break
        text = llm.content_text(resp)
        try:
            narr = extract_json(text)
        except ValueError as e:
            problems = [f"{e}: {text.strip()[:120]!r}"]
        else:
            problems = validate(narr, scenario)
            if not problems:
                note = "narration from model" if attempt == 0 else "narration from model after one repair round"
                return narr, "llm", note
        attempts.append(f"attempt {attempt + 1}: " + "; ".join(problems))
        if attempt == 0:
            messages.append({"role": "assistant", "content": text or ""})
            messages.append({
                "role": "user",
                "content": "That response was rejected. Problems:\n- "
                           + "\n- ".join(problems)
                           + "\nReturn the corrected JSON object only, same schema, nothing else.",
            })
    note = "fell back to template narration: " + " | ".join(attempts)
    return template(scenario), "template", note
