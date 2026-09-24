#!/usr/bin/env python3
"""Triage report for what's new on the `reference` (upstream) remote.

Upstream (NousResearch/hermes-agent) moves fast enough that a full commit-by-
commit review at sync time is not tractable — see docs/hermes-update-runbook.md.
This script turns "what changed upstream" into a short, recurring digest
instead: it always surfaces security commits and commits touching our
load-bearing local-delta files (LOCAL_DELTA_PATHS in hermes_update_guard.py,
i.e. files a merge could silently regress), and bulk-summarizes everything
else by conventional-commit type.

A commit counts as security when its type is `security`/`sec`, when its scope
names `security` (`fix(security): …`, `ci(security): …`), or when its subject
cites a GHSA or CVE id. Until 2026-09-24 only the `security` type counted, which
reported 11 of ~50 (see docs/decisions/0006-security-sync-2026-09-24.md).

Security commits are dropped from the report once handled:
- landed: one of our own commits carries a `(cherry picked from commit <sha>)`
  or `Ported from upstream <sha>` trailer naming it, and
- triaged: it is listed in scripts/upstream_digest_triaged.txt as deliberately
  not taken (not applicable / out of scope), with the reason.
So the Security section lists only what still needs a decision.

Run this on a schedule (e.g. via Hermes) between syncs, not as a substitute
for the sync itself.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TRIAGED_FILE = SCRIPT_DIR / "upstream_digest_triaged.txt"

sys.path.insert(0, str(SCRIPT_DIR))
from hermes_update_guard import LOCAL_DELTA_PATHS  # noqa: E402

TYPE_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?!?:")
ADVISORY_RE = re.compile(r"\b(?:GHSA(?:-[0-9a-z]{4}){3}|CVE-\d{4}-\d{4,})\b", re.IGNORECASE)
LANDED_RE = re.compile(
    r"(?:cherry picked from commit|Ported from upstream)\s+([0-9a-f]{7,40})", re.IGNORECASE
)
SECURITY_WORDS = {"security", "sec"}
# Types whose commits change no behaviour, so a `security` scope on them is a
# test or doc about security rather than a fix (e.g. `test(security): …`).
NON_FIX_TYPES = {"refactor", "test", "tests", "docs", "doc", "style", "perf"}


def run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def commit_type(subject: str) -> str:
    match = TYPE_RE.match(subject)
    return match.group("type").lower() if match else "other"


def is_security(subject: str) -> bool:
    if ADVISORY_RE.search(subject):
        return True
    match = TYPE_RE.match(subject)
    if not match:
        return False
    ctype = match.group("type").lower()
    if ctype in SECURITY_WORDS:
        return True
    if ctype in NON_FIX_TYPES:
        return False
    scope = match.group("scope") or ""
    tokens = {tok.lower() for tok in re.split(r"[,/\s]+", scope) if tok}
    return bool(tokens & SECURITY_WORDS)


def landed_shas(bodies: str) -> set[str]:
    """Upstream SHAs our own commits say they cherry-picked or ported."""
    return {m.group(1).lower() for m in LANDED_RE.finditer(bodies)}


def load_triaged(path: Path = TRIAGED_FILE) -> dict[str, str]:
    """`<sha> <reason>` per line; blank lines and `#` comments ignored."""
    triaged: dict[str, str] = {}
    if not path.exists():
        return triaged
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        sha, _, reason = line.partition(" ")
        triaged[sha.lower()] = reason.strip()
    return triaged


def matches_any(sha: str, prefixes: set[str] | dict[str, str]) -> bool:
    return any(sha.startswith(p) or p.startswith(sha) for p in prefixes)


LOG_FORMAT = "%x00%H%x09%cs%x09%s"


def parse_log(log: str) -> list[tuple[str, str, str, list[str]]]:
    """Parse `git log --name-only --pretty=format:<LOG_FORMAT>` output into
    (sha, commit date, subject, files)."""
    commits = []
    for record in log.split("\x00"):
        lines = record.strip("\n").splitlines()
        if not lines:
            continue
        sha, _, rest = lines[0].partition("\t")
        day, _, subject = rest.partition("\t")
        files = [line for line in lines[1:] if line.strip()]
        commits.append((sha, day, subject, files))
    return commits


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the report as JSON (used by the estate reviewer)",
    )
    args = parser.parse_args()

    if not args.no_fetch:
        run("fetch", args.remote)

    upstream_ref = f"{args.remote}/{args.branch}"
    range_spec = f"{args.local_branch}..{upstream_ref}"
    # One git call for every commit's files: a `git show` per commit took
    # minutes once upstream was tens of thousands of commits ahead.
    commits = parse_log(run("log", "--name-only", f"--pretty=format:{LOG_FORMAT}", range_spec))

    if not commits and not args.json:
        print(f"No new commits on {upstream_ref} since {args.local_branch}.")
        return 0

    # Only our own commits (not upstream's history merged into ours) can say
    # they landed an upstream commit.
    landed = landed_shas(run("log", "--format=%B", f"{upstream_ref}..{args.local_branch}"))
    triaged = load_triaged()

    delta_prefixes = tuple(LOCAL_DELTA_PATHS)
    security: list[tuple[str, str, str]] = []
    landed_count = 0
    triaged_count = 0
    delta_risk: list[tuple[str, str, list[str]]] = []
    type_counts: Counter[str] = Counter()

    for sha, day, subject, files in commits:
        if is_security(subject):
            if matches_any(sha, landed):
                landed_count += 1
            elif matches_any(sha, triaged):
                triaged_count += 1
            else:
                security.append((sha, day, subject))
            continue
        hits = [f for f in files if f.startswith(delta_prefixes)]
        if hits:
            delta_risk.append((sha, subject, hits))
            continue
        type_counts[commit_type(subject)] += 1

    handled = landed_count + triaged_count
    other_total = len(commits) - len(security) - handled - len(delta_risk)
    if args.json:
        print(json.dumps({
            "upstream_ref": upstream_ref,
            "local_branch": args.local_branch,
            "new_commits": len(commits),
            "security": [
                {"sha": sha, "date": day, "subject": subject}
                for sha, day, subject in security
            ],
            "security_landed": landed_count,
            "security_triaged": triaged_count,
            "touches_local_delta": len(delta_risk),
            "other": other_total,
        }, indent=2))
        return 0

    print(f"# Upstream digest: {upstream_ref} vs {args.local_branch}")
    print(f"{len(commits)} new commit(s).\n")

    print(f"## Security — needs a decision ({len(security)})")
    if security:
        for sha, day, subject in security:
            print(f"- {sha[:10]} {day} {subject}")
    else:
        print("- none")
    print(
        f"({handled} more already handled: {landed_count} landed or ported, "
        f"{triaged_count} triaged as not taken in {TRIAGED_FILE.name})\n"
    )

    print(f"## Touches local-delta files ({len(delta_risk)})")
    if delta_risk:
        for sha, subject, hits in delta_risk:
            print(f"- {sha[:10]} {subject}")
            for hit in hits:
                print(f"    {hit}")
    else:
        print("- none")
    print()

    print("## Everything else, by type")
    for ctype, count in type_counts.most_common():
        print(f"- {ctype}: {count}")
    print(f"\n{other_total} commit(s) need no special attention before the next sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
