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
  strava_activities  List/search synced activities already in open brain
"""

import json
import logging
import os
import time
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
        "by Strava activity ID). Returns a summary of what was synced."
    ),
    "parameters": {
        "type": "object",
        "properties": {
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
