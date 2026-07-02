# Upstream Security Sync Checkpoint — 2026-07-02

> **Disposable artifact.** Point-in-time resume + cleanup state for ONE sync.
> SHAs/branches below are valid only for this update. Delete this file once
> the remaining steps are done and the post-smoke has passed for a day.
> Standing process: `docs/hermes-update-runbook.md`.

## Status: DONE through post-smoke

Production `main` and the live gateway are UPDATED and verified. Only the
Cleanup Window (bottom of this doc) remains, deliberately held for a burn-in
period.

- PR: [mtp-44/hermes-agent#4](https://github.com/mtp-44/hermes-agent/pull/4) —
  merged after all required CI checks green, merge commit `39ffabee0`.
- Production `main` fast-forwarded to `39ffabee0`. Gateway restarted, running
  clean since `2026-07-02 09:24`.
- Full targeted test suite (resume/IDOR, browser SSRF/CDP, cron scheduler,
  packaging metadata, i18n parity, session-boundary security) plus a full
  `scripts/run_tests_parallel.py` run compared against a `main` baseline in a
  separate worktree — identical pre-existing failing-file set (macOS/network
  environment noise) except one flaky-under-parallelism LSP e2e test that
  passes standalone.
- Post-update guard + `openbrain_conformance_smoke.py --live-smoke`: all
  green, 98/98 focused tests passed, health-monitor reports all 7 services
  (`ollama`, `gateway`, `config`, `telegram`, `openbrain`, `disk`, `memory`)
  healthy after restart.

## Scope

Given the fork was ~1,113 commits behind upstream (last synced 2026-06-26),
a full merge was deferred. This sync cherry-picked only the security-relevant
subset: 12 of 15 flagged `security(...)` commits (the remaining 3 need a
`sessions` table schema migration from non-security upstream commits not yet
merged — see PR description), plus small prerequisite/adaptation commits
needed to apply them against our tree. Full non-security merge remains a
separate, future task — see `scripts/upstream_digest.py` for triaging it.

## Fixes made beyond straight cherry-pick

All because upstream's commits assumed refactors we don't have yet:

| File | Fix |
|---|---|
| `gateway/slash_commands.py` | `await self._session_db.get_session(...)` → `asyncio.to_thread(...)`; our `SessionDB` is sync, not an async facade like upstream's current tree. |
| `tests/gateway/test_resume_command.py` | Converted several test methods to `async`/`await` — the commits' own tests called the new async `_resume_row_visible`/`_resume_target_allowed` without awaiting (upstream fixes this in a later, schema-dependent commit not taken here). |
| `tests/gateway/test_matrix_project_context_isolation.py` | Updated a pre-existing test that asserted the now-fixed cross-room enumeration behavior (Matrix `/resume --all` is now admin-gated). |
| `tests/gateway/test_session_boundary_security_state.py` | Fixed mock gaps (`session_store._entries`, `_session_db.get_session`/`resolve_resume_session_id`) in a shared fixture that the new IDOR guard exposed. |
| `locales/*.yaml` | Backfilled `gateway.resume.blocked_not_owner` (added only to `en.yaml` upstream) into the other 15 locale catalogs — this repo enforces key/placeholder parity across all locales. |
| `scripts/release.py` | Added 3 `AUTHOR_MAP` entries for this sync's cherry-picked commit authors (CI's contributor-attribution check). |
| `tests/hermes_cli/test_commands.py` | Unrelated finding, fixed in the same PR: the local `routing-classifier` plugin (`e555765a4`, first ever run through CI this session) leaks its `/route` command into 4 Discord-skill-command tests that only mocked skill discovery, not the plugin-command tier. Mocked `hermes_cli.plugins.get_plugin_commands` to `{}` in those 4 tests. |

## Self-inflicted issue caught and fixed live

While diffing dependency changes in a disposable worktree (`/tmp/hermes-sync-check`,
now removed), running `uv sync` there repointed the global
`/Users/mh/.local/bin/hermes` symlink at that worktree's venv instead of the
production repo's. Caught by the pre-restart guard (`launchagents-targets`
failed: `com.mh.hermes-dashboard.plist` target missing). Fixed by relinking
to `/Users/mh/ai/agents/hermes-agent/.venv/bin/hermes` before restarting.
**Lesson: after any `uv sync` in a scratch worktree, verify
`/Users/mh/.local/bin/hermes` still points at the production repo before
restarting the gateway.**

Also: production `uv sync` needs `--extra messaging` in addition to
`--extra all --extra dev`, or it prunes `python-telegram-bot[webhooks]` again
(same class of issue as the 2026-06-26 sync, this time avoided proactively).

## New tooling from this sync

`scripts/upstream_digest.py` — triage report for `reference/main` vs `main`.
Surfaces `security(...)` commits and commits touching
`LOCAL_DELTA_PATHS` (from `hermes_update_guard.py`); bulk-summarizes
everything else by conventional-commit type. Meant to run on a schedule
between syncs so the next full merge isn't a cold review of 1000+ commits.

## Cleanup Window (only after post-smoke passes + 1 day of real Telegram traffic)

- Remote branch `sync/security-2026-07-02` already deleted via PR merge
  (`gh pr merge --delete-branch`); prune the stale local remote-tracking ref:
  `git fetch --prune origin`
- Prune older `archive/pre-hermes-update-*` tags, keeping the newest
  known-good (`archive/pre-hermes-update-20260702-074406` from this sync).
- Delete this file.
