# Hermes Update Runbook

This runbook is the required update path for the local production Hermes agent.
It is written for Codex, Claude, and humans. The goal is to make updates
boring: verify the current production state, preserve local Open Brain deltas,
apply the update, run smoke checks, and leave a rollback label.

## Prompt For Claude Or Codex

When starting the next Hermes update cycle, open the agent in
`/Users/mh/ai/agents/hermes-agent` and give it this instruction:

```text
Read docs/hermes-update-runbook.md and follow it exactly for this Hermes update.
Run scripts/hermes_update_guard.py --pre before changing anything.
Do not proceed past any blocking failure.
After the update/restart, run scripts/hermes_update_guard.py --post.
Use scripts/hermes_update_guard.py --post --live-smoke if network access is available.
Do not print secrets.
If you hit a blocker, stop and report the failing check, suspected cause, and safest next step.
```

## Current Production Contract

- Production repo: `/Users/mh/ai/agents/hermes-agent`
- Expected production branch: `main`
- Runtime home: `/Users/mh/.hermes`
- Gateway launch label: `ai.hermes.gateway`
- Hermes is the front door; Open Brain is the durable memory/retrieval backend.
- The hosted Open Brain MCP must expose `query_brain`, `analyze_brain_query`,
  `capture_thought`, feedback, commitments, drift, graph/timeline, and related
  tools.

## Agent Rule

Before any update, run:

```bash
cd /Users/mh/ai/agents/hermes-agent
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --pre
```

If the guard fails, stop and fix the blocker before pulling, merging, rebasing,
or restarting production. Use `--allow-dirty` only when auditing an already
dirty tree, not as permission to update through unreviewed changes.

After any update and gateway restart, run:

```bash
cd /Users/mh/ai/agents/hermes-agent
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post
```

For the focused Hermes/Open Brain conformance suite, run:

```bash
cd /Users/mh/ai/agents/hermes-agent
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/openbrain_conformance_smoke.py
```

This box runs 24/7 with network access, so `--live-smoke` is the default
post-update expectation. Only drop it when running offline or in a sandbox that
cannot reach Open Brain:

```bash
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post --live-smoke
```

For machine-readable output:

```bash
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post --json
```

## Pre-Update Checklist

1. Run the guard with `--pre`.
2. Confirm `main` is the production branch and matches `origin/main`.
3. Confirm the working tree is clean.
4. Confirm the local Hermes/Open Brain integration surface is intact. The guard
   checks this for you: `LOCAL_DELTA_PATHS` (files present) and
   `LOCAL_DELTA_PATTERNS` (the load-bearing symbol still lives in each file) in
   `scripts/hermes_update_guard.py` are the canonical list. Edit the script, not
   this doc, when the delta surface changes. At a high level it covers Open Brain
   routing/config and memory provider, the query_brain formatter plugin, session
   and Telegram feedback hooks, `/ob` capture, the health monitor, and the
   LaunchAgents.
5. Confirm `~/.hermes/config.yaml` loads and `mcp_servers.open_brain` expands
   its auth header. Never print secret values.
6. Confirm SSL/CA inputs are valid. A partial venv update or stale CA env var
   must be fixed before provider calls.
7. Tag the current production state before risky work:

```bash
git tag archive/pre-hermes-update-$(date +%Y%m%d-%H%M%S) HEAD
```

Push the tag only when you intend to keep it as shared rollback state:

```bash
git push origin <tag-name>
```

## Update Rules

- Prefer small upstream syncs. Do not let the fork drift by thousands of commits.
- Keep `main` as the production branch unless a handoff explicitly says
  otherwise.
- Do not resolve upstream conflicts with blanket "keep ours." Check whether
  upstream moved code into a new mixin/module and reapply the local delta there.
- Treat these local deltas as load-bearing:
  - Open Brain MCP routing and config
  - `/ob` manual capture
  - session-boundary capture (now provider-driven via the generic
    `gateway/boundary_capture.py`; needs the `openbrain` memory provider
    available — `OPENBRAIN_MCP_KEY` in `~/.hermes/.env`)
  - Open Brain memory provider
  - query_brain formatter plugin
  - Telegram Open Brain feedback buttons
  - health monitor and LaunchAgents
- **`plugins.enabled` must list `openbrain-commands`** (Phase 5c.3). The
  read-only commands `/brief`, `/digest`, `/stale`, `/finance-check` now live in
  the `plugins/openbrain-commands/` standalone plugin, which loads only when
  enabled in `~/.hermes/config.yaml`. If the entry is missing after an update,
  those four commands silently disappear. The post-update smoke must confirm
  `/brief` still responds. Automatable pre-restart rehearsal (catches the
  missing-entry case before it reaches the live gateway):
  `.venv/bin/python -m pytest tests/plugins/test_openbrain_commands_plugin.py`
  exercises the real `PluginManager` and asserts the four commands load only
  when the plugin is enabled.
- The broader local conformance wrapper is
  `.venv/bin/python scripts/openbrain_conformance_smoke.py`. Use `--live-smoke`
  when network access is available, and `--allow-dirty` only when auditing an
  intentional local worktree change.
- Keep the Open Brain hosted MCP as canonical. Do not point Hermes at the local
  experimental Open Brain MCP prototype.

## Update Steps

The happy path. Do not skip the guard at the start or the smoke at the end.

Remotes:

- `origin` → `mtp-44/hermes-agent` — your fork, the production push target.
- `reference` → `NousResearch/hermes-agent` — upstream mirror, push disabled.
  This is where new upstream work comes from.

1. Run the pre-update guard and the pre-update checklist above. Do not proceed
   past a blocking failure.
2. Fetch upstream and your fork:

   ```bash
   git fetch reference
   git fetch origin
   ```

3. Create a dated sync branch off production and bring upstream in there, never
   directly on `main`:

   ```bash
   git switch main
   git switch -c sync/unified-retrieval-main-$(date +%Y-%m-%d)
   git merge reference/main   # or cherry-pick/rebase a specific upstream range
   ```

4. Resolve conflicts per the Update Rules above. For every load-bearing local
   delta, confirm the logic survived where upstream moved it — do not blanket
   "keep ours."
5. Reinstall dependencies into the project venv (lockfile-driven):

   ```bash
   uv sync
   ```

   If provider calls fail afterward with SSL/CA errors, repair per
   `docs/rca-ssl-cacert-post-git-pull.md`:

   ```bash
   /Users/mh/ai/agents/hermes-agent/.venv/bin/python -m pip install --force-reinstall certifi openai httpx
   ```

6. Open a PR from the sync branch into `main` and let CI pass before merging
   (this mirrors PR #1). Merge into `main` only on green.
7. Tag the pre-restart production state if you have not already (see
   Pre-Update Checklist step 7).
8. Restart the gateway from the freshly merged `main`:

   ```bash
   HERMES_HOME=/Users/mh/.hermes hermes gateway restart
   ```

9. Run the Post-Update Smoke below.

## Post-Update Smoke

After dependencies, merge/replay, and restart:

1. Run `scripts/hermes_update_guard.py --post`.
   For the full local adapter smoke, run
   `scripts/openbrain_conformance_smoke.py --phase post`; add `--live-smoke`
   when Open Brain network access is available.
2. Confirm `health-monitor.jsonl` has fresh healthy rows for:
   - `ollama`
   - `gateway`
   - `telegram`
   - `openbrain`
   - `disk`
   - `memory`
3. Confirm Open Brain exposes at least the tool floor
   (`EXPECTED_OPENBRAIN_TOOL_FLOOR` in the guard, currently 30). The live count
   is expected to be at or above the floor and to grow over time, so a higher
   number is fine — only a count below the floor is a regression.
4. Send a real Telegram smoke message and confirm the gateway logs an inbound
   message plus a response.
5. Ask a recall question that should call `mcp_open_brain_query_brain`.
6. Ask an analytical question that should call `mcp_open_brain_analyze_brain_query`.
7. Trigger `/ob` or a safe session-boundary capture only when you are prepared
   to write to Open Brain.
8. Send `/brief` and confirm it responds (read-only). A "no such command" /
   silent reply means the `openbrain-commands` plugin did not load — check it is
   in `plugins.enabled` (Phase 5c.3 read-only commands live in that plugin).
9. Check `~/.hermes/logs/gateway.log`, `agent.log`, and `health-monitor.jsonl`.
   Prefer structured health JSONL over stale stderr in `health-monitor.log`.

## Rollback

If the post-update guard fails or Telegram/Open Brain behavior regresses:

1. Stop further cleanup.
2. Preserve logs and the failing SHA.
3. Check out the pre-update tag or recorded SHA.
4. Restart the gateway.
5. Run the post-update guard again.

Example:

```bash
cd /Users/mh/ai/agents/hermes-agent
git checkout <known-good-sha-or-tag>
HERMES_HOME=/Users/mh/.hermes hermes gateway restart
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post
```

Do not force-push `main` unless the explicit goal is to undo the public sync.

## Cleanup Window

Only after production has passed the post-update guard and a real Telegram smoke
for at least one day:

- delete stale `sync/*` update branches
- delete interrupted `claude/*` worktrees/branches after confirming no session
  needs them
- keep the most recent known-good `archive/pre-hermes-update-*` tag and
  `backup/pre-pull-main-2026-05-01`; prune older `archive/pre-hermes-update-*`
  tags once a newer update has proven stable, so they do not accumulate:

  ```bash
  git tag --list 'archive/pre-hermes-update-*' --sort=-creatordate
  # keep the newest, delete the rest locally and on origin as needed:
  # git tag -d <old-tag>
  # git push origin --delete <old-tag>
  ```

Per-sync cleanup checklists (with that update's specific SHAs, branches, and
worktrees) go in `docs/cleanup/<date>-<slug>.md` and are deleted once executed.
See `docs/cleanup/2026-06-19-after-upstream-sync.md` for the template.

Cleanup is not part of the update itself. It is a separate, lower-risk task.
