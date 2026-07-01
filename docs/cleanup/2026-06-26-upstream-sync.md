# Upstream Sync Checkpoint — 2026-06-26

> **Disposable artifact.** Point-in-time resume + cleanup state for ONE sync.
> SHAs/branches/worktrees below are valid only for this update. Delete this file
> once the remaining steps are done and the post-smoke has passed for a day.
> Standing process: `docs/hermes-update-runbook.md` / `UPDATING.md`.

## ★ PICK UP HERE — resume state (2026-07-01)

**DONE through post-smoke. Production `main` and the live gateway are UPDATED
and verified.** Only the Cleanup Window (bottom of this doc) remains, and it is
deliberately held for a burn-in period — see "Remaining" below.

- PR: [mtp-44/hermes-agent#2](https://github.com/mtp-44/hermes-agent/pull/2) —
  merged `2026-07-01T09:36:30Z` after 33/33 CI checks green, merge commit
  `8e681874b`.
- Production `main` fast-forwarded to `8e681874b`, then again to `7dd212b34`
  (see the follow-on fix below). Gateway restarted twice (once per merge);
  currently running clean since `2026-07-01 12:16:46`.
- Full test verification used `scripts/run_tests_parallel.py` (the actual
  per-file-isolated runner CI uses — **do not** trust a raw `pytest -q` full-repo
  run for this kind of check: single-process cross-file module-state leakage
  produced ~1300 false failures before switching runners). One real regression
  was found and fixed on the sync branch: `tests/gateway/test_13121_shutdown_inflight_transcript_flush.py`
  called `_finalize_shutdown_agents` synchronously, but the merge's conflict
  resolution kept it `async` — the coroutine was created but never awaited, so
  the flush silently never ran in the test (commit `4af797348`). Everything else
  divergent from unmerged `main` was pre-existing macOS-only environment noise
  (confirmed identical on `main`): sensitive-path guard false positives on
  `/var/folders/...`, AF_UNIX path-length limits, systemd/WSL-only tests, a
  blocked model-catalog network endpoint (403), and one order-dependent flake in
  `test_ignore_user_config_flags.py` (reproduces on `main` too — worse there,
  deterministic 5/5). CI's contributor-attribution check also caught 4 upstream
  emails missing from `scripts/release.py` `AUTHOR_MAP` — added (commit
  `922fcbc4d`).
- **Accidental production package pruning, caught and fixed live:** ran
  `uv sync --locked --extra all --extra dev` directly in
  `/Users/mh/ai/agents/hermes-agent` (not a worktree) to diff test failures
  against `main` — this pruned lazy-installed packages not in the `all`/`dev`
  extras, including the live Telegram bot's `python-telegram-bot[webhooks]`.
  Restored immediately via `uv pip install "python-telegram-bot[webhooks]==22.6"`
  before the next gateway restart; confirmed via `gateway.log` that Telegram is
  the only active platform (discord/slack/matrix never appear), so nothing else
  was actually load-bearing among the other pruned (orphaned, undeclared-anywhere)
  packages. **Lesson: never run `uv sync` against the live production repo dir —
  always do dependency-install diffs in a disposable worktree/venv.**
- **Separate bug found + fixed during manual `/brief` verification** (unrelated
  to this sync — confirmed via `git log` that the file was untouched across the
  858-commit merge): `plugins/memory/openbrain/__init__.py`'s `_capture_record`
  sent `domain`/`category`/`subcategory` as top-level `capture_thought` args,
  which the tool's real schema (`content`/`metadata`/`embedding`/`contact_id`)
  silently drops — so every session-end auto-capture landed with only the
  server's default metadata, never the `record_type`/`source_app` that
  `gateway/open_brain.py`'s `_is_hermes_brief_candidate()` requires for
  `/brief`, `/digest`, `/stale` to find it. Fixed in
  [PR #3](https://github.com/mtp-44/hermes-agent/pull/3) (merged
  `2026-07-01T10:14:58Z`, commit `d1a0620b2` → `7dd212b34` on `main`), with a
  regression test. **This only fixes captures going forward — old
  already-stored session summaries still carry the wrong metadata and won't
  retroactively surface; a metadata backfill would be a separate follow-up if
  wanted.**
- **Unrelated loose end, preserved not lost:** production `main` had a
  pre-existing uncommitted edit to `package-lock.json` (present before this
  session started). It conflicted with upstream's own lockfile changes during
  the fast-forward, so it's sitting safely in
  `git -C /Users/mh/ai/agents/hermes-agent stash list` → `stash@{0}` (message:
  "pre-existing uncommitted package-lock.json change (unrelated to upstream
  sync)"), not applied, not dropped. Needs a human decision on what that edit
  was for.

### Remaining

Only the Cleanup Window (below). **Deliberately held** — user said "hold off"
on 2026-07-01 pending a burn-in period of real Telegram traffic, doubly
warranted since a second live-affecting fix (PR #3) landed shortly after
restart. Revisit and run the Cleanup Window once there's been a day of normal
use with no new issues.

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
