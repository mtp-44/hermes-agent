# Updating Hermes (quick reference)

Short, exact, in-order. This is the happy path for updating the local
**production** Hermes agent without breaking the Open Brain connection.

For the full version — rationale, load-bearing deltas, rollback, cleanup — see
[`docs/hermes-update-runbook.md`](docs/hermes-update-runbook.md). When the two
disagree, the runbook wins.

## Facts

- Production repo: `/Users/mh/ai/agents/hermes-agent`
- Production branch: `main` (must match `origin/main`)
- Runtime home: `/Users/mh/.hermes`
- Project venv: `/Users/mh/ai/agents/hermes-agent/.venv`
- Remotes: `origin` = your fork (push target); `reference` =
  `NousResearch/hermes-agent` upstream (pull source, push disabled).

Hermes is the front door; Open Brain is a **separate hosted MCP server** Hermes
connects to. An update never touches Open Brain's data — the only thing that can
break the link is a Hermes change that drops a load-bearing local delta or the
`mcp_servers.open_brain` config. The guard + post-smoke below exist to catch
exactly that.

## Can I update at any time?

Yes. There is no maintenance window. The connection is the MCP contract, not
Hermes internals, so a pull + gateway restart simply reconnects. The rule is not
*when* but *how*: never skip step 1 (pre-guard) or step 8 (post-smoke).

## Steps

```bash
cd /Users/mh/ai/agents/hermes-agent
VENV=/Users/mh/ai/agents/hermes-agent/.venv/bin/python
```

1. **Pre-flight guard.** Stop on any blocking failure — do not pull through it.

   ```bash
   $VENV scripts/hermes_update_guard.py --pre
   ```

2. **Tag a rollback point.**

   ```bash
   git tag archive/pre-hermes-update-$(date +%Y%m%d-%H%M%S) HEAD
   ```

3. **Fetch both remotes.**

   ```bash
   git fetch reference
   git fetch origin
   ```

4. **Sync on a dated branch, never directly on `main`.**

   ```bash
   git switch main
   git switch -c sync/$(date +%Y-%m-%d)
   git merge reference/main   # or cherry-pick/rebase a specific upstream range
   ```

   Resolve conflicts per the runbook's "Update Rules" — for every load-bearing
   local delta (Open Brain routing/config, memory provider, query_brain
   formatter plugin, `/ob` capture, session + Telegram feedback hooks, health
   monitor, LaunchAgents, and `openbrain-commands` in `plugins.enabled`), confirm
   the logic survived where upstream moved it. **Never blanket "keep ours."**

5. **Reinstall deps from the lockfile.**

   ```bash
   uv sync
   ```

   If provider calls later fail with SSL/CA errors, repair per
   [`docs/rca-ssl-cacert-post-git-pull.md`](docs/rca-ssl-cacert-post-git-pull.md):

   ```bash
   $VENV -m pip install --force-reinstall certifi openai httpx
   ```

6. **PR the sync branch into `main`; merge only on green CI.** Mirrors PR #1.

7. **Restart the gateway from the merged `main`.**

   ```bash
   HERMES_HOME=/Users/mh/.hermes hermes gateway restart
   ```

8. **Post-update smoke.** This box is online 24/7, so `--live-smoke` is the
   default expectation (drop it only when offline).

   ```bash
   $VENV scripts/hermes_update_guard.py --post --live-smoke
   $VENV scripts/openbrain_conformance_smoke.py --phase post --live-smoke
   ```

   Then confirm by hand:
   - `health-monitor.jsonl` has fresh healthy rows for `ollama`, `gateway`,
     `telegram`, `openbrain`, `disk`, `memory`.
   - Open Brain exposes at least the tool floor (currently 30; higher is fine).
   - A real Telegram message gets a reply.
   - A recall question calls `query_brain`; an analytical one calls
     `analyze_brain_query`.
   - `/brief` responds (proves the `openbrain-commands` plugin loaded).

## If it goes wrong

Roll back to the tag from step 2, restart, re-run the post-guard:

```bash
git checkout <archive/pre-hermes-update-… tag or known-good SHA>
HERMES_HOME=/Users/mh/.hermes hermes gateway restart
$VENV scripts/hermes_update_guard.py --post
```

Do not force-push `main` unless the explicit goal is to undo a public sync.
Cleanup (deleting `sync/*` branches, pruning old tags) is a separate, later task
— see the runbook's "Cleanup Window".
