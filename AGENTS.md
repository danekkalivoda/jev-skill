# Rules for agents working on this repo

- The skill lives in `skills/jev/`. `~/.agents/skills/jev` on the owner's machine links here, so edits are live at once.
- Other people use this skill. Keep changes small and backward compatible; a tier decision must never break because of a new feature.
- Every change that alters behaviour, output fields or `models.json` gets a line under `## Unreleased` in `skills/jev/CHANGELOG.md`, in the same commit.
- Release only when the owner asks: rename `## Unreleased` to `## X.Y.Z` (patch = fixes and docs, minor = new output field or option, major = changed tiers or removed fields), add a fresh empty `## Unreleased`, commit, create an annotated tag `vX.Y.Z`, push `main` and the tag, then publish a GitHub release with that version's changelog section as notes (`gh release create vX.Y.Z --title vX.Y.Z --notes-file <section> --verify-tag`).
- Test runs of `route.py` set `JEV_LOG` to a temp file so they do not land in the real decision log.
- Commit messages: subject and short body only, no Co-Authored-By or AI attribution.
