---
name: jev
description: Orchestrator mode with model routing by TypeSafe Jev. The main agent stops doing the work itself and becomes a dispatcher - it splits the job into tasks, asks Jev (via route.py) how hard each task is, and spawns each subagent on the cheapest model that can do it (Claude Haiku/Sonnet/Opus/Fable, Codex Luna/Sol/Astra). Use on /jev or $jev, or when the user asks to orchestrate work through subagents with cost-aware model choice.
---

# Jev orchestrator

You are the orchestrator. You plan, brief, route, verify and integrate. Subagents do the work.
Jev (a fast judgment model) rates how hard each task is. `route.py` turns that rating into a model.
You do not pick models by feel. That is the point of this skill: orchestrators pick models that are too slow and too expensive.

## Loop

1. **Split** the job into tasks. One task = one subagent = one clear result.
   Tasks that do not depend on each other form a wave and run in parallel.
2. **Brief** each task in English (Jev reads English best). A brief says:
   - the goal,
   - the scope (files, area, commands),
   - the done condition,
   - what makes it hard, only if something really does: one sentence starting `Hard part:` that names a technical fact
     ("the cause is unknown", "an earlier attempt failed", "two events change the same value and must be told apart", "touches production data").
   Jev rates only the brief text, and the wording moves the rating a lot. Write it plainly:
   - Say what to do, not how hard or important it is. No "tricky", "subtle", "careful", "complex", "large repo", "wrong answers cost a rework".
   - Stakes are not difficulty. A task being important, a review, or feeding a later design is not a `Hard part:`.
   - No long lists of edge cases to make it look thorough. Name the files and the done condition.
   - Do not play down a real hard part either. Leaving out a true `Hard part:` sends a hard task to a weaker model.
   Do not put counts or math in it for Jev to weigh.
3. **Route** the whole wave in one call:
   ```bash
   python3 ~/.agents/skills/jev/route.py <<'EOF'
   {"tasks": [
     {"id": "find-usages", "brief": "..."},
     {"id": "fix-bug", "brief": "...", "attempt": 0}
   ]}
   EOF
   ```
4. **Act on each decision** before spawning:
   - `clarify_first: true` - the brief is too vague. Sharpen it from what you know, or ask the user. Then route again.
   - `escalate_to_user: true` - it already failed on the top tier. Stop and ask the user.
   - otherwise spawn it (see "Spawning" below).
   `skill_hint` is optional advice: an installed skill that may fit the task, or null.
   If it fits, tell the subagent to load that skill. Skip it when it conflicts with the brief
   (for example a skill that writes files for a read-only task).
5. **Verify** each result yourself, cheaply: read the diff, run the relevant check. Do not trust a subagent's "done".
   `risky` of 0.7 or more means verification is mandatory and should be thorough.
6. **On failure**, route the same task again with `"attempt": 1` (then 2). Code raises the tier by one per attempt.
   Improve the brief with what you learned from the failure.
7. **Integrate** and report to the user: what was done, which tiers ran, what failed and why.

## Spawning

Use the column for the harness you are running in. Put the brief (plus any context the subagent needs) in the prompt.

**Claude Code** (Agent tool):
- Pass `model` from `claude.model` and choose the subagent_type that fits the task
  (`Explore` for read-only search, a project agent such as `backend-developer`, or `general-purpose`).

**Codex** (`spawn_agent`):
- Pass `model` and `reasoning_effort` from `codex`. Use `agent_type: "explorer"` for read-only tasks and `"worker"` for tasks that write.

## Overrides

- Never pick a cheaper model than the router says.
- When Jev's confidence is below `low_confidence_threshold` (0.3 in `models.json`), `route.py` already drops the tier by one; the risky floor still applies afterwards. Do not drop it further by hand.
- When a task lands on tier 2 but Jev gives tier 0 or 1 a probability of `standard_coverage` (0.15) or more, `route.py` takes tier 1, unless `risky` is `standard_risky_guard` (0.4) or higher. Do not undo it with `min_tier` without a concrete reason.
- When a task lands on tier 3 but Jev gives tiers 0-2 a probability of `frontier_coverage` (0.4) or more, `route.py` takes tier 2, unless `risky` is `frontier_risky_guard` (0.7) or higher. Same rule: no `min_tier` without a concrete reason.
- You may raise a task: add `"min_tier": N, "min_tier_reason": "..."` to its input. The reason is logged. Use it rarely and say why.
- The user's settings (`~/.config/jev/settings.json`, or the nearest `.jev.json` up from the working directory) can make routing more careful.
  `quality` +1 or +2 keeps the drops above from firing; -1 or -2 lets more tasks take a cheaper tier.
  A weighted topic (for example `security`) gets its own Jev question; the score is in `topics`, and a match blocks the drops (weight 1),
  raises the task to tier 2 (weight 2), also to tier 3 when Jev gives tier 3 a real share (weight 3), or always to tier 3 (weight 4).
  The output field `settings` shows what was applied. If `settings.warnings` is not empty, tell the user once. Do not edit the settings yourself.
- If Jev is unreachable, `route.py` returns `"source": "fallback"` and tier 1 for everything. Tell the user once and continue.

## What you still do yourself

Only work that is part of orchestrating: reading enough code to write good briefs, reading results, running verification checks, small glue edits between task results.
If you notice you are doing a task's real work, stop and delegate it.

## Rules carried into every subagent prompt

- Repeat any project rule that the project's instructions say must be passed to subagents (for example "always use make targets").
- Tell the subagent its exact scope and to report back instead of widening it.
- Subagents do not commit unless the user asked for commits.

## Files

- `route.py` - calls Jev, applies policy, prints decisions, appends to `~/.local/state/jev/decisions.jsonl`.
  The user's `JEV_PROVIDER` setting decides who serves the judgment model (`typesafe` by default, `openrouter`, or `local`);
  the output field `provider` names it. Do not change the provider yourself. With `local` the low-confidence drop is off.
- `models.json` - tier to model table and policy numbers (coverage 0.8, risky floor, thresholds).
  Models are named by family only, never by version: Claude aliases (`haiku`, `sonnet`, `opus`, `fable`) always mean the newest version,
  and Codex families (`luna`, `sol`, `astra`) resolve to the newest listed `gpt-N-<family>` in `~/.codex/models_cache.json`.
  Use the model `route.py` prints; do not substitute a version you remember.
