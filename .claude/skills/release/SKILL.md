---
name: release
description: Release a new version of the jev skill (changelog, tag, push, GitHub release). Use only when the owner asks to release or publish a version.
disable-model-invocation: true
---

# Release the jev skill

1. Make sure every change is committed and listed under `## Unreleased` in `skills/jev/CHANGELOG.md`.
   If a behaviour change is missing there, add the line and commit it first.
2. Pick the version from the last tag (`git describe --tags --abbrev=0`):
   - patch (1.1.0 -> 1.1.1): fixes and docs,
   - minor (1.1.0 -> 1.2.0): a new output field or option,
   - major (1.1.0 -> 2.0.0): changed tiers or removed fields.
   If the owner did not name the version, say which one you picked and why in one line.
3. Dry run, read the notes it prints:
   `python3 .claude/skills/release/release.py X.Y.Z --dry-run`
4. Release:
   `python3 .claude/skills/release/release.py X.Y.Z`
   It runs a route.py smoke test, turns `## Unreleased` into `## X.Y.Z`, commits `Release vX.Y.Z`,
   creates the annotated tag, pushes `main` and the tag, and publishes the GitHub release with those notes.
5. Report the release URL. Colleagues update with `npx skills update`.

If the script stops, fix the reason it prints and run it again. Never add Co-Authored-By or AI attribution to commits.
