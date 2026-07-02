#!/usr/bin/env python3
"""Triage report for what's new on the `reference` (upstream) remote.

Upstream (NousResearch/hermes-agent) moves fast enough that a full commit-by-
commit review at sync time is not tractable — see docs/hermes-update-runbook.md.
This script turns "what changed upstream" into a short, recurring digest
instead: it always surfaces security(...) commits and commits touching our
load-bearing local-delta files (LOCAL_DELTA_PATHS in hermes_update_guard.py,
i.e. files a merge could silently regress), and bulk-summarizes everything
else by conventional-commit type.

Run this on a schedule (e.g. via Hermes) between syncs, not as a substitute
for the sync itself.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
from hermes_update_guard import LOCAL_DELTA_PATHS  # noqa: E402

TYPE_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(\([^)]*\))?[:!]")


def run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def commit_type(subject: str) -> str:
    match = TYPE_RE.match(subject)
    return match.group("type").lower() if match else "other"


def touched_files(sha: str) -> list[str]:
    out = run("show", "--name-only", "--pretty=format:", sha)
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="reference", help="upstream remote name")
    parser.add_argument("--branch", default="main", help="upstream branch name")
    parser.add_argument(
        "--local-branch", default="main", help="local branch to diff against"
    )
    parser.add_argument(
        "--no-fetch", action="store_true", help="skip `git fetch` before diffing"
    )
    args = parser.parse_args()

    if not args.no_fetch:
        run("fetch", args.remote)

    upstream_ref = f"{args.remote}/{args.branch}"
    range_spec = f"{args.local_branch}..{upstream_ref}"
    log = run("log", "--pretty=format:%H\t%s", range_spec)
    commits = [line.split("\t", 1) for line in log.splitlines() if line.strip()]

    if not commits:
        print(f"No new commits on {upstream_ref} since {args.local_branch}.")
        return 0

    delta_prefixes = tuple(LOCAL_DELTA_PATHS)
    security: list[tuple[str, str]] = []
    delta_risk: list[tuple[str, str, list[str]]] = []
    type_counts: Counter[str] = Counter()

    for sha, subject in commits:
        ctype = commit_type(subject)
        if ctype == "security":
            security.append((sha, subject))
            continue
        files = touched_files(sha)
        hits = [f for f in files if f.startswith(delta_prefixes)]
        if hits:
            delta_risk.append((sha, subject, hits))
            continue
        type_counts[ctype] += 1

    print(f"# Upstream digest: {upstream_ref} vs {args.local_branch}")
    print(f"{len(commits)} new commit(s).\n")

    print(f"## Security ({len(security)})")
    if security:
        for sha, subject in security:
            print(f"- {sha[:9]} {subject}")
    else:
        print("- none")
    print()

    print(f"## Touches local-delta files ({len(delta_risk)})")
    if delta_risk:
        for sha, subject, hits in delta_risk:
            print(f"- {sha[:9]} {subject}")
            for hit in hits:
                print(f"    {hit}")
    else:
        print("- none")
    print()

    print("## Everything else, by type")
    other_total = len(commits) - len(security) - len(delta_risk)
    for ctype, count in type_counts.most_common():
        print(f"- {ctype}: {count}")
    print(f"\n{other_total} commit(s) need no special attention before the next sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
