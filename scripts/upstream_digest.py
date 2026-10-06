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
names `security` (`fix(security): …`, `ci(security): …`) or one of the
security-gate scopes in SECURITY_SCOPES (`fix(approval): …`, `fix(redact): …`),
or when its subject or body cites a GHSA or CVE id. Until 2026-09-24 only the
`security` type counted, which reported 11 of ~50 (see
docs/decisions/0006-security-sync-2026-09-24.md); the gate scopes were added the
same day after `feat(approvals)` deny rules and the IMDS approval flag turned up
outside the report.

Type and scope still missed fixes filed under an ordinary scope:
`fix(agent): anchor the Telegram token regex … (ReDoS)` was live on the fork
for two weeks unreported (HA-0013), and 2026-10-06 counted ~110 more since the
pin. So a behaviour-changing commit (SECURITY_VOCAB_TYPES) also counts when its
subject uses security vocabulary (SECURITY_VOCAB_RE: ReDoS, TOCTOU, SSRF,
traversal, redaction, a secret or env leak, a guard bypass, …), minus the
NOT_SECURITY_RE shapes that reuse those words for something else (catalog pins,
resource leaks). Measured on main..reference/main at 2026-10-06: 8 of the 9
known misses caught, about three in four new hits genuinely security-relevant,
about 40 a month. The vocabulary is a net, not a guarantee: a subject that
names no security idea (`stop mixed platform bundles from re-exposing blocked
tools`) still gets through.

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
# Scopes naming a security gate: the command-approval gate and secret redaction.
# `auth` and `secrets` stay out — upstream uses them mostly for provider OAuth
# plumbing and the vault feature, which would bury the signal.
SECURITY_SCOPES = SECURITY_WORDS | {"approval", "approvals", "redact", "redaction", "ssrf"}
# Types whose commits change no behaviour, so a `security` scope on them is a
# test or doc about security rather than a fix (e.g. `test(security): …`).
NON_FIX_TYPES = {"refactor", "test", "tests", "docs", "doc", "style", "perf"}
# Types that change behaviour, the only ones the vocabulary applies to. `perf`
# is here although NON_FIX_TYPES excludes it from the scope rule: a ReDoS fix is
# often filed as `perf(…)`, and the vocabulary names the security idea itself.
SECURITY_VOCAB_TYPES = {"fix", "feat", "perf", "hardened", "harden", "hardening"}
_SECRET_NOUNS = (
    r"(?:secret|token|credential|api[ _-]?key|password|key material|cookie|session id"
    r"|env(?:ironment)?|HERMES_SESSION\w*)s?"
)
_GUARD_NOUNS = (
    r"(?:guard|gate|approval|allowlist|deny[- ]?(?:list|rule)|scanner|sandbox|policy"
    r"|check|filter|redact\w*|confirmation|consent|auth\w*)"
)
SECURITY_VOCAB_RE = re.compile(
    r"redos|toctou|\bssrf\b|\bxss\b|\bcsrf\b|\brce\b|remote code|code execution"
    r"|path[- ]traversal|directory traversal|traversal-shaped|exfil\w*|privilege escalation"
    r"|symlink-safe|world-readable|owner-only|\bredact\w*|unredact|spoof\w*|hijack\w*|smuggl\w*"
    rf"|\bleak\w*\W+(?:[\w-]+\W+){{0,4}}{_SECRET_NOUNS}|{_SECRET_NOUNS}\W+(?:[\w-]+\W+){{0,3}}leak"
    rf"|{_GUARD_NOUNS}\W+(?:[\w-]+\W+){{0,4}}bypass|bypass\w*\W+(?:[\w-]+\W+){{0,3}}{_GUARD_NOUNS}"
    r"|\bclose[sd]?\W+(?:[\w-]+\W+){0,5}(?:bypass|hole|loophole)",
    re.IGNORECASE,
)
# Subjects that reuse the vocabulary for something else: plugin-catalog pins of
# products named like it (bot-forge, Afterforge) and resource leaks.
NOT_SECURITY_RE = re.compile(
    r"plugin-catalog|^catalog:|bot-forge|afterforge|memory leak"
    r"|leaked (?:daemon|process|coroutine|connection|fd|file descriptor|thread|socket)",
    re.IGNORECASE,
)


def run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def commit_type(subject: str) -> str:
    match = TYPE_RE.match(subject)
    return match.group("type").lower() if match else "other"


def is_security(subject: str, body: str = "") -> bool:
    if ADVISORY_RE.search(subject):
        return True
    match = TYPE_RE.match(subject)
    if not match:
        return False
    ctype = match.group("type").lower()
    if ctype in SECURITY_WORDS:
        return True
    if ctype in NON_FIX_TYPES:
        return ctype == "perf" and _names_security(subject)
    scope = match.group("scope") or ""
    tokens = {tok.lower() for tok in re.split(r"[,/\s]+", scope) if tok}
    if tokens & SECURITY_SCOPES:
        return True
    # An advisory cited only in the body: a follow-up fix or a dependency bump
    # (`chore(deps): refresh … lockfile` naming its GHSA below the subject).
    if body and ADVISORY_RE.search(body):
        return True
    return ctype in SECURITY_VOCAB_TYPES and _names_security(subject)


def _names_security(subject: str) -> bool:
    return bool(SECURITY_VOCAB_RE.search(subject)) and not NOT_SECURITY_RE.search(subject)


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


# The body sits between \x1f and \x1e so its own newlines cannot be mistaken
# for the --name-only file list that follows it.
LOG_FORMAT = "%x00%H%x09%cs%x09%s%x1f%b%x1e"


def parse_log(log: str) -> list[tuple[str, str, str, str, list[str]]]:
    """Parse `git log --name-only --pretty=format:<LOG_FORMAT>` output into
    (sha, commit date, subject, body, files)."""
    commits = []
    for record in log.split("\x00"):
        if not record.strip("\n"):
            continue
        head, _, files_part = record.partition("\x1e")
        first, _, body = head.partition("\x1f")
        sha, _, rest = first.strip("\n").partition("\t")
        day, _, subject = rest.partition("\t")
        files = [line for line in files_part.splitlines() if line.strip()]
        commits.append((sha, day, subject, body.strip(), files))
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

    for sha, day, subject, body, files in commits:
        if is_security(subject, body):
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
