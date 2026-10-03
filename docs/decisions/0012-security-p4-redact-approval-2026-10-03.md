---
id: HA-0012
date: 2026-10-03
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, redaction, approvals, tirith]
verdict: "**Port two of the five upstream security commits that surfaced on 2026-10-03 and triage three, as `sec/p4-2026-10-03`; the digest is back at 0 undecided (76 landed, 101 triaged).** Ported by hand: `cf94e3eea3` (GCS `X-Goog-Signature` and Azure SAS `sig` count as URL credentials in the query-parameter redaction) and `eb8d21f482` (the seven hermes/docker/podman dangerous-command rules get a linear global-flag fragment with one parse per flag, so a long flag run no longer backtracks for minutes on the tool thread and freezes the gateway; no approval decision changes). Triaged: `45079e6330` (tirith cold-scan download fix; tirith has been installed here since 2026-04-21, fail-open on), `6c3aae398a` (at-rest modes under the `HERMES_HOME_MODE=0701` hatch; `~/.hermes` is 0700 with `home_mode: auto`), `e57fa350cb` (an approval-prompt feature in upstream's split approval modules the fork does not have). Both policy-1 gates pass. The live gateway picks the ports up at its next restart."
---

# Port the P4 fixes: signed-URL redaction and the global-flag regex

## Context

`HA-0011` cleared the 2026-09-28 estate review's two undecided upstream
security commits. The reviewer's re-run the same day fetched `reference/main`
afresh and found five more, dated 2026-09-28 to 10-01. Under
`FORK_POLICY.md` policy 1 each is ported or triaged with a reason.

| Upstream | Subject | Decision |
|---|---|---|
| `cf94e3eea3` (2026-09-28) | fix(redact): GCS and Azure SAS signatures count as URL credentials | **Ported** |
| `eb8d21f482` (2026-09-30) | fix(approval): give global-flag runs one parse in dangerous-command rules | **Ported** |
| `45079e6330` (2026-09-29) | fix(security): the first tirith scan no longer waits on its download | Triaged: not applicable to this install |
| `6c3aae398a` (2026-09-28) | fix(security): harden at-rest secrets in browser profiles, media caches, and the in-flight journal | Triaged: not applicable while `~/.hermes` is 0700 |
| `e57fa350cb` (2026-10-01) | feat(approval): a turn can hold its approval prompts open until answered | Triaged: deferred, a feature in modules the fork lacks |

## What landed

- `2149d7767b` (`cf94e3eea3` by hand): two names added to
  `_SENSITIVE_QUERY_PARAMS` in `agent/redact.py`. Upstream's test change
  targets the strict URL-credential pass for debug uploads, which the fork
  does not have (`75af6dc57c` is triaged absent), so a `redact_cdp_url` test
  in `tests/agent/test_redact.py` covers both signatures instead.
- `cba87551b2` (`eb8d21f482` by hand): `_GLOBAL_FLAGS` and
  `_CONTAINER_GLOBAL_FLAGS` added to `tools/approval.py` (the fork keeps its
  rules there, not in upstream's `tools/approval_detection.py`), and the
  seven rules rewritten with them. Possessive quantifiers need Python 3.11;
  the fork's venv is 3.11.15. Upstream's regression test is taken as
  `tests/tools/test_approval_global_flag_scan.py`, pointed at
  `tools.approval`: long flag runs still reach the target, non-matching runs
  finish, and the whitespace decisions are unchanged.
- `7b1bbaa584`: the three triage lines in `scripts/upstream_digest_triaged.txt`,
  with the facts they rest on: `~/.hermes/bin/tirith` present since
  2026-04-21 and `tirith_fail_open: true`; `~/.hermes` mode 0700 and
  `home_mode: auto`, the other local accounts (`brain`, `test`) unable to
  traverse it; no `tools/approval_*` modules in the fork.

## Verification

- `tests/agent/test_redact.py`: 303 passed.
- `tests/tools/test_approval*.py` including the new file: 518 passed.
- Policy-1 sync gate: `scripts/openbrain_conformance_smoke.py` passed;
  `tests/plugins/test_routing_classifier_plugin.py`: 19 passed.
- `scripts/upstream_digest.py --no-fetch --json`: undecided 0, landed 76,
  triaged 101.

## Consequences and follow-ups

- The live gateway runs the pre-port code until its next restart; the
  approval-regex fix is the one that matters for it (a gateway freeze on a
  long flag run). Mark's restart command is in `HA-0011`.
- `6c3aae398a` is the one to revisit if `home_mode` ever opens the home: the
  at-rest modes would then matter, and the 610-line patch would need a hand
  port.
- The reviewer's `upstream-security` finding clears on its next run.
