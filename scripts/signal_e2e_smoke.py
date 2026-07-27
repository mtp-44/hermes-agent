#!/usr/bin/env python3
"""Guarded live Signal adapter smoke for an isolated Hermes test account.

This target is deliberately opt-in and separate from the deterministic offline
conformance suite.  It sends one challenge through the configured signal-cli
daemon and waits for the allowlisted test user to reply with the challenge shown
on their Signal device.  Neither account identifier nor message body is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class SignalSmokeConfig:
    http_url: str
    account: str
    allowed_user: str
    home_channel: str


def _is_e164(value: str) -> bool:
    return (
        value.startswith("+")
        and value[1:].isdigit()
        and 7 <= len(value[1:]) <= 15
    )


def _is_signal_recipient(value: str) -> bool:
    if _is_e164(value):
        return True
    candidate = value[4:] if value.upper().startswith("PNI:") else value
    try:
        return str(uuid.UUID(candidate)) == candidate.lower()
    except ValueError:
        return False


def _require_private_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("protected handoff file does not exist") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("protected handoff must be a regular, non-symlink file")
    if info.st_uid != os.getuid():
        raise ValueError("protected handoff must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("protected handoff permissions must be 0600 or stricter")


def load_protected_handoff(path: Path) -> SignalSmokeConfig:
    """Load private identifiers without echoing them to stdout or logs."""
    _require_private_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("protected handoff is not readable JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("protected handoff must contain a JSON object")

    required = {
        "signal_http_url",
        "signal_account",
        "signal_allowed_user",
        "signal_home_channel",
    }
    if not required.issubset(raw):
        raise ValueError("protected handoff is missing required Signal fields")

    config = SignalSmokeConfig(
        http_url=str(raw["signal_http_url"]).strip(),
        account=str(raw["signal_account"]).strip(),
        allowed_user=str(raw["signal_allowed_user"]).strip(),
        home_channel=str(raw["signal_home_channel"]).strip(),
    )
    validate_smoke_config(config)
    return config


def validate_smoke_config(config: SignalSmokeConfig) -> None:
    parsed = urlparse(config.http_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Signal smoke endpoint must be an uncredentialed loopback HTTP URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Signal smoke endpoint has an invalid port") from exc
    if not port:
        raise ValueError("Signal smoke endpoint must include an explicit port")

    if not _is_e164(config.account) or not _is_signal_recipient(config.allowed_user):
        raise ValueError(
            "Signal smoke requires an E.164 daemon account and a valid private recipient"
        )
    if config.allowed_user != config.home_channel:
        raise ValueError("Signal allowed user and home channel must identify the same test user")
    if config.account == config.allowed_user:
        raise ValueError("Signal smoke requires separate Hermes and user accounts")


def validate_isolated_home(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    production_root = (Path.home() / ".hermes").resolve()
    if resolved == production_root or production_root in resolved.parents:
        raise ValueError("live Signal smoke refuses the production Hermes home or its profiles")
    if not resolved.is_dir():
        raise ValueError("isolated Hermes home does not exist")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise ValueError("isolated Hermes home permissions must exclude group/other access")
    return resolved


def apply_signal_environment(config: SignalSmokeConfig, hermes_home: Path) -> None:
    for unsafe in ("SIGNAL_ALLOW_ALL_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        if os.environ.get(unsafe, "").strip().lower() in {"1", "true", "yes", "on"}:
            raise ValueError(f"{unsafe} must not be enabled for the live Signal smoke")

    os.environ.update(
        {
            "HERMES_HOME": str(hermes_home),
            "SIGNAL_HTTP_URL": config.http_url,
            "SIGNAL_ACCOUNT": config.account,
            "SIGNAL_ALLOWED_USERS": config.allowed_user,
            "SIGNAL_HOME_CHANNEL": config.home_channel,
            "SIGNAL_REACTIONS": "true",
            "SIGNAL_GROUP_ALLOWED_USERS": "",
            "SIGNAL_ALLOW_ALL_USERS": "false",
            "GATEWAY_ALLOW_ALL_USERS": "false",
        }
    )


async def run_live_smoke(config: SignalSmokeConfig, timeout: float) -> None:
    from gateway.config import Platform, load_gateway_config
    from gateway.platforms.signal import SignalAdapter

    gateway_config = load_gateway_config()
    platform_config = gateway_config.platforms.get(Platform.SIGNAL)
    if not platform_config or not platform_config.enabled:
        raise RuntimeError("isolated configuration did not enable Signal")
    home = platform_config.home_channel
    if not home or home.chat_id != config.home_channel:
        raise RuntimeError("isolated configuration did not load the Signal home channel")

    adapter = SignalAdapter(platform_config)
    acknowledged = asyncio.Event()
    challenge = f"WP2-SIGNAL-E2E-{secrets.token_hex(8)}"

    async def _capture_reply(event):
        if (
            event.source.user_id == config.allowed_user
            and secrets.compare_digest((event.text or "").strip(), challenge)
        ):
            acknowledged.set()
        return None

    adapter.set_message_handler(_capture_reply)
    connected = await adapter.connect()
    if not connected:
        raise RuntimeError("Signal adapter could not connect to the configured daemon")

    try:
        result = await adapter.send(
            config.home_channel,
            "Hermes isolated Signal smoke. Reply with this exact line:\n\n"
            f"{challenge}",
        )
        if not result.success:
            raise RuntimeError("Signal adapter outbound smoke send failed")
        print("signal_e2e=waiting_for_allowlisted_reply")
        try:
            await asyncio.wait_for(acknowledged.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("timed out waiting for the allowlisted Signal reply") from exc
    finally:
        await adapter.disconnect()

    print("signal_e2e=pass outbound=1 inbound=1 identifiers=redacted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement that this sends a real Signal message.",
    )
    parser.add_argument(
        "--secrets-file",
        type=Path,
        required=True,
        help="Mode-0600 JSON handoff containing the test account identifiers.",
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        required=True,
        help="Isolated, non-production HERMES_HOME used for smoke state.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for the allowlisted reply (default: 180).",
    )
    args = parser.parse_args(argv)

    if not args.live:
        parser.error("--live is required; the offline conformance smoke never implies it")
    if args.timeout <= 0 or args.timeout > 900:
        parser.error("--timeout must be between 1 and 900 seconds")

    try:
        config = load_protected_handoff(args.secrets_file)
        hermes_home = validate_isolated_home(args.hermes_home)
        apply_signal_environment(config, hermes_home)
        asyncio.run(run_live_smoke(config, args.timeout))
    except (ValueError, RuntimeError) as exc:
        print(f"signal_e2e=fail reason={exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
