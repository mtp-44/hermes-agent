import importlib.util
import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_openbrain_module():
    path = Path(__file__).parent.parent.parent.parent / "plugins" / "memory" / "openbrain" / "__init__.py"
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


def test_capture_record_metadata_satisfies_brief_digest_stale_filters():
    """#regression: capture_thought only accepts content/metadata/embedding/
    contact_id — domain/category/subcategory as top-level args are silently
    dropped by the server, so /brief, /digest, and /stale (which filter on
    metadata.record_type / metadata.source_app) never found these captures.
    """
    module = _load_openbrain_module()
    provider = module.OpenBrainMemoryProvider()
    record = {
        "type": "session_summary",
        "summary_text": "Objective: test Outcome: it worked",
        "topics": ["memory", "openbrain"],
        "routing": "canonical",
    }
    context = {
        "session_id": "sess-1",
        "platform": "telegram",
        "boundary_reason": "gateway_shutdown",
        "boundary_id": "b1",
    }

    with patch.object(provider, "_call_mcp_tool", return_value={"result": {}}) as mock_call:
        provider._capture_record(record, context)

    tool_name, args = mock_call.call_args[0]
    assert tool_name == "capture_thought"
    assert "domain" not in args
    assert "category" not in args
    assert "subcategory" not in args
    metadata = args["metadata"]

    from gateway.open_brain import _is_hermes_brief_candidate

    assert _is_hermes_brief_candidate(metadata) is True
    assert metadata["session_id"] == "sess-1"
    assert metadata["source_app"] == "hermes_gateway"


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


# --- Edge cases ---

def test_is_available_false_without_key(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.delenv("OPENBRAIN_MCP_KEY", raising=False)
    monkeypatch.delenv("MCP_ACCESS_KEY", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    provider = module.OpenBrainMemoryProvider()
    assert provider.is_available() is False


def test_parse_mcp_response_raises_on_unparseable_body():
    module = _load_openbrain_module()
    import pytest
    with pytest.raises(ValueError, match="Unable to parse"):
        module._parse_mcp_response("data: not-json\n")


def test_parse_mcp_response_returns_empty_for_blank():
    module = _load_openbrain_module()
    assert module._parse_mcp_response("") == {}
    assert module._parse_mcp_response("   ") == {}


def test_on_session_end_skips_ineligible_context(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.setenv("OPENBRAIN_MCP_KEY", "test-key")
    provider = module.OpenBrainMemoryProvider()

    calls = []
    provider._capture_record = lambda r, c: calls.append(r)

    provider.on_session_end([], capture_context={"eligible": False, "capture_records": []})
    assert calls == []


def test_on_session_end_skips_non_primary_agent(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.setenv("OPENBRAIN_MCP_KEY", "test-key")
    provider = module.OpenBrainMemoryProvider()
    provider._agent_context = "sub"

    calls = []
    provider._capture_record = lambda r, c: calls.append(r)

    provider.on_session_end(
        [],
        capture_context={"eligible": True, "capture_records": [{"type": "session_summary"}]},
    )
    assert calls == []


def test_call_mcp_tool_retries_on_network_error(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.setenv("OPENBRAIN_MCP_KEY", "test-key")
    provider = module.OpenBrainMemoryProvider()

    attempt_count = 0

    def fake_urlopen(req, timeout):
        nonlocal attempt_count
        attempt_count += 1
        raise urllib.error.URLError("connection refused")

    with patch.object(module.urllib.request, "urlopen", fake_urlopen):
        with patch.object(module.time, "sleep"):  # skip real sleeps
            import pytest
            with pytest.raises(RuntimeError, match="network error"):
                provider._call_mcp_tool("capture_thought", {"content": "test"})

    assert attempt_count == module._RETRY_ATTEMPTS


def test_call_mcp_tool_does_not_retry_on_4xx(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.setenv("OPENBRAIN_MCP_KEY", "test-key")
    provider = module.OpenBrainMemoryProvider()

    attempt_count = 0

    def fake_urlopen(req, timeout):
        nonlocal attempt_count
        attempt_count += 1
        err = urllib.error.HTTPError(url="", code=401, msg="Unauthorized", hdrs=None, fp=None)
        err.read = lambda: b"unauthorized"
        raise err

    with patch.object(module.urllib.request, "urlopen", fake_urlopen):
        import pytest
        with pytest.raises(RuntimeError, match="HTTP 401"):
            provider._call_mcp_tool("capture_thought", {"content": "test"})

    assert attempt_count == 1  # no retry on 4xx


def test_call_mcp_tool_succeeds_on_retry_after_transient_5xx(monkeypatch):
    module = _load_openbrain_module()
    monkeypatch.setenv("OPENBRAIN_MCP_KEY", "test-key")
    provider = module.OpenBrainMemoryProvider()

    attempt_count = 0

    def fake_urlopen(req, timeout):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            err = urllib.error.HTTPError(url="", code=503, msg="Service Unavailable", hdrs=None, fp=None)
            err.read = lambda: b"unavailable"
            raise err
        response = MagicMock()
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        response.read.return_value = b'{"result": {"id": "ok"}}'
        return response

    with patch.object(module.urllib.request, "urlopen", fake_urlopen):
        with patch.object(module.time, "sleep"):
            result = provider._call_mcp_tool("capture_thought", {"content": "test"})

    assert result == {"result": {"id": "ok"}}
    assert attempt_count == 2


def test_sync_ledger_concurrent_writes_do_not_corrupt():
    module = _load_openbrain_module()
    provider = module.OpenBrainMemoryProvider()

    with tempfile.TemporaryDirectory() as tmpdir:
        provider._sync_ledger_path = Path(tmpdir) / "openbrain_sync.json"

        def mark(ids):
            for record_id in ids:
                provider._mark_synced(record_id)

        threads = [
            threading.Thread(target=mark, args=([f"id-{i}-{j}" for j in range(10)],))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        payload = json.loads(provider._sync_ledger_path.read_text(encoding="utf-8"))
        assert len(payload["synced_record_ids"]) == 50
