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

If the chosen provider is not set up or not reachable, `route.py` does not switch to another one.
It returns the fallback tier and says why.

## Decision log

Every routing decision is appended to `~/.local/state/jev/decisions.jsonl`.
Set `JEV_LOG` to write somewhere else.

## Versions

Releases are git tags `vX.Y.Z`. See [skills/jev/CHANGELOG.md](skills/jev/CHANGELOG.md).
