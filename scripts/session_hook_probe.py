from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "COOKIE",
    "AUTH",
)
DEFAULT_OUTPUT_DIR = Path.home() / ".hermes" / "hook-probes"


def _redact_env_value(name: str, value: str) -> str:
    upper_name = name.upper()
    if any(marker in upper_name for marker in SENSITIVE_ENV_MARKERS):
        return f"[REDACTED length={len(value)}]"
    if len(value) > 400:
        return f"{value[:400]}...[truncated total_length={len(value)}]"
    return value


def _interesting_file_hints(env_map: dict[str, str]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for name, value in sorted(env_map.items()):
        if not value:
            continue
        if not (name.upper().endswith("_FILE") or name.upper().endswith("_PATH")):
            continue
        path = Path(value).expanduser()
        info: dict[str, Any] = {
            "env_var": name,
            "path": str(path),
            "exists": path.exists(),
        }
        if path.exists() and path.is_file():
            info["size_bytes"] = path.stat().st_size
        hints.append(info)
    return hints


def build_report(stdin_bytes: bytes, label: str) -> dict[str, Any]:
    env_map = dict(os.environ)
    redacted_env = {name: _redact_env_value(name, value) for name, value in sorted(env_map.items())}
    stdin_preview = stdin_bytes[:2000].decode("utf-8", errors="replace")

    return {
        "label": label,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "stdin": {
            "size_bytes": len(stdin_bytes),
            "preview_utf8": stdin_preview,
        },
        "environment": {
            "count": len(redacted_env),
            "values": redacted_env,
            "file_hints": _interesting_file_hints(env_map),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic hook payload recorder for validating session-end or stop-hook behavior.",
    )
    parser.add_argument("--label", default="hook-probe", help="Short label to include in the saved report.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where JSON probe reports should be written.",
    )
    args = parser.parse_args()

    stdin_bytes = sys.stdin.buffer.read()
    report = build_report(stdin_bytes, label=args.label)

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"_{args.label.replace('/', '_')}.json"
    )
    output_path = output_dir / filename
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps({"status": "ok", "report_path": str(output_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
