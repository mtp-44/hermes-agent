"""Strava tool — syncs cycling/running activities to the open-brain Supabase.

Env vars (all read from ~/.hermes/.env at runtime):
  STRAVA_CLIENT_ID          Strava app client ID
  STRAVA_CLIENT_SECRET      Strava app client secret
  STRAVA_REFRESH_TOKEN      Initial long-lived refresh token

  SUPABASE_URL              open-brain Supabase project URL
  SUPABASE_SERVICE_ROLE_KEY service-role key for direct REST write access

Token cache lives at ~/.hermes/strava_token.json and is updated after each
OAuth refresh so the newest refresh_token is always persisted there.

Tools registered:
  strava_sync        Fetch recent activities from Strava and sync to open brain
                     (reconcile=true instead re-checks already-synced rows against
                     Strava: rename/stat updates and removal of deleted activities)
  strava_activities  List/search synced activities already in open brain
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
_STRAVA_API_BASE = "https://www.strava.com/api/v3"

_ACTIVITY_TYPES = {"Ride", "Run", "Walk", "Hike", "VirtualRide", "EBikeRide", "MountainBikeRide"}


# ── Config ─────────────────────────────────────────────────────────────────────

def _get_strava_config() -> Dict[str, str]:
    return {
        "client_id": os.getenv("STRAVA_CLIENT_ID", ""),
        "client_secret": os.getenv("STRAVA_CLIENT_SECRET", ""),
        "refresh_token": os.getenv("STRAVA_REFRESH_TOKEN", ""),
    }


def _get_supabase_config():
    return (
        os.getenv("SUPABASE_URL", "").rstrip("/"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    )


def _token_cache_path():
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "strava_token.json"


# ── OAuth token management ──────────────────────────────────────────────────────

def _load_token_cache() -> Dict:
    path = _token_cache_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _save_token_cache(data: Dict):
    path = _token_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _get_valid_access_token() -> str:
    """Return a non-expired Strava access token, refreshing via OAuth if needed."""
    cfg = _get_strava_config()
    if not cfg["client_id"] or not cfg["client_secret"] or not cfg["refresh_token"]:
        raise RuntimeError(
            "Strava credentials missing. Set STRAVA_CLIENT_ID, "
            "STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN in ~/.hermes/.env"
        )

    cache = _load_token_cache()
    # Token is good for 5+ more minutes — reuse it
    if cache.get("access_token") and cache.get("expires_at", 0) > time.time() + 300:
        return cache["access_token"]

    # Use persisted refresh_token if available (may differ from env after rotations)
    refresh_token = cache.get("refresh_token") or cfg["refresh_token"]

    resp = httpx.post(
        _STRAVA_TOKEN_URL,
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    _save_token_cache({
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    })

    logger.info("Strava token refreshed, expires at %s", data["expires_at"])
    return data["access_token"]


# ── Strava API ──────────────────────────────────────────────────────────────────

def _fetch_activities(count: int = 30, after_epoch: Optional[int] = None) -> List[Dict]:
    token = _get_valid_access_token()
    activities: List[Dict] = []
    page = 1
    while len(activities) < count:
        per_page = min(count - len(activities), 200)
        params: Dict[str, Any] = {"per_page": per_page, "page": page}
        if after_epoch:
            params["after"] = after_epoch

        resp = httpx.get(
            f"{_STRAVA_API_BASE}/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return activities


# ── Content formatting ──────────────────────────────────────────────────────────

def _format_content(a: Dict) -> str:
    distance_km = round(a.get("distance", 0) / 1000, 1)
    moving_s = a.get("moving_time", 0)
    hours, rem = divmod(moving_s, 3600)
    mins = rem // 60
    duration = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
    date_str = (a.get("start_date_local") or a.get("start_date", ""))[:10]
    name = a.get("name", "Activity")
    atype = a.get("sport_type") or a.get("type", "Ride")
    elev = round(a.get("total_elevation_gain", 0))

    parts = [f"{atype}: {distance_km} km in {duration} on {date_str} — {name}"]
    if elev:
        parts.append(f"Elevation: {elev} m")
    avg_hr = a.get("average_heartrate")
    if avg_hr:
        parts.append(f"Avg HR: {int(avg_hr)} bpm")
    avg_w = a.get("average_watts")
    if avg_w:
        parts.append(f"Avg power: {int(avg_w)} W")
    avg_speed_kmh = round((a.get("average_speed") or 0) * 3.6, 1)
    if avg_speed_kmh > 0:
        parts.append(f"Avg speed: {avg_speed_kmh} km/h")
    return " | ".join(parts)


def _build_metadata(a: Dict) -> Dict:
    avg_speed = a.get("average_speed")
    meta: Dict[str, Any] = {
        "domain": "fitness",
        "source": "strava",
        "strava_id": a["id"],
        "activity_type": a.get("sport_type") or a.get("type"),
        "name": a.get("name"),
        "date": (a.get("start_date_local") or a.get("start_date", ""))[:10],
        "datetime_utc": a.get("start_date"),
        "distance_m": a.get("distance"),
        "moving_time_s": a.get("moving_time"),
        "elapsed_time_s": a.get("elapsed_time"),
        "elevation_m": a.get("total_elevation_gain"),
        "avg_speed_kmh": round(avg_speed * 3.6, 2) if avg_speed else None,
        "max_speed_kmh": round((a.get("max_speed") or 0) * 3.6, 2) or None,
        "avg_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "avg_watts": a.get("average_watts"),
        "max_watts": a.get("max_watts"),
        "kilojoules": a.get("kilojoules"),
        "calories": a.get("calories"),
        "suffer_score": a.get("suffer_score"),
        "kudos_count": a.get("kudos_count"),
        "achievement_count": a.get("achievement_count"),
        "gear_id": a.get("gear_id"),
        "trainer": a.get("trainer", False),
    }
    return {k: v for k, v in meta.items() if v is not None}


# ── Embeddings ──────────────────────────────────────────────────────────────────
# Must match open_brain/core/embeddings.py: text-embedding-3-small, 1536 dims.
# Rows without embeddings are invisible to query_brain's pgvector search.

_EMBEDDING_MODEL = "openai/text-embedding-3-small"
_OPENROUTER_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"


def _embed_content(text: str) -> Optional[List[float]]:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set — inserting thought without embedding")
        return None
    try:
        resp = httpx.post(
            _OPENROUTER_EMBED_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": _EMBEDDING_MODEL, "input": [text]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as e:
        logger.warning("Embedding failed, inserting without embedding: %s", e)
        return None


# ── Supabase REST helpers ───────────────────────────────────────────────────────

def _sb_headers(key: str, with_prefer: bool = True) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {key}",
        "apikey": key,
        "Content-Type": "application/json",
    }
    if with_prefer:
        h["Prefer"] = "return=representation"
    return h


def _activity_exists(url: str, key: str, strava_id: int) -> bool:
    resp = httpx.get(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key, with_prefer=False),
        params={"metadata->>strava_id": f"eq.{strava_id}", "select": "id", "limit": "1"},
        timeout=10,
    )
    resp.raise_for_status()
    return len(resp.json()) > 0


def _insert_activity(url: str, key: str, activity: Dict) -> Dict:
    content = _format_content(activity)
    payload: Dict[str, Any] = {"content": content, "metadata": _build_metadata(activity)}
    embedding = _embed_content(content)
    if embedding:
        payload["embedding"] = embedding
    resp = httpx.post(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


def _rows_since(url: str, key: str, since_date: str, limit: int = 2000) -> List[Dict]:
    """Every synced Strava row dated on/after since_date (YYYY-MM-DD)."""
    resp = httpx.get(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key, with_prefer=False),
        params={
            "metadata->>source": "eq.strava",
            "metadata->>date": f"gte.{since_date}",
            "select": "id,content,metadata,created_at",
            "order": "created_at.asc",
            "limit": str(limit),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _update_activity(
    url: str, key: str, row_id: str, content: str, metadata: Dict, reembed: bool
) -> None:
    payload: Dict[str, Any] = {"content": content, "metadata": metadata}
    if reembed:
        embedding = _embed_content(content)
        if embedding:
            payload["embedding"] = embedding
        else:
            logger.warning("Row %s updated but embedding refresh failed — vector is stale", row_id)
    resp = httpx.patch(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key, with_prefer=False),
        params={"id": f"eq.{row_id}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()


def _delete_row(url: str, key: str, row_id: str) -> None:
    resp = httpx.delete(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key, with_prefer=False),
        params={"id": f"eq.{row_id}"},
        timeout=15,
    )
    resp.raise_for_status()


def _query_activities(
    url: str,
    key: str,
    limit: int = 20,
    date: Optional[str] = None,
    year: Optional[str] = None,
    activity_type: Optional[str] = None,
) -> List[Dict]:
    params: Dict[str, str] = {
        "metadata->>domain": "eq.fitness",
        "metadata->>source": "eq.strava",
        "select": "id,content,metadata,created_at",
        "order": "metadata->>date.desc",
        "limit": str(limit),
    }
    if date:
        params["metadata->>date"] = f"eq.{date}"
    elif year:
        params["metadata->>date"] = f"like.{year}-*"
    if activity_type:
        params["metadata->>activity_type"] = f"eq.{activity_type}"
    resp = httpx.get(
        f"{url}/rest/v1/thoughts",
        headers=_sb_headers(key, with_prefer=False),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── Tool handlers ───────────────────────────────────────────────────────────────

def _handle_strava_sync(args: dict, **_kw) -> str:
    if args.get("reconcile"):
        return _handle_strava_reconcile(args)

    count = min(int(args.get("count", 30)), 2000)
    activity_types = args.get("activity_types")  # None = all

    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        return tool_error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in ~/.hermes/.env")

    try:
        activities = _fetch_activities(count=count)
    except httpx.HTTPStatusError as e:
        return tool_error(f"Strava API error {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return tool_error(f"Failed to fetch Strava activities: {e}")

    if activity_types:
        if isinstance(activity_types, str):
            activity_types = [t.strip() for t in activity_types.split(",")]
        activities = [
            a for a in activities
            if (a.get("sport_type") or a.get("type", "")) in activity_types
        ]

    synced, skipped, errors = [], [], []
    for a in activities:
        strava_id = a.get("id")
        if not strava_id:
            continue
        try:
            if _activity_exists(supabase_url, supabase_key, strava_id):
                skipped.append(strava_id)
                continue
            _insert_activity(supabase_url, supabase_key, a)
            synced.append({
                "strava_id": strava_id,
                "name": a.get("name"),
                "type": a.get("sport_type") or a.get("type"),
                "date": (a.get("start_date_local") or "")[:10],
                "distance_km": round(a.get("distance", 0) / 1000, 1),
            })
        except Exception as e:
            errors.append({"strava_id": strava_id, "error": str(e)})
            logger.error("Failed to sync activity %s: %s", strava_id, e)

    return json.dumps({
        "synced": len(synced),
        "already_in_brain": len(skipped),
        "errors": len(errors),
        "new_activities": synced,
        "error_details": errors,
    })


# Deleting is the one irreversible half of reconcile: an activity removed on
# Strava can never be re-synced. A large phantom count almost always means the
# Strava fetch came back short, not that the rides really went away.
_MAX_DELETES_ABSOLUTE = 20
_MAX_DELETES_RATIO = 0.25


def _row_summary(row: Dict, reason: str) -> Dict:
    meta = row.get("metadata", {})
    return {
        "row_id": row.get("id"),
        "strava_id": meta.get("strava_id"),
        "date": meta.get("date"),
        "name": meta.get("name"),
        "distance_km": round((meta.get("distance_m") or 0) / 1000, 1),
        "reason": reason,
    }


def _handle_strava_reconcile(args: dict) -> str:
    """Re-check already-synced rows against Strava.

    strava_sync only ever inserts, so anything you change on Strava after a sync
    leaves the brain wrong: a renamed ride keeps its old name, and an activity
    you delete (a double upload from two head units, say) lingers forever as a
    phantom row. This pass repairs all three directions — update, insert, delete
    — over a trailing window.
    """
    days = max(1, min(int(args.get("reconcile_days", 30)), 730))
    confirm_deletes = bool(args.get("confirm_deletes", False))

    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        return tool_error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in ~/.hermes/.env")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    since_date = cutoff.date().isoformat()
    # Fetch two days wider than the window we judge, so an activity sitting on
    # the boundary (Strava filters on UTC, rows are stamped local) is never
    # mistaken for one that was deleted.
    after_epoch = int((cutoff - timedelta(days=2)).timestamp())

    try:
        live = _fetch_activities(count=2000, after_epoch=after_epoch)
    except httpx.HTTPStatusError as e:
        return tool_error(f"Strava API error {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return tool_error(f"Failed to fetch Strava activities: {e}")

    live_by_id = {a["id"]: a for a in live if a.get("id")}

    try:
        rows = _rows_since(supabase_url, supabase_key, since_date)
    except Exception as e:
        return tool_error(f"Failed to read synced activities from open brain: {e}")

    phantoms: List[Dict] = []
    duplicates: List[Dict] = []
    changed: List[tuple] = []
    seen: Dict[int, Dict] = {}

    for row in rows:
        raw_id = (row.get("metadata") or {}).get("strava_id")
        try:
            strava_id = int(raw_id)
        except (TypeError, ValueError):
            continue  # not a synced Strava activity row
        if strava_id in seen:
            duplicates.append(row)  # same activity stored twice — keep the oldest
            continue
        seen[strava_id] = row

        activity = live_by_id.get(strava_id)
        if activity is None:
            phantoms.append(row)
            continue

        new_content = _format_content(activity)
        # Merge rather than replace: keeps detail-only fields (calories, suffer
        # score) that the activity-list endpoint doesn't return.
        new_metadata = {**(row.get("metadata") or {}), **_build_metadata(activity)}
        if new_content != row.get("content") or new_metadata != row.get("metadata"):
            changed.append((row, activity, new_content, new_metadata))

    missing = [
        a for strava_id, a in live_by_id.items()
        if strava_id not in seen and (a.get("start_date_local") or "")[:10] >= since_date
    ]

    removable = (
        [(row, "deleted on Strava") for row in phantoms]
        + [(row, "duplicate row for the same activity") for row in duplicates]
    )
    delete_guard: Optional[str] = None
    if len(removable) > max(_MAX_DELETES_ABSOLUTE, len(rows) * _MAX_DELETES_RATIO):
        delete_guard = (
            f"{len(removable)} of {len(rows)} rows in the window look deleted — that is more "
            "likely a short Strava fetch than a real bulk delete. Nothing was removed. "
            "Re-run with a smaller reconcile_days to inspect a narrower window."
        )

    updated, inserted, deleted, errors = [], [], [], []

    for row, activity, new_content, new_metadata in changed:
        try:
            _update_activity(
                supabase_url, supabase_key, row["id"], new_content, new_metadata,
                reembed=new_content != row.get("content"),
            )
            updated.append({
                "strava_id": (row.get("metadata") or {}).get("strava_id"),
                "date": new_metadata.get("date"),
                "was": row.get("content"),
                "now": new_content,
            })
        except Exception as e:
            errors.append({"row_id": row.get("id"), "op": "update", "error": str(e)})
            logger.error("Reconcile failed to update row %s: %s", row.get("id"), e)

    for activity in missing:
        try:
            _insert_activity(supabase_url, supabase_key, activity)
            inserted.append({
                "strava_id": activity.get("id"),
                "date": (activity.get("start_date_local") or "")[:10],
                "name": activity.get("name"),
                "distance_km": round(activity.get("distance", 0) / 1000, 1),
            })
        except Exception as e:
            errors.append({"strava_id": activity.get("id"), "op": "insert", "error": str(e)})
            logger.error("Reconcile failed to insert activity %s: %s", activity.get("id"), e)

    pending = [_row_summary(row, reason) for row, reason in removable]

    if removable and confirm_deletes and not delete_guard:
        for row, reason in removable:
            try:
                _delete_row(supabase_url, supabase_key, row["id"])
                deleted.append(_row_summary(row, reason))
            except Exception as e:
                errors.append({"row_id": row.get("id"), "op": "delete", "error": str(e)})
                logger.error("Reconcile failed to delete row %s: %s", row.get("id"), e)
        pending = []

    result: Dict[str, Any] = {
        "mode": "reconcile",
        "window_days": days,
        "since": since_date,
        "strava_activities_in_window": len(live_by_id),
        "brain_rows_in_window": len(rows),
        "updated": updated,
        "inserted": inserted,
        "deleted": deleted,
        "errors": errors,
    }
    if delete_guard:
        result["delete_guard"] = delete_guard
        result["pending_deletes"] = pending
    elif pending:
        result["pending_deletes"] = pending
        result["next_step"] = (
            "These rows point at activities that no longer exist on Strava and deleting them "
            "cannot be undone. Show them to the user, and only re-run with confirm_deletes=true "
            "once they agree."
        )
    return json.dumps(result)


def _handle_strava_activities(args: dict, **_kw) -> str:
    limit = min(int(args.get("limit", 20)), 500)
    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        return tool_error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in ~/.hermes/.env")

    try:
        rows = _query_activities(
            supabase_url,
            supabase_key,
            limit=limit,
            date=args.get("date"),
            year=str(args.get("year")) if args.get("year") else None,
            activity_type=args.get("activity_type"),
        )
    except Exception as e:
        return tool_error(f"Failed to query open brain: {e}")

    activities = []
    for row in rows:
        meta = row.get("metadata", {})
        activities.append({
            "date": meta.get("date"),
            "type": meta.get("activity_type"),
            "name": meta.get("name"),
            "distance_km": round((meta.get("distance_m") or 0) / 1000, 1),
            "moving_time_s": meta.get("moving_time_s"),
            "elevation_m": meta.get("elevation_m"),
            "avg_speed_kmh": meta.get("avg_speed_kmh"),
            "avg_heartrate": meta.get("avg_heartrate"),
            "avg_watts": meta.get("avg_watts"),
            "calories": meta.get("calories"),
            "strava_id": meta.get("strava_id"),
            "summary": row.get("content"),
        })

    return json.dumps({"count": len(activities), "activities": activities})


# ── Availability check ──────────────────────────────────────────────────────────

def check_strava_requirements() -> bool:
    cfg = _get_strava_config()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["refresh_token"])


# ── Schemas ─────────────────────────────────────────────────────────────────────

STRAVA_SYNC_SCHEMA = {
    "name": "strava_sync",
    "description": (
        "Fetch recent Strava activities and sync them to the open brain so they can be "
        "searched and compared later. Already-synced activities are skipped (deduplication "
        "by Strava activity ID). Returns a summary of what was synced.\n"
        "Set reconcile=true instead to repair already-synced rows over a trailing window: "
        "renames and corrected stats are patched, activities deleted on Strava (double "
        "uploads, for instance) are flagged for removal, and anything missing is inserted. "
        "Use it whenever the brain disagrees with Strava — e.g. a ride shows up twice. "
        "Deletions are only carried out when the user has agreed and confirm_deletes=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reconcile": {
                "type": "boolean",
                "description": "Run a reconcile pass over already-synced rows instead of a plain sync (default false).",
                "default": False,
            },
            "reconcile_days": {
                "type": "integer",
                "description": "How many trailing days the reconcile pass covers (default 30, max 730).",
                "default": 30,
            },
            "confirm_deletes": {
                "type": "boolean",
                "description": (
                    "Reconcile only. Actually delete the rows whose Strava activity is gone. "
                    "Leave false first to see them under pending_deletes, show the user, and "
                    "only set true once they have agreed — deletion cannot be undone."
                ),
                "default": False,
            },
            "count": {
                "type": "integer",
                "description": "How many recent activities to fetch from Strava (default 30, max 2000). Use a large value to backfill full history.",
                "default": 30,
            },
            "activity_types": {
                "type": "string",
                "description": (
                    "Comma-separated list of activity types to sync, e.g. 'Ride,VirtualRide'. "
                    "Leave empty to sync all types."
                ),
            },
        },
        "required": [],
    },
}

STRAVA_ACTIVITIES_SCHEMA = {
    "name": "strava_activities",
    "description": (
        "List Strava activities already synced to the open brain (full history 2014→present). "
        "Returns structured data (date, distance, elevation, HR, power, etc.) "
        "so you can compare rides, find PRs, or spot trends. "
        "ALWAYS use this (not query_brain) for date-specific or year-specific questions like "
        "'did I ride on 2023-05-23' or 'how many rides in 2024' — filter by date or year."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of activities to return (default 20, max 500).",
                "default": 20,
            },
            "date": {
                "type": "string",
                "description": "Exact date filter, ISO format YYYY-MM-DD, e.g. '2023-05-23'.",
            },
            "year": {
                "type": "integer",
                "description": "Filter to a single year, e.g. 2024.",
            },
            "activity_type": {
                "type": "string",
                "description": "Filter by Strava activity type, e.g. 'Ride', 'VirtualRide', 'Run'.",
            },
        },
        "required": [],
    },
}


# ── Registry ────────────────────────────────────────────────────────────────────

from tools.registry import registry, tool_error

registry.register(
    name="strava_sync",
    toolset="strava",
    schema=STRAVA_SYNC_SCHEMA,
    handler=_handle_strava_sync,
    check_fn=check_strava_requirements,
    emoji="🚴",
)

registry.register(
    name="strava_activities",
    toolset="strava",
    schema=STRAVA_ACTIVITIES_SCHEMA,
    handler=_handle_strava_activities,
    check_fn=check_strava_requirements,
    emoji="📊",
)
