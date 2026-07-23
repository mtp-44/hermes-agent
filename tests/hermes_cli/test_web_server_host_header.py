"""Tests for GHSA-ppp5-vxwm-4cf7 — Host-header validation.

DNS rebinding defence: a victim browser that has the dashboard open
could be tricked into fetching from an attacker-controlled hostname
that TTL-flips to 127.0.0.1. Same-origin / CORS checks won't help —
the browser now treats the attacker origin as same-origin. Validating
the Host header at the application layer rejects the attack.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_repo = str(Path(__file__).resolve().parents[1])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


class TestHostHeaderValidator:
    """Unit test the _is_accepted_host helper directly — cheaper and
    more thorough than spinning up the full FastAPI app."""

    def test_loopback_bind_accepts_loopback_names(self):
        from hermes_cli.web_server import _is_accepted_host

        for bound in ("127.0.0.1", "localhost", "::1"):
            for host_header in (
                "127.0.0.1", "127.0.0.1:9119",
                "localhost", "localhost:9119",
                "[::1]", "[::1]:9119",
            ):
                assert _is_accepted_host(host_header, bound), (
                    f"bound={bound} must accept host={host_header}"
                )

    def test_loopback_bind_rejects_attacker_hostnames(self):
        """The core rebinding defence: attacker-controlled hosts that
        TTL-flip to 127.0.0.1 must be rejected."""
        from hermes_cli.web_server import _is_accepted_host

        for bound in ("127.0.0.1", "localhost"):
            for attacker in (
                "evil.example",
                "evil.example:9119",
                "rebind.attacker.test:80",
                "localhost.attacker.test",  # subdomain trick
                "127.0.0.1.evil.test",  # lookalike IP prefix
                "",  # missing Host
            ):
                assert not _is_accepted_host(attacker, bound), (
                    f"bound={bound} must reject attacker host={attacker!r}"
                )

    def test_zero_zero_bind_accepts_anything(self):
        """0.0.0.0 means operator explicitly opted into all-interfaces
        (requires --insecure). No Host-layer defence is possible — rely
        on operator network controls."""
        from hermes_cli.web_server import _is_accepted_host

        for host in ("10.0.0.5", "evil.example", "my-server.corp.net"):
            assert _is_accepted_host(host, "0.0.0.0")
            assert _is_accepted_host(host + ":9119", "0.0.0.0")

    def test_explicit_non_loopback_bind_requires_exact_match(self):
        """If the operator bound to a specific non-loopback hostname,
        the Host header must match exactly."""
        from hermes_cli.web_server import _is_accepted_host

        assert _is_accepted_host("my-server.corp.net", "my-server.corp.net")
        assert _is_accepted_host("my-server.corp.net:9119", "my-server.corp.net")
        # Different host — reject
        assert not _is_accepted_host("evil.example", "my-server.corp.net")
        # Loopback — reject (we bound to a specific non-loopback name)
        assert not _is_accepted_host("localhost", "my-server.corp.net")

    def test_case_insensitive_comparison(self):
        """Host headers are case-insensitive per RFC — accept variations."""
        from hermes_cli.web_server import _is_accepted_host

        assert _is_accepted_host("LOCALHOST", "127.0.0.1")
        assert _is_accepted_host("LocalHost:9119", "127.0.0.1")


class TestHostHeaderMiddleware:
    """End-to-end test via the FastAPI app — verify the middleware
    rejects bad Host headers with 400."""

    def test_rebinding_request_rejected(self):
        from fastapi.testclient import TestClient
        from hermes_cli.web_server import app

        # Simulate start_server having set the bound_host
        app.state.bound_host = "127.0.0.1"
        try:
            client = TestClient(app)
            # The TestClient sends Host: testserver by default — which is
            # NOT a loopback alias, so the middleware must reject it.
            resp = client.get(
                "/api/status",
                headers={"Host": "evil.example"},
            )
            assert resp.status_code == 400
            assert "Invalid Host header" in resp.json()["detail"]
        finally:
            # Clean up so other tests don't inherit the bound_host
            if hasattr(app.state, "bound_host"):
                del app.state.bound_host

    def test_legit_loopback_request_accepted(self):
        from fastapi.testclient import TestClient
        from hermes_cli.web_server import app

        app.state.bound_host = "127.0.0.1"
        try:
            client = TestClient(app)
            # /api/status is in _PUBLIC_API_PATHS — passes auth — so the
            # only thing that can reject is the host header middleware
            resp = client.get(
                "/api/status",
                headers={"Host": "localhost:9119"},
            )
            # Either 200 (endpoint served) or some other non-400 —
            # just not the host-rejection 400
            assert resp.status_code != 400 or (
                "Invalid Host header" not in resp.json().get("detail", "")
            )
        finally:
            if hasattr(app.state, "bound_host"):
                del app.state.bound_host

    def test_no_bound_host_skips_validation(self):
        """If app.state.bound_host isn't set (e.g. running under test
        infra without calling start_server), middleware must pass through
        rather than crash."""
        from fastapi.testclient import TestClient
        from hermes_cli.web_server import app

        # Make sure bound_host isn't set
        if hasattr(app.state, "bound_host"):
            del app.state.bound_host

        client = TestClient(app)
        resp = client.get("/api/status")
        # Should get through to the status endpoint, not a 400
        assert resp.status_code != 400


class TestWebSocketHostOriginGuard:
    """WebSocket upgrades must enforce the same dashboard boundary as HTTP."""

    def test_rebinding_websocket_host_is_rejected(self, monkeypatch):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        import hermes_cli.web_server as ws

        monkeypatch.setattr(ws.app.state, "bound_host", "127.0.0.1", raising=False)
        monkeypatch.setattr(ws, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)

        client = TestClient(ws.app)
        url = f"/api/events?token={ws._SESSION_TOKEN}&channel=security-test"
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                url,
                headers={
                    "Host": "evil.example",
                    "Origin": "http://evil.example",
                },
            ):
                pass

        assert exc.value.code == 4403

    def test_rebinding_websocket_origin_is_rejected(self, monkeypatch):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        import hermes_cli.web_server as ws

        monkeypatch.setattr(ws.app.state, "bound_host", "127.0.0.1", raising=False)
        monkeypatch.setattr(ws, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)

        client = TestClient(ws.app)
        url = f"/api/events?token={ws._SESSION_TOKEN}&channel=security-test"
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                url,
                headers={
                    "Host": "localhost:9119",
                    "Origin": "http://evil.example",
                },
            ):
                pass

        assert exc.value.code == 4403

    def test_loopback_websocket_host_and_origin_are_accepted(self, monkeypatch):
        from fastapi.testclient import TestClient

        import hermes_cli.web_server as ws

        monkeypatch.setattr(ws.app.state, "bound_host", "127.0.0.1", raising=False)
        monkeypatch.setattr(ws, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)

        client = TestClient(ws.app)
        url = f"/api/events?token={ws._SESSION_TOKEN}&channel=security-test"
        with client.websocket_connect(
            url,
            headers={
                "Host": "localhost:9119",
                "Origin": "http://localhost:9119",
            },
        ):
            pass

    def test_nonsecret_websocket_health_path_returns_version(self, monkeypatch):
        from fastapi.testclient import TestClient

        import hermes_cli.web_server as ws

        monkeypatch.setattr(ws.app.state, "bound_host", "127.0.0.1", raising=False)
        client = TestClient(ws.app)
        with client.websocket_connect(
            "/api/ws-health",
            headers={
                "Host": "localhost:9119",
                "Origin": "http://localhost:9119",
            },
        ) as connection:
            assert connection.receive_json() == {"ok": True, "version": ws.__version__}


class TestDeclaredPublicHost:
    """Loopback bind behind a TLS-terminating reverse proxy (tailscale serve,
    Caddy): the operator-declared public hostname is accepted as Host — and
    only via the explicit third argument, which start_server couples to
    forcing the auth gate on (so the widened allowlist never exposes an
    unauthenticated surface)."""

    def test_declared_host_accepted_on_loopback_bind(self):
        from hermes_cli.web_server import _is_accepted_host

        for host_header in (
            "mini.tail1234.ts.net",
            "mini.tail1234.ts.net:443",
            "MINI.TAIL1234.TS.NET",
        ):
            assert _is_accepted_host(
                host_header, "127.0.0.1", "mini.tail1234.ts.net"
            ), f"declared public host must be accepted: {host_header!r}"

    def test_attacker_hosts_still_rejected_with_declaration(self):
        from hermes_cli.web_server import _is_accepted_host

        for attacker in (
            "evil.example",
            "mini.tail1234.ts.net.evil.example",  # suffix trick
            "evil-mini.tail1234.ts.net:443x",  # not an exact hostname match
            "",
        ):
            assert not _is_accepted_host(
                attacker, "127.0.0.1", "mini.tail1234.ts.net"
            ), f"must reject {attacker!r}"

    def test_no_declaration_keeps_loopback_allowlist(self):
        from hermes_cli.web_server import _is_accepted_host

        assert not _is_accepted_host("mini.tail1234.ts.net", "127.0.0.1", "")
        assert _is_accepted_host("localhost:9119", "127.0.0.1", "")

    def test_declared_public_host_forces_auth_gate(self, monkeypatch):
        """start_server derives auth_required = non-loopback OR declared
        public host; verify the helper resolves the env declaration."""
        import hermes_cli.web_server as ws

        monkeypatch.setenv(
            "HERMES_DASHBOARD_PUBLIC_URL", "https://mini.tail1234.ts.net"
        )
        assert ws._declared_public_host() == "mini.tail1234.ts.net"
        monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "")

    def test_websocket_guard_accepts_declared_public_host(self, monkeypatch):
        """Loopback bind behind a reverse proxy: the WS Host/Origin guard must
        honour the declared public hostname exactly like the HTTP middleware —
        FastAPI HTTP middleware does not run for WebSocket routes, so the
        check is repeated in _ws_host_origin_reason (regression: the PWA's
        gateway socket 4403'd through tailscale serve while HTTP passed)."""
        from starlette.datastructures import Headers

        import hermes_cli.web_server as ws

        monkeypatch.setattr(ws.app.state, "bound_host", "127.0.0.1", raising=False)
        monkeypatch.setattr(
            ws.app.state, "public_host", "mini.tail1234.ts.net", raising=False
        )

        class _FakeWs:
            headers = Headers(
                {
                    "host": "mini.tail1234.ts.net",
                    "origin": "https://mini.tail1234.ts.net",
                }
            )

        assert ws._ws_host_origin_reason(_FakeWs()) is None

        class _EvilWs:
            headers = Headers(
                {"host": "evil.example", "origin": "https://evil.example"}
            )

        assert ws._ws_host_origin_reason(_EvilWs()) is not None
