# 2026-09-24 security triage: the 85 approval/redaction commits

Input: the 85 upstream commits the widened `scripts/upstream_digest.py`
(HA-0007) surfaced — 21 dangerous-command detection, 25 redaction,
39 approval flow. Verdicts come from reading each diff against HEAD and, where
it could be shown, running upstream's own test input through HEAD's
`tools.approval` / `agent.redact` functions (fake secrets only). Only a handful
apply with `git apply --check`; nearly every port is by behaviour.

**Result: 51 not taken** (recorded with reasons in
`scripts/upstream_digest_triaged.txt`), **34 to port** (still listed by the
digest), **plus four local gaps upstream has too**.

## Verified against HEAD on 2026-09-24

Re-run by hand after triage, with the live `~/.hermes/config.yaml`:

- `approvals.deny` globs are bypassed by `launchctl  load x` (two spaces),
  a tab, `env -S 'launchctl\_unload x'` and `launchctl 'unload' x`: none
  matches, and none is a DANGEROUS pattern, so each runs unprompted.
- `curl … | zsh` and `dash -c id` are not detected (`| bash` is).
- The redactor stalls: `'token'*1000 + ' = x'` (5 KB) takes 21 s,
  `'a'*20000 + '=value'` 7.7 s. `redact_sensitive_text` runs on every log
  line (`RedactingFormatter`) and every outbound message
  (`gateway/run.py:328`), so crafted text can stall the gateway.
- Unmasked (fake values): `MCP_ACCESS_KEY=<v>` (a real variable in
  `~/.hermes/.env`), `  api_key: <v>` through `read_file`
  (`file_read=True` skips the YAML pass), a `Bearer` token inside a Python
  dict repr, and `x-brain-key: <v>`, the Open Brain key's own line in
  `config.yaml`.
- `~/.hermes/config.yaml` is write-refused to the file tools (sensitive path),
  but `~/Library/LaunchAgents/*.plist` is writable without a prompt, and
  launchd loads such a plist at next login with no `launchctl` call.

## Port batches, in order

### P1 — high

> **Landed 2026-09-24 as `sec/p1-2026-09-24`:
> [`HA-0009`](../decisions/0009-security-p1-port-2026-09-24.md).** Items 1–4
> below are done except the partials listed there. The config mitigations
> further down went live the same day.

1. **Redactor ReDoS.** `fe0cfdf99c`, with its parents outside the list
   `13ad903a3c` (possessive rewrite) and `28524adb0e` (keyword pre-gate):
   replace `_CFG_DOTTED_RE` and make `_YAML_ASSIGN_RE` possessive (Python
   3.11+, the venv has 3.11.15). Self-contained.
2. **Redaction leaks.**
   - Terminal classification: `8ab9e79d0a` (applies cleanly; quoted
     `$HERMES_HOME` reads, quote-aware `_command_segments`) and `f5907fd052`
     (config backups; `~/.hermes` holds 17 `config.yaml` copies).
   - Env-name suffixes: `8563fe3435` part (a), needing `b41eee450b`'s
     word-boundary helpers and `ba0f5839d4` for the lowercase regex. Part (b),
     the `process(action=list)` raw preview, stands alone.
   - Secret-file reads: `d205cef418` (`secret_file` flag at the three
     `file_tools.py` call sites), using the linear line-number prefix from
     `979576d938` and leaving that commit's rc exemption out.
   - Python repr chain: `47668280e7` → `92d4c0233e` → `5280dec0ee`.
   - **Local:** add `x-brain-key` (a bare `-key` header name) to the
     header/YAML secret names. Upstream misses it too.
3. **Detection.**
   - `4a9902d513`: zsh/ksh/dash in every pipe-to-shell and `-c` rule,
     including `skills_guard.py`. Regex only.
   - Deny-rule matching: `58faa10134` → `50617d1c75` → `6178e9f4ee`
     (whitespace collapse, `env -S` split). Quoted verbs still bypass
     upstream, so the robust fix is the widened config globs below.
   - `679e07a074` (outside the list; launchctl label-before-verb bypass,
     reachable in HEAD) with its follow-up `ea870b4d3e`.
4. **Local: bind approval taps to their prompt.** A Telegram button carries
   only an id mapped to the session (`telegram/adapter.py:4503,5193`), and
   `resolve_gateway_approval` resolves the session's *oldest* pending entry
   (`tools/approval.py`, FIFO). A late tap on an expired prompt, or a tap on
   the second of two parallel prompts, approves a different command from the
   one on screen. PWA `approval.respond` is also session-FIFO
   (`tui_gateway/server.py`). Upstream still has this. Fix: carry a request id
   in the approval data and resolve by id.

### P2 — low

> **Landed 2026-09-24 as `sec/p2-2026-09-24`:
> [`HA-0010`](../decisions/0010-security-p2-port-2026-09-24.md).**
> `b90dbac1d6` was replaced by a local behaviour subset. The partials are
> listed in the ADR.

- **Approval state:** `7876d183c9` + `9b06d3d081` (a string
  `command_allowlist` becomes per-character globs, so `*` allows everything;
  hand-removed entries are resurrected on save). `2afb405337` (lock-commit
  half only) with `a31a31826c`'s stale-tap half (resolve first, render
  "expired"). `e37a0321eb` (move `HERMES_EXEC_ASK=1` into `start_gateway()`;
  importing `gateway.run` from the CLI locks the CLI out of approvals).
- **Scope rendering (UX, the HA-0007 known gap):** `a3297bd232` +
  `02d8cbadec` (Telegram part) + `165d1849e2`: pass
  `allow_permanent`/`allow_session` through `gateway/run.py` so
  protected-file prompts show Once/Deny only.
- **Detection regexes:** `85ce25687e` + `ce0b10cb21` (package uninstalls),
  `6437701228` (docker daemon redirects; applies cleanly), `6ae1fab336`
  (deno eval).
- **Nested `$(` slowdown (found during P1): fixed locally, PR
  `fix/detector-nested-subst`.** The scanner is memoised and the
  literal-substitution rewrite only tokenises literal runs. 400 levels of
  `$(` went from 16.5 s to about 0.1 s, `"$(e" * 400` from 7 s and 3 KB from
  about 100 s. Word reading is still quadratic in the number of nested
  command starts (a 9 KB pathological string still takes about 20 s), which
  is the parser rework below.
- **Detector parser:** `4eff83cdec` (quadratic variant loop; 33 KB quoted
  heredoc = 15 s) with `e383c28d2f` (faithful variant). `b90dbac1d6`
  (execution-bearing options, ~570 lines, pulls in `d41f621071`,
  `98bf8b2073`, `d127fb2197`): prefer a narrow regex widening instead.
- **Redaction chains:** control-char splits `8563fe3435`(c) → `aecb9ca894` →
  `9377c5a539`; dotted `sk-` `7b57cda6d9` (outside the list) → `c2aa2ff25f` →
  `aebc71d78c`.

## Config-only mitigations (no code)

Tested against the live matcher on 2026-09-24:

- Rewrite `approvals.deny` as `*launchctl*<verb>*` for load, unload, bootstrap,
  bootout, kickstart, remove, disable, enable, **kill, stop** (the last two
  are new), plus `*git*clean*-*f*`, `*git*reset*--hard*`,
  `*hermes*config*set*approvals*`, `*hermes*config*set*command_allowlist*` and
  `*hermes*config*set*security*`. These block every bypass above, including
  quoted verbs and `$L` label variables that upstream's fix misses. They still
  allow `launchctl list/print`, `git status`, `git clean -n` and
  `hermes config show`. Cost: `launchctl list | grep load` is also blocked.
- Add `"*.plist"` to `security.protected_instruction_extra_patterns`, so file
  tool writes to any plist need one-operation approval.

## Not taken, notable

- The "cancelled vs denied" attribution chain (`aac74be2f1` … `2dfb795cb7`)
  is fail-closed in HEAD. **Porting `aac74be2f1` alone is a hazard:** HEAD's CLI
  tail blocks only on `"deny"`, so a new `"timeout"` return would approve.
  Port it whole or not at all.
- `c5e841ab0e` would cut Telegram, Signal and PWA approval waits from 300 s
  to `approvals.timeout` (60). Upstream reversed that later.
- `approvals.timeout: 60` governs only the CLI; gateway waits use
  `gateway_timeout` (default 300).
