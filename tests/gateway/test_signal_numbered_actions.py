"""WP4: numbered reply commands, the Signal fallback for multi-choice actions.

A reaction is one binary choice on one message. The 21:00 digest offers three
verdicts per commitment across several commitments, so those actions are
rendered as numbered commands and dispatched through the same generic action
seam the reaction path uses.
"""

import pytest

from gateway.config import PlatformConfig


def _make_signal_adapter(monkeypatch, account="+15551234567", **extra):
    monkeypatch.setenv("SIGNAL_GROUP_ALLOWED_USERS", extra.pop("group_allowed", ""))
    from gateway.platforms.signal import SignalAdapter

    config = PlatformConfig()
    config.enabled = True
    config.extra = {"http_url": "http://localhost:8080", "account": account, **extra}
    return SignalAdapter(config)


def _stub_rpc(return_value):
    captured = []

    async def mock_rpc(method, params, rpc_id=None):
        captured.append({"method": method, "params": dict(params)})
        return return_value

    return mock_rpc, captured


def _digest_metadata(commitments=(("c-aaa", 1), ("c-bbb", 2))):
    return {
        "actions": [
            action
            for commitment_id, number in commitments
            for action in (
                {"label": f"{number} ✅", "action_id": "cdone", "token": commitment_id},
                {"label": f"{number} 🗑", "action_id": "cdrop", "token": commitment_id},
                {"label": f"{number} 👀", "action_id": "cseen", "token": commitment_id},
            )
        ]
    }


def _text_envelope(sender, text, timestamp=999):
    return {
        "envelope": {
            "source": sender,
            "sourceNumber": sender,
            "sourceUuid": "uuid-sender",
            "timestamp": timestamp,
            "dataMessage": {"message": text, "timestamp": timestamp},
        }
    }


def _enable(monkeypatch, tmp_path, sender):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", sender)
    monkeypatch.setenv("SIGNAL_REACTION_FEEDBACK", "true")


class _Manager:
    def __init__(self, handler, ids=("cdone", "cdrop", "cseen")):
        self._handler = handler
        self._ids = ids

    def get_action_handler(self, action_id):
        return self._handler if action_id in self._ids else None


class TestNumberedCommandParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("/c 1 done", (1, "cdone")),
            ("/c 2 drop", (2, "cdrop")),
            ("/c 10 seen", (10, "cseen")),
            ("/c done 3", (3, "cdone")),
            ("/C 1 DONE", (1, "cdone")),
            ("  /c 1 done  ", (1, "cdone")),
        ],
    )
    def test_accepts_both_orders_and_casings(self, text, expected):
        from gateway.platforms.signal import SignalAdapter

        assert SignalAdapter.parse_numbered_command(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "c 1 done",  # no leading slash
            "/c 1",
            "/c 1 done please",
            "/c one done",
            "/c 1 explode",
            "/note something",
            "just talking about /c 1 done",
        ],
    )
    def test_ordinary_text_is_never_intercepted(self, text):
        from gateway.platforms.signal import SignalAdapter

        assert SignalAdapter.parse_numbered_command(text) is None


class TestNumberedRendering:
    @pytest.mark.asyncio
    async def test_digest_send_appends_a_command_legend_and_stores_the_mapping(
        self, monkeypatch, tmp_path
    ):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, calls = _stub_rpc({"timestamp": 7000})

        sent = await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        assert sent.message_id == "7000"
        body = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert "/c <n> done" in body and "/c <n> drop" in body and "/c <n> seen" in body
        assert "(n = 1–2)" in body
        # The multi-choice widget must not be advertised as a reaction.
        assert "React" not in body

        resolved = adapter._reaction_feedback_store.resolve_numbered_action(
            account=adapter.account, chat_id=sender, ordinal=2, action_id="cdrop"
        )
        assert resolved["token"] == "c-bbb"

    @pytest.mark.asyncio
    async def test_binary_feedback_still_renders_as_a_reaction_prompt(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, calls = _stub_rpc({"timestamp": 7001})

        await adapter.send(
            sender,
            "Answer",
            metadata={
                "actions": [
                    {"label": "👍 Good", "action_id": "obg", "token": "q"},
                    {"label": "👎 Bad", "action_id": "obb", "token": "q"},
                ]
            },
        )
        body = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert body.endswith("React 👍 or 👎")
        assert "/c " not in body

    @pytest.mark.asyncio
    async def test_no_legend_and_no_store_while_the_opt_in_is_off(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("SIGNAL_ALLOWED_USERS", sender)
        monkeypatch.delenv("SIGNAL_REACTION_FEEDBACK", raising=False)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, calls = _stub_rpc({"timestamp": 7002})

        await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        body = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert body == "daily brief"
        assert list(tmp_path.glob("signal_reaction_feedback*")) == []


class TestNumberedDispatch:
    @pytest.mark.asyncio
    async def test_command_reaches_the_registered_handler_with_the_right_token(
        self, monkeypatch, tmp_path
    ):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, calls = _stub_rpc({"timestamp": 7100})
        await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        seen = []

        async def handler(action_id, token, context):
            seen.append((action_id, token, context["user_id"], context["numbered_ordinal"]))
            return "✅ Marked done"

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager(handler))

        calls.clear()
        await adapter._handle_envelope(_text_envelope(sender, "/c 2 done"))

        assert seen == [("cdone", "c-bbb", sender, 2)]
        reply = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert reply == "2: ✅ Marked done"

    @pytest.mark.asyncio
    async def test_each_numbered_item_is_independent(self, monkeypatch, tmp_path):
        """Acting on one commitment must not consume the others.

        This is why numbered actions are not stored in the reaction table, whose
        claim is deliberately message-wide.
        """
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, _ = _stub_rpc({"timestamp": 7200})
        await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        seen = []

        async def handler(action_id, token, context):
            seen.append((action_id, token))
            return "ok"

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager(handler))

        await adapter._handle_envelope(_text_envelope(sender, "/c 1 seen"))
        await adapter._handle_envelope(_text_envelope(sender, "/c 2 done"))
        # Correcting an earlier choice keeps working — these replace buttons
        # that stayed pressable, not a one-shot reaction.
        await adapter._handle_envelope(_text_envelope(sender, "/c 1 done"))

        assert seen == [("cseen", "c-aaa"), ("cdone", "c-bbb"), ("cdone", "c-aaa")]

    @pytest.mark.asyncio
    async def test_a_newer_digest_renumbers_and_wins(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)

        adapter._rpc, _ = _stub_rpc({"timestamp": 8000})
        await adapter.send(sender, "yesterday", metadata=_digest_metadata((("old-1", 1),)))
        adapter._rpc, _ = _stub_rpc({"timestamp": 9000})
        await adapter.send(sender, "today", metadata=_digest_metadata((("new-1", 1),)))

        seen = []

        async def handler(action_id, token, context):
            seen.append(token)
            return "ok"

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager(handler))
        await adapter._handle_envelope(_text_envelope(sender, "/c 1 done"))

        assert seen == ["new-1"], "yesterday's item 1 must not answer today's command"

    @pytest.mark.asyncio
    async def test_unknown_number_answers_without_dispatching(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, calls = _stub_rpc({"timestamp": 7300})

        class Boom:
            @staticmethod
            def get_action_handler(action_id):  # pragma: no cover - must not run
                raise AssertionError("dispatched an unknown number")

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Boom())
        await adapter._handle_envelope(_text_envelope(sender, "/c 9 done"))

        reply = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert "No item 9" in reply

    @pytest.mark.asyncio
    async def test_expired_correlation_does_not_dispatch(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._reaction_feedback_store.store_numbered_actions(
            account=adapter.account,
            chat_id=sender,
            message_timestamp=7400,
            numbered=[(1, "cdone", "c-old", "1 ✅")],
            ttl_seconds=-1,
        )
        adapter._rpc, calls = _stub_rpc({"timestamp": 7401})

        class Boom:
            @staticmethod
            def get_action_handler(action_id):  # pragma: no cover - must not run
                raise AssertionError("dispatched an expired correlation")

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Boom())
        await adapter._handle_envelope(_text_envelope(sender, "/c 1 done"))

        reply = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert "No item 1" in reply

    @pytest.mark.asyncio
    async def test_unauthorized_sender_is_ignored_before_lookup(self, monkeypatch, tmp_path):
        allowed = "+15550000001"
        stranger = "+15559999999"
        _enable(monkeypatch, tmp_path, allowed)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, _ = _stub_rpc({"timestamp": 7500})
        await adapter.send(allowed, "daily brief", metadata=_digest_metadata())

        adapter._rpc, calls = _stub_rpc({"timestamp": 7501})

        class Boom:
            @staticmethod
            def get_action_handler(action_id):  # pragma: no cover - must not run
                raise AssertionError("unauthorized command dispatched")

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Boom())
        await adapter._handle_envelope(_text_envelope(stranger, "/c 1 done"))

        # No dispatch and no reply to the stranger; the command falls through to
        # the ordinary path, where run.py's authorization gate rejects it.
        assert [c for c in calls if c["method"] == "send"] == []

    @pytest.mark.asyncio
    async def test_a_wildcard_allowlist_never_dispatches_commands(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("SIGNAL_ALLOWED_USERS", "*")
        monkeypatch.setenv("SIGNAL_REACTION_FEEDBACK", "true")
        adapter = _make_signal_adapter(monkeypatch)
        adapter._reaction_feedback_store.store_numbered_actions(
            account=adapter.account,
            chat_id=sender,
            message_timestamp=7600,
            numbered=[(1, "cdone", "c-1", "1 ✅")],
            ttl_seconds=60,
        )
        adapter._rpc, calls = _stub_rpc({"timestamp": 7601})

        class Boom:
            @staticmethod
            def get_action_handler(action_id):  # pragma: no cover - must not run
                raise AssertionError("wildcard allowlist dispatched an action")

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: Boom())
        await adapter._handle_envelope(_text_envelope(sender, "/c 1 done"))
        assert [c for c in calls if c["method"] == "send"] == []

    @pytest.mark.asyncio
    async def test_handler_failure_is_reported_not_silent(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, _ = _stub_rpc({"timestamp": 7700})
        await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        async def handler(action_id, token, context):
            raise RuntimeError("open brain unreachable")

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager(handler))
        adapter._rpc, calls = _stub_rpc({"timestamp": 7701})
        await adapter._handle_envelope(_text_envelope(sender, "/c 1 done"))

        reply = next(c for c in calls if c["method"] == "send")["params"]["message"]
        assert "Couldn't mark 1 done" in reply

    @pytest.mark.asyncio
    async def test_correlation_survives_an_adapter_restart(self, monkeypatch, tmp_path):
        sender = "+15550000001"
        _enable(monkeypatch, tmp_path, sender)
        adapter = _make_signal_adapter(monkeypatch)
        adapter._rpc, _ = _stub_rpc({"timestamp": 7800})
        await adapter.send(sender, "daily brief", metadata=_digest_metadata())

        restarted = _make_signal_adapter(monkeypatch)
        restarted._rpc, calls = _stub_rpc({"timestamp": 7801})
        seen = []

        async def handler(action_id, token, context):
            seen.append(token)
            return "ok"

        import hermes_cli.plugins as plugins

        monkeypatch.setattr(plugins, "get_plugin_manager", lambda: _Manager(handler))
        await restarted._handle_envelope(_text_envelope(sender, "/c 1 done"))

        assert seen == ["c-aaa"]
