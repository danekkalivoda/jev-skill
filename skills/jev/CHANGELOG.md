# Changelog

All notable changes to the jev skill. Newest first.

## Unreleased

## 1.1.1

- Move the changelog into the skill folder so installed copies include it.
- Add `AGENTS.md` with the rules for logging changes and releasing.

## 1.1.0

- Add `skill_hint` per task: a separate Jev Choice request runs in parallel and names an installed skill when confidence is at least 0.7.
- User-only skills are never suggested.
- The skill hint never blocks the tier decision.
- Pin the Jev model to `jev-1.13.0` in `models.json`.

## 1.0.0

- Initial release.
- Tier routing: Jev judges each task, code picks the cheapest model tier.
- Guards for risky and unclear tasks raise the tier.
