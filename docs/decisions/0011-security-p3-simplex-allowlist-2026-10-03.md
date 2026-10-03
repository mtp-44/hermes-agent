---
id: HA-0011
date: 2026-10-03
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, authz, simplex]
verdict: "**Port upstream `4670467534` (SimpleX allowlist: match the numeric contactId only, never the mutable display name) as `sec/p3-2026-10-03`, one cherry-picked fix adapted by hand; triage upstream `df1b647b42` (a spurious tirith first-download CLI warning) as cosmetic, not a vulnerability. The upstream digest is back at 0 undecided (74 landed, 98 triaged).** The fork's `gateway/authz_mixin.py` still matched `SIMPLEX_ALLOWED_USERS` against `source.user_name`, which any SimpleX contact can set to collide with an allowed contact's display name (upstream #44729); SimpleX is not enabled in this deployment, so nothing was exposed, and the door is now closed if it ever is. Both policy-1 gates pass: the Open Brain conformance smoke and the routing-classifier plugin tests. The live gateway keeps the old code until its next restart (Mark's action, see below); the change touches authorization only for the `simplex` platform value. Clears the `upstream-security` finding of the 2026-09-28 estate review."
---

# Port the P3 security fix: SimpleX allowlist by contactId only

## Context

The estate reviewer's run of 2026-09-28 (mini-bootstrap PR #5) reported two
upstream security commits on `reference/main` undecided since 2026-09-24.
Under `FORK_POLICY.md` policy 1, security fixes are the one standing reason
to pull from upstream; each is either ported or listed with a reason in
`scripts/upstream_digest_triaged.txt`.

| Upstream | Subject | Decision |
|---|---|---|
| `4670467534` (2026-09-27) | fix(security): remove mutable display-name from SimpleX allowlist check | **Ported** |
| `df1b647b42` (2026-09-24) | fix(security): don't warn while tirith's first download runs | **Triaged**: cosmetic |

## What landed

`be3dae94ef`, `git cherry-pick -x 4670467534`, resolved by hand because the
fork's authorization code differs from upstream's:

- `gateway/authz_mixin.py`: the block that added `source.user_name` to the
  checked ids for the `simplex` platform is removed; the comment states why.
  Upstream's version moved this logic into a module-level
  `_principal_matches_allowlist`; the fork keeps its method, so upstream's
  new function was not taken.
- `plugins/platforms/simplex/adapter.py`: the module docstring and the setup
  prompt no longer say display names are accepted. The fork has no
  `_SETUP_PROMPTS` tuple, so the prompt text was changed where the fork
  defines it (`interactive_setup`).
- `tests/gateway/test_unauthorized_dm_behavior.py`: upstream's tests merged
  cleanly: a display name in the allowlist no longer authorizes, and a
  contact with a colliding display name but a different contactId stays
  unauthorized.
- `tests/gateway/test_simplex_plugin.py`: upstream's prompt test was written
  against `_SETUP_PROMPTS`; the fork's version reads the prompt from
  `interactive_setup`'s source instead. Upstream's unrelated multiplex-scope
  test, which needs fixtures the fork lacks, was not taken.

`bf271d0f96` adds the triage line for `df1b647b42`: it only silences a
"tirith enabled but not available" warning while tirith's background first
download runs; this deployment has tirith installed and `tirith_fail_open`,
so the warning could not mislead a security decision here.

## Verification

- `tests/gateway/test_unauthorized_dm_behavior.py` and
  `tests/gateway/test_simplex_plugin.py`: 70 passed.
- Policy-1 sync gate: `scripts/openbrain_conformance_smoke.py` passed (update
  guard plus the focused Open Brain and Hermes tests);
  `tests/plugins/test_routing_classifier_plugin.py`: 19 passed.
- `scripts/upstream_digest.py --no-fetch --json`: `security` undecided 0,
  landed 74, triaged 98.

## Consequences and follow-ups

- **The live gateway (`ai.hermes.gateway`) still runs the pre-port code**
  until it is restarted; the venv is an editable install of this checkout,
  so the next start picks the fix up. Agents do not restart it. Mark's
  command, from the 2026-09-16 standup note:
  `cd /Users/mh/.hermes && /Users/mh/.hermes/venv/bin/python -m hermes_cli.main gateway start`.
  Since SimpleX is not enabled here, there is no urgency.
- Operators of a SimpleX deployment must put numeric contactIds in
  `SIMPLEX_ALLOWED_USERS`; display-name entries now fail closed.
- The reviewer's `upstream-security` finding clears on its next run.
