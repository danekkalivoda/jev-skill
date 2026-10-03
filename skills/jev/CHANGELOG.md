# Changelog

All notable changes to the jev skill. Newest first.

## Unreleased

## 1.3.0

- Add user settings in `~/.config/jev/settings.json` (or `JEV_SETTINGS`), overridden by the nearest `.jev.json` of a project. No file means the routing is unchanged.
- `quality` (-2 to 2) moves every decision: lower takes cheaper tiers more often, +1 turns off the cost-saving drops, +2 also needs 90% coverage.
- `topics` gives a subject a weight from 0 to 4 and asks Jev one extra question per task. `security` is built in; other topics need a description. A match blocks the drops (1), raises to tier 2 (2), to tier 3 when Jev gives it 20% or more (3), or always to tier 3 (4).
- Add `topics` to each decision and `settings` (with `warnings`) to the output. A bad setting is skipped with a warning.
- When a drop is blocked, the reason says so: `kept tier N (topic security)` or `(quality +1)`.

## 1.2.0

- Add `JEV_PROVIDER` to choose who serves the judgment model: `typesafe` (default, unchanged), `openrouter` (`OPENROUTER_API_KEY`), or `local` (a System One compatible server at `JEV_LOCAL_URL`, model `nimble`).
- Move the pinned model from `jev_model` to `providers` in `models.json`. A provider can override policy numbers.
- `local` sends one question per request (questions sharing a request sway each other there and big waves overflow its context), skips the low-confidence drop, waits up to 15 s per question, and gives no skill hint when more than 25 skills are installed. Its tier thresholds are still the ones calibrated on Jev.
- Add `provider` to the output and the decision log.
- An unknown provider or a missing key or URL gives the usual fallback tier with the reason.

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
