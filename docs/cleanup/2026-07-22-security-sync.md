# 2026-07-22 security-only upstream sync — notes + cleanup checklist

Sync branch: `sync/security-2026-07-22` (off `main` @ `e14baa05ca`).
Rollback tag: `archive/pre-hermes-update-20260722-103422`.
Upstream state at digest time: `reference/main` @ `9ecacd6bf`, 2,612 commits
ahead of `main` (7 security, 227 touching LOCAL_DELTA_PATHS, 2,378 routine).

## What landed (6 of 7 flagged security commits, + 4 prerequisites)

Clean cherry-picks (`-x`, no conflicts):

- `2abe11a7f` security(ci): untrusted refs via env, not run: interpolation
- `3817ff180` security(raft): body-size limit on chunked requests
- `8986981df` security(gateway): client_max_size on 3 uncapped aiohttp servers
- `a061788d4` security(providers): strip credential headers on cross-host
  redirects in fetch_models

Dashboard chain — our tree had **no** `_is_sensitive_filename` guard at all
(the #57505 chain never landed here), so the flagged commit needed its three
prerequisites first; all four then applied clean:

- `bc55c201c` → `1bcc52c14` → `7485fe060` (block/.pattern/.case-insensitive
  `.env` guard) → `43ec69cef` security(dashboard): widen guard past `.env`
  (auth.json, OAuth stores, webhook HMAC, bws_cache.json, .envrc).
  Verified wired into list/read/download endpoints post-pick.

Vision — `9ae17b8ac` **adapted, not cherry-picked**: upstream's fix lands in
`tools/image_source.py`, which only exists after the `316e77517` resolver
refactor (~750 lines, 5 commits) that we don't have. Instead:

- `c1826e269` cherry-picked (adds `agent.file_safety.raise_if_read_blocked`
  chokepoint + image-gen call sites). 4 conflicts, all trivial adjacent-drift
  with an empty "ours" side — resolved by taking upstream's added lines.
- Local commit `0940b798b` applies `raise_if_read_blocked()` to our three
  local-file branches in `tools/vision_tools.py` (vision_analyze_tool,
  `_vision_analyze_native`, video_analyze_tool); upstream's tests ported
  verbatim and pass. Re-do note for the future full merge: when the
  `image_source.py` refactor arrives, upstream's `9ae17b8ac` supersedes the
  vision_tools.py half of `0940b798b` — expect a small conflict there and
  take upstream's shape.

## Deferred (1)

- `16332af60` security(gateway): anchor api_server MEDIA tag resolution —
  **not applicable**: the vulnerable feature (`2068754d6`, MEDIA data-URL
  inlining in `gateway/platforms/api_server.py`) is not in our tree (no
  MEDIA handling in that file at all). Comes free with the full merge;
  nothing to fix until then.

## Verification

- 290/290 tests across all touched surfaces (vision, video, image_gen ×3,
  web_server files, aiohttp caps, raft adapter, fetch_models).
- Clean byte-compile of gateway/hermes_cli/tools/plugins/providers/agent.
- `hermes_update_guard.py --pre` PASS before branching.
- Conformance smoke: all config/launchagent checks PASS; embedded post-guard
  fails only on `health-monitor-services` — **pre-existing, unrelated**: the
  monitor's Open Brain probe has 401'd since 2026-07-11 and its Telegram
  alerting is dead (`missing TELEGRAM_BOT_TOKEN or alert chat target`).
  Real gateway→Open Brain traffic confirmed healthy (query_brain success
  2026-07-21 in agent.log). Tracked as its own task; the post-guard will
  keep failing on this check until that's fixed.
- No `locales/` touched → no i18n parity work this sync.
- No `uv sync` run anywhere this session (no dependency changes in any
  picked commit → no lockfile impact, no symlink-hijack exposure).

## Cleanup checklist (execute after 1+ day of stable production)

- [ ] delete branch `sync/security-2026-07-22` (local + origin) after merge
- [ ] prune older `archive/pre-hermes-update-*` tags, keep newest known-good
- [ ] delete this file once executed
- [ ] git stash "npm-churn package-lock peer-flags (pre security-sync
      2026-07-22)" on `fix/session-capture-signal-gate` — drop it if the
      churn is unwanted, or pop+commit if intentional
