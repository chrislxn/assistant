"""Phase 1.5-M3a: validate schema + admin queue filter for observation_only.

Creates real test candidates via direct DB insert (observation_only, expired,
pending, requires_review), then verifies admin API behavior via HTTP.

Requires asyncpg + DATABASE_URL. Runs inside the kiwi-mem Docker container.
ACCESS_TOKEN is read from environment or .env.

Usage:
    DATABASE_URL=... ACCESS_TOKEN=xxx python3 scripts/test_observation_m3a.py
"""

import asyncio
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "")
KIWI_BASE = os.getenv("KIWI_BASE", "http://127.0.0.1:8080").rstrip("/")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

_HEADERS = {"User-Agent": "kiwi-m3a-test/1.0"}
_SSL_CONTEXT = ssl.create_default_context()

TIMESTAMP = str(int(time.time()))
TEST_TAG = f"[TEST_M3A] observation_m3a_{TIMESTAMP}"

TEST_CANDIDATES = [
    {"status": "pending",          "valid_to": None,                               "memory_type": "test_m3a"},
    {"status": "requires_review",  "valid_to": None,                               "memory_type": "test_m3a"},
    {"status": "observation_only", "valid_to": "NOW() + INTERVAL '7 days'",         "memory_type": "emotional_observation"},
    {"status": "expired",          "valid_to": "NOW() - INTERVAL '1 day'",          "memory_type": "thought_observation"},
]

# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def _resolve_token():
    if ACCESS_TOKEN:
        return ACCESS_TOKEN
    for path in (".env", "../.env"):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("ACCESS_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""

# ---------------------------------------------------------------------------
# HTTP helpers (used from inside Docker container → localhost)
# ---------------------------------------------------------------------------

def _get(path: str, **params):
    qs_parts = []
    for k, v in params.items():
        if v is not None and v != "":
            qs_parts.append(f"{k}={urllib.request.quote(str(v))}")
    url = f"{KIWI_BASE}{path}?token={TOKEN}"
    if qs_parts:
        url += "&" + "&".join(qs_parts)
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:200], "_status": e.code}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict):
    url = f"{KIWI_BASE}{path}?token={TOKEN}"
    data = json.dumps(body).encode()
    headers = {**_HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:200], "_status": e.code}
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# Database helpers (asyncpg, for direct candidate insert + cleanup)
# ---------------------------------------------------------------------------

async def _insert_test_candidate(pool, spec: dict) -> str:
    """Insert a test candidate with specified status and optional valid_to.
    Returns the candidate_id UUID string."""
    import uuid
    cid = str(uuid.uuid4())

    valid_to_sql = spec["valid_to"]  # raw SQL expression or None
    if valid_to_sql == "NOW() + INTERVAL '7 days'":
        row = await pool.fetchrow(
            """INSERT INTO memory_candidates
               (candidate_id, status, memory_type, rendered_text, subject_key,
                source_trust, privacy_level, confidence, importance, valid_to)
               VALUES ($1, $2, $3, $4, $5, 'system_generated', 'personal', 0.7, 5,
                       NOW() + INTERVAL '7 days')
               RETURNING candidate_id""",
            cid, spec["status"], spec["memory_type"],
            f"{TEST_TAG} {spec['status']} candidate",
            f"eval:m3a:{spec['status']}",
        )
    elif valid_to_sql == "NOW() - INTERVAL '1 day'":
        row = await pool.fetchrow(
            """INSERT INTO memory_candidates
               (candidate_id, status, memory_type, rendered_text, subject_key,
                source_trust, privacy_level, confidence, importance, valid_to)
               VALUES ($1, $2, $3, $4, $5, 'system_generated', 'personal', 0.7, 5,
                       NOW() - INTERVAL '1 day')
               RETURNING candidate_id""",
            cid, spec["status"], spec["memory_type"],
            f"{TEST_TAG} {spec['status']} candidate",
            f"eval:m3a:{spec['status']}",
        )
    else:
        row = await pool.fetchrow(
            """INSERT INTO memory_candidates
               (candidate_id, status, memory_type, rendered_text, subject_key,
                source_trust, privacy_level, confidence, importance)
               VALUES ($1, $2, $3, $4, $5, 'system_generated', 'personal', 0.7, 5)
               RETURNING candidate_id""",
            cid, spec["status"], spec["memory_type"],
            f"{TEST_TAG} {spec['status']} candidate",
            f"eval:m3a:{spec['status']}",
        )
    return str(row["candidate_id"])


async def _cleanup_all(pool, candidate_ids: list):
    """Delete all test candidates and verify cleanup.

    Order matters: delete child tables (memory_items) and related data
    (memories) before deleting parent (memory_candidates), so fallback
    subqueries against memory_candidates still have rows to match.
    """
    # 1. Delete memory_items by known source_candidate_ids
    for cid in candidate_ids:
        await pool.execute("DELETE FROM memory_items WHERE source_candidate_id = $1::uuid", cid)

    # 2. Delete legacy memories by test tag
    await pool.execute("DELETE FROM memories WHERE content LIKE $1", f"%{TEST_TAG}%")

    # 3. Delete any stray memory_items linked to test candidates (fallback)
    await pool.execute(
        "DELETE FROM memory_items WHERE source_candidate_id::text IN "
        "(SELECT candidate_id::text FROM memory_candidates WHERE subject_key LIKE 'eval:m3a:%')"
    )

    # 4. Delete memory_candidates by ID + subject_key fallback
    for cid in candidate_ids:
        await pool.execute("DELETE FROM memory_candidates WHERE candidate_id = $1::uuid", cid)
    await pool.execute("DELETE FROM memory_candidates WHERE subject_key LIKE 'eval:m3a:%'")

    # 5. Verify all three tables
    rem_c = await pool.fetchval(
        "SELECT count(*) FROM memory_candidates WHERE subject_key LIKE 'eval:m3a:%'"
    )
    rem_m = await pool.fetchval(
        "SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TEST_TAG}%"
    )
    rem_i = await pool.fetchval(
        "SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TEST_TAG}%"
    )
    return rem_c, rem_m, rem_i

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def main():
    global TOKEN
    TOKEN = _resolve_token()
    if not TOKEN:
        print("ERROR: ACCESS_TOKEN not set", file=sys.stderr)
        sys.exit(2)

    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)

    failures = 0
    checks = 0

    def check(name, condition, detail=""):
        nonlocal failures, checks
        checks += 1
        if condition:
            print(f"  ✅ {name}{' — ' + detail if detail else ''}")
        else:
            failures += 1
            print(f"  ❌ {name}{' — ' + detail if detail else ''}")

    print("Phase 1.5-M3a: schema + admin queue filter\n")

    # Setup: insert 4 test candidates
    candidate_ids = {}
    for spec in TEST_CANDIDATES:
        cid = await _insert_test_candidate(pool, spec)
        candidate_ids[spec["status"]] = cid
        print(f"  Created {spec['status']}: {cid}")

    obs_id = candidate_ids["observation_only"]
    exp_id = candidate_ids["expired"]
    pend_id = candidate_ids["pending"]
    req_id = candidate_ids["requires_review"]

    print("")

    try:
        # ==================================================================
        # A. Schema
        # ==================================================================
        print("A. Schema: valid_to column on memory_candidates")
        row = await pool.fetchrow("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'memory_candidates' AND column_name = 'valid_to'
        """)
        check("A1: valid_to column exists", row is not None,
              f"{row['data_type']}, nullable={row['is_nullable']}" if row else "NOT FOUND")
        print("")

        # ==================================================================
        # B. Default admin queue
        # ==================================================================
        print("B. Default admin queue returns pending + requires_review only")
        resp = _get("/admin/candidates", limit=50)
        if "error" in resp:
            check("B: admin list accessible", False, str(resp.get("error", ""))[:80])
        else:
            candidates = resp.get("candidates", [])
            cids = {c.get("candidate_id") for c in candidates}
            statuses = {c.get("status") for c in candidates}
            check("B1: default list returns results", len(candidates) > 0,
                  f"{len(candidates)} candidates")
            check("B2: pending test candidate visible", pend_id in cids)
            check("B3: requires_review test candidate visible", req_id in cids)
            check("B4: observation_only NOT in default", obs_id not in cids)
            check("B5: expired NOT in default", exp_id not in cids)
        print("")

        # ==================================================================
        # C. Explicit status queries
        # ==================================================================
        print("C. Explicit status queries")
        resp = _get("/admin/candidates", status="observation_only", limit=10)
        cids_obs = {c.get("candidate_id") for c in resp.get("candidates", [])}
        check("C1: ?status=observation_only includes test candidate", obs_id in cids_obs)

        resp = _get("/admin/candidates", status="expired", limit=10)
        cids_exp = {c.get("candidate_id") for c in resp.get("candidates", [])}
        check("C2: ?status=expired includes test candidate", exp_id in cids_exp)

        resp = _get("/admin/candidates", status="pending,requires_review", limit=10)
        cids_pr = {c.get("candidate_id") for c in resp.get("candidates", [])}
        check("C3: ?status=pending,requires_review includes pending", pend_id in cids_pr)
        check("C4: ?status=pending,requires_review includes requires_review", req_id in cids_pr)
        check("C5: ?status=pending,requires_review excludes observation_only", obs_id not in cids_pr)
        check("C6: ?status=pending,requires_review excludes expired", exp_id not in cids_pr)
        print("")

        # ==================================================================
        # D. Commit protection
        # ==================================================================
        print("D. Admin commit rejects observation_only / expired")

        resp = _post(f"/admin/candidates/{obs_id}/commit", {})
        st = resp.get("_status", 0)
        check("D1: observation_only commit rejected (409)", st == 409,
              f"status={st} msg={str(resp.get('error',''))[:80]}")
        body_text = str(resp.get("error", "")) + " " + str(resp.get("detail", ""))
        check("D2: observation_only guard message present",
              "short-term observations" in body_text)

        resp = _post(f"/admin/candidates/{exp_id}/commit", {})
        st = resp.get("_status", 0)
        check("D3: expired commit rejected (409)", st == 409,
              f"status={st} msg={str(resp.get('error',''))[:80]}")

        # Verify no memory_items written
        mi_count = await pool.fetchval(
            "SELECT count(*) FROM memory_items WHERE source_candidate_id = $1::uuid", obs_id
        )
        check("D4: no memory_items row for observation_only after commit attempt", mi_count == 0)

        mi_count2 = await pool.fetchval(
            "SELECT count(*) FROM memory_items WHERE source_candidate_id = $1::uuid", exp_id
        )
        check("D5: no memory_items row for expired after commit attempt", mi_count2 == 0)

        # Verify no legacy memories written
        mem_count = await pool.fetchval(
            "SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TEST_TAG}%"
        )
        check("D6: no legacy memories rows for test candidates", mem_count == 0)

        # Verify statuses unchanged
        obs_status = await pool.fetchval(
            "SELECT status FROM memory_candidates WHERE candidate_id = $1::uuid", obs_id
        )
        check("D7: observation_only status unchanged after commit attempt",
              obs_status == "observation_only",
              f"status={obs_status}")
        print("")

        # ==================================================================
        # E. Regression
        # ==================================================================
        print("E. Existing behavior regression")
        resp = _get("/admin/candidates", status="pending", limit=10)
        check("E1: ?status=pending works", "error" not in resp)
        resp = _get("/admin/candidates", status="requires_review", limit=10)
        check("E2: ?status=requires_review works", "error" not in resp)
        print("")

    finally:
        # ==================================================================
        # F. Cleanup
        # ==================================================================
        print("F. Cleanup")
        all_ids = list(candidate_ids.values())
        rem_c, rem_m, rem_i = await _cleanup_all(pool, all_ids)
        check("F1: memory_candidates remaining = 0", rem_c == 0, f"remaining={rem_c}")
        check("F2: memories remaining = 0", rem_m == 0, f"remaining={rem_m}")
        check("F3: memory_items remaining = 0", rem_i == 0, f"remaining={rem_i}")
        await pool.close()
        print("")

    # ==================================================================
    # Summary
    # ==================================================================
    total = checks
    passed = total - failures
    print(f"RESULTS: {passed}/{total} PASS")
    if failures:
        print(f"         {failures} FAILURES")
        sys.exit(1)
    else:
        print("         ALL PASSED ✅")
        sys.exit(0)


if __name__ == "__main__":
    TOKEN = ""
    asyncio.run(main())
