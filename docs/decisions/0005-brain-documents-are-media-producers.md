---
id: HA-0005
date: 2026-09-04
repo: hermes-agent
status: active
tags: [gateway, media-delivery, open-brain, signal, documents, local-model, ruling]
verdict: "**Open Brain's `get_record_document` is a media *producer* tool: the gateway auto-appends `MEDIA:<local_path>` from its result, so \"show me the invoice\" attaches the PDF whether or not the model writes the tag.** Three live attempts on 2026-09-04 said prompting would not do it. With `OB-0021` (Hermes on the new `documents` profile) the model called the tool for the Miele invoice at 12:33 and 12:42 and still described the PDF in prose; the local `qwen3.6:35b-mlx`, in a compacted 61K-token Signal session, then stopped calling the tool at all and walked the filesystem with `search_files`/`terminal`/`read_file`, ending turns on \"Let me try extracting text from it:\". A `system_prompt` paragraph telling it to emit `MEDIA:<local_path>` changed nothing. **Why not a plugin:** the obvious seam, `transform_llm_output`, is first-string-wins in `agent/turn_finalizer.py`, and `~/.hermes/plugins/model-badge` already owns it (the 🔵 badge on every reply) — a second transformer would silently drop either the badge or the attachment. **What changed (`gateway/run.py`):** `mcp_open_brain_get_record_document` joins `_AUTO_APPEND_MEDIA_TOOL_NAMES` beside `image_generate` and TTS; the JSON-path extraction that only knew `image_generate`'s `{\"success\": true, \"image\": ...}` is one helper, `_json_media_paths`, that also unwraps the MCP adapter's `{\"result\": \"<json>\"}` and reads `local_path`; the history dedup collector uses the same helper so a compression-boundary rescan never re-sends the PDF. Paths still have to pass the extension-anchored MEDIA regex; `download_url` alone attaches nothing; error payloads attach nothing. `strict: false` in `~/.hermes/config.yaml` means the archive path `~/Backups/open_brain/documents/…` is deliverable — it is under no denied prefix. Pinned by five tests in `tests/gateway/test_media_extraction.py` and a `LOCAL_DELTA_PATTERNS` entry in `scripts/hermes_update_guard.py`, because an upstream replay that rebuilds the allowlist would turn the feature back into prose without any test failing. **Upstream side:** `open_brain` `0d8d491` added `local_path` to the tool result — this record depends on it. **Not changed:** the `system_prompt` hint stays (harmless, and it helps a model that does read it); the local model's habit of ending a turn on an announced-but-unexecuted action is a model problem this does not fix."
---

# Brain originals are attached by the gateway, not by the model

## What happened

Three requests for the Miele dishwasher invoice on 2026-09-04, in one Signal
session, after `OB-0021` gave Hermes the `get_record_document` tool:

| time | model | tool called? | outcome |
|---|---|---|---|
| 12:33 | cloud (session tier) | yes — `document_retrieval` event | signed URL in result, no `local_path` yet; Hermes hunted the BrainDrop copy and described the PDF |
| 12:42 | cloud | yes | `local_path` now in result; Hermes still described the PDF, no `MEDIA:` tag |
| 12:58 | `qwen3.6:35b-mlx`, session ~61K tokens, compacted | **no** — `search_files`, `terminal`, `read_file` | "I can't directly render it… Let me try extracting text from it:" — turn ends |

Between the second and third attempt a paragraph went into the Hermes
`system_prompt`: call the tool, put `MEDIA:<local_path>` on its own line, do
not describe the PDF, do not search the filesystem. It changed nothing.

## Why the gateway, and why not a plugin

The gateway already solves this exact problem for `image_generate` and TTS:
`_collect_auto_append_media_tags` scans the current turn's results from an
allowlist of producer tools and appends their deliverables as `MEDIA:` tags
"so delivery doesn't depend on the model restating the path". A brain original
is the same case.

A plugin looked cleaner but cannot work here. `transform_llm_output` — the one
hook that can edit the final reply — takes the first non-empty string and
stops (`agent/turn_finalizer.py`), and `~/.hermes/plugins/model-badge` already
uses it for the 🔵 badge. Two transformers means one of them is dropped, and
which one depends on directory sort order. The outbound decorator seam attaches
buttons, not text. `transform_tool_result` could put a `MEDIA:` tag into the
tool result, but only allowlisted producers are scanned — the allowlist is the
point (#16721), so it would have to change anyway.

## The change

`gateway/run.py`:

- `_AUTO_APPEND_MEDIA_TOOL_NAMES` += `mcp_open_brain_get_record_document`.
- `_JSON_MEDIA_TOOL_PATH_FIELDS` += `local_path`; new `_JSON_MEDIA_TOOL_NAMES`.
- New `_json_media_paths(tool_name, content)`: parses the payload, unwraps the
  MCP adapter's `{"result": "<json>"}`, rejects `{"error": …}` and an
  unsuccessful `image_generate`, and returns the first path field that passes
  `_TOOL_MEDIA_RE`. Both the current-turn collector and the history dedup
  collector call it, replacing two copies of image_generate-only logic.

`scripts/hermes_update_guard.py`: a `LOCAL_DELTA_PATTERNS` entry asserting the
allowlist entry, the helper call and the `local_path` field are all present.

`tests/gateway/test_media_extraction.py`: `TestOpenBrainDocumentDelivery` —
wrapped result attaches; error attaches nothing; URL-only attaches nothing;
history dedup covers the JSON shape; `image_generate` unchanged.

Depends on `open_brain` `0d8d491` (`get_record_document` returns `local_path`).

## What this does not fix

The local model not calling the tool at all in a long, compacted session. That
is a model and context problem; the practical answer today is a fresh session
(`/new`) before asking, or a cloud tier for that turn. The `system_prompt`
paragraph stays for the models that read it.
