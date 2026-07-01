# Upstream Sync Checkpoint — 2026-06-26

> **Disposable artifact.** Point-in-time resume + cleanup state for ONE sync.
> SHAs/branches/worktrees below are valid only for this update. Delete this file
> once the remaining steps are done and the post-smoke has passed for a day.
> Standing process: `docs/hermes-update-runbook.md` / `UPDATING.md`.

## ★ PICK UP HERE — resume state

**Conflict resolution is DONE and committed on a sync branch in an isolated
worktree. Production `main` and the live gateway are UNTOUCHED.** What remains is
the live tail: PR → green CI → merge to `main` → gateway restart → post-smoke.

- Worktree: `/Users/mh/ai/agents/hermes-sync-2026-06-26`
- Sync branch: `sync/unified-retrieval-main-2026-06-26`
  - `f0d67e677` test(telegram): repoint OB-seam tests to relocated adapter
  - `18c6a6635` Merge reference/main (858 upstream commits) — conflicts resolved
  - branched off `29e6e7093` (production `main` HEAD, the unpushed `UPDATING.md`)
- Upstream merged: `reference/main` @ `e3db1ef92` (was merge-base `c06898098`,
  2026-06-19). 858 commits, ~1473 files.
- Rollback tag on production: `archive/pre-hermes-update-20260626-151029` → `main`.
- Pre-guard before work: PASS except `git-origin-match` (the known unpushed
  `UPDATING.md`). Post-merge guard delta checks: **15/15 files + 16/16 markers PASS.**
- `uv sync --extra dev` clean (skip `--all-extras`: the `matrix` extra's
  `python-olm` native build fails — unrelated to this sync).
- Targeted tests green: `test_openbrain_commands_plugin` + `test_message_actions`
  (33) + `test_telegram_open_brain_feedback` (4). Module import smoke OK.

### Remaining steps (do in a FRESH session with context headroom)

1. `cd /Users/mh/ai/agents/hermes-sync-2026-06-26`
2. Run the broader suite (not just the targeted files) to confirm the 858-commit
   merge didn't break anything outside the conflict surface:
   `.venv/bin/python -m pytest -q` (or the project's CI subset).
3. **Prereq for merge to `main`:** production `main` must equal `origin/main`
   first — push the pending `29e6e7093` `UPDATING.md` commit:
   `git -C /Users/mh/ai/agents/hermes-agent push origin main`.
4. Push the sync branch, open a PR into `main`, merge **only on green CI** (mirrors PR #1).
5. `HERMES_HOME=/Users/mh/.hermes hermes gateway restart`
6. Post-smoke (box is online 24/7 → `--live-smoke` is the default):
   - `.venv/bin/python scripts/hermes_update_guard.py --post --live-smoke`
   - `.venv/bin/python scripts/openbrain_conformance_smoke.py --phase post --live-smoke`
   - Manual: health-monitor.jsonl fresh healthy rows (ollama/gateway/telegram/
     openbrain/disk/memory); OB tool floor ≥30; real Telegram reply; recall Q →
     `query_brain`, analytical Q → `analyze_brain_query`; **`/brief` responds**
     (proves `openbrain-commands` plugin loaded).
7. Then the Cleanup Window (below).

## How the 7 conflicts were resolved

All were the same class: a local Open Brain / memory-boundary hook sitting next
to an upstream improvement to the *same* session-teardown method. Combined both
sides (never blanket keep-ours); every load-bearing OB delta preserved.

| File | Resolution |
|---|---|
| `agent/conversation_compression.py` | Took upstream's new `in_place` compaction structure (#38763); restored our `commit_memory_session(boundary_reason="compression", parent_session_id=…)` kwargs on the now-shared top-level extraction call. |
| `gateway/run.py` | `_finalize_shutdown_agents`: kept it **async** (caller awaits) + upstream in-flight transcript flush (#13121) **then** our `await _capture_session_summary_if_eligible`, so the summary sees the flushed turn. Loop iterates `items()` (needs `session_key` for the `entry` lookup the shared tail uses). |
| `gateway/slash_commands.py` | `/new` reset: our OB `commit_memory_session` boundary capture **then** upstream's timeout-wrapped cleanup (#35994). |
| `cli.py` | Kept our `shutdown_memory_provider(_session_msgs, boundary_reason="cli_close")` calls + upstream's added logging. |
| `run_agent.py` | Kept our `on_session_end(…, capture_context=…)` + upstream's exception logging. |
| `hermes_cli/commands.py` | `_SLACK_VIA_HERMES_ONLY`: union of our expanded set + upstream's new `moa`. |
| `scripts/release.py` | `AUTHOR_MAP`: pure dict union. |

## Load-bearing relocation handled

Upstream `560010547` bundled all platform adapters as plugins, moving
`gateway/platforms/telegram.py` → `plugins/platforms/telegram/adapter.py`. Git
rename-detection carried our **entire Phase 5c generic action-seam delta** intact
(`set_action_handler`, `stage_actions`/`pop_staged_actions`/`attach_actions`,
`_dispatch_action_callback`, OB query 👍/👎 + proactive ✅/🙈 feedback wiring).
Follow-on edits made for the new path:

- `scripts/hermes_update_guard.py`: `LOCAL_DELTA_PATHS` + `LOCAL_DELTA_PATTERNS`
  telegram entries repointed to `plugins/platforms/telegram/adapter.py` (committed in the merge commit).
- Two local tests repointed their imports (commit `f0d67e677`).

No other live imports of the old `gateway.platforms.telegram` path remain
(remaining hits are docstring/comment references only:
`tools/send_message_tool.py:1017`, `gateway/run.py:12564` config-key mention,
and two test docstrings — cosmetic, safe to leave or tidy later).

## Cleanup Window (only after post-smoke passes + 1 day of real Telegram traffic)

- Remove the worktree: `git -C /Users/mh/ai/agents/hermes-agent worktree remove /Users/mh/ai/agents/hermes-sync-2026-06-26`
- Delete the sync branch once merged: `git branch -d sync/unified-retrieval-main-2026-06-26`
- Prune older `archive/pre-hermes-update-*` tags, keeping the newest known-good.
- Delete this file.
