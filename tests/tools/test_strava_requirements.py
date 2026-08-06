"""check_strava_requirements gates on Strava AND Supabase creds.

Both strava tools read/write open-brain rows over Supabase REST; when the
hosted stack is closed (L6) the Supabase creds disappear from ~/.hermes/.env
and the tools must unregister cleanly instead of being advertised (the schema
tells the model to ALWAYS prefer them) and dying as hung TCP connects.
"""

import pytest

from tools.strava_tool import check_strava_requirements

_STRAVA_VARS = {
    "STRAVA_CLIENT_ID": "12345",
    "STRAVA_CLIENT_SECRET": "s" * 40,
    "STRAVA_REFRESH_TOKEN": "r" * 40,
}
_SUPABASE_VARS = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "aaa.bbb.ccc",
}


def _set_env(monkeypatch, mapping):
    for name in {**_STRAVA_VARS, **_SUPABASE_VARS}:
        monkeypatch.delenv(name, raising=False)
    for name, value in mapping.items():
        monkeypatch.setenv(name, value)


def test_available_with_full_credentials(monkeypatch):
    _set_env(monkeypatch, {**_STRAVA_VARS, **_SUPABASE_VARS})
    assert check_strava_requirements() is True


def test_unavailable_without_supabase_creds(monkeypatch):
    """Post-L6 state: Strava creds still present, Supabase creds deleted."""
    _set_env(monkeypatch, _STRAVA_VARS)
    assert check_strava_requirements() is False


@pytest.mark.parametrize("missing", sorted(_SUPABASE_VARS))
def test_unavailable_when_one_supabase_var_missing(monkeypatch, missing):
    env = {**_STRAVA_VARS, **_SUPABASE_VARS}
    env.pop(missing)
    _set_env(monkeypatch, env)
    assert check_strava_requirements() is False


def test_unavailable_without_strava_creds(monkeypatch):
    _set_env(monkeypatch, _SUPABASE_VARS)
    assert check_strava_requirements() is False
