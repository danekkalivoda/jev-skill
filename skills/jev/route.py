#!/usr/bin/env python3
"""Pick a model tier for each delegated task, using TypeSafe Jev.

Input (stdin, JSON):
  {"tasks": [{"id": "a", "brief": "English task description", "attempt": 0, "min_tier": null, "min_tier_reason": null}]}
Output (stdout, JSON): one decision per task, with the model to use in Claude Code and in Codex,
plus "skill_hint": {"name", "confidence"} naming an installed skill the sub-agent could load, or null.

Jev only judges the task. Code owns the policy: coverage threshold, floors, retries, model table.
JEV_PROVIDER picks who serves the judgment model: typesafe (default), openrouter, or local (see models.json).
Stdlib only, so it runs the same under Claude Code, Codex, or any other agent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "models.json").read_text(encoding="utf-8"))


def load_setting(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    # Agent shells often skip the interactive profile (Codex passes only core env vars), so read it directly.
    pattern = re.compile(rf"""^\s*(?:export\s+)?{re.escape(name)}=["']?([^"'\s]+)""")
    for profile in (".zshenv", ".zshrc", ".zprofile", ".bash_profile", ".profile"):
        path = Path.home() / profile
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = pattern.match(line)
                if match:
                    return match.group(1)
    return None


PROVIDER_NAME = (load_setting("JEV_PROVIDER") or "typesafe").lower()
PROVIDER = CONFIG["providers"].get(PROVIDER_NAME)  # None for an unknown name; endpoint() reports it
# A provider may override policy numbers, because thresholds calibrated on one model do not carry over to another.
POLICY = {**CONFIG["policy"], **(PROVIDER or {}).get("policy", {})}
TIERS = CONFIG["tiers"]
MAX_TIER = len(TIERS) - 1
LOG = Path(os.environ.get("JEV_LOG", Path.home() / ".local/state/jev/decisions.jsonl"))

STATE = "An orchestrator is delegating a task to an AI coding sub-agent working in a software repository."

TIER_LEVELS = [
    "Mechanical: the steps are fully specified or obvious, with no design decisions. "
    "For example: search the code for something, rename or move things, run a given command and report the output, "
    "summarize or extract facts from named files, apply an exact edit.",
    "Standard: a clear goal and a well-specified change or investigation needing some judgment. "
    "For example: implement a small feature or fix in one to three files following existing patterns, "
    "write tests for specified behavior, trace how a known flow works.",
    "Hard: needs real reasoning across several parts of a codebase. "
    "For example: debug a failure whose cause is unknown, a multi-file refactor, design an API or data model change, "
    "review code carefully for correctness bugs.",
    "Frontier: open-ended, novel, or subtle work where mistakes are costly or hard to spot. "
    "For example: system architecture with trade-offs, a security audit, concurrency or data-integrity problems, "
    "a problem that earlier attempts failed to solve.",
]


def endpoint() -> tuple[str, dict, str]:
    """URL, headers and model of the chosen provider. Raises when the provider is unknown or not set up."""
    if PROVIDER is None:
        raise RuntimeError(f"unknown JEV_PROVIDER '{PROVIDER_NAME}' (known: {', '.join(CONFIG['providers'])})")
    headers = {"Content-Type": "application/json"}
    url = PROVIDER.get("url") or load_setting(PROVIDER["url_env"])
    if not url:
        raise RuntimeError(f"{PROVIDER['url_env']} not found in env or shell profile")
    if "key_env" in PROVIDER:
        key = load_setting(PROVIDER["key_env"])
        if not key:
            raise RuntimeError(f"{PROVIDER['key_env']} not found in env or shell profile")
        headers["Authorization"] = f"Bearer {key}"
    return url, headers, PROVIDER["model"]


def latest_codex_model(family: str) -> str:
    """Newest listed Codex model of a family (luna, sol, astra), read from the cache Codex refreshes itself."""
    pattern = re.compile(rf"^gpt-(\d+(?:\.\d+)*)-{re.escape(family)}$")
    try:
        cache = json.loads((Path.home() / ".codex/models_cache.json").read_text(encoding="utf-8"))
        versions = [
            (tuple(int(part) for part in match.group(1).split(".")), model["slug"])
            for model in cache["models"]
            if model.get("visibility") == "list" and (match := pattern.match(model.get("slug", "")))
        ]
    except (OSError, ValueError, KeyError, TypeError):
        versions = []
    if versions:
        return max(versions)[1]
    return f"gpt-{CONFIG['codex_fallback_generation']}-{family}"


def questions_for(task: dict) -> dict:
    tid, brief = task["id"], task["brief"]
    return {
        f"tier:{tid}": {
            "type": "score",
            "instructions": {
                "task": brief,
                "question": "How capable must the AI coding agent be to complete `task` reliably on the first attempt?",
            },
            "criteria": TIER_LEVELS,
        },
        f"risky:{tid}": {
            "type": "noul",
            "instructions": {
                "task": brief,
                "question": "Could a mistake while doing `task` cause serious or hard-to-reverse harm, such as data loss, "
                "a security hole, broken production, destructive git operations, or wrong handling of money or permissions?",
            },
        },
        f"unclear:{tid}": {
            "type": "noul",
            "instructions": {
                "task": brief,
                "question": "Is `task` too vague for an agent to start work without asking questions, "
                "because the goal, the scope, or the done condition is missing?",
            },
        },
    }


def ask_jev(tasks: list[dict]) -> dict:
    target = endpoint()
    questions: dict = {}
    for task in tasks:
        questions.update(questions_for(task))
    if not PROVIDER.get("one_question_per_request"):
        return post_questions(target, questions)
    # Some servers put all questions of a request into one prompt, so the answers sway each other and a big wave
    # overflows the context. One question per request keeps each rating independent of the rest of the wave.
    answers: dict = {}
    for name, question in questions.items():
        response = post_questions(target, {name: question})
        answers.update(response["answers"])
    return {"answers": answers, "model": response.get("model")}


def post_questions(target: tuple[str, dict, str], questions: dict) -> dict:
    url, headers, model = target
    body = json.dumps({"state": STATE, "model": model, "questions": questions}).encode()
    request = urllib.request.Request(url, data=body, headers=headers)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=POLICY["timeout_seconds"]) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in (429, 529, 500, 502, 503):
                break
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
        time.sleep(0.5 * 2**attempt)
    raise RuntimeError(f"Jev request failed: {last_error}")


SKILL_STATE_INTRO = "An orchestrator is delegating a task to an AI coding sub-agent. Task brief:\n\n"
SKILL_NONE = "none"
SKILL_HINT_GRACE_SECONDS = 0.5
SKILL_NONE_DESC = "No special skill. Ordinary implementation, edits, fixes, searches and reading named files."
SKILL_QUESTION = (
    "A sub-agent will do the task in `state`. Pick a skill only if its instructions are written specifically for this kind of work "
    "and would change how the agent does it. If the task is an ordinary code change, search or fix that a capable coding agent does "
    "without special instructions, pick none."
)


def skill_frontmatter(path: Path) -> dict | None:
    """name, description and disable-model-invocation from a SKILL.md header (simple YAML subset)."""
    match = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8", errors="ignore"), re.S)
    if not match:
        return None
    lines, out, i = match.group(1).split("\n"), {}, 0
    while i < len(lines):
        key_match = re.match(r"^(name|description|disable-model-invocation):\s*(.*)$", lines[i])
        i += 1
        if not key_match:
            continue
        key, value = key_match.group(1), key_match.group(2).strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            parts = []
            while i < len(lines) and (lines[i].startswith(" ") or not lines[i].strip()):
                parts.append(lines[i].strip())
                i += 1
            value = " ".join(part for part in parts if part)
        elif value[:1] in "\"'" and len(value) > 1:
            quote = value[0]
            while not (len(value) > 1 and value.endswith(quote)) and i < len(lines):
                value += " " + lines[i].strip()
                i += 1
            value = value[1:-1] if value.endswith(quote) else value[1:]
            if quote == '"':
                value = value.replace('\\"', '"')
        if key == "disable-model-invocation":
            out[key] = value.strip("\"'").lower() == "true"
            continue
        out[key] = re.sub(r"\s+", " ", value).strip()
    return out


def installed_skills() -> dict[str, str]:
    """Skills a sub-agent could load, name -> description. Skips jev and skills the model may not invoke itself."""
    roots = [Path.home() / ".claude/skills", Path.home() / ".agents/skills", Path.cwd() / ".claude/skills", Path.cwd() / ".agents/skills"]
    skills: dict[str, str] = {}
    excluded: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                meta = skill_frontmatter(path)
            except OSError:
                continue
            name = (meta or {}).get("name")
            if not name or name == "jev":
                continue
            if meta.get("disable-model-invocation"):
                excluded.add(name)
                continue
            description = meta.get("description", "")
            if name not in skills or len(description) > len(skills[name]):
                skills[name] = description
    for name in excluded:
        skills.pop(name, None)
    return skills


def short_description(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def ask_skill_hint(target: tuple[str, dict, str], brief: str, criteria: dict[str, str], deadline: float) -> dict | None:
    """One Choice question per task. Best effort: one retry at most, never past the deadline, any error gives None."""
    try:
        url, headers, model = target
        body = json.dumps({
            "state": SKILL_STATE_INTRO + brief,
            "model": model,
            "questions": {"skill": {"type": "choice", "instructions": SKILL_QUESTION, "criteria": criteria}},
        }).encode()
        for _ in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0.2:
                return None
            request = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=remaining) as response:
                    answer = json.loads(response.read())["answers"]["skill"]
                break
            except urllib.error.HTTPError as error:
                if error.code not in (429, 529, 500, 502, 503):
                    return None
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
        else:
            return None
        name, confidence = answer["choice"], round(float(answer["confidence"]), 2)
        if name == SKILL_NONE or name not in criteria or confidence < POLICY["skill_hint_confidence"]:
            return None
        return {"name": name, "confidence": confidence}
    except Exception:
        return None


def smallest_covering_tier(probabilities: dict[str, float]) -> int:
    """Cheapest tier k where P(needed tier <= k) reaches the coverage threshold."""
    cumulative = 0.0
    for tier in range(MAX_TIER + 1):
        cumulative += probabilities.get(str(tier), 0.0)
        if cumulative >= POLICY["coverage"]:
            return tier
    return MAX_TIER


def decide(task: dict, answers: dict | None, error: str | None, skill_hint: dict | None = None) -> dict:
    tid = task["id"]
    reasons: list[str] = []
    risky = unclear = None
    score = confidence = None
    if answers is None:
        tier = POLICY["fallback_tier"]
        reasons.append(f"fallback: {error}")
    else:
        tier_answer = answers[f"tier:{tid}"]
        score = round(tier_answer["score"], 2)
        confidence = round(tier_answer["confidence"], 2)
        probabilities = tier_answer["probabilities"]
        tier = smallest_covering_tier(probabilities)
        reasons.append(f"jev tier {tier} (score {score}, confidence {confidence})")
        risky = round(answers[f"risky:{tid}"]["noul"], 2)
        unclear = round(answers[f"unclear:{tid}"]["noul"], 2)
        # Jev over-rates: most tasks land on tier 2 even when tier 1 is plausible. The standard model is close to the
        # hard one on most benchmarks, so a modest chance of tier 1 is enough, unless the task is risky.
        standard_share = round(probabilities.get("0", 0.0) + probabilities.get("1", 0.0), 2)
        hard_share = round(standard_share + probabilities.get("2", 0.0), 2)
        if tier == 2 and standard_share >= POLICY["standard_coverage"] and risky < POLICY["standard_risky_guard"]:
            tier = 1
            reasons.append(f"P(tier <= 1) {standard_share} reaches {POLICY['standard_coverage']}: dropped to tier 1")
        # Same for tier 3: the frontier model costs 2.5x the hard one; keep it for risky tasks and clear frontier work.
        elif tier == 3 and hard_share >= POLICY["frontier_coverage"] and risky < POLICY["frontier_risky_guard"]:
            tier = 2
            reasons.append(f"P(tier <= 2) {hard_share} reaches {POLICY['frontier_coverage']}: dropped to tier 2")
        # Low confidence means Jev is guessing; a guess must not buy the pricier model.
        elif confidence < POLICY["low_confidence_threshold"] and tier > 0:
            tier -= 1
            reasons.append(f"confidence {confidence} below {POLICY['low_confidence_threshold']}: dropped to tier {tier}")
        if risky >= POLICY["risky_threshold"] and tier < POLICY["risky_floor_tier"]:
            tier = POLICY["risky_floor_tier"]
            reasons.append(f"risky {risky}: raised to tier {tier}")

    min_tier = task.get("min_tier")
    if isinstance(min_tier, int) and min_tier > tier:
        tier = min_tier
        reasons.append(f"orchestrator floor tier {min_tier}: {task.get('min_tier_reason') or 'no reason given'}")

    attempt = int(task.get("attempt") or 0)
    escalate_to_user = False
    if attempt > 0:
        tier += attempt
        reasons.append(f"retry {attempt}: +{attempt} tier")
        if tier > MAX_TIER:
            escalate_to_user = True
            reasons.append("already failed at the top tier: ask the user before retrying")
    tier = min(tier, MAX_TIER)

    row = TIERS[tier]
    return {
        "id": tid,
        "tier": tier,
        "label": row["label"],
        "claude": row["claude"],
        "codex": {"model": latest_codex_model(row["codex"]["family"]), "reasoning_effort": row["codex"]["reasoning_effort"]},
        "risky": risky,
        "unclear": unclear,
        "clarify_first": unclear is not None and unclear >= POLICY["unclear_threshold"],
        "escalate_to_user": escalate_to_user,
        "reasons": reasons,
        "probabilities": None if answers is None else {k: round(v, 2) for k, v in probabilities.items()},
        "skill_hint": None if answers is None else skill_hint,
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        tasks = payload["tasks"]
        assert isinstance(tasks, list) and tasks, "tasks must be a non-empty list"
        for task in tasks:
            assert task.get("id") and task.get("brief"), "each task needs id and brief"
    except (ValueError, KeyError, AssertionError) as error:
        print(json.dumps({"error": f"bad input: {error}"}))
        return 2

    started = time.monotonic()
    answers = error = model = None
    # Skill hints run in parallel with the tier request. They are optional: errors become null, and they get
    # only their own short deadline, so they can never change or noticeably delay the tier output.
    skill_deadline = started + POLICY["skill_hint_timeout_seconds"]
    pool = ThreadPoolExecutor(max_workers=len(tasks) + 1)
    hint_futures: dict = {}
    try:
        target = endpoint()
        skills = installed_skills()
        # Some providers cap the number of choices; the hint is optional, so it is skipped rather than cut down.
        if skills and len(skills) < PROVIDER.get("max_choices", 255):
            criteria = {name: short_description(text) or "(no description)" for name, text in sorted(skills.items())}
            criteria[SKILL_NONE] = SKILL_NONE_DESC
            hint_futures = {task["id"]: pool.submit(ask_skill_hint, target, task["brief"], criteria, skill_deadline) for task in tasks}
    except Exception:
        hint_futures = {}
    try:
        response = pool.submit(ask_jev, tasks).result()
        answers, model = response["answers"], response.get("model")
    except Exception as exc:  # any failure falls back to the default tier, never blocks the orchestrator
        error = str(exc)
    if answers is not None:  # after the tier answer, wait at most a short grace (and never past the hint deadline)
        hint_wait_until = min(skill_deadline, time.monotonic() + SKILL_HINT_GRACE_SECONDS)
        wait(list(hint_futures.values()), timeout=max(0.0, hint_wait_until - time.monotonic()))
    skill_hints = {tid: future.result() if future.done() and not future.exception() else None for tid, future in hint_futures.items()}
    pool.shutdown(wait=False)
    decisions = [decide(task, answers, error, skill_hints.get(task["id"])) for task in tasks]
    elapsed_ms = int((time.monotonic() - started) * 1000)

    output = {"source": "jev" if answers else "fallback", "provider": PROVIDER_NAME, "jev_model": model, "ms": elapsed_ms, "decisions": decisions}
    print(json.dumps(output, ensure_ascii=False, indent=2))

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cwd": os.getcwd(), "tasks": tasks, **output}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if any(not future.done() for future in hint_futures.values()):
        # A late skill-hint request must not hold the process open (Python joins pool threads at exit).
        sys.stdout.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
