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

## Decision log

Every routing decision is appended to `~/.local/state/jev/decisions.jsonl`.
Set `JEV_LOG` to write somewhere else.

## Versions

Releases are git tags `vX.Y.Z`. See [CHANGELOG.md](CHANGELOG.md).
