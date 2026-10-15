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

A project can have its own `.jev.json` in its root. Its values win over the user file; topics are merged.
Set `JEV_SETTINGS` to read the user settings from another path.
A bad file or value is skipped and reported in the output field `settings.warnings`; routing goes on.

## Decision log

Every routing decision is appended to `~/.local/state/jev/decisions.jsonl`.
Set `JEV_LOG` to write somewhere else.
Each line is one call: `ts`, `cwd`, `tasks` (the input), then the output fields (`source`, `provider`, `jev_model`, `ms`, `settings`, `decisions`). `provider` came in 1.2.0, `settings` in 1.3.0; older lines lack them.

## Versions

Releases are git tags `vX.Y.Z`. See [skills/jev/CHANGELOG.md](skills/jev/CHANGELOG.md).
