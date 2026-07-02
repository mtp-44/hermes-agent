"""Tests for add_telegram_free_response_chat — the safe, YAML-round-tripping
alternative to hand-editing telegram.free_response_chats in config.yaml.

Regression coverage for the 2026-07-01 incident: hand-edited list entries
picked up a stray indent three times in one day, each time collapsing
config.yaml parsing entirely (silently dropping every user override) for
hours before anyone noticed.
"""

import os
from unittest.mock import patch

import pytest
import yaml

from hermes_cli.config import add_telegram_free_response_chat


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path):
    env_file = tmp_path / ".env"
    env_file.touch()
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        yield tmp_path


def _config_path(tmp_path):
    return tmp_path / "config.yaml"


def _load(tmp_path):
    return yaml.safe_load(_config_path(tmp_path).read_text(encoding="utf-8"))


def test_appends_to_empty_config(tmp_path):
    add_telegram_free_response_chat("-5433465714")

    data = _load(tmp_path)
    assert data["telegram"]["free_response_chats"] == ["-5433465714"]


def test_appends_to_existing_list_and_stays_parseable(tmp_path):
    _config_path(tmp_path).write_text(
        "telegram:\n"
        "  free_response_chats:\n"
        "  - '-5433465714'\n"
        "  - '-5240111863'\n",
        encoding="utf-8",
    )

    add_telegram_free_response_chat("-5184861251", "Agile")

    data = _load(tmp_path)
    assert data["telegram"]["free_response_chats"] == [
        "-5433465714", "-5240111863", "-5184861251",
    ]
    # comment landed and the file is still valid YAML (round-trip check
    # inside the function already asserts this, but confirm here too)
    assert "# Agile" in _config_path(tmp_path).read_text(encoding="utf-8")


def test_duplicate_chat_id_is_a_noop(tmp_path):
    add_telegram_free_response_chat("-5433465714")
    add_telegram_free_response_chat("-5433465714")

    data = _load(tmp_path)
    assert data["telegram"]["free_response_chats"] == ["-5433465714"]


def test_refuses_to_edit_already_broken_config(tmp_path, capsys):
    _config_path(tmp_path).write_text(
        "telegram:\n"
        "  free_response_chats:\n"
        "  - '-5433465714'\n"
        "   - '-5240111863'\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        add_telegram_free_response_chat("-9999999999")

    # file must be untouched — we refuse rather than compounding the damage
    assert "-9999999999" not in _config_path(tmp_path).read_text(encoding="utf-8")
    assert "Refusing to edit" in capsys.readouterr().err


def test_preserves_other_config_keys(tmp_path):
    _config_path(tmp_path).write_text(
        "model:\n  default: some/model\n"
        "telegram:\n  require_mention: true\n",
        encoding="utf-8",
    )

    add_telegram_free_response_chat("-5433465714")

    data = _load(tmp_path)
    assert data["model"]["default"] == "some/model"
    assert data["telegram"]["require_mention"] is True
    assert data["telegram"]["free_response_chats"] == ["-5433465714"]
