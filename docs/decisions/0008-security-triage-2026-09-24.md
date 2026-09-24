---
id: HA-0008
date: 2026-09-24
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, triage, redaction, approvals]
verdict: "Triage of the 85 approval/redaction upstream commits the widened digest surfaced: 51 not taken (recorded with reasons in scripts/upstream_digest_triaged.txt), 34 to port in batches. P1 is a live redactor ReDoS (5 KB of text = 21 s stall on every log line and outbound message), four confirmed secret-leak shapes including MCP_ACCESS_KEY and the x-brain-key line, and detection gaps where `curl … | zsh` and whitespace/env -S/quoted-verb variants of the new approvals.deny globs run unprompted. Four gaps are local fixes that upstream lacks too: a Telegram/PWA approval tap resolves the session's oldest pending prompt, not the one tapped; x-brain-key is unmasked; approvals.deny misses quoted verbs; LaunchAgents plists are writable without a prompt. Nothing was ported in this record."
---

# Triage the 85 approval/redaction commits

## Context

[`HA-0007`](0007-approval-gate-sync-2026-09-24.md) widened
`scripts/upstream_digest.py` to count the `approval(s)`, `redact(ion)` and
`ssrf` gate scopes as security. That moved 85 upstream commits from invisible
to needs-a-decision.

## Decision

Every commit was read against HEAD. Where possible, upstream's own test input
was run through HEAD's functions (fake secrets only), and the headline claims
were then re-run by hand against the live config. **51 are not taken**, each
with a reason in `scripts/upstream_digest_triaged.txt`: smart approvals (unused
in manual mode), code absent from the fork, false-positive-only fixes,
unused features and refactor follow-ups. **34 are to port.** They stay listed
by the digest until they land. Evidence, batches, order and the config-only
mitigations are in
[`docs/cleanup/2026-09-24-security-triage.md`](../cleanup/2026-09-24-security-triage.md).

## Consequences

- The digest's Security section now lists exactly the 34 to port.
- The P1 batches (ReDoS, redaction leaks, detection, tap-to-prompt binding)
  are the next security sync. P2 can follow or be re-triaged.
- The deny-glob and plist mitigations are config changes to the live
  `~/.hermes/config.yaml` and need Mark's go. Until they, or the deny-bundle
  port, land, the ten `approvals.deny` rules from HA-0007 can be bypassed
  with a doubled space.
