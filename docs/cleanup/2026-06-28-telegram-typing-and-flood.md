# 2026-06-28 — Telegram stuck-typing fix + flood-control tuning

Two related fixes after a live report of MyHermes showing a perpetual "…typing"
bubble in Telegram while the model was actually idle.

## Symptom

- Telegram DM showed `••• typing` indefinitely.
- The 35B model (`qwen3.6:35b-mlx`) was idle: ollama runner at 0.0% CPU, CPU
  time frozen, no new turns logged for 7+ minutes after the last response.
- Telegram clears a typing action after ~5s unless refreshed, so something was
  still re-sending `sendChatAction` after the handler had already finished.

## Fix 1 — `_outbound_actions` UnboundLocalError (code, committed)

`gateway/run.py` `_run_agent_inner`: `_outbound_actions` was only bound deep in
the `try` body (after the agent-run awaits, ~L16850). If the run was interrupted
/ cancelled / raised before that point, the `finally` block (~L17178) referenced
`_outbound_actions` and raised `UnboundLocalError`, which **masked the original
exception and broke the cleanup path** — a plausible way for the `_keep_typing`
refresh task to be orphaned (perpetual typing bubble).

Fix: hoist `_outbound_actions: list = []` to the top of the `try` so the
`finally` can always reference it. `tests/gateway/test_message_actions.py`
(21 tests) green.

Interrupt trigger in the wild: user sent a follow-up ("what are you still
working on?") while a prior multi-minute turn was still wrapping up → session
interrupt → exception before the bind point.

The already-orphaned loop was cleared operationally by restarting the gateway
(`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`).

## Fix 2 — streaming `edit_interval` 0.2 → 1.0 (config, NOT in repo)

Lives in `~/.hermes/config.yaml` (not version-controlled). The effective
streaming config is the **top-level `streaming:` block** — `config.py` loads
`yaml_cfg.get("streaming")` first and only falls back to `gateway.streaming`.
That block had `edit_interval: 0.2` (**5 edits/s**), far over Telegram's
~1 edit/s per-chat flood envelope.

On long multi-tool turns, three independent editors hit the same chat:
1. tool-progress bubble — ≤1 edit / 1.5s (`run.py` `_PROGRESS_EDIT_INTERVAL`)
2. token-stream bubble — 1 edit / `edit_interval`
3. model-badge plugin — one `editMessageText` post-stream to stamp the badge

They are independently throttled but uncoordinated, so the combined per-chat
rate crossed the envelope and Telegram flood-controlled (self-healing: it waits
the requested ~12–13s and retries — costs seconds, only on long turns).

Set `streaming.edit_interval: 1.0` (on the envelope). Gateway restarted to load.
Trade-off: marginally choppier token streaming for no flood-control on long
turns. The dominant latency driver remains 35B decode, not edit cadence.

To revert: set `edit_interval` back and restart the gateway.
