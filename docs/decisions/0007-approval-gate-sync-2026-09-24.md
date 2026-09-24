---
id: HA-0007
date: 2026-09-24
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, approvals]
verdict: "Pull three upstream approval-gate features under policy 1, although upstream tags them feat: protected agent-instruction files always need write approval (fe66596df3 + desktop follow-up 04154a37d3), user-defined `approvals.deny` rules that block even under yolo (e2fe529efb), and `/deny <reason>` (cb6c47af08). Why: Hermes runs with terminal.cwd /Users/mh/ai, where 24 AGENTS.md/CLAUDE.md files steer every agent in the estate, so an injected write there persists across Claude, Codex and Hermes. HA-0006 had triaged fe66596df3 as a deferred feature; this reverses that. Same day, the upstream digest learned the approval/redact/ssrf gate scopes, which moves 85 upstream commits from invisible to needs-a-decision."
---

# Pull the approval-gate hardening; widen the digest to see it

## Context

A feature review of upstream on 2026-09-24 (fork at v0.18.0 + backports,
upstream at v0.21.4, 27,245 commits ahead) found nothing worth a feature sync,
but three upstream commits that close attack paths in this deployment:

- **`fe66596df3` protected agent-instruction files.** `write_file` and `patch`
  targeting `AGENTS.md`, `CLAUDE.md`, `SOUL.md`, `.cursorrules` or a
  project-local `.hermes/` dir always prompt the human for that one operation,
  even under yolo, and fail closed when there's no human channel. Hermes runs
  with `terminal.cwd: /Users/mh/ai`. That tree holds 24 such files, and every
  agent in the estate reads them, so a prompt-injected write persists across
  Claude, Codex and Hermes.
- **`e2fe529efb` `approvals.deny`.** Fnmatch globs that block a terminal
  command before the yolo/off bypass. This is the enforceable form of the
  estate's written rules (`launchctl`, plist edits, `git clean` /
  `reset --hard` in `~/ai`).
- **`cb6c47af08` `/deny <reason>`.** The reason is relayed to the agent. With
  `approvals.mode: manual`, every denial used to reach it with no reason.

[`HA-0006`](0006-security-sync-2026-09-24.md) triaged `fe66596df3` as
"deferred: new feature, not a fix". Policy 1 covers security fixes. A gate
that closes a prompt-injection persistence vector is one, whatever its commit
type. This record reverses that triage.

## What happened

- `cb6c47af08` cherry-picked clean. `e2fe529efb` conflicted only on the
  approvals defaults block, where both `unattended_mode` and `deny` were kept.
- `fe66596df3`: upstream's `hermes_cli/config_defaults.py` doesn't exist here,
  so its two `security.protected_instruction_*` defaults moved into
  `hermes_cli/config.py`. The test-file conflict held only upstream-only
  classes (`TestGetWriteDeniedError`, `TestSafeRootDenialMessageIntegration`),
  which were dropped. All helpers it calls exist in the fork's
  `tools/approval.py`.
- `04154a37d3` (desktop renderer: approvals for `patch`/`write_file` rows)
  was taken because the PWA is a build of `apps/desktop`. Without it a
  protected write from the PWA would raise an approval with no button and
  time out.
- `4179c5a99c` (rewording in `cli-config.yaml.example`) was skipped because
  our example file has no such entry.
- **Test adaptation.** The fork's blanket `/private/var/` sensitive prefix
  refuses every macOS `tmp_path` before the new gate is reached. Inside
  `TestProtectedInstructionFiles` only, the prefixes are narrowed to
  upstream's current `/private/var/db/` + `/private/var/root/`. The production
  prefix is unchanged: loosening it is a separate, non-security decision.

**Digest.** `scripts/upstream_digest.py` only matched the `security`/`sec`
scope, so `feat(approvals)`, `feat(approval)` (IMDS credential fetches,
docker daemon redirects) and the `fix(approval)` / `fix(redact)` waves never
reached its Security section. It now also treats `approval(s)`, `redact(ion)`
and `ssrf` as gate scopes. `auth` (127 commits, mostly provider OAuth) and
`secrets` (mostly the vault feature) stay out so they don't bury the signal.
Result: Security section 0 → **85 undecided** (48 `fix(approval)`,
22 `fix(redact)`, 8 `feat(approval(s))`, 7 other). These aren't triaged
here; that's the next security pass.

## Verification

- New tests fail against unpatched `main` (17 failed + 22 errors) and pass on
  the branch: file-write safety + deny rules + approve/deny commands +
  approval interrupt, 110/110. Digest tests 37/37.
- Sync gate: conformance smoke 296/296 (run with the worktree on
  `PYTHONPATH`; without it plugin discovery resolves to the live checkout),
  routing classifier 19/19.
- Full suite vs a fresh unmodified-`main` worktree: 38,458 → 38,512 passed
  (+54 new tests), 64 failed on both, identical by test id: 0 new, 0 fixed.

## Consequences

- Hermes' own writes to `AGENTS.md`/`CLAUDE.md` via its file tools now ask
  every time, in every repo, including this fork's own point-up block. The
  terminal tool (`echo >> AGENTS.md`) is not covered by this gate and still
  goes through the ordinary command approval.
- `approvals.deny` ships empty in the defaults. Ten rules were written into
  `~/.hermes/config.yaml` on 2026-09-24, before this merged (backup
  `~/.hermes/backups/config.yaml.pre-approvals-deny-20260924-104334`). They block
  `launchctl load/unload/bootstrap/bootout/kickstart/remove/disable/enable`,
  `git … clean -f…` and `git … reset … --hard`, and allow `launchctl
  list/print`, `git clean -n` and `git reset --soft`. They do nothing until the
  gateway runs this code. They are unconditional, so Hermes can no longer
  restart a launchd service even when asked; Mark does that by hand.
- The Telegram adapter's approval buttons ignore `allow_session` /
  `allow_permanent`, so a protected-write prompt still offers session/always.
  The gate treats any approval as one operation and persists nothing, so the
  labels are misleading but safe.
- The PWA picks up the desktop fix only after a PWA release
  (`tailnet_pwa/WP4_RELEASE_RUNBOOK.md`). Until then, protected writes from
  the PWA fail closed.
