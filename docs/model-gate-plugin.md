# model-gate: routing stall-shaped work away from the local model

Status: LIVE (user plugin, 2026-07-22). Code: `~/.hermes/plugins/model-gate/`
(its own git repo — NOT in this repository; this doc is the repo-side runbook
because the plugin leans on gateway seams documented here).

## Why it exists

2026-07-22: "check the strava rides and tell me how many kms were ridden on
the Caracals" sent `qwen3.6:35b-mlx` into a 16-minute tool-thrash (11 API
calls, duplicate tool calls, truncated 80K `strava_activities` dump) ending in
`/stop` — while a frontier model answered the same question in 4 API calls /
~2.5 minutes. Multi-hop analytical tasks (brain fact + external dataset +
arithmetic) are a known local-model failure mode; offline routing parity does
not predict multi-turn planning quality.

## What it does

When the session's active model is **local**, three deterministic detectors
(no LLM on any critical path) route work to a model choice:

| # | Detector | Trigger | UX |
|---|----------|---------|----|
| 1 | Escalation-shaped text | message has ≥1 aggregation term (`how many`, `total`, `since`, `compare`, `kms`, …) AND ≥1 dataset term (`strava`, `rides`, `finance`, `spend`, `thoughts`, …) | message **held**; tier buttons |
| 2 | Big session | session `last_prompt_tokens` ≥ 55K (local prefill knee) | message **held**; tier buttons |
| 3 | Mid-turn thrash | running local turn reaches ≥6 tool calls with ≥2 duplicate (tool, args) signatures | 🌀 advisory with **stop & rerun** buttons alongside the stuck turn |

Buttons (2026-07-22 lineup):

    [🔴 Highest (gpt-5.6-sol)]  [🟠 Workhorse (gpt-5.6-terra)]  [🔵 Local]

- A tier press re-injects `/model <id> --provider openai-codex --session`
  through the gateway's own switch path, then (re-)dispatches the held
  message. **`--provider` is mandatory**: without it `/model` keeps the
  current provider, so from a local session the id resolves against the
  Ollama endpoint and 404s (observed live).
- Thrash buttons re-inject `/stop` first, then switch, then replay the
  session's last inbound message.
- Any gate press starts a 10-minute per-session cooldown; held messages
  expire after 15 minutes (an expired press echoes the original text back).
- `/tier` in any chat shows the same three buttons as a **manual switcher**
  (no message run; 🔵 clears the session override + evicts the cached agent,
  reverting to the config default). Intercepted in `pre_gateway_dispatch`
  because the thin plugin-command contract (`fn(raw_args)`) has no
  event/source to route buttons with; a stub `/tier` command is registered
  for menu discoverability.

All failure paths fail open (normal dispatch / silent skip). Never gates
slash commands, non-local sessions, internal events, or its own re-dispatches.

## ★ Updating the tiers when OpenAI's lineup changes

**Two files, one restart.** The tier ids are pinned, not discovered — when
OpenAI ships a new top/second tier (or renames), do this:

1. **`~/.hermes/plugins/model-gate/__init__.py`** — edit the `TIERS` map:

   ```python
   TIERS = {
       "mgh": ("🔴 Highest", "gpt-5.6-sol"),     # ← new top-tier id
       "mgw": ("🟠 Workhorse", "gpt-5.6-terra"),  # ← new second-tier id
   }
   ```

   The gate buttons, thrash buttons, and `/tier` all read this one map.
   Gotchas:
   - Use **explicit ids**, not family aliases — e.g. bare `gpt-5.6` routes
     to Sol; if you ever want the second tier you must say `gpt-5.6-terra`.
   - This repo's static model catalog (`hermes_cli/provider_catalog.py` /
     `models.py`) usually lags new launches. That's fine: the
     `openai-codex` switch path **soft-accepts unlisted ids**
     (`hermes_cli/model_switch.py`, "soft-accepted" around the
     openai-codex branch) — a "not found in model listing" warning on
     switch is expected and harmless if the backend serves the model.
   - Verify the id exists on the Codex backend before trusting it for real
     work: press the tier button and confirm the switch reply says
     `Provider: OpenAI Codex` and the next turn's API calls succeed
     (watch `~/.hermes/logs/agent.log` for
     `API call #1: model=<id> provider=openai-codex`). A wrong id fails
     fast with HTTP 404 after 3 retries.

2. **`~/.hermes/plugins/model-badge/__init__.py`** — add the new top-tier id
   to `TOP_MODEL_IDS` so replies badge 🔴 (top cloud) instead of 🟡 (lesser).
   Keep old ids in the set; it's a badge classifier, not a router.

3. **Restart the gateway** (plugins load at startup):

   ```bash
   launchctl kickstart -k gui/501/ai.hermes.gateway
   ```

   Verify in `~/.hermes/logs/agent.log`:

   ```
   model-gate plugin registered (tiers={'mgh': '<new-top>', 'mgw': '<new-second>'}, ...)
   ```

   NOTE: the restart drops model-gate's in-memory pending registry — any
   unpressed gate buttons go stale ("Expired — resend").

4. Commit in each plugin repo (they are separate git repos under
   `~/.hermes/plugins/`).

## Which OpenAI subscription pays for this

The `openai-codex` provider reuses the Codex CLI's OAuth
(`~/.codex/auth.json`, `auth_mode` = chatgpt; **no API key**). As of
2026-07-22 the token claims show `chatgpt_plan_type: team` — i.e. the
**ChatGPT Team subscription**, billed against its Codex usage limits, not
API pay-per-token. ToS-gray, same status as the desktop picker /
`/model` usage documented in the project memory. If switches start
returning 401, re-run `codex login`.

## Gateway seams the plugin depends on (this repo)

| Seam | Where | Used for |
|------|-------|----------|
| `pre_gateway_dispatch` hook | `gateway/run.py` (`_handle_message`), contract in `hermes_cli/plugins.py` `VALID_HOOKS` | holding/intercepting messages (`{"action": "skip"}`), `/tier` intercept |
| `post_tool_call` hook | emitted from `model_tools.py` `_emit_post_tool_call_hook` | thrash detection (may fire on an agent worker thread → plugin uses `run_coroutine_threadsafe` onto the gateway loop) |
| Generic message actions (`act:` seam) | `gateway/platforms/actions.py`; Telegram render/dispatch in `plugins/platforms/telegram/adapter.py` | all inline buttons; action ids `mgh`/`mgw`/`mgl`/`mth`/`mtw`; 64-byte callback budget |
| `/model` command | `gateway/slash_commands.py` `_handle_model_command` | tier switches via synthetic re-injected events (battle-tested provider resolution / cache eviction) |
| Session store internals | `gateway/session.py` (`SessionEntry.last_prompt_tokens`, `SessionStore._entries`) | token gate reads; thrash detector's `session_id` → origin reverse-map |
| `_session_model_overrides` / `_evict_cached_agent` | `gateway/run.py` | local-model detection; `/tier` → Local revert |

Renaming/refactoring any of these will silently degrade the plugin (it fails
open) — grep `~/.hermes/plugins/model-gate/__init__.py` after gateway
refactors touching the table above.

## Known limitations

- Pending registry is in-memory; gateway restarts invalidate outstanding
  buttons.
- Thrash rerun replays the session's **last** inbound message.
- Topic-group thread routing of prompts not implemented (DMs primary).
- Related repo bugs found during the incident, tracked separately:
  `tools/tool_result_storage` inline-truncates oversized tool results when
  "no sandbox write" is available (starved the model of the 80K Strava
  dump), and `tools/strava_tool.py` has no connect timeout (a stuck TCP
  SYN to Supabase stalled a turn for 374s).
