#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/run_tests.sh" -j 4 \
  tests/hermes_cli/test_dashboard_auth_middleware.py \
  tests/hermes_cli/test_dashboard_auth_password_login.py \
  tests/hermes_cli/test_dashboard_auth_ws_auth.py \
  tests/hermes_cli/test_dashboard_auth_prefix.py \
  tests/hermes_cli/test_web_server_host_header.py \
  tests/hermes_cli/test_dashboard_register.py \
  tests/hermes_cli/test_web_server_files.py \
  tests/hermes_cli/test_pwa_action_routes.py \
  tests/gateway/test_message_actions.py \
  tests/plugins/test_openbrain_query_brain_format_plugin.py \
  tests/plugins/test_openbrain_commands_plugin.py
