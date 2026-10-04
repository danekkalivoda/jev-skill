#!/usr/bin/env python3
"""Release the jev skill: changelog section, commit, tag, push, GitHub release.

Usage: release.py X.Y.Z [--dry-run]
Run from anywhere; it works on the repo two levels above this skill folder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CHANGELOG = REPO / "skills/jev/CHANGELOG.md"
SMOKE_INPUT = json.dumps({"tasks": [
    {"id": "smoke-rename", "brief": "Rename the function get_tools to list_tools in app/tools/registry.py and update its callers."},
]})


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=REPO, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def fail(message: str) -> None:
    print(f"release: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    if len(args) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", args[0]):
        fail("usage: release.py X.Y.Z [--dry-run]")
    version = args[0]
    tag = f"v{version}"

    if run("git", "branch", "--show-current", capture=True) != "main":
        fail("not on main")
    if run("git", "status", "--porcelain", capture=True):
        fail("working tree is not clean; commit the changes first")
    run("git", "fetch", "--quiet", "origin", "main", "--tags")
    if run("git", "rev-list", "--count", "HEAD..origin/main", capture=True) != "0":
        fail("origin/main has commits you do not have; pull first")
    if run("git", "tag", "--list", tag, capture=True):
        fail(f"tag {tag} already exists")

    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## Unreleased\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not match or not match.group(1).strip():
        fail("the Unreleased section in the changelog is empty")
    notes = match.group(1).strip() + "\n"

    if subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=REPO).returncode:
        fail("tests failed")

    with tempfile.NamedTemporaryFile(suffix=".jsonl") as log:
        smoke = subprocess.run(
            [sys.executable, str(REPO / "skills/jev/route.py")], input=SMOKE_INPUT, text=True,
            capture_output=True, env={**os.environ, "JEV_LOG": log.name},
        )
    try:
        decision = json.loads(smoke.stdout)["decisions"][0]
        assert "tier" in decision and "skill_hint" in decision
    except (ValueError, KeyError, IndexError, AssertionError):
        fail(f"route.py smoke test failed:\n{smoke.stdout}{smoke.stderr}")
    print(f"smoke test: source {json.loads(smoke.stdout)['source']}, tier {decision['tier']}")

    print(f"release {tag} with notes:\n{notes}")
    if dry_run:
        print("dry run: nothing changed")
        return

    updated = text[:match.start()] + f"## Unreleased\n\n## {version}\n\n{notes}\n" + text[match.end():]
    CHANGELOG.write_text(updated, encoding="utf-8")
    run("git", "add", str(CHANGELOG))
    run("git", "commit", "--quiet", "-m", f"Release {tag}")
    run("git", "tag", "-a", tag, "-m", tag)
    run("git", "push", "--quiet", "origin", "main", tag)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as notes_file:
        notes_file.write(notes)
    run("gh", "release", "create", tag, "--title", tag, "--notes-file", notes_file.name, "--verify-tag", "--latest")
    os.unlink(notes_file.name)
    print(run("gh", "release", "view", tag, "--json", "url", "--jq", ".url", capture=True))


if __name__ == "__main__":
    main()
