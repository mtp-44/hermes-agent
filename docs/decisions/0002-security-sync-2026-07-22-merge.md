---
id: HA-0002
date: 2026-07-30
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy]
verdict: "Merge PR #7 sync/security-2026-07-22 (credential-header stripping on redirects, dashboard .env/credential-store guards, aiohttp/raft body-size caps, CI ref-injection fix) into the live branch fix/session-capture-signal-gate; sync gate passed, zero test regressions across the full suite, all three claimed fixes exercised and confirmed, deployed to all three live services same day."
---

# Merge the 2026-07-22 security sync into the live branch

## Context

`hermes-agent` production runs on `fix/session-capture-signal-gate`
(39 ahead of `origin/main`, per this fork's policy 1 — the fork does not
track `main` directly and pulls from it only for security fixes). On
2026-07-22 PR #7 `sync/security-2026-07-22` landed on `origin/main`: 13
commits (12 non-merge + the merge), entirely security/hardening work plus
its own docs note. Because the live tree was mid-`~/ai`-rebuild (Phase 4,
2026-07-29), the merge was deliberately deferred rather than folded into
the filesystem move, so the two changes would stay separately diagnosable.
It sat unmerged in production for 8 days.

The 13 commits: `fetch_models` credential-header stripping on cross-host
redirects; a shared local-file credential-read guard for vision/image-gen
inputs (extended to cover xAI); three successive widenings of the dashboard
managed-files `.env` guard (case-insensitivity, pattern/suffix matching,
other credential-store basenames); explicit `client_max_size` caps on three
previously-uncapped aiohttp servers; a chunked-request body-size limit on
the Raft adapter; a CI fix passing untrusted refs through `env:` rather than
`run:` string interpolation; and a docs commit recording the sync.

## Verification

- `git merge-tree --write-tree` dry-run: clean, 0 conflicts (re-verified
  immediately before merging, per policy — the 2026-07-29 dry-run was not
  taken on trust).
- Pre-merge full-suite baseline: 1427 failed / 36847 passed / 242 skipped /
  15 errors, root-caused to a single pre-existing macOS write/path-approval
  guard issue (tests expecting to write into pytest's sandboxed `tmp_path`
  instead have writes redirected, so assertions on the resulting file fail;
  one visible side effect was a stray `shared.txt` written into the repo
  root by `tests/tools/test_file_staleness.py`, cleaned up — confirmed
  pre-existing, not a merge artifact).
- Sync gate: `scripts/openbrain_conformance_smoke.py` (294 passed) and
  `tests/plugins/test_routing_classifier_plugin.py` (19 passed) — both
  clean.
- Post-merge full suite: 1426 failed / 36891 passed / 225 skipped / 15
  errors. Diffed failing-test-ID sets pre vs. post: **zero new failures**;
  one test (`test_thousand_clean_writes_emit_one_info`, timing-sensitive)
  newly passing.
- All 9 new/extended security test files run directly and confirmed
  passing (205 tests): `.env`/credential-store guard (case variants,
  `.envrc`, other basenames), `fetch_models` cross-host vs. same-host
  redirect credential handling, aiohttp/raft body-size caps rejecting
  oversized payloads — not merely a process-start check.
- Push to `origin/fix/session-capture-signal-gate` initially rejected
  non-fast-forward: the live checkout was also one commit behind its own
  remote (`218194165`, Mark's 2026-07-29 "join the estate doc spine"
  commit — a direct child of the pre-sync HEAD, docs-only, pre-existing
  gap unrelated to this task). Dry-run merge-tree confirmed clean, merged,
  re-pushed.

## Decision

Merge into the live tree at `/Users/mh/ai/agents/hermes-agent` (not
`~/ai.new/hermes-agent`, which is the inert Phase 5 cutover build) because
that is the tree three launchd services actually execute, confirmed by
resolving `import hermes_cli` through each service's Python: `ai.hermes.gateway`
via `~/.hermes/venv`'s editable install, and both `com.mh.hermes-pwa` and
`com.mh.hermes-dashboard` via `~/.hermes/hermes-agent/venv`'s editable
install — both finders map to `/Users/mh/ai/agents/hermes-agent/...` by
absolute path regardless of which venv or wrapper script is entered. The
`~/.hermes/hermes-agent` directory's own checked-out source (a separate
clone of upstream `NousResearch/hermes-agent`) is inert; only its `venv/`
matters, and that venv is editable-installed against the live tree.

Deployed same day via `launchctl kickstart -k` on all three services, one
at a time, each verified before the next (`hermes doctor` clean, gateway
`/health` returns 200 and reconnects to signal/telegram/webhook/all 3 MCP
servers, PWA answers 302, dashboard TUI process alive — no fresh asset
rebuild triggered on the dashboard restart).

## Consequences

- Production carries the 2026-07-22 hardening as of 2026-07-30.
  `~/ai.new/hermes-agent` fast-forwarded to match (`b046873`) so the
  pending Phase 5 cutover carries the verified code, not a stale copy.
- **Known deviation from house testing policy, noted rather than
  corrected here:** `CLAUDE.md` mandates `scripts/run_tests.sh` (hermetic
  env — unset provider keys, `TZ=UTC`, `LANG=C.UTF-8`, subprocess-per-file
  isolation) over raw `pytest` for CI parity. Both the baseline and
  post-merge full-suite runs in this task used raw
  `.venv/bin/python -m pytest -q`, per the handoff's explicit instruction.
  The pre/post comparison is still internally valid — identical method
  both times, so the zero-regression result holds — but the absolute
  failure count (1427 pre-existing) may not match what CI or
  `run_tests.sh` would report, since env parity (real API keys present,
  local TZ, no subprocess isolation between test files) differs from CI.
  If that pre-existing failure count is ever investigated for real (not
  just diffed against), re-baseline with `run_tests.sh` first.
- The Phase 4 rebuild's carried item 2 (this task) is closed; only the
  three ⚠️ Phase 5 items in
  `/Users/mh/.claude/plans/think-this-through-the-sunny-treehouse.md`
  remain before cutover, of which this was one.
