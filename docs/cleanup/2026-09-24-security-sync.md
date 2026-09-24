# 2026-09-24 security-only upstream sync — notes + cleanup checklist

Sync branch: `sync/security-2026-09-24` (off `main` @ `1b6df28b60`).
Rollback tag: `archive/pre-hermes-update-20260924-072731` (local, not pushed).
Upstream state at triage: `reference/main` @ `7de8728cba`, 27,121 commits past
the 2026-07-02 fork point `88bd1c01e1`. Triage window: everything after the
previous security sync's digest point `9ecacd6bf` (2026-07-22).

The `reference` remote was missing (push-disabled `reference` re-added this
session). `scripts/upstream_digest.py` reported 11 security commits; the real
count is ~50 because its filter only matches the `security(...)` type and misses
`fix(security): …`, GHSA and CVE subjects. Triage used
`security|GHSA|CVE` in type/scope/subject instead.

## Why hand ports, not cherry-picks

Of 32 applicable candidates only 2 cherry-picked cleanly. Upstream's
2026-09-02 compaction waves, the 2026-07-29 test prune (46k → 20k tests) and
the SessionState consolidation moved nearly every touched file. Each commit was
ported by behaviour in its own cluster worktree (`~/wt/hsec-{a..e}-*`), with its
upstream tests adapted and checked to fail without the fix.

## Landed (30 commits; upstream SHA in each commit body)

Clean picks: `42626da1ce` DNS-pinned SSRF-safe fetches, `3ae25e0fbd` voice
playback env scrub, `a1e6ea7d71` owner-only shell snapshots, `95a566b1e7`,
`9677495004` (+ test follow-up `6290cd0d59`).

Ported:

- **Redaction** — GitLab token families (`950fe236d0`); background-process
  notifications + completion output + forced user-facing redaction on direct
  sends (`6c7cfd6621`, `c0d204810d`, `84b4fb9eca`); terminal exception results +
  ACP stderr (`72eda946be`); config-file reads (`0997a23e57`, with prerequisite
  `.env` detection `cf755f5c42`/`15d7103aa7`); MCP OAuth log material
  (`aa0beef684`) and reflected XSS in the callback page (`d3fc0cca0f`); exfil
  suffix anchoring in threat patterns + skills guard (prerequisite
  `6b290b81d5`, then `21e52c1fd4`).
- **State / credential stores** — `state.db`/WAL/SHM and quick snapshots
  owner-only (`1916cb249d`); write-deny read-blocked credential stores while
  keeping control files (`auth.json`, `config.yaml`, …) writable
  (`e7cd1848c9`, `1c0d95badb`).
- **Child-process env** — voice/transcription subprocesses get a scrubbed env
  (`24a6fb6448`); case-insensitive credential-name matching (`b534f4b8c8`);
  profile-scoped passthrough incl. shared-snapshot save/restore (`7138b9587a`,
  inert unless `gateway.multiplex_profiles`).
- **Shared shell snapshot** — no `HERMES_SESSION_*` leakage across concurrent
  sessions (`f2e32ceead`), multiline-env injection (`9677495004`), `mktemp`
  temp paths (`911d380296`). The last one fixes a live race here: macOS
  `/bin/bash` 3.2 expands `$BASHPID` to empty, so every concurrent writer
  shared one temp file (`test_concurrent_writes_never_tear_the_snapshot`
  failed on unmodified `main`). **Merge this chain whole** — `f197425653`
  alone breaks export persistence until `7b5c7f4a28`.
- **Approvals / guards** — webhook-type (unattended) platforms deny flagged
  commands immediately instead of timing out (`ef71f2cad8`; new
  `approvals.unattended_mode`, default `deny`; Telegram/Signal/api_server
  unaffected); Tirith emoji variation-selector false positive (`fbc5df3c41`).
- **GitSpawn RCE, GHSA-7x36-8jrh-v4pw** (`f6234d00c5`) — new
  `noninteractive_git_env()` / `harden_git_argv()` in
  `hermes_cli/_subprocess_compat.py`, routed through every git call against a
  possibly-untrusted repo: `agent/coding_context`, `agent/context_references`,
  `tui_gateway` probes, `web_server` branch probe, `web_git`, `cli.py -w`
  worktrees, `kanban_db` worktrees, honcho repo probe. Real booby-trapped
  `.git/config` test in `tests/security/test_gitspawn_config_injection.py`.
- **SSRF** — remaining preflighted fetches incl. Telegram `send_image` fallback
  (`0cd4afeafd`).
- **Supply chain** — removed `optional-skills/creative/blender-mcp` after the
  2026-08-08 upstream repo hijack (`bdbdfead04`; not installed in
  `~/.hermes/skills`).

## Skipped

Not applicable (vulnerable code absent in our tree): `3966e5de94`
(async_delegation has no state.db writer), `56d2438a45` (no
`config_home.py`; our `ensure_hermes_home` never skipped), `5921ba8c06` +
`6b3a7af73d` (our lifecycle guard is a raw-text regex — wrapper prefixes are
already caught; verified), `9345c67854` webhook GHSA (per-route toolsets
feature absent), `940c610994` (blocklist defined in `local.py` itself),
`c44b423e52` (delegation marker absent; cron marker only read in Python).

Out of scope for this deployment: Slack SSRF `2e08b778ab`, photon ×2,
OpenViking, Windows ×2, desktop Electron/TCC, Docker snapshot isolation
`fc61608a17`, multiplex dotenv `1aa62ceb45`, profile isolation ×2
(`0c74353c86`, `e70db09f51`), profile exports into images `c26f75baab`,
pet/image-provider SSRF `3933fdf63b`, hindsight, bundled-skill lint, plugin
catalog repins, OSV cache perf, new scanning features, npm `nanoid`, tornado
and httplib2 dependency bumps (no dependency changes this sync; no `uv sync`
run anywhere).

## Behaviour changes to expect after deploy

- Live `~/.hermes/state.db{,-wal,-shm}` tighten 0644 → 0600 on first
  `SessionDB` open. Everything reading it runs as `mh`.
- Webhook-triggered flagged commands are denied immediately
  (`approvals.unattended_mode: approve` restores the old auto-approve).
- Git run by the agent against repos ignores global/system git config: the
  `web_git` review pane loses git identity/keychain helper/pre-commit hooks,
  `-w` worktree fetch of a private HTTPS remote falls back to local HEAD, and
  `safe.directory` is ignored (upstream's carry-over `01a3206e90` not ported).
- `/snapshot create` can now raise if it cannot set permissions (the
  `cli_commands_mixin` handler doesn't catch).

## Known gaps left open

- ffmpeg conversions in the voice paths still inherit the full env (same as
  upstream).
- Lifecycle guard doesn't follow referenced scripts (`bash ~/restart.sh`,
  `launchctl submit`) — needs the larger upstream rework.
- Direct `sqlite3.connect` on `state.db` outside `SessionDB` (module helpers,
  `doctor.py`) not hardened (same as upstream).

## Verification

See the PR description for the final numbers (conformance smoke, routing
classifier gate, i18n parity, full-suite diff vs a fresh `main` baseline).

## Cleanup checklist (after 1+ day of stable production)

- [ ] remove worktrees `~/wt/hermes-security-2026-09-24`, `~/wt/hsec-*`
- [ ] delete branches `sync/security-2026-09-24` and
      `sync/security-2026-09-24-{a-redaction,b-filesafety,c-envscrub,d-guards,e-gitspawn}`
      (local + origin)
- [ ] prune older `archive/pre-hermes-update-*` tags, keep newest known-good
- [ ] delete this file once executed
