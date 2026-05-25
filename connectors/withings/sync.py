#!/usr/bin/env python3
"""Withings Cloud API → kiwi-mem /data/health sync.

OAuth 2.0 Authorization Code Grant.  Tokens cached in ~/.withings_tokens.json.

First run (interactive):
  python3 sync.py --auth

Normal run (non-interactive, cron-safe):
  python3 sync.py

Setup:
  1. Go to https://developer.withings.com/ → Sign Up / Log In
  2. Create a "Web App" or "Desktop App"
  3. Get client_id and client_secret
  4. Put them in ~/.hermes/.env as WITHINGS_CLIENT_ID / WITHINGS_CLIENT_SECRET
  5. Run: python3 sync.py --auth
     This opens a browser for you to authorize.
     After authorizing, the tokens are saved and reused indefinitely.
"""

import os
import sys
import json
import time
import hashlib
import urllib.parse
import webbrowser
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
KIWI_MEM    = "http://127.0.0.1:8080"
WITHINGS_BASE    = "https://wbsapi.withings.net"
AUTH_BASE        = "https://account.withings.com/oauth2_user"

def _load_dotenv() -> dict[str, str]:
    """Read ~/.hermes/.env into a dict (does not pollute os.environ)."""
    env = {}
    env_file = Path("/home/chris/.hermes/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("\"'")
    return env

_dotenv = _load_dotenv()
KIWI_TOKEN       = os.getenv("KIWI_MEM_ACCESS_TOKEN", _dotenv.get("ACCESS_TOKEN", ""))
CLIENT_ID        = os.getenv("WITHINGS_CLIENT_ID", _dotenv.get("WITHINGS_CLIENT_ID", ""))
CLIENT_SECRET    = os.getenv("WITHINGS_CLIENT_SECRET", _dotenv.get("WITHINGS_CLIENT_SECRET", ""))
REDIRECT_URI     = "https://agent.xeon.im/withings/callback"
TOKEN_FILE       = Path("/home/chris/.withings_tokens.json")
LAST_SYNC_FILE   = Path("/home/chris/.withings_last_sync")

HEADERS          = {"Authorization": f"Bearer {KIWI_TOKEN}"}
SCOPES           = "user.metrics"

# Measurement → Health Auto Export metric_name mapping
# see https://developer.withings.com/api-reference/#tag/measure
MEAS_TYPE = {
    1:   "weight_body_mass",          # kg
    5:   "lean_body_mass",            # kg (fat free mass)
    6:   "body_fat_percentage",       # %
    8:   "body_fat_mass",             # kg (fat mass weight) — informational
    9:   "blood_pressure_diastolic",  # we merge 9+10 below
    10:  "blood_pressure_systolic",   # we merge 9+10 below
    76:  "muscle_mass",              # kg
    88:  "bone_mass",                # kg
}


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _load_tokens() -> Optional[dict]:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def _save_tokens(data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    TOKEN_FILE.chmod(0o600)


def _token_refresh(refresh_token: str) -> dict:
    """Exchange refresh_token for new access_token.  Withings refresh tokens
    are long-lived (no expiry unless revoked)."""
    r = httpx.post(f"{WITHINGS_BASE}/v2/oauth2", data={
        "action":        "requesttoken",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=15)
    data = r.json()
    if data.get("status") != 0:
        raise RuntimeError(f"Token refresh failed: {data.get('error', '?')}")
    body = data["body"]
    return {
        "access_token":  body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at":    int(time.time()) + body["expires_in"] - 60,
        "userid":        body.get("userid"),
    }


def _get_valid_token() -> str:
    """Return a valid access_token, refreshing if necessary."""
    tokens = _load_tokens()
    if not tokens:
        raise RuntimeError("No tokens — run with --auth first")
    if tokens.get("expires_at", 0) < time.time():
        tokens = _token_refresh(tokens["refresh_token"])
        _save_tokens(tokens)
    return tokens["access_token"]


def oauth_flow() -> None:
    """Interactive OAuth 2.0 Authorization Code Grant.

    Opens browser to Withings authorize page.  The redirect comes back to
    https://agent.xeon.im/withings/callback (handled by kiwi-mem), which
    writes the code to /tmp/withings_auth_code.  We poll that file.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET in ~/.hermes/.env")
        sys.exit(1)

    # PKCE
    code_verifier = hashlib.sha256(os.urandom(40)).hexdigest()[:128]
    code_challenge = hashlib.sha256(code_verifier.encode()).hexdigest()

    params = {
        "response_type":         "code",
        "client_id":             CLIENT_ID,
        "state":                 "hermes_withings_sync",
        "scope":                 SCOPES,
        "redirect_uri":          REDIRECT_URI,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_BASE}/authorize2?{urllib.parse.urlencode(params)}"
    print("\nOpening browser for Withings authorization...\n")
    print(f"If the browser doesn't open, visit:\n\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for authorization (timeout 120s)...")
    deadline = time.time() + 120
    auth_code = None
    while time.time() < deadline:
        try:
            import shlex, subprocess
            raw = subprocess.check_output(
                ["docker", "compose", "-f", "/home/chris/assistant/docker-compose.yml",
                 "exec", "-T", "kiwi-mem", "cat", "/tmp/withings/auth_code"],
                stderr=subprocess.DEVNULL, timeout=3
            )
            data = json.loads(raw)
            auth_code = data.get("code")
            if auth_code:
                # Clear the file so it's not reused
                subprocess.call(
                    ["docker", "compose", "-f", "/home/chris/assistant/docker-compose.yml",
                     "exec", "-T", "kiwi-mem", "rm", "-f", "/tmp/withings/auth_code"],
                    stderr=subprocess.DEVNULL, timeout=3
                )
                break
        except Exception:
            pass
        time.sleep(1.5)

    if not auth_code:
        print("Timed out waiting for authorization.")
        sys.exit(1)

    # Exchange code for tokens
    r = httpx.post(f"{WITHINGS_BASE}/v2/oauth2", data={
        "action":         "requesttoken",
        "client_id":      CLIENT_ID,
        "client_secret":  CLIENT_SECRET,
        "grant_type":     "authorization_code",
        "code":           auth_code,
        "redirect_uri":   REDIRECT_URI,
        "code_verifier":  code_verifier,
    }, timeout=15)
    data = r.json()
    if data.get("status") != 0:
        print(f"Token exchange failed: {data}")
        sys.exit(1)

    body = data["body"]
    tokens = {
        "access_token":  body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at":    int(time.time()) + body["expires_in"] - 60,
        "userid":        body.get("userid"),
        "scope":         body.get("scope", SCOPES),
    }
    _save_tokens(tokens)
    print(f"✓ Authorized — userid={tokens['userid']}")


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_measurements(access_token: str, last_sync: Optional[int] = None) -> dict:
    """Fetch measurements from Withings API.

    last_sync: Unix timestamp — only fetch data since this time. 0 = full history.
    """
    # startdate/enddate are required.  Use broad range for initial sync,
    # narrow to last_sync for incremental.
    from datetime import timezone as tz_mod
    if last_sync and last_sync > 0:
        start_ts = int(last_sync)
    else:
        start_ts = 946684800  # 2000-01-01
    end_ts = int(datetime.now(tz_mod.utc).timestamp()) + 86400

    params = {
        "action":     "getmeas",
        "meastypes":  "1,5,6,8,9,10,76,88",
        "startdate":  str(start_ts),
        "enddate":    str(end_ts),
    }

    r = httpx.post(
        f"{WITHINGS_BASE}/v2/measure",
        params={"action": params.pop("action")},
        data=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    data = r.json()
    if data.get("status") != 0:
        raise RuntimeError(f"getmeas failed: {data.get('error', '?')}")

    measuregrps = data["body"].get("measuregrps", [])
    return _convert_to_health_api(measuregrps)


def _convert_to_health_api(measuregrps: list) -> dict:
    """Convert Withings measuregrps → Health Auto Export /data/health format."""
    by_date: dict[str, dict[str, list[dict]]] = {}  # {date: {metric_name: [data_points]}}

    for grp in measuregrps:
        grp_date_raw = grp.get("date")  # Unix timestamp
        if not grp_date_raw:
            continue
        dt = datetime.fromtimestamp(grp_date_raw)
        d = dt.strftime("%Y-%m-%d")
        ts_iso = dt.strftime("%Y-%m-%d %H:%M:%S -0400")

        # Collect systolic/diastolic for blood_pressure merge
        bp_sys = None
        bp_dia = None

        for m in grp.get("measures", []):
            mtype = m["type"]
            unit  = m.get("unit", 0)
            value = m["value"]

            # Withings uses 10^x scale; convert to real value
            real_value = float(value) * (10.0 ** float(unit))

            if mtype in (9, 10):  # blood pressure — merge later
                if mtype == 10:
                    bp_sys = int(real_value)
                else:
                    bp_dia = int(real_value)
                continue

            metric_name = MEAS_TYPE.get(mtype)
            if not metric_name:
                continue

            by_date.setdefault(d, {}).setdefault(metric_name, []).append({
                "qty":    round(real_value, 2),
                "date":   ts_iso,
                "source": "Withings",
            })

        # Emit merged blood pressure if both values present
        if bp_sys and bp_dia:
            by_date.setdefault(d, {}).setdefault("blood_pressure", []).append({
                "systolic": bp_sys,
                "diastolic": bp_dia,
                "date":     ts_iso,
                "source":   "Withings",
            })

    # Build metrics array
    metrics = []
    for d in sorted(by_date.keys()):
        for metric_name, data_points in by_date[d].items():
            if metric_name == "blood_pressure":
                metrics.append({
                    "name": "blood_pressure",
                    "units": "mmHg",
                    "data": data_points,
                })
            else:
                metrics.append({
                    "name": metric_name,
                    "units": _units_for(metric_name),
                    "data": data_points,
                })

    return {"data": {"metrics": metrics}}


def _units_for(name: str) -> str:
    m = {
        "weight_body_mass":      "kg",
        "body_fat_percentage":   "%",
        "lean_body_mass":        "kg",
        "body_fat_mass":         "kg",
        "muscle_mass":           "kg",
        "bone_mass":             "kg",
        "blood_pressure":        "mmHg",
    }
    return m.get(name, "count")


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync(last_sync_ts: Optional[int] = None) -> tuple[int, int]:
    """Run a sync cycle.  Returns (measurement_count, http_status)."""
    token  = _get_valid_token()
    body   = fetch_measurements(token, last_sync_ts)

    if not body["data"]["metrics"]:
        return 0, 200

    r = httpx.post(
        f"{KIWI_MEM}/data/health",
        json=body,
        headers=HEADERS,
        timeout=120,
    )
    count = sum(len(m.get("data", [])) for m in body["data"]["metrics"])
    return count, r.status_code


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--auth" in sys.argv:
        oauth_flow()
        # After auth, do a full backfill sync
        print("Running initial sync (full history)...")
        n, status = sync()
        if status == 200:
            LAST_SYNC_FILE.write_text(str(int(time.time())))
            print(f"  ✓ {n} data points synced")
        else:
            print(f"  ✗ HTTP {status}")
        sys.exit(0)

    # Normal sync: only fetch since last run
    last = None
    if LAST_SYNC_FILE.exists():
        last = int(LAST_SYNC_FILE.read_text().strip() or "0")

    try:
        n, status = sync(last)
    except Exception as e:
        print(f"sync failed: {e}", file=sys.stderr)
        sys.exit(1)

    if n > 0:
        LAST_SYNC_FILE.write_text(str(int(time.time())))
    if status != 200:
        print(f"sync HTTP {status}: {n} points", file=sys.stderr)
        sys.exit(1)
    if n > 0:
        print(f"synced {n} data points")
    sys.exit(0)
