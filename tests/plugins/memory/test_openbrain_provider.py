import importlib.util
import json
import tempfile
from pathlib import Path


def _load_openbrain_module():
    path = Path("/Users/mh/ai/agents/hermes-agent/plugins/memory/openbrain/__init__.py")
    spec = importlib.util.spec_from_file_location("test_openbrain_provider_module", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_mcp_response_supports_json_and_sse():
    module = _load_openbrain_module()

    direct = module._parse_mcp_response('{"result":{"ok":true}}')
    sse = module._parse_mcp_response('event: message\ndata: {"result":{"ok":true}}\n\n')

    assert direct["result"]["ok"] is True
    assert sse["result"]["ok"] is True


def test_record_sync_id_is_stable():
    module = _load_openbrain_module()
    provider = module.OpenBrainMemoryProvider()
    record = {"type": "action_item", "title": "Add provider", "routing": "canonical"}
    context = {"boundary_id": "abc123"}

    first = provider._record_sync_id(record, context)
    second = provider._record_sync_id(dict(record), dict(context))

    assert first == second


def test_render_record_content_includes_structured_metadata():
    module = _load_openbrain_module()
    provider = module.OpenBrainMemoryProvider()
    record = {
        "type": "session_summary",
        "summary_text": "Objective: test Outcome: it worked",
        "topics": ["memory", "openbrain"],
        "routing": "canonical",
    }
    context = {"session_id": "sess-1", "boundary_reason": "new_session", "boundary_id": "b1"}

    content = provider._render_record_content(record, context)

    assert "Hermes auto-capture: session_summary" in content
    assert "Structured metadata:" in content
    assert '"boundary_id": "b1"' in content


def test_sync_ledger_round_trip():
    module = _load_openbrain_module()
    provider = module.OpenBrainMemoryProvider()

    with tempfile.TemporaryDirectory() as tmpdir:
        provider._sync_ledger_path = Path(tmpdir) / "openbrain_sync.json"
        provider._mark_synced("abc")
        provider._mark_synced("def")

        second = module.OpenBrainMemoryProvider()
        second._sync_ledger_path = provider._sync_ledger_path
        second._load_sync_ledger()

        assert second._is_synced("abc") is True
        assert second._is_synced("def") is True
        payload = json.loads(provider._sync_ledger_path.read_text(encoding="utf-8"))
        assert payload["synced_record_ids"] == ["abc", "def"]


def test_is_available_can_read_hermes_home_env_file(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.delenv("OPENBRAIN_MCP_KEY", raising=False)
    monkeypatch.delenv("MCP_ACCESS_KEY", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / ".env").write_text("MCP_ACCESS_KEY=test-key\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(home))
        provider = module.OpenBrainMemoryProvider()
        assert provider.is_available() is True
