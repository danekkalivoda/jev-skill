# jev skill

An agent skill that turns the main agent into an orchestrator. It splits a job
into tasks and hands each task to a subagent. Before each handoff it asks
[TypeSafe Jev](https://typesafe.ai) how hard the task is, then picks the
cheapest model that can do it (Claude Haiku/Sonnet/Opus/Fable or Codex
Luna/Sol/Astra). It can also suggest an installed skill the subagent should load.

## Install

```sh
npx skills add danekkalivoda/jev-skill
```

## Update

```sh
npx skills update
```

## Setup

Set your TypeSafe API key in your shell profile (for example `~/.zshenv`):

```sh
export TYPESAFE_API_KEY=...
```

### Other providers

TypeSafe is the default. Set `JEV_PROVIDER` in the same shell profile to use another one:

| `JEV_PROVIDER` | Also set | Model |
| --- | --- | --- |
| `typesafe` (default) | `TYPESAFE_API_KEY` | Jev |
| `openrouter` | `OPENROUTER_API_KEY` | Jev, billed to your OpenRouter account |
| `local` | `JEV_LOCAL_URL` | [nimble](https://ollama.com/library/nimble) on your own machine |

`JEV_LOCAL_URL` is the full address of a server that speaks the same System One API as Jev,
for example `http://localhost:8080/v1/systemone`. No key is sent.

The tier thresholds in `models.json` were calibrated on Jev. With `local` they are not calibrated yet,
so treat its tiers as experimental. Skill hints are off for `local` when more than 25 skills are installed.
`local` is also slower: it rates one question at a time, about 3 seconds per task on an M1 Max.

`local` has a safety net. Before the first question `route.py` checks that the server answers (about 1.5 seconds at most),
and each question gets 8 seconds. If local is down or fails and `TYPESAFE_API_KEY` is set, the whole wave goes to TypeSafe instead.
The output and the log then say `"provider": "typesafe"`, and each decision's `reasons` says local was skipped.
The numbers live in the `local` entry of `models.json` (`fallback_provider`, `connect_timeout_seconds`, `policy`).
If there is no other provider to use, `route.py` returns the fallback tier and says why.

### Task fields

Each task has `id` and `brief`. These are optional:

| Field | Meaning |
| --- | --- |
| `attempt` | Number of failed tries so far. Each one adds a tier. |
| `prev_tier` | 0 to 3. The tier that just failed. With `attempt` above 0 the retry goes to at least `prev_tier + 1` instead of adding `attempt` to a fresh rating. Ask the user only when `prev_tier` is 3. |
| `min_tier`, `min_tier_reason` | A floor set by the orchestrator. |
| `max_tier`, `max_tier_reason` | A cap, for when the user asks for a cheaper model. Applied last, and it wins over `min_tier`. The reason goes into `reasons`. |
| `kind` | What the task is. Any word of it that is a listed kind counts (`Review `, `code-review`). `review`, `check`, `critic` or `advisor` stay on Claude when agent settings send work to Codex (see [Agents](#agents-claude-or-codex)). |
| `read_only` | `true` when the task changes no files. Can use its own agent settings. |
| `agent`, `agent_reason` | `claude` or `codex`: the user asked for this agent for this task. Wins over the settings. |

## Settings

By default the router picks the cheapest model it trusts. To make it more careful, or cheaper,
create `~/.config/jev/settings.json`:

```json
{
  "quality": 0,
  "topics": { "security": 3 }
}
```

`quality` moves all decisions, from -2 to 2:

| `quality` | Effect |
| --- | --- |
| `-2` | cheapest: a tier is enough when Jev gives it 70% (default 80%) |
| `-1` | cheaper: 75% is enough |
| `0` | default |
| `1` | no cost-saving drops to a lower tier |
| `2` | no drops, and a tier must reach 90% |

`topics` gives a subject its own weight, from 0 to 4. Jev gets one extra question per task and topic
("does this task involve security?"). When it says yes (0.6 or more):

| Weight | Effect |
| --- | --- |
| `0` | off |
| `1` | no cost-saving drops to a lower tier |
| `2` | at least the hard tier (Opus, Sol high) |
| `3` | at least the hard tier, and the frontier tier (Fable, Astra) when Jev gives it 20% or more |
| `4` | always the frontier tier |

`security` is built in. Any other topic needs a short description:

```json
{ "topics": { "money": { "weight": 2, "description": "payments, invoices, prices or other handling of money" } } }
```

### Agents: Claude or Codex

By default every task goes to Claude. The `agents` section sends tasks to Codex (your ChatGPT subscription) instead.
It only picks who runs the task. The tier is chosen first and never changes because of it.

```json
{
  "agents": {
    "default": "claude",
    "tiers": {
      "0": "codex",
      "1": { "agent": "codex", "model": "sol", "effort": "medium" },
      "2": "claude",
      "3": "claude"
    },
    "read_only": { "tiers": { "2": "codex" } },
    "claude_only": { "topics": ["security", "visual"], "kinds": ["review", "check", "critic", "advisor"] },
    "fallback": true
  }
}
```

| Key | Meaning |
| --- | --- |
| `default` | Agent for a tier not listed in `tiers`. Default `claude`. |
| `tiers` | Per tier `"0"` to `"3"`: an agent name, or an object with `agent` and optional `model` and `effort`. A Claude `model` is `haiku`, `sonnet`, `opus` or `fable` (full `claude-...` names are refused with a warning, because Claude Code's Agent tool takes only these aliases). A Codex `model` is a family (`luna`, `sol`, `astra`, `terra`, resolved to the newest listed one) or a full `gpt-...` name. Without `model` or `effort` the tier's own model from `models.json` is used. Efforts: Claude `low` to `max`, Codex `low` to `ultra`. |
| `read_only` | Same `tiers`, used instead for tasks with `"read_only": true`. Tiers not listed there use the main `tiers`. |
| `claude_only.topics` | Topics (they must have a weight above 0 in `topics`) that always stay on Claude when Jev's score reaches the topic threshold. If Jev is down, topic scores are unknown and the task stays on Claude. Default none. |
| `claude_only.kinds` | Task kinds that always stay on Claude. Default `review`, `check`, `critic`, `advisor`. `[]` turns it off. |
| `fallback` | `true` (default): each decision names an `alternate` agent for the case where the chosen one hits its subscription limit. `false`: no alternate. |

A task that hits an exception has `alternate: null`, whichever agent the tier names, so a limit on Claude never moves it to Codex. A topic with no score (Jev down or no answer) counts as a match. An explicit task `agent` beats the exceptions; overriding one adds a warning, and so does an `agent` that is not `claude` or `codex`.
The same keys in a project `.jev.json` win over the user file: `tiers` merge tier by tier, lists and `fallback` are replaced.

Each decision then has `agent` (`claude` or `codex`), `model`, `effort` (null when not set), and `alternate`
(`{"agent", "model", "effort"}` of the other agent, or null when `fallback` is off, an exception applies, or the task named its agent).
`claude` and `codex` stay as before. `reasons` says why the agent was chosen, and the decision log keeps it for comparing the two.
Without an `agents` section: `agent` is `claude`, `alternate` is null, and nothing else changes.

A project can have its own `.jev.json` in its root. Its values win over the user file; topics are merged.
Set `JEV_SETTINGS` to read the user settings from another path.
A bad file or value is skipped and reported in the output field `settings.warnings`; routing goes on.

## Decision log

Every routing decision is appended to `~/.local/state/jev/decisions.jsonl`.
Set `JEV_LOG` to write somewhere else.
Each line is one call: `ts`, `cwd`, `tasks` (the input), then the output fields (`source`, `provider`, `jev_model`, `ms`, `settings`, `decisions`). `provider` came in 1.2.0, `settings` in 1.3.0; older lines lack them.
Since 1.6.0 each decision has `agent`, `model`, `effort` and `alternate`; with agent settings, `reasons` holds an `agent ...` line saying why.

## Versions

Releases are git tags `vX.Y.Z`. See [skills/jev/CHANGELOG.md](skills/jev/CHANGELOG.md).
