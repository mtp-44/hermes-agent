"""Signal reactions surface as ``gateway_platform_event`` ``reaction`` events (hooks.md contract),
never as agent turns, and only from chats the adapter serves."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig

ACCOUNT = "+15551234567"
SENDER = "+15559876543"
SENDER_UUID = "05668cf3-8ffa-467e-9b24-f5eefa5cf475"
GROUP_ID = "Z3JvdXAtaWQtMQ=="


@pytest.fixture(autouse=True)
def _reset_signal_scheduler():
    from plugins.platforms.signal.signal_rate_limit import _reset_scheduler
    _reset_scheduler()
    yield
    _reset_scheduler()


@pytest.fixture
def subscribed(monkeypatch):
    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda name: name == "gateway_platform_event")


def _adapter(monkeypatch, *, group_allowed="", **extra):
    monkeypatch.setenv("SIGNAL_GROUP_ALLOWED_USERS", group_allowed)
    from plugins.platforms.signal.adapter import SignalAdapter
    config = PlatformConfig()
    config.enabled = True
    config.extra = {"http_url": "http://localhost:8080", "account": ACCOUNT, **extra}
    adapter = SignalAdapter(config)
    adapter.turns = []

    async def _turn(event):
        adapter.turns.append(event)

    adapter.handle_message = _turn
    return adapter


def _capture(adapter):
    seen = []

    async def _handler(event, source):
        seen.append((event, source))

    adapter.set_platform_event_handler(_handler)
    return seen


def _envelope(reaction=None, *, group_id=None, text=None):
    data = {"timestamp": 1777600700000}
    if reaction is not None:
        data["reaction"] = reaction
    if text is not None:
        data["message"] = text
    if group_id:
        data["groupInfo"] = {"groupId": group_id, "groupName": "Family"}
    return {"envelope": {"sourceNumber": SENDER, "sourceUuid": SENDER_UUID, "sourceName": "Ada",
                         "timestamp": 1777600700000, "dataMessage": data}}


def _reaction(emoji="👍", ts=1777600600000, remove=False):
    return {"emoji": emoji, "targetAuthorNumber": ACCOUNT, "targetAuthorUuid": "acct-uuid",
            "targetSentTimestamp": ts, "isRemove": remove}


@pytest.mark.asyncio
async def test_dm_reaction_emits_event_and_never_starts_a_turn(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)
    adapter._remember_sent_message_timestamp(1777600600000)
    seen = _capture(adapter)

    await adapter._handle_envelope(_envelope(_reaction()))

    assert adapter.turns == []
    assert len(seen) == 1
    event, source = seen[0]
    assert event == {
        "platform": "signal", "event_type": "reaction",
        "payload": {"emojis": ["👍"], "custom_emoji_ids": [], "removed": False, "chat_id": SENDER,
                    "message_id": "1777600600000", "thread_id": None, "is_own_message": True},
    }
    assert source.platform == Platform.SIGNAL
    assert source.chat_type == "dm" and source.chat_id == SENDER
    assert source.user_id == SENDER and source.user_id_alt == SENDER_UUID


@pytest.mark.asyncio
async def test_reaction_to_someone_elses_message_is_not_own(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(_reaction(ts=1234)))
    assert seen[0][0]["payload"]["is_own_message"] is False


@pytest.mark.asyncio
async def test_reaction_removal_emits_removed_with_no_emojis(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(_reaction(remove=True)))
    payload = seen[0][0]["payload"]
    assert payload["removed"] is True and payload["emojis"] == []
    assert adapter.turns == []


@pytest.mark.asyncio
async def test_allowed_group_reaction_emits_despite_require_mention(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch, group_allowed=GROUP_ID, require_mention=True)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(_reaction(), group_id=GROUP_ID))

    assert adapter.turns == []
    event, source = seen[0]
    assert event["payload"]["chat_id"] == f"group:{GROUP_ID}"
    assert source.chat_type == "group" and source.chat_id_alt == GROUP_ID and source.user_id == SENDER


@pytest.mark.asyncio
@pytest.mark.parametrize("group_allowed", ["", "some-other-group"])
async def test_reaction_in_a_group_the_adapter_does_not_serve_emits_nothing(monkeypatch, subscribed, group_allowed):
    adapter = _adapter(monkeypatch, group_allowed=group_allowed)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(_reaction(), group_id=GROUP_ID))
    assert seen == [] and adapter.turns == []


@pytest.mark.asyncio
async def test_no_subscribers_means_no_work_and_no_turn(monkeypatch):
    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda name: False)
    adapter = _adapter(monkeypatch)
    seen = _capture(adapter)
    with patch.object(adapter, "build_source", MagicMock(side_effect=AssertionError("built a source"))):
        await adapter._handle_envelope(_envelope(_reaction()))
    assert seen == [] and adapter.turns == []


@pytest.mark.asyncio
async def test_no_handler_means_no_event_and_no_turn(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)
    await adapter._handle_envelope(_envelope(_reaction()))
    assert adapter.turns == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reaction", [
    {"emoji": "👍"},                                         # no target timestamp
    {"emoji": "", "targetSentTimestamp": 5},                 # add without an emoji
    {"emoji": "👍", "targetSentTimestamp": True},            # not an id
])
async def test_malformed_reaction_is_dropped(monkeypatch, subscribed, reaction):
    adapter = _adapter(monkeypatch)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(reaction))
    assert seen == [] and adapter.turns == []


@pytest.mark.asyncio
async def test_handler_failure_is_contained(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)

    async def _boom(event, source):
        raise RuntimeError("observer bug")

    adapter.set_platform_event_handler(_boom)
    await adapter._handle_envelope(_envelope(_reaction()))  # no raise
    assert adapter.turns == []


@pytest.mark.asyncio
async def test_plain_message_is_unaffected(monkeypatch, subscribed):
    adapter = _adapter(monkeypatch)
    seen = _capture(adapter)
    await adapter._handle_envelope(_envelope(text="hello"))
    assert seen == []
    assert [e.text for e in adapter.turns] == ["hello"]


def _runner(authorized):
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner._is_user_authorized = lambda source: source.user_id == authorized
    return runner


@pytest.mark.parametrize("authorized, fires", [(SENDER, True), ("+10000000000", False)])
def test_runner_boundary_authorizes_the_reactor(monkeypatch, subscribed, authorized, fires):
    """Full path: Signal fire-site -> runner post-auth gate -> plugin hook dispatch."""
    adapter = _adapter(monkeypatch)
    adapter.set_platform_event_handler(_runner(authorized)._handle_gateway_platform_event)
    invoked = MagicMock()
    with patch("hermes_cli.lifecycle.invoke_hook", invoked):
        asyncio.run(adapter._handle_envelope(_envelope(_reaction())))
    if fires:
        invoked.assert_called_once()
        args, kwargs = invoked.call_args
        assert args == ("gateway_platform_event",)
        assert kwargs["platform"] == "signal" and kwargs["event_type"] == "reaction"
        assert kwargs["payload"]["message_id"] == "1777600600000"
    else:
        invoked.assert_not_called()
    assert adapter.turns == []
