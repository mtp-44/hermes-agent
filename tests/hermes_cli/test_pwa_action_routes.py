"""Focused PWA contract tests for the message-action REST seam."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hermes_cli import plugins, web_server


@pytest.fixture
def action_client():
    previous_required = getattr(web_server.app.state, "auth_required", None)
    previous_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = "127.0.0.1"
    client = TestClient(web_server.app, base_url="http://127.0.0.1:9119")

    try:
        yield client
    finally:
        client.close()
        web_server.app.state.auth_required = previous_required
        web_server.app.state.bound_host = previous_host


def test_action_dispatch_requires_dashboard_authorization(action_client):
    response = action_client.post(
        "/api/actions/dispatch",
        json={"callback_id": "act:approve:token"},
    )

    assert response.status_code == 401
    assert "approve" not in response.text


def test_action_dispatch_rejects_malformed_callback_id(action_client):
    action_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    response = action_client.post(
        "/api/actions/dispatch",
        json={"callback_id": "not-an-action"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed action callback"


def test_action_dispatch_invokes_registered_handler_with_desktop_context(
    action_client, monkeypatch
):
    calls = []

    def handler(action_id, token, context):
        calls.append((action_id, token, context))
        return "Recorded"

    manager = SimpleNamespace(get_action_handler=lambda action_id: handler)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    action_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    response = action_client.post(
        "/api/actions/dispatch",
        json={"callback_id": "act:approve:opaque-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "ack": "Recorded"}
    assert calls == [("approve", "opaque-token", {"platform": "desktop"})]


def test_action_dispatch_keeps_handler_failures_generic(action_client, monkeypatch):
    def failed_handler(*_args):
        raise RuntimeError("private handler details")

    manager = SimpleNamespace(get_action_handler=lambda action_id: failed_handler)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    action_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    response = action_client.post(
        "/api/actions/dispatch",
        json={"callback_id": "act:approve:opaque-token"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Action handler failed"
    assert "private handler details" not in response.text
