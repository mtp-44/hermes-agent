import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from gateway.client_inbox import (
    ClientInboxTargetError,
    ClientInboxValidationError,
    enqueue_client_inbox_item,
)
from gateway.config import GatewayConfig, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget
from hermes_state import SessionDB


@pytest.fixture
def inbox_db(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("home-session", source="telegram", chat_id="42")
    try:
        yield db
    finally:
        db.close()


def _enqueue(db, event_id="evt-1", **overrides):
    payload = {
        "event_id": event_id,
        "session_id": "home-session",
        "body": "Daily brief",
        "kind": "daily_digest",
        "priority": "normal",
        "actions": [{"label": "Done", "callback_id": "act:done:7"}],
        "reference": {"producer": "test"},
        "created_at": 100.0,
        "db": db,
    }
    payload.update(overrides)
    return enqueue_client_inbox_item(**payload)


def test_offline_persistence_retry_reload_and_restart(inbox_db, tmp_path):
    first, created = _enqueue(inbox_db)
    retry, retry_created = _enqueue(inbox_db, created_at=999.0)

    assert created is True
    assert retry_created is False
    assert retry == first
    assert retry["created_at"] == 100.0

    db_path = inbox_db.db_path
    inbox_db.close()
    restarted = SessionDB(db_path=db_path)
    try:
        assert restarted.list_client_inbox_items("home-session") == [first]
    finally:
        restarted.close()
    inbox_db._conn = None


def test_idempotency_conflict_malformed_payload_and_unknown_target(inbox_db):
    _enqueue(inbox_db)

    with pytest.raises(ValueError, match="different payload"):
        _enqueue(inbox_db, body="Different body")
    with pytest.raises(ClientInboxValidationError, match="malformed"):
        _enqueue(
            inbox_db,
            event_id="bad-action",
            actions=[{"label": "Broken", "callback_id": "bogus"}],
        )
    with pytest.raises(ClientInboxValidationError, match="priority"):
        _enqueue(inbox_db, event_id="bad-priority", priority="surprise")
    with pytest.raises(ClientInboxTargetError, match="unknown session"):
        enqueue_client_inbox_item(
            event_id="unknown",
            session_id="missing",
            body="No target",
            db=inbox_db,
        )


def test_state_transitions_ordering_expiry_and_stale_action(inbox_db):
    _enqueue(inbox_db, event_id="same-b", created_at=200.0)
    _enqueue(inbox_db, event_id="same-a", created_at=200.0)
    _enqueue(inbox_db, event_id="old", created_at=50.0)
    _enqueue(
        inbox_db,
        event_id="expired",
        created_at=10.0,
        expires_at=20.0,
    )

    assert [
        item["event_id"]
        for item in inbox_db.list_client_inbox_items("home-session", now=30.0)
    ] == ["same-b", "same-a", "old"]

    read = inbox_db.update_client_inbox_item(
        "same-a", "home-session", mark_read=True, now=210.0
    )
    assert read["read_at"] == 210.0
    dismissed = inbox_db.update_client_inbox_item(
        "old", "home-session", dismiss=True, now=220.0
    )
    assert dismissed["read_at"] == dismissed["dismissed_at"] == 220.0

    claimed = inbox_db.claim_client_inbox_action(
        "same-b", "home-session", "act:done:7", now=230.0
    )
    assert claimed["acted_at"] == 230.0
    assert (
        inbox_db.claim_client_inbox_action(
            "same-b", "home-session", "act:done:7", now=231.0
        )
        is None
    )
    finished = inbox_db.finish_client_inbox_action(
        "same-b", "home-session", "act:done:7", "Recorded", now=232.0
    )
    assert finished["action_ack"] == "Recorded"


def test_existing_peer_mapping_resolves_home_session(inbox_db, tmp_path, monkeypatch):
    from gateway import mirror

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    index = sessions_dir / "sessions.json"
    index.write_text(
        json.dumps({
            "agent:main:telegram:dm": {
                "session_id": "home-session",
                "updated_at": "2026-07-27T12:00:00Z",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "42",
                    "user_id": "mark",
                },
            }
        })
    )
    monkeypatch.setattr(mirror, "_SESSIONS_INDEX", index)

    item, created = enqueue_client_inbox_item(
        event_id="mapped",
        session_platform="telegram",
        chat_id="42",
        user_id="mark",
        body="Mapped brief",
        db=inbox_db,
    )

    assert created is True
    assert item["session_id"] == "home-session"


@pytest.mark.asyncio
async def test_delivery_router_supports_generic_client_inbox_target(
    inbox_db, monkeypatch
):
    captured = {}

    def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"event_id": "router-event", "session_id": "home-session"}, True

    monkeypatch.setattr(
        "gateway.client_inbox.enqueue_client_inbox_item",
        fake_enqueue,
    )
    router = DeliveryRouter(GatewayConfig())
    result = await router.deliver(
        "Digest",
        [DeliveryTarget(platform=Platform.CLIENT_INBOX)],
        metadata={
            "event_id": "router-event",
            "session_platform": "telegram",
            "session_chat_id": "42",
        },
    )

    assert result["client_inbox"]["success"] is True
    assert captured["body"] == "Digest"
    assert captured["session_platform"] == "telegram"
    assert captured["chat_id"] == "42"


class _FakeTransport:
    def __init__(self):
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)
        return True


def test_two_connected_clients_receive_one_live_event_each(inbox_db, monkeypatch):
    from tui_gateway import server

    first = _FakeTransport()
    second = _FakeTransport()
    monkeypatch.setattr(server, "_db", inbox_db)
    monkeypatch.setattr(server, "_client_inbox_poller_started", False)
    server._client_inbox_subscribers.clear()

    for request_id, transport in ((1, first), (2, second)):
        response = server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "client_inbox.subscribe",
                "params": {"session_id": "home-session"},
            },
            transport=transport,
        )
        assert response["result"]["items"] == []

    _enqueue(inbox_db, event_id="live", created_at=time.time())
    deadline = time.time() + 3
    while time.time() < deadline and not (first.frames and second.frames):
        time.sleep(0.05)

    for transport in (first, second):
        events = [
            frame
            for frame in transport.frames
            if frame.get("params", {}).get("type") == "client_inbox.changed"
        ]
        assert len(events) == 1
        assert events[0]["params"]["payload"]["item"]["event_id"] == "live"
        server._unsubscribe_client_inbox_transport(transport)
