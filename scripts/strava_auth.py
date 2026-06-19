#!/usr/bin/env python3
"""One-time Strava OAuth setup — gets a refresh token with activity:read_all scope.

Run once:
    python scripts/strava_auth.py

It will print an authorization URL. Open it in a browser, authorize, then paste
the code from the redirect URL back here. The resulting tokens are saved to
~/.hermes/strava_token.json and the refresh token is printed so you can update
~/.hermes/.env.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in ~/.hermes/.env")
    sys.exit(1)

SCOPE = "activity:read_all"
REDIRECT_URI = "http://localhost"

auth_url = (
    f"https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&response_type=code"
    f"&scope={SCOPE}"
)

print("\n1. Open this URL in your browser:\n")
print(f"   {auth_url}\n")
print("2. Click 'Authorize' in Strava.")
print("3. You'll be redirected to a localhost URL that won't load — that's fine.")
print("4. Copy the full redirect URL (e.g. http://localhost/?state=&code=abc123...&scope=...)\n")

redirect = input("Paste the redirect URL here: ").strip()

# Extract the code from the redirect URL
try:
    qs = parse_qs(urlparse(redirect).query)
    code = qs.get("code", [""])[0]
    if not code:
        raise ValueError("no code param")
except Exception:
    # Maybe they pasted just the code
    code = redirect.strip()

if not code:
    print("ERROR: Could not extract code from input.")
    sys.exit(1)

print(f"\nExchanging code for tokens...")
resp = httpx.post(
    "https://www.strava.com/oauth/token",
    data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    },
    timeout=20,
)

if resp.status_code != 200:
    print(f"ERROR {resp.status_code}: {resp.text}")
    sys.exit(1)

data = resp.json()
cache_path = Path.home() / ".hermes" / "strava_token.json"
cache_path.write_text(json.dumps({
    "access_token": data["access_token"],
    "refresh_token": data["refresh_token"],
    "expires_at": data["expires_at"],
}))

print("\n✓ Tokens saved to ~/.hermes/strava_token.json")
print(f"\nScope granted: {data.get('scope', 'unknown')}")
print(f"\nUpdate ~/.hermes/.env with this refresh token:")
print(f"  STRAVA_REFRESH_TOKEN={data['refresh_token']}")

if data["refresh_token"] != os.getenv("STRAVA_REFRESH_TOKEN", ""):
    env_path = Path.home() / ".hermes" / ".env"
    content = env_path.read_text()
    old_line = f"STRAVA_REFRESH_TOKEN={os.getenv('STRAVA_REFRESH_TOKEN', '')}"
    new_line = f"STRAVA_REFRESH_TOKEN={data['refresh_token']}"
    if old_line in content:
        env_path.write_text(content.replace(old_line, new_line))
        print("\n✓ ~/.hermes/.env updated automatically.")
    else:
        print("\n  (Could not auto-update .env — please update manually.)")
else:
    print("\n  (Refresh token unchanged — .env is already correct.)")

print("\nDone! Run strava_sync in hermes to pull your rides into the brain.\n")
