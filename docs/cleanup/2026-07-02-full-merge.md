# Full Non-Security Upstream Merge Checkpoint — 2026-07-02

> **Disposable artifact.** Point-in-time resume + cleanup state for ONE sync.
> SHAs/branches below are valid only for this update. Delete this file once
> the remaining steps are done and the post-smoke has passed for a day.
> Standing process: `docs/hermes-update-runbook.md`. Parent handoff:
> `docs/handoff-upstream-security-sync-2026-07-02.md` (item 2).

## Status: DONE through post-smoke

Production `main` and the live gateway are UPDATED and verified. Cleanup
window (bottom of this doc) is deliberately held for a burn-in period.

- PR: [mtp-44/hermes-agent#6](https://github.com/mtp-44/hermes-agent/pull/6) —
  merged after all required CI checks green, merge commit `e430f809b`.
- Production `main` fast-forwarded `115b72a58` → `e430f809b`. Gateway
  restarted, running clean since `2026-07-02 13:55:11`.
- Scope: ~1,160 commits from `reference/main` (NousResearch/hermes-agent),
  the full non-security merge deferred by the 2026-07-02 security-only sync
  (parent handoff item 2). Upstream had drifted further (to `88bd1c01e`) by
  the time this merge ran, so the actual merge base was slightly ahead of
  the 1,160 figure quoted when the work started.
- Full `scripts/run_tests_parallel.py` suite (35,789 tests) compared against
  a fresh unmerged-`main` baseline in a separate worktree: merged tree is
  equal-or-better (61 failures vs. baseline's 64). Every differing file was
  triaged individually (see "Test diff triage" below) — no true regressions.
  `tests/agent/test_i18n.py` (16-locale parity): 47/47 passed. Byte-compile
  clean across all tracked `.py` files.
- CI on PR #6: all required checks green (lint, ruff+ty, 8 parallel Python
  test slices, e2e, TypeScript ×5, Docker lint, supply-chain/OSV scans,
  uv.lock check, contributor attribution).
- Post-restart: `hermes_update_guard.py --post --live-smoke` and
  `openbrain_conformance_smoke.py --phase post --live-smoke` both green,
  98/98 focused OB/Hermes tests passed. Health monitor reports all 7
  services (gateway, config, telegram, openbrain, disk, memory, ollama)
  healthy post-restart. `gateway.log` shows a clean Telegram reconnect and
  the newly-merged self-heal stale-session logic (#54878) already firing
  correctly on a crashed-gateway leftover entry.
- DB backup taken before restart:
  `/Users/mh/.hermes/backups/state.db.pre-full-merge-20260702-135347`.
- Rollback tags: `archive/pre-hermes-update-20260702-121602` (pre-merge, on
  the old `115b72a58` tip) and `archive/pre-hermes-update-20260702-135442`
  (post-fast-forward, pre-restart, on `e430f809b`).
- Still needed: a real Telegram smoke message from a human (the one
  post-update-smoke step this session can't self-trigger).

## Conflict resolutions

16 files conflicted. None were resolved with blanket "keep ours" — every
load-bearing local delta was confirmed to survive, reapplied on top of
upstream's version where upstream had restructured the surrounding code.

| File | Resolution |
|---|---|
| `scripts/release.py` | `AUTHOR_MAP`: pure dict union (same pattern as every prior sync). |
| `hermes_state.py` | Two independent additive hunks (new `MAX_FTS5_QUERY_CHARS` constant, new `idx_sessions_handoff_state` index) — combined both sides. |
| `gateway/run.py` (4 conflicts) | (1) Two unrelated new-method blocks (`_startup_route_snapshot`/`_startup_mcp_snapshot`/`_log_startup_snapshot` vs `_sync_session_model_from_agent`) — combined both. (2) `_finalize_shutdown_agents` loop var: kept our `for session_key, agent in active_agents.items()` — upstream's `.values()` would break the OB session-summary block a few lines down that needs `session_key`; confirmed via `git show reference/main` that upstream's own current file doesn't have that block at all (pure HEAD-side addition, never touched by upstream). (3)+(4) Cache-coherence re-baseline: dropped an early, buggy re-baseline call on our side that ran BEFORE transcript persistence (the exact bug the surviving later comment at the correct call site describes) — upstream had independently fixed the same #45966 bug by moving/deferring the call; took upstream's side plus its new `_run_start_session_id` race guard + `_record_gateway_session_peer` call (already defined via clean auto-merge in `gateway/session.py`). |
| `gateway/session.py` | `get_or_create_session`: upstream added a self-heal stale-routing wrapper (#54878) around the existing suspended/resume_pending logic, and independently added a freshness gate on `resume_pending` (#46934) that is a strict superset of our own pre-existing `_should_reset` check. Verified via 3-way diff (`git checkout --conflict=diff3`) that upstream's side already contains everything our side had; took upstream's side wholesale. |
| `gateway/slash_commands.py` | `/compress`: removed an early, buggy session-repoint-before-persist call on our side (same #44794 data-loss class as the run.py cache-coherence bug above — data loss if the write later failed) since the correct guarded version already exists later in the same function. |
| `agent/conversation_compression.py` | Upstream added a compression-lock lease refresher (new `_CompressionLockLeaseRefresher` class, TTL/refresh-interval plumbing, more `try/finally` exception-safety around lock release) — a large structural rewrite of `compress_context`. Diffed the two full function bodies whitespace-insensitively to confirm the ONLY substantive content difference (beyond the new refresher feature) was our `commit_memory_session(boundary_reason=..., parent_session_id=...)` kwargs. Took upstream's function wholesale and reapplied just that one kwargs delta. |
| `plugins/platforms/telegram/adapter.py` (4 conflicts) | (1)+(2) Independent new-attribute/new-method blocks (Phase 5c `_action_handler`/`_staged_actions` vs upstream's `_post_connect_task` + `_mark_connected`/`_mark_disconnected`/`_set_fatal_error`/`_should_drop_delayed_delivery`) — combined both. (3)+(4) Message-edit `chat_id` handling: took upstream's `normalize_telegram_chat_id(chat_id)` fix (already used pervasively elsewhere in the file) but kept our `reply_markup` dict-building (Phase 5c action buttons on edited messages), which upstream's side didn't have. |
| `pyproject.toml`, `tools/lazy_deps.py` | Pure `aiohttp==3.14.0` → `3.14.1` version-bump conflicts (4 in `lazy_deps.py`, 2 in `pyproject.toml`) — took upstream's newer pin throughout, kept consistent. |
| `tools/browser_tool.py` (3 conflicts) | (1) Import list: combined our `redact_sensitive_text`/`_redact_url_query_params`/`_redact_url_userinfo` with upstream's new `redact_cdp_url`. (2) `_sanitize_url_for_logs`: upstream consolidated CDP-URL redaction into `agent.redact.redact_cdp_url` as the single source of truth (explicit intent per its docstring: "cannot drift apart") — took upstream's version, then removed the now-unused `_redact_url_query_params`/`_redact_url_userinfo` imports. (3) Pure addition: upstream's new browser-`eval` SSRF/sensitive-primitive blocklist (`_RISKY_BROWSER_EVAL_PATTERNS`, `_enforce_browser_eval_policy`, etc.) — took wholesale. |
| `tools/browser_supervisor.py` (2 conflicts) | Same `redact_cdp_url` consolidation, plus one pure addition (`_redact_supervisor_text`) — took upstream's side both times. |
| 6 test files (`test_13121_shutdown_inflight_transcript_flush.py`, `test_async_session_db.py`, `test_clean_shutdown_marker.py`, `test_session_boundary_security_state.py`, `test_hermes_state.py`, `test_tui_gateway_server.py`, `test_browser_eval_ssrf.py`) | All either pure test additions for the upstream features above (took upstream's side) or the `test_session_boundary_security_state.py` fixture, which already had our fuller IDOR-guard mock setup from the prior sync (kept ours). `test_tui_gateway_server.py`'s flaky-test fix was functionally identical on both sides (own-key filtering) — kept our simpler version. |
| `uv.lock` | Regenerated via `uv lock` from the resolved `pyproject.toml` rather than hand-merged. |

## Test diff triage (merged vs. unmerged-`main` baseline)

- **3 tests in `test_anthropic_adapter.py`** (`TestResolveAnthropicToken::*`,
  `TestResolveWithRefresh::*`) fail on baseline but pass on merged — the
  merge fixed a Claude Code credential-resolution bug. Net improvement, not
  investigated further.
- **`test_gateway_shutdown.py::test_gateway_stop_systemd_service_restart_exits_cleanly`**
  failed on baseline, passed on merged — likely a timing flake, not
  reproduced on merged in the same run.
- **`tests/run_agent/test_run_agent.py`** was SIGKILL'd on baseline (140s
  runner ceiling exceeded) but completed in 158.5s on merged — infra
  contention from running two full suites simultaneously, not a code issue.
- **`TestSilentFileMisplacementE2E::test_relative_write_after_env_cleanup_lands_in_user_cwd`**
  (new upstream test) fails on merged; doesn't exist on baseline (too new).
  Reproduced identically on a clean, unmodified `reference/main` worktree
  (`/tmp/hermes-reference-check`, since removed) — confirmed as an
  upstream-inherited flake (same class as the pre-existing `/var/folders`
  sensitive-path false positives), not something this merge introduced.
- All other failures are byte-identical sets on both trees: known
  pre-existing macOS/host-environment noise (`TestSensitivePathCheck`
  `/var/folders` false positives, AF_UNIX path-length limits in
  `test_voice_mode.py`, systemd/WSL-only tests, blocked model-catalog
  network endpoint).

## Self-inflicted issue caught and fixed live

While syncing dependencies in a THIRD disposable worktree
(`/tmp/hermes-reference-check`, used to verify the `TestSilentFileMisplacementE2E`
failure against pure upstream), the global `/Users/mh/.local/bin/hermes`
symlink was hijacked again — the same recurring gotcha from the
2026-07-02 resume-hardening sync earlier the same day, and the parent
handoff's original 2026-06-26 finding. This time it repointed to a
*different, already-stale* worktree's venv
(`/private/tmp/hermes-main-baseline-20260702/.venv/bin/hermes`) rather than
the worktree just synced, suggesting the hijack isn't always the
most-recently-synced venv. Caught via a routine `readlink` check between
steps, well before touching production; relinked immediately.

**Lesson (reinforcing the existing one): check the symlink after every
`uv sync` in every worktree, and don't assume which venv it will point to
if it does get hijacked — always `readlink` and compare against
`/Users/mh/ai/agents/hermes-agent/.venv/bin/hermes` explicitly, don't
eyeball it.**

## Cleanup Window

Only after production has passed the post-update guard and a real Telegram
smoke for at least one day:

- Remove worktree: `git -C /Users/mh/ai/agents/hermes-agent worktree remove /Users/mh/ai/agents/hermes-sync-2026-07-02`
- Delete sync branch: `git branch -d sync/full-merge-main-2026-07-02` (local + `origin --delete`)
- Prune older `archive/pre-hermes-update-*` tags, keeping the newest known-good.
- Delete this file.

**Unrelated stale debt noticed in passing (not this sync's responsibility,
flagging for the next cleanup pass):** `sync/unified-retrieval-main-2026-06-26`
branch and `/Users/mh/ai/agents/hermes-sync-2026-06-26` worktree from the
2026-06-26 sync are still present a week later — that sync's own cleanup
window was held pending a burn-in period and a human decision on an
unrelated `package-lock.json` stash that was never followed up on.
