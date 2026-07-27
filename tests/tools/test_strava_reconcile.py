"""Tests for strava_sync's reconcile pass (tools/strava_tool.py).

strava_sync only inserts, so the brain silently drifts from Strava: a renamed
ride keeps its old name, and an activity deleted on Strava (a double upload from
two head units, say) lingers as a phantom row forever. _handle_strava_reconcile
repairs update/insert/delete, with deletion gated behind confirm_deletes.
"""

import json
from unittest.mock import patch

import pytest

from tools import strava_tool


# ── fixtures ───────────────────────────────────────────────────────────────

def _activity(strava_id, name="Morning Ride", distance=120780.0, date="2026-07-26"):
    return {
        "id": strava_id,
        "name": name,
        "type": "Ride",
        "sport_type": "Ride",
        "distance": distance,
        "moving_time": 17152,
        "elapsed_time": 19838,
        "total_elevation_gain": 1337.0,
        "average_speed": 7.05,
        "start_date": f"{date}T05:45:13Z",
        "start_date_local": f"{date}T07:45:13Z",
    }


def _row(row_id, activity):
    return {
        "id": row_id,
        "content": strava_tool._format_content(activity),
        "metadata": strava_tool._build_metadata(activity),
        "created_at": f"{activity['start_date_local'][:10]}T20:00:00Z",
    }


@pytest.fixture
def reconcile_env():
    """Patch every network edge; yield the recorded writes."""
    calls = {"updated": [], "inserted": [], "deleted": []}

    with patch.object(strava_tool, "_get_supabase_config", return_value=("https://sb.test", "key")), \
         patch.object(strava_tool, "_update_activity",
                      side_effect=lambda u, k, rid, c, m, reembed: calls["updated"].append((rid, c, reembed))), \
         patch.object(strava_tool, "_insert_activity",
                      side_effect=lambda u, k, a: calls["inserted"].append(a["id"])), \
         patch.object(strava_tool, "_delete_row",
                      side_effect=lambda u, k, rid: calls["deleted"].append(rid)):
        yield calls


def _run(live, rows, **args):
    with patch.object(strava_tool, "_fetch_activities", return_value=live), \
         patch.object(strava_tool, "_rows_since", return_value=rows):
        return json.loads(strava_tool._handle_strava_reconcile(dict(args)))


# ── deletion of activities removed on Strava ───────────────────────────────

class TestPhantomRows:
    """A row whose Strava activity is gone."""

    def test_phantom_is_reported_not_deleted_without_confirmation(self, reconcile_env):
        gone = _activity(19471110175)
        kept = _activity(19474561707, distance=120650.0)
        result = _run([kept], [_row("row-gone", gone), _row("row-kept", kept)])

        assert reconcile_env["deleted"] == []
        assert result["deleted"] == []
        pending = result["pending_deletes"]
        assert [p["strava_id"] for p in pending] == [19471110175]
        assert pending[0]["reason"] == "deleted on Strava"
        assert "confirm_deletes=true" in result["next_step"]

    def test_phantom_is_deleted_once_confirmed(self, reconcile_env):
        gone = _activity(19471110175)
        kept = _activity(19474561707, distance=120650.0)
        result = _run([kept], [_row("row-gone", gone), _row("row-kept", kept)],
                      confirm_deletes=True)

        assert reconcile_env["deleted"] == ["row-gone"]
        assert [d["strava_id"] for d in result["deleted"]] == [19471110175]
        assert "pending_deletes" not in result

    def test_second_row_for_the_same_activity_is_removable(self, reconcile_env):
        activity = _activity(19474561707)
        rows = [_row("row-first", activity), _row("row-second", activity)]
        result = _run([activity], rows, confirm_deletes=True)

        assert reconcile_env["deleted"] == ["row-second"]  # oldest survives
        assert result["deleted"][0]["reason"] == "duplicate row for the same activity"


class TestDeleteGuard:
    """A short Strava fetch must never wipe the window."""

    def test_bulk_phantoms_are_refused_even_with_confirmation(self, reconcile_env):
        rows = [_row(f"row-{i}", _activity(1000 + i)) for i in range(40)]
        result = _run([], rows, confirm_deletes=True)

        assert reconcile_env["deleted"] == []
        assert result["deleted"] == []
        assert "delete_guard" in result
        assert len(result["pending_deletes"]) == 40

    def test_small_window_wiped_clean_is_still_allowed(self, reconcile_env):
        rows = [_row(f"row-{i}", _activity(1000 + i)) for i in range(3)]
        _run([], rows, confirm_deletes=True)

        assert len(reconcile_env["deleted"]) == 3


# ── drift in activities that still exist ───────────────────────────────────

class TestChangedActivities:

    def test_rename_patches_content_and_reembeds(self, reconcile_env):
        stored = _activity(19474561707, name="Morning Ride")
        renamed = _activity(19474561707, name="2026 Arber Radmarathon Route C")
        result = _run([renamed], [_row("row-1", stored)])

        assert len(reconcile_env["updated"]) == 1
        row_id, content, reembed = reconcile_env["updated"][0]
        assert row_id == "row-1"
        assert "Arber Radmarathon" in content
        assert reembed is True
        assert result["updated"][0]["now"] == content

    def test_unchanged_activity_is_left_alone(self, reconcile_env):
        activity = _activity(19474561707)
        result = _run([activity], [_row("row-1", activity)])

        assert reconcile_env["updated"] == []
        assert result["updated"] == []
        assert "pending_deletes" not in result

    def test_metadata_only_change_updates_without_reembedding(self, reconcile_env):
        stored = _activity(19474561707)
        richer = {**_activity(19474561707), "kudos_count": 12}
        result = _run([richer], [_row("row-1", stored)])

        assert len(reconcile_env["updated"]) == 1
        assert reconcile_env["updated"][0][2] is False  # content identical
        assert result["updated"][0]["was"] == result["updated"][0]["now"]

    def test_detail_only_metadata_is_preserved_on_update(self, reconcile_env):
        """calories/suffer_score come from the detail endpoint — a list-based
        reconcile must merge, not wipe them."""
        stored_row = _row("row-1", _activity(19474561707, name="Morning Ride"))
        stored_row["metadata"]["calories"] = 2800
        renamed = _activity(19474561707, name="Arber Radmarathon")

        captured = {}
        with patch.object(strava_tool, "_update_activity",
                          side_effect=lambda u, k, rid, c, m, reembed: captured.update(m)):
            _run([renamed], [stored_row])

        assert captured["calories"] == 2800
        assert captured["name"] == "Arber Radmarathon"


# ── activities missing from the brain ──────────────────────────────────────

class TestMissingActivities:

    def test_activity_in_window_but_not_in_brain_is_inserted(self, reconcile_env):
        known = _activity(19474561707)
        fresh = _activity(19480000000, name="Evening Ride", date="2026-07-27")
        result = _run([known, fresh], [_row("row-1", known)])

        assert reconcile_env["inserted"] == [19480000000]
        assert result["inserted"][0]["name"] == "Evening Ride"

    def test_boundary_activity_outside_the_window_is_not_inserted(self, reconcile_env):
        """The Strava fetch runs 2 days wider than the judged window so boundary
        rides aren't read as deleted — those extras must not be inserted."""
        known = _activity(19474561707)
        old = _activity(19400000000, date="2020-01-01")
        result = _run([known, old], [_row("row-1", known)])

        assert reconcile_env["inserted"] == []
        assert result["inserted"] == []


# ── plumbing ───────────────────────────────────────────────────────────────

class TestDispatchAndArgs:

    def test_sync_handler_routes_reconcile_to_the_reconcile_pass(self):
        with patch.object(strava_tool, "_handle_strava_reconcile", return_value="{}") as reconcile:
            strava_tool._handle_strava_sync({"reconcile": True, "reconcile_days": 7})
        reconcile.assert_called_once_with({"reconcile": True, "reconcile_days": 7})

    def test_plain_sync_does_not_reconcile(self):
        with patch.object(strava_tool, "_handle_strava_reconcile") as reconcile, \
             patch.object(strava_tool, "_get_supabase_config", return_value=("", "")):
            strava_tool._handle_strava_sync({})
        reconcile.assert_not_called()

    def test_window_is_clamped_and_fetched_wider_than_it_is_judged(self, reconcile_env):
        with patch.object(strava_tool, "_fetch_activities", return_value=[]) as fetch, \
             patch.object(strava_tool, "_rows_since", return_value=[]) as rows_since:
            result = json.loads(strava_tool._handle_strava_reconcile({"reconcile_days": 5000}))

        assert result["window_days"] == 730
        fetched_after = fetch.call_args.kwargs["after_epoch"]
        judged_since = rows_since.call_args[0][2]
        assert fetched_after < strava_tool.datetime.fromisoformat(
            judged_since).replace(tzinfo=strava_tool.timezone.utc).timestamp()

    def test_missing_supabase_config_is_an_error(self):
        with patch.object(strava_tool, "_get_supabase_config", return_value=("", "")):
            assert "SUPABASE_URL" in strava_tool._handle_strava_reconcile({})

    def test_strava_fetch_failure_is_reported_not_raised(self):
        with patch.object(strava_tool, "_get_supabase_config", return_value=("https://sb.test", "k")), \
             patch.object(strava_tool, "_fetch_activities", side_effect=RuntimeError("boom")):
            assert "boom" in strava_tool._handle_strava_reconcile({})

    def test_reconcile_params_are_declared_on_the_sync_schema(self):
        props = strava_tool.STRAVA_SYNC_SCHEMA["parameters"]["properties"]
        assert {"reconcile", "reconcile_days", "confirm_deletes"} <= set(props)
