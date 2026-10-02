#!/usr/bin/env python3
"""Pick a model tier for each delegated task, using TypeSafe Jev.

Input (stdin, JSON):
  {"tasks": [{"id": "a", "brief": "English task description", "attempt": 0, "min_tier": null, "min_tier_reason": null}]}
Output (stdout, JSON): one decision per task, with the model to use in Claude Code and in Codex.

Jev only judges the task. Code owns the policy: coverage threshold, floors, retries, model table.
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "models.json").read_text(encoding="utf-8"))
POLICY = CONFIG["policy"]
TIERS = CONFIG["tiers"]
MAX_TIER = len(TIERS) - 1
LOG = Path(os.environ.get("JEV_LOG", Path.home() / ".local/state/jev/decisions.jsonl"))
API = "https://api.typesafe.ai/v1/systemone"

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


def load_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    # Agent shells often skip the interactive profile (Codex passes only core env vars), so read it directly.
    pattern = re.compile(r"""^\s*(?:export\s+)?TYPESAFE_API_KEY=["']?([^"'\s]+)""")
    for name in (".zshenv", ".zshrc", ".zprofile", ".bash_profile", ".profile"):
        path = Path.home() / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = pattern.match(line)
                if match:
                    return match.group(1)
    return None


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
    key = load_key()
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY not found in env or shell profile")
    questions: dict = {}
    for task in tasks:
        questions.update(questions_for(task))
    body = json.dumps({"state": STATE, "model": "jev-latest", "questions": questions}).encode()
    request = urllib.request.Request(
        API,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
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


def smallest_covering_tier(probabilities: dict[str, float]) -> int:
    """Cheapest tier k where P(needed tier <= k) reaches the coverage threshold."""
    cumulative = 0.0
    for tier in range(MAX_TIER + 1):
        cumulative += probabilities.get(str(tier), 0.0)
        if cumulative >= POLICY["coverage"]:
            return tier
    return MAX_TIER


def decide(task: dict, answers: dict | None, error: str | None) -> dict:
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
    try:
        response = ask_jev(tasks)
        answers, model = response["answers"], response.get("model")
    except Exception as exc:  # any failure falls back to the default tier, never blocks the orchestrator
        error = str(exc)
    decisions = [decide(task, answers, error) for task in tasks]
    elapsed_ms = int((time.monotonic() - started) * 1000)

    output = {"source": "jev" if answers else "fallback", "jev_model": model, "ms": elapsed_ms, "decisions": decisions}
    print(json.dumps(output, ensure_ascii=False, indent=2))

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cwd": os.getcwd(), "tasks": tasks, **output}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
