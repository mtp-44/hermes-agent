"""Regression coverage for collection-time HERMES_HOME isolation."""

from __future__ import annotations

import os
from pathlib import Path


_COLLECTION_HERMES_HOME = os.environ.get("HERMES_HOME")


def test_collection_time_hermes_home_uses_session_temp(pytestconfig):
    session_home = getattr(pytestconfig, "_hermes_session_home", None)

    assert session_home
    assert _COLLECTION_HERMES_HOME == session_home
    assert Path(session_home).exists()
    assert Path(session_home).name.startswith("hermes-pytest-session-")
    assert Path(session_home) != Path.home() / ".hermes"


def test_per_test_hermes_home_still_overrides_session_temp(pytestconfig):
    session_home = getattr(pytestconfig, "_hermes_session_home", None)

    assert session_home
    assert os.environ["HERMES_HOME"] != session_home
    assert Path(os.environ["HERMES_HOME"]).name == "hermes_test"
