---
id: HA-0013
date: 2026-10-06
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, redaction, redos, upstream-digest]
verdict: "**Port `70f79c8cde` (anchor the Telegram token regex to a digit-run start, ReDoS) as `sec/p5-2026-10-06`. The digest never surfaced it**: its subject is `fix(agent): …`, and `scripts/upstream_digest.py` classifies by type and scope only, so a security fix filed under a non-security scope reads as ordinary work. On the fork, `_TELEGRAM_RE` took 3.01 s on 40k digits after a colon, quadratic, holding the GIL on every redacted surface. Afterwards it takes 0.000 s, and the full redactor takes 0.016 s on 300k. The two upstream regression tests were taken unchanged (305 passed). The same branch syncs the repo's stale plist copies with the live ones and corrects `HA-0004`, whose fact 1 described a fork-only key as upstream. The digest gap is open: the 2026-10-06 inventory counted 213 security-keyword upstream commits in `tools/` and `agent/` that are neither landed nor triaged."
---

# Port the Telegram-token ReDoS fix the digest missed

## Context

The 2026-10-06 fork-vs-upstream inventory
(`/Users/mh/ai/notes/hermes-assessment-2026-10-06/INVENTORY.md`) ran the
fork's redactor against upstream fixes the digest had not classified. One was
live: `70f79c8cde` (upstream, 2026-09-23), "fix(agent): anchor the Telegram
token regex to a digit-run start (ReDoS)".

`_TELEGRAM_RE` runs on every redacted surface that contains a colon: logs,
outbound messages and tool-output spills. A long digit run with no
`:<token>` after it was rescanned from every digit. Upstream's real trigger
was a 2 MB `gh pr diff` whose Unity `_typelessdata` line held about 1.5M
digits; it pinned a core for hours.

## What landed

| Commit | What |
|---|---|
| `62dccaae8c` | The `(?<!\d)` lookbehind in `agent/redact.py`, ported by hand because the fork lays the pattern out over three lines, plus upstream's two tests in `tests/agent/test_redact.py`. Authored as upstream's author. |
| `7139d0c8a1` | `launchd/com.mh.{hermes-health-monitor,hermes-pwa,ollama}.plist` replaced with byte copies of `bootstrap/launchd/current/`. Two still pointed at `/Users/mh/ai/agents/hermes-agent`, and ollama still had the retired `-1` pin. launchd was not touched. |
| this record | `HA-0004` correction: `model.ollama_keep_alive` is the fork's own `b58cde8d4f`, not upstream. |

## Verification

- Before and after on the fork, `_typelessdata: ` + 40k zeros: old pattern
  3.01 s, new pattern 0.000 s. Full `redact_sensitive_text(force=True)` on
  300k zeros: 0.016 s. The new 300k test fails on the old pattern by
  construction (quadratic, about 170 s against a 2 s budget).
- `tests/agent/test_redact.py`: 305 passed (303 plus 2 new).
- Policy-1 sync gate: see the FORK_POLICY sync-history entry.

## Consequences and follow-ups

- **The digest's blind spot is the real finding.** The filter reads the
  type and scope only, so `fix(agent)`, `fix(tools)`, `fix(mcp)` and similar
  security fixes are invisible. A body-aware filter, or one that looks at
  touched paths, is the follow-up. Until then, "0 undecided" means "0
  undecided among commits labelled as security".
- Today's fetch raised five new undecided commits, all dated 2026-10-05:
  `fb5bba80dc` (spoofed XFF on dashboard login limits) and four approval-
  and consent-prompt fixes (`2b525ced84`, `762f419fe8`, `ef1faa4cf8`,
  `0df1837e81`). They are left for the next triage.
- The live gateway runs the old pattern until it restarts after the merge.
