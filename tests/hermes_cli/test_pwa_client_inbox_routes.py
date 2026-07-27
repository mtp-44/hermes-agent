from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import hermes_state
from hermes_cli import plugins, web_server


@pytest.fixture
def inbox_client(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    seeded = hermes_state.SessionDB(db_path=db_path)
    seeded.create_session("home-session", source="telegram", chat_id="42")
    seeded.create_client_inbox_item(
        event_id="digest-1",
        session_id="home-session",
        created_at=100.0,
        kind="daily_digest",
        priority="normal",
        body="Daily brief",
        actions=[{"label": "Done", "callback_id": "act:done:7"}],
    )
    seeded.close()

    real_session_db = hermes_state.SessionDB
    monkeypatch.setattr(
        hermes_state,
        "SessionDB",
        lambda *args, **kwargs: real_session_db(db_path=db_path),
    )

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


def _authorize(client):
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/client-inbox?session_id=home-session", None),
        (
            "patch",
            "/api/client-inbox/digest-1",
            {"session_id": "home-session", "read": True},
        ),
        (
            "post",
            "/api/actions/dispatch",
            {
                "callback_id": "act:done:7",
                "inbox_event_id": "digest-1",
                "session_id": "home-session",
            },
        ),
    ],
)
def test_inbox_routes_require_dashboard_authorization(
    inbox_client, method, path, json_body
):
    if json_body is None:
        response = getattr(inbox_client, method)(path)
    else:
        response = getattr(inbox_client, method)(path, json=json_body)
    assert response.status_code == 401
    assert "Daily brief" not in response.text


def test_reload_and_read_state_round_trip(inbox_client):
    _authorize(inbox_client)
    listed = inbox_client.get("/api/client-inbox?session_id=home-session")
    assert listed.status_code == 200
    assert [item["event_id"] for item in listed.json()["items"]] == ["digest-1"]

    updated = inbox_client.patch(
        "/api/client-inbox/digest-1",
        json={"session_id": "home-session", "read": True},
    )
    assert updated.status_code == 200
    assert updated.json()["item"]["read_at"] is not None

    reloaded = inbox_client.get("/api/client-inbox?session_id=home-session")
    assert reloaded.json()["items"][0]["read_at"] is not None


def test_action_dispatch_ack_is_persisted_and_second_device_is_stale(
    inbox_client, monkeypatch
):
    calls = []

    def handler(action_id, token, context):
        calls.append((action_id, token, context))
        return "Commitment completed"

    monkeypatch.setattr(
        plugins,
        "get_plugin_manager",
        lambda: SimpleNamespace(get_action_handler=lambda _action_id: handler),
    )
    _authorize(inbox_client)
    payload = {
        "callback_id": "act:done:7",
        "inbox_event_id": "digest-1",
        "session_id": "home-session",
    }

    first = inbox_client.post("/api/actions/dispatch", json=payload)
    second = inbox_client.post("/api/actions/dispatch", json=payload)

    assert first.status_code == 200
    assert first.json()["ack"] == "Commitment completed"
    assert first.json()["item"]["action_ack"] == "Commitment completed"
    assert second.status_code == 409
    assert calls == [("done", "7", {"platform": "desktop"})]


def test_action_rejects_wrong_item_callback_and_malformed_payload(inbox_client):
    _authorize(inbox_client)
    wrong = inbox_client.post(
        "/api/actions/dispatch",
        json={
            "callback_id": "act:other:7",
            "inbox_event_id": "digest-1",
            "session_id": "home-session",
        },
    )
    malformed = inbox_client.post(
        "/api/actions/dispatch",
        json={
            "callback_id": "not-an-action",
            "inbox_event_id": "digest-1",
            "session_id": "home-session",
        },
    )
    missing_session = inbox_client.post(
        "/api/actions/dispatch",
        json={"callback_id": "act:done:7", "inbox_event_id": "digest-1"},
    )

    assert wrong.status_code == 400
    assert malformed.status_code == 400
    assert missing_session.status_code == 400


def test_failed_handler_releases_claim_for_retry(inbox_client, monkeypatch):
    attempts = 0

    def handler(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return "Recovered"

    monkeypatch.setattr(
        plugins,
        "get_plugin_manager",
        lambda: SimpleNamespace(get_action_handler=lambda _action_id: handler),
    )
    _authorize(inbox_client)
    payload = {
        "callback_id": "act:done:7",
        "inbox_event_id": "digest-1",
        "session_id": "home-session",
    }

    assert inbox_client.post("/api/actions/dispatch", json=payload).status_code == 500
    assert inbox_client.post("/api/actions/dispatch", json=payload).status_code == 200
