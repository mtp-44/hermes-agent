---
id: HA-0004
date: 2026-08-19
repo: hermes-agent
status: active
tags: [fork-policy, providers, config, mistral, ollama, no-core-patch]
verdict: "Two upstream facts, both found the hard way, and a ruling that **no core patch was made**. (1) `model.ollama_keep_alive` is **not provider-scoped**: `agent/agent_init.py:1844-1853` reads it from the top-level `model` block with no endpoint check and `agent/transports/chat_completions.py:447,567` put it into `extra_body` unconditionally, so *every* provider receives a `keep_alive` field in the request body. Ollama accepts it, OpenAI Codex tolerates it, and **Mistral rejects it with HTTP 422 `extra_forbidden`** — proven by issuing the identical request twice with that field as the only variable. It cannot be scoped in config, because Hermes supports per-`custom_providers` overrides for `context_length` only (`agent_init.py:1550-1570`), and it cannot be fixed from a plugin, because `pre_api_request` is an observability hook that receives a sanitised copy and cannot mutate `api_kwargs`. (2) **`mistral` is not in `CANONICAL_PROVIDERS`** (38 entries, none Mistral) and is absent from `_build_provider_choices`'s static fallback, so `--provider mistral` resolves to no endpoint and `list_authenticated_providers` returns no Mistral row even with a valid key present — `\"mistral\"` appearing in `models.py`'s `_MODELS_DEV_PREFERRED` is a *catalog source* list, not provider registration. The route that works is a `custom_providers` entry, giving slug `custom:mistral` (`providers.custom_provider_slug`), with `runtime_provider._host_derived_api_key` deriving `MISTRAL_API_KEY` from the `api.mistral.ai` hostname so no key is written into `config.yaml`. **Ruling: neither was patched in core.** `docs/FORK_POLICY.md` policy 2 says no load-bearing logic in core paths and policy 3 says swap-don't-debug; a one-line endpoint gate in `chat_completions.py` would have been correct and tiny, and was still refused, because it would attach to every future security sync. The keep-alive pin moved *out* of Hermes instead — see `BS-0002` — and Mistral was wired as a custom provider rather than by adding a canonical one. What future work should take from this: **a config key named for one vendor is not evidence that it is scoped to that vendor**, and the next non-Ollama provider added here will hit the same 422 unless the pin stays out of the request path."
---

# `ollama_keep_alive` is global, and `mistral` is not a canonical provider

## Context

Adding a Mistral escalation tier to the `model-gate` plugin (2026-08-19, see
`BS-0002` and `model_lab` `ML-0002`) failed twice, both times in core behaviour
rather than in the plugin.

**First failure — no such provider.** `/model mistral-medium-latest --provider
mistral --session` resolved to nothing:

```
CANONICAL_PROVIDERS        38 entries, none Mistral
list_authenticated_providers  ['OpenRouter', 'GitHub Copilot', 'Anthropic',
                               'OpenAI Codex', 'Alibaba']   ← with the key present
```

**Second failure — a stray body field.** Once wired as `custom:mistral`, every
turn returned:

```
HTTP 422 {'type': 'extra_forbidden', 'loc': ['body', 'keep_alive'], 'input': -1}
```

Isolated by issuing the same request twice against `api.mistral.ai`, `keep_alive`
the only difference: with it, 422; without it, a normal completion.

## Decision

Fix both **outside** core.

- Mistral is a `custom_providers` entry named `Mistral`, so its slug is
  `custom:mistral` and its key is host-derived. No canonical provider added.
- `model.ollama_keep_alive` was removed from `~/.hermes/config.yaml` entirely and
  the 35B pin moved to a launchd job (`BS-0002`), so no provider receives the field.

A one-line gate in `agent/transports/chat_completions.py` — only send `keep_alive`
when the endpoint is Ollama — was considered and **rejected under policy 2**,
despite being the smaller and more obviously correct change.

## Consequences

- **The fork stays clean.** No core diff was added, so the next security sync
  carries no extra merge burden. That was the whole point of the refusal.
- **The 422 class is closed for good, not just for Mistral**, because the field is
  no longer in any request. Had it been fixed by gating, it would have stayed one
  config edit away from returning.
- `ollama_num_ctx` is injected by the same ungated mechanism
  (`chat_completions.py:442-446`, into `options`). It is unset today, so it is
  latent rather than live — **if it is ever set, expect the same 422 on any
  non-Ollama provider.**
- Revisiting means either upstream gating these params by endpoint — the right home
  for the fix, and worth checking on the next security pull — or accepting a core
  patch and the sync cost that comes with it.
