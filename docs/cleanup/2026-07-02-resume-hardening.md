# /resume Hardening Checkpoint — 2026-07-02

> **Disposable artifact.** Point-in-time resume + cleanup state for ONE sync.
> SHAs/branches below are valid only for this update. Delete this file once
> the remaining steps are done and the post-smoke has passed for a day.
> Standing process: `docs/hermes-update-runbook.md`. Parent handoff:
> `docs/handoff-upstream-security-sync-2026-07-02.md` (item 1).

## Status: DONE through post-smoke

Production `main` and the live gateway are UPDATED and verified. No cleanup
window is being held this time (see Cleanup Window below — most of it is
already done).

- PR: [mtp-44/hermes-agent#5](https://github.com/mtp-44/hermes-agent/pull/5) —
  merged after all required CI checks green, merge commit `c2d0119d4`.
- Production `main` fast-forwarded to `c2d0119d4`, then to `43a43fcf2` (docs
  commit updating the parent handoff). Gateway restarted and running clean.
- Targeted suite (15 files covering session/resume/AsyncSessionDB/Telegram-
  topic paths): 658/658 passed. Full `run_tests_parallel.py` suite compared
  against a fresh unmerged-`main` baseline in a separate worktree: identical
  failing-file set (pre-existing macOS/network noise) plus 2 apparently-new
  failures confirmed to be order-dependent flakes that pass in isolation on
  both trees. `tests/agent/test_i18n.py` (16-locale parity): 47/47 passed.
  Byte-compile clean.
- Post-update guard + `openbrain_conformance_smoke.py --live-smoke`: all
  green, 98/98 focused tests passed, health-monitor reports all 7 services
  healthy after restart. `PRAGMA table_info(sessions)` confirmed the new
  `chat_id`/`thread_id` columns are live.
- DB backup taken before restart:
  `/Users/mh/.hermes/backups/state.db.pre-resume-hardening-20260702-120137`.
- Rollback tag: `archive/pre-hermes-update-20260702-120323`.

## Scope

The parent security-sync handoff deferred 4 `/resume` hardening commits
(`5248877c6`, `599a6391d`, `f1e58d8c1`, `5b3f06425` — chat/thread-origin
proof, DM scoping tightening, shared-group fallback, `user_id_alt` fail-closed)
because they depended on upstream infrastructure not yet merged. The handoff
guessed this was "1 schema commit"; it was actually **8 non-security
prerequisites**, discovered one at a time by cherry-picking in a disposable
worktree and, at each failure, finding the actual upstream commit that
introduced the missing piece rather than patching around the gap:

| Commit | What it added | Why it was needed |
|---|---|---|
| `e28e14443` | `sessions` table gains `chat_id`/`thread_id` columns (additive, via the existing declarative column reconciler); session-restart preservation | The schema the 4 target commits write to |
| `3b6193eaf` | Upsert-instead-of-INSERT-OR-IGNORE session-metadata enrichment | Referenced by the same docstring the schema commit touches |
| `89daacb45` → `ea26f2271` → `cdc14e964` → `15506e4cc` | `AsyncSessionDB` offload facade + both call-site-routing commits | `5248877c6`'s own test file (`test_session_boundary_security_state.py`) already assumed `AsyncSessionDB` existed by the time it was authored upstream |
| `4e0f5c37d` | Offloads the Telegram topic-recovery helper tree off the event loop | This is the specific upstream commit that updates `test_resume_command.py`'s `_make_runner` helper to wrap `SessionDB` in `AsyncSessionDB` — without it, the test harness didn't match the facade's contract and every resume test failed with `TypeError: object NoneType can't be used in 'await' expression` |
| `449d11ca1` | Clears session-scoped `/model` overrides on `/resume` (#10702) | Its test (`test_resume_clears_session_model_overrides`) arrived as diff-context noise on `599a6391d`'s cherry-pick; pulled in the actual implementing commit instead of leaving an orphaned failing test |

Plus the 4 target security commits themselves, plus **1 local fix**:

- `7b115d3ad` — the *previous* sync's adaptation commit `bd0e71685` had
  wrapped `_resume_target_allowed`'s `self._session_db.get_session(...)` call
  in `asyncio.to_thread(...)` because `AsyncSessionDB` didn't exist in our
  tree at the time. Once this sync added `AsyncSessionDB`, that wrap started
  awaiting a coroutine object instead of the row dict
  (`AttributeError: 'coroutine' object has no attribute 'get'`). Reverted to
  `await self._session_db.get_session(target_id) or {}`, matching upstream's
  current body exactly.

## Fixes made beyond straight cherry-pick

Conflict resolutions worth flagging (all "combine both sides", never
keep-ours):

| File | Fix |
|---|---|
| `gateway/slash_commands.py` | Titled-session listing (`/resume` with no arg, and numeric `/resume N`) had diverged: our tree's already-merged base IDOR fix filters through the general-purpose `_resume_row_visible` (covers every platform); the incoming commit's version only had an inline Matrix-specific room check (the vulnerability the later IDOR fix closed). Kept `_resume_row_visible`, added the `await` the newly-async `_list_titled_sessions()` now needs. |
| `tests/gateway/test_session_boundary_security_state.py` | Combined our fixture's extra mock config (`get_session`, `resolve_resume_session_id`, `session_store._entries` fallback — needed by the already-merged IDOR guard) with the incoming `AsyncSessionDB(MagicMock())` wrapping convention, addressing mocks via `._db.<method>` per `AsyncSessionDB.__getattr__`'s forwarding contract. |
| `tests/gateway/test_matrix_project_context_isolation.py`, `tests/gateway/test_resume_command.py` | Several trivial "same behavior, different comment/param name" conflicts from parallel upstream commits touching the same test line — took whichever side had the explanatory comment. |

## Self-inflicted issue caught and fixed live (again)

While running a second `uv sync` — this time in a *separate baseline-
comparison worktree* (`/tmp/hermes-main-baseline`, used to diff the probe
branch's full test-suite failures against unmerged `main`) — the global
`/Users/mh/.local/bin/hermes` symlink was hijacked again, exactly as
documented in the parent handoff's gotcha list from the *first* `uv sync`
earlier the same day. Caught immediately via `readlink` before touching
production; relinked to
`/Users/mh/ai/agents/hermes-agent/.venv/bin/hermes`.

**Lesson: this isn't a one-time risk from "the first worktree sync in a
session" — check the symlink after *every* `uv sync` in *any* worktree,
no matter how many times you've already checked it earlier in the same
session.**

## Cleanup Window

- Sync branch `sync/resume-hardening-2026-07-02`: deleted (both local and
  `origin`) immediately after merge — no burn-in hold requested this time.
- Disposable worktrees `/tmp/hermes-resume-probe` and
  `/tmp/hermes-main-baseline`: removed.
- Rollback tag `archive/pre-hermes-update-20260702-120323`: kept for now,
  prune along with other stale `archive/pre-hermes-update-*` tags at the
  next cleanup pass.
- Delete this file once a day of real Telegram traffic has passed with no
  `/resume`-related issues.
