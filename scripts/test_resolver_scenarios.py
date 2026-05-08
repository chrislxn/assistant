"""
Phase 1.0 M2 — Resolver Scenario Tests
========================================
Covers 7 resolver scenarios. All test data uses subject_key LIKE 'test:resolver:%'
and extractor_name='test_resolver'. Script cleans up after itself.
"""

import asyncio
import sys
import os

# Ensure we can import database from the kiwi-mem package directory
# (works both from repo root and from inside the Docker container at /app)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "kiwi-mem"))
sys.path.insert(0, os.path.join(_here, ".."))  # fallback: kiwi-mem is /app
# Docker path: script lives at /app/scripts, modules at /app
sys.path.insert(0, "/app")

from database import (
    init_tables, close_pool, get_pool,
    resolve_candidate,
)

# --- helpers -----------------------------------------------------------

PREFIX = "test:resolver:"
EXTRACTOR = "test_resolver"

def _cid(n: int) -> str:
    """Return the candidate_id globals dict key for scenario n."""
    return f"c{n}"

def _mk_test_title(desc: str) -> str:
    return f"[TEST] resolver: {desc}"

async def _insert_candidate(conn, *, n: int, memory_type: str, source_trust: str,
                              extractor_name: str = EXTRACTOR,
                              subject_key: str = "", predicate_key: str = "",
                              rendered_text: str = "", importance: int = 5,
                              confidence: float = 0.7, status: str = "pending_auto",
                              privacy_level: str = "personal") -> str:
    """Insert a test candidate and return its candidate_id."""
    sk = subject_key or f"{PREFIX}s{n}"
    pk = predicate_key or f"p{n}"
    rt = rendered_text or f"Test rendered_text for scenario {n}"
    row = await conn.fetchrow(
        """
        INSERT INTO memory_candidates
            (memory_type, subject_key, predicate_key, rendered_text,
             source_trust, extractor_name, confidence, importance, status,
             privacy_level)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING candidate_id
        """,
        memory_type, sk, pk, rt,
        source_trust, extractor_name, confidence, importance, status,
        privacy_level,
    )
    return str(row["candidate_id"])

async def _insert_memory_item(conn, *, memory_type: str = "preference",
                                source_trust: str = "user_direct",
                                subject_key: str = "", predicate_key: str = "p0",
                                rendered_text: str = "",
                                status: str = "active") -> str:
    """Insert a test memory_item and return its memory_id."""
    row = await conn.fetchrow(
        """
        INSERT INTO memory_items
            (memory_type, subject_key, predicate_key, rendered_text,
             source_trust, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING memory_id
        """,
        memory_type, subject_key, predicate_key, rendered_text,
        source_trust, status,
    )
    return str(row["memory_id"])


# --- test suite --------------------------------------------------------

PASS = 0
FAIL = 0

def check(scenario: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {detail}" if detail else "")
    else:
        FAIL += 1
        print(f"  ❌ FAIL: {detail}" if detail else f"  ❌ FAIL")
        print(f"     Scenario: {scenario}")


async def run_tests():
    global PASS, FAIL
    await init_tables()
    pool = await get_pool()

    async with pool.acquire() as conn:
        # -- Prepare data ------------------------------------------------
        print("=== Preparing test data ===\n")

        cids = {}

        # S1: user_direct + preference → auto_commit
        cids["c1"] = await _insert_candidate(conn, n=1,
            memory_type="preference", source_trust="user_direct",
            rendered_text="User prefers dark theme for coding",
            importance=7, confidence=0.9)

        # S2: user_direct + unknown → keep_pending (not in _LOW_RISK_TYPES)
        cids["c2"] = await _insert_candidate(conn, n=2,
            memory_type="unknown", source_trust="user_direct",
            rendered_text="Some unknown observation",
            importance=5, confidence=0.8)

        # S3: assistant_inferred + preference → keep_pending
        cids["c3"] = await _insert_candidate(conn, n=3,
            memory_type="preference", source_trust="assistant_inferred",
            extractor_name="hermes_agent",
            rendered_text="User likes spicy food",
            importance=5, confidence=0.7, status="pending")

        # S4: system_generated + health_observation_summary → auto_commit
        cids["c4"] = await _insert_candidate(conn, n=4,
            memory_type="health_observation_summary", source_trust="system_generated",
            extractor_name="health_pipeline",
            rendered_text="2026-05-08: 7000 steps, 7.2h sleep",
            importance=6, confidence=0.95)

        # S5: health_pattern → requires_review
        cids["c5"] = await _insert_candidate(conn, n=5,
            memory_type="health_pattern", source_trust="system_generated",
            extractor_name="health_pipeline",
            rendered_text="User seems to have chronic sleep issues",
            importance=7, confidence=0.6, status="pending")

        # S6: diagnosis-like + conflict (BLOCK-1 regression)
        # First, create an active memory_item
        active_s6 = await _insert_memory_item(conn,
            subject_key=f"{PREFIX}s6", predicate_key="p6",
            rendered_text="Existing: user prefers tabs over spaces")
        # Then candidate with same key + diagnosis keyword
        cids["c6"] = await _insert_candidate(conn, n=6,
            memory_type="preference", source_trust="user_direct",
            subject_key=f"{PREFIX}s6", predicate_key="p6",
            rendered_text="User has clinical diagnosis of dark-mode preference",
            importance=7, confidence=0.9)

        # S7: eligible conflict → old superseded + new inserted
        active_s7 = await _insert_memory_item(conn,
            subject_key=f"{PREFIX}s7", predicate_key="p7",
            rendered_text="Existing: user likes vim")
        cids["c7"] = await _insert_candidate(conn, n=7,
            memory_type="preference", source_trust="user_direct",
            subject_key=f"{PREFIX}s7", predicate_key="p7",
            rendered_text="User now prefers vscode",
            importance=6, confidence=0.9)

        print(f"  7 scenarios prepared.\n")

        # -- Run resolver ------------------------------------------------
        print("=== Running resolver ===\n")

        results = {}
        for label, cid in cids.items():
            r = await resolve_candidate(cid)
            results[label] = r
            print(f"  {label}: action={r['action']}  "
                  f"memory_id={'YES' if r['memory_id'] else '—'}  "
                  f"legacy_id={r['legacy_id'] or '—'}  "
                  f"superseded_id={'YES' if r['superseded_id'] else '—'}")

        # -- Assertions --------------------------------------------------
        print("\n=== Assertions ===\n")

        # S1: user_direct + preference → committed
        s1 = results["c1"]
        check("S1", s1["action"] == "auto_commit", "S1 action=auto_commit")
        check("S1", s1["memory_id"] is not None, "S1 memory_id not None")
        check("S1", s1["legacy_id"] is not None, "S1 legacy_id not None — dual-write OK")

        # S2: user_direct + unknown → keep_pending
        s2 = results["c2"]
        check("S2", s2["action"] == "keep_pending", "S2 action=keep_pending")
        check("S2", s2["memory_id"] is None, "S2 no memory_items write")
        check("S2", s2["legacy_id"] is None, "S2 no memories write")

        # S3: assistant_inferred + hermes_agent → keep_pending
        s3 = results["c3"]
        check("S3", s3["action"] == "keep_pending", "S3 action=keep_pending")
        check("S3", s3["memory_id"] is None, "S3 no memory_items write")

        # S4: system_generated + health_obs_summary → auto_commit
        s4 = results["c4"]
        check("S4", s4["action"] == "auto_commit", "S4 action=auto_commit")
        check("S4", s4["memory_id"] is not None, "S4 memory_id not None")
        check("S4", s4["legacy_id"] is not None, "S4 health obs dual-write OK")

        # S4: Verify health_pattern / health_baseline NOT created
        hp_count = await conn.fetchval(
            "SELECT count(*) FROM memory_candidates WHERE memory_type = 'health_pattern' AND extractor_name = $1",
            EXTRACTOR,
        )
        check("S4", hp_count == 0, "S4 no health_pattern created")

        # S5: health_pattern → requires_review
        s5 = results["c5"]
        check("S5", s5["action"] == "requires_review", "S5 action=requires_review")
        check("S5", s5["memory_id"] is None, "S5 no memory_items write")
        cs5 = await conn.fetchval(
            "SELECT status FROM memory_candidates WHERE candidate_id = $1",
            cids["c5"],
        )
        check("S5", cs5 == "requires_review", f"S5 candidate.status={cs5}")

        # S6: diagnosis + conflict → old item remains active (BLOCK-1)
        s6 = results["c6"]
        check("S6", s6["action"] == "keep_pending",
              f"S6 action={s6['action']} (keep_pending — diagnosis blocked auto_commit)")
        check("S6", s6["memory_id"] is None, "S6 no memory_items write")
        # Verify old item still active
        old6 = await conn.fetchrow(
            "SELECT status, valid_to FROM memory_items WHERE memory_id = $1",
            active_s6,
        )
        check("S6", old6 is not None, "S6 old item still exists")
        check("S6", old6["status"] == "active",
              f"S6 old item status={old6['status']} (active)")
        check("S6", old6["valid_to"] is None,
              f"S6 old item valid_to={'NULL' if old6['valid_to'] is None else 'NOT NULL'} (NULL)")

        # S7: eligible conflict → old superseded + new inserted
        s7 = results["c7"]
        check("S7", s7["action"] == "auto_commit", "S7 action=auto_commit")
        check("S7", s7["memory_id"] is not None, "S7 new item inserted")
        check("S7", s7["superseded_id"] == active_s7,
              f"S7 superseded_id={s7['superseded_id'][:8] if s7['superseded_id'] else 'None'}... matches old")
        old7 = await conn.fetchrow(
            "SELECT status, valid_to FROM memory_items WHERE memory_id = $1",
            active_s7,
        )
        check("S7", old7["status"] == "superseded",
              f"S7 old item status={old7['status']} (superseded)")
        check("S7", old7["valid_to"] is not None, "S7 old item valid_to IS NOT NULL")

    # -- Cleanup ---------------------------------------------------------
    print("\n=== Cleanup ===\n")

    # Collect legacy_ids produced by auto_commits
    legacy_ids = [r["legacy_id"] for r in results.values() if r["legacy_id"]]

    async with pool.acquire() as conn:
        # Delete test memory_items
        r1 = await conn.execute(
            "DELETE FROM memory_items WHERE subject_key LIKE $1",
            f"{PREFIX}%",
        )
        print(f"  memory_items deleted: {r1.split()[-1]} rows")

        # Delete test candidates
        r2 = await conn.execute(
            "DELETE FROM memory_candidates WHERE extractor_name = $1",
            EXTRACTOR,
        )
        print(f"  memory_candidates deleted: {r2.split()[-1]} rows")

        # Delete test legacy memories by the exact IDs produced
        if legacy_ids:
            r3 = await conn.execute(
                "DELETE FROM memories WHERE id = ANY($1)",
                legacy_ids,
            )
            print(f"  memories deleted: {r3.split()[-1]} rows (IDs: {legacy_ids})")
        else:
            print("  memories: no legacy IDs to clean")

    # -- Verify cleanup --------------------------------------------------
    print("\n=== Verify cleanup ===\n")

    async with pool.acquire() as conn:
        c1 = await conn.fetchval(
            "SELECT count(*) FROM memory_items WHERE subject_key LIKE $1",
            f"{PREFIX}%",
        )
        check("cleanup", c1 == 0,
              f"memory_items test rows: {c1} (expect 0)")

        c2 = await conn.fetchval(
            "SELECT count(*) FROM memory_candidates WHERE extractor_name = $1",
            EXTRACTOR,
        )
        check("cleanup", c2 == 0,
              f"memory_candidates test rows: {c2} (expect 0)")

        # If we had legacy_ids, verify they're gone
        if legacy_ids:
            c3 = await conn.fetchval(
                "SELECT count(*) FROM memories WHERE id = ANY($1)",
                legacy_ids,
            )
            check("cleanup", c3 == 0,
                  f"memories test rows: {c3} (expect 0)")

    await close_pool()

    # -- Report ----------------------------------------------------------
    print(f"\n{'='*40}")
    total = PASS + FAIL
    print(f"  {total} assertions:  {PASS} PASS  /  {FAIL} FAIL")
    print(f"{'='*40}")

    if FAIL > 0:
        print("\n❌ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("\n✅ ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(run_tests())
