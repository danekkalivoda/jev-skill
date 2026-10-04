# Rules for agents working on this repo

- The skill lives in `skills/jev/`. `~/.agents/skills/jev` on the owner's machine links here, so edits are live at once.
- Other people use this skill. Keep changes small and backward compatible; a tier decision must never break because of a new feature.
- Every change that alters behaviour, output fields or `models.json` gets a line under `## Unreleased` in `skills/jev/CHANGELOG.md`, in the same commit.
- Release only when the owner asks, with the `release` skill (`.claude/skills/release/`). It holds the version rules and runs the checks.
- Tests: `python3 -m unittest discover tests` (offline). Add a case when you change `decide()` or the policy numbers.
- Test runs of `route.py` set `JEV_LOG` to a temp file so they do not land in the real decision log.
- Commit messages: subject and short body only, no Co-Authored-By or AI attribution.
