"""Phase 1.4-M2b: memory_items shadow comparison.

Creates paired test data in both memories and memory_items tables,
then compares privacy behavior between legacy retrieval and the new
memory_items retrieval helpers.

Uses asyncpg directly (not HTTP API) because search_memory_items() and
get_recent_memory_items() are not exposed via any endpoint.

Usage:
    DATABASE_URL=postgresql://... python3 scripts/eval_memory_items_shadow.py
"""

import asyncio
import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(2)

LEVELS = ("public_like", "personal", "sensitive", "restricted", "sealed")

ACTORS = {
    "hermes_agent":  {"visible": {"public_like", "personal"},                 "hidden": {"sensitive", "restricted", "sealed"}},
    "claude_mcp":    {"visible": {"public_like", "personal", "sensitive"},     "hidden": {"restricted", "sealed"}},
    "telegram_bot":  {"visible": {"public_like", "personal", "sensitive"},     "hidden": {"restricted", "sealed"}},
    "api_client":    {"visible": {"public_like", "personal", "sensitive", "restricted"}, "hidden": {"sealed"}},
    "unknown_actor": {"visible": {"public_like", "personal"},                 "hidden": {"sensitive", "restricted", "sealed"}},
    "local_bot":     {"visible": {"public_like", "personal", "sensitive", "restricted"}, "hidden": {"sealed"}},
}

EXCLUDE_TESTS = [
    {
        "case_id": "exclude_api_client_restricted",
        "actor": "api_client",
        "exclude": {"restricted"},
        "expected_visible": {"public_like", "personal", "sensitive"},
        "expected_hidden": {"restricted", "sealed"},
    },
    {
        "case_id": "exclude_claude_mcp_sensitive",
        "actor": "claude_mcp",
        "exclude": {"sensitive"},
        "expected_visible": {"public_like", "personal"},
        "expected_hidden": {"sensitive", "restricted", "sealed"},
    },
]

# Synthetic content — explicitly non-realistic
SYNTHETIC_NOTES = {
    "public_like":    "synthetic public-domain reference note — no personal data",
    "personal":       "synthetic private-note reference — no real private data",
    "sensitive":      "synthetic secret-domain reference — no real secret",
    "restricted":     "synthetic private-domain reference — no real health data",
    "sealed":         "synthetic sealed-domain reference — no real financial data",
}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_POOL = None


async def _get_pool():
    global _POOL
    if _POOL is None:
        import asyncpg
        _POOL = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    return _POOL


async def _insert_legacy(pool, title: str, content: str, privacy_level: str) -> int:
    row = await pool.fetchrow(
        """INSERT INTO memories (title, content, importance, privacy_level, source, source_trust, memory_type, status, subject_key)
           VALUES ($1, $2, 1, $3, 'eval_shadow', 'system_generated', 'test_eval', 'active', $4)
           RETURNING id""",
        title, content, privacy_level, f"eval:shadow:{privacy_level}",
    )
    return row["id"]


async def _insert_memory_item(pool, title: str, content: str, privacy_level: str) -> str:
    row = await pool.fetchrow(
        """INSERT INTO memory_items (rendered_text, privacy_level, memory_type, source_trust, subject_key, importance, status)
           VALUES ($1, $2, 'test_eval', 'system_generated', $3, 1, 'active')
           RETURNING memory_id""",
        content, privacy_level, f"eval:shadow:{privacy_level}",
    )
    return str(row["memory_id"])


async def setup_test_data(anchor: str):
    """Create 5 paired test records. Returns (legacy_ids, item_ids)."""
    legacy_ids = []
    item_ids = []
    pool = await _get_pool()

    for level in LEVELS:
        title = f"[SHADOW] memory_items_bridge {level}"
        content = f"{anchor} — {level}: {SYNTHETIC_NOTES[level]}"  # no tag embedded (match by level in content)

        lid = await _insert_legacy(pool, title, content, level)
        legacy_ids.append(lid)

        mid = await _insert_memory_item(pool, title, content, level)
        item_ids.append(mid)

    return legacy_ids, item_ids


async def cleanup_test_data(legacy_ids, item_ids):
    """Delete test records and verify. Returns (legacy_del, legacy_rem, items_del, items_rem)."""
    pool = await _get_pool()

    legacy_del = 0
    for lid in legacy_ids:
        result = await pool.execute("DELETE FROM memories WHERE id = $1", lid)
        legacy_del += 1

    items_del = 0
    for mid in item_ids:
        result = await pool.execute("DELETE FROM memory_items WHERE memory_id = $1::uuid", mid)
        items_del += 1

    # Fallback: delete by eval:shadow subject_key
    await pool.execute("DELETE FROM memories WHERE subject_key LIKE 'eval:shadow:%' AND source = 'eval_shadow'")
    await pool.execute("DELETE FROM memory_items WHERE subject_key LIKE 'eval:shadow:%' AND source_trust = 'system_generated' AND memory_type = 'test_eval'")

    legacy_rem = await pool.fetchval(
        "SELECT count(*) FROM memories WHERE subject_key LIKE 'eval:shadow:%' AND source = 'eval_shadow'"
    )
    items_rem = await pool.fetchval(
        "SELECT count(*) FROM memory_items WHERE subject_key LIKE 'eval:shadow:%' AND source_trust = 'system_generated' AND memory_type = 'test_eval'"
    )
    return legacy_del, legacy_rem, items_del, items_rem


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def _visible_levels(results: list[dict], content_contains: dict[str, str]) -> set[str]:
    """Determine which privacy levels are visible in results by content match."""
    visible = set()
    for r in results:
        content = r.get("content", "") or r.get("rendered_text", "") or ""
        for level, needle in content_contains.items():
            if needle in content:
                visible.add(level)
    return visible


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def main():
    from database import (
        search_memories, get_recent_memories,
        search_memory_items, get_recent_memory_items,
        get_allowed_privacy_levels,
        close_pool,
    )

    anchor = f"kiwi_shadow_bridge_{int(time.time())}"

    # Content needles for matching results back to privacy levels
    # Each test content contains: "{anchor} — {level}: synthetic ..."
    content_contains = {level: f"{anchor} — {level}:" for level in LEVELS}

    summary = {
        "total_cases": 0,
        "passed": 0,
        "failed": 0,
        "legacy_leak_count": 0,
        "items_leak_count": 0,
        "mismatch_count": 0,
        "cases": [],
        "cleanup": {},
    }

    legacy_ids = []
    item_ids = []

    try:
        # Setup
        legacy_ids, item_ids = await setup_test_data(anchor)
        if len(legacy_ids) < 5 or len(item_ids) < 5:
            print(f"ERROR: only {len(legacy_ids)} legacy / {len(item_ids)} items created", file=sys.stderr)
            sys.exit(2)

        # ================================================================
        # A. Search path — all actors
        # ================================================================
        for actor, rules in ACTORS.items():
            expected_v = rules["visible"]
            expected_h = rules["hidden"]

            # Legacy search
            legacy_results = await search_memories(anchor, limit=20, track_recall=False, actor=actor)
            legacy_visible = _visible_levels(legacy_results, content_contains)

            # Items search
            items_results = await search_memory_items(anchor, limit=20, actor=actor)
            items_visible = _visible_levels(items_results, content_contains)

            legacy_unexpected = legacy_visible & expected_h
            items_unexpected = items_visible & expected_h
            mismatch = (legacy_visible != items_visible)

            case_pass = (not mismatch and not legacy_unexpected and not items_unexpected)

            summary["total_cases"] += 1
            if case_pass:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
            if legacy_unexpected:
                summary["legacy_leak_count"] += len(legacy_unexpected)
            if items_unexpected:
                summary["items_leak_count"] += len(items_unexpected)
            if mismatch:
                summary["mismatch_count"] += 1

            summary["cases"].append({
                "case_id": f"search_{actor}",
                "actor": actor,
                "path": "search",
                "legacy_visible": sorted(legacy_visible),
                "items_visible": sorted(items_visible),
                "expected_visible": sorted(expected_v),
                "mismatch": mismatch,
                "legacy_unexpected": sorted(legacy_unexpected),
                "items_unexpected": sorted(items_unexpected),
            })

        # ================================================================
        # B. Recent path — all actors
        # ================================================================
        for actor, rules in ACTORS.items():
            expected_v = rules["visible"]
            expected_h = rules["hidden"]

            legacy_results = await get_recent_memories(limit=50, actor=actor)
            # Filter to anchor only
            anchor_results = [r for r in legacy_results if anchor in (r.get("content", "") or "")]
            legacy_visible = _visible_levels(anchor_results, content_contains)

            items_results = await get_recent_memory_items(limit=50, actor=actor)
            anchor_items = [r for r in items_results if anchor in (r.get("content", "") or "")]
            items_visible = _visible_levels(anchor_items, content_contains)

            legacy_unexpected = legacy_visible & expected_h
            items_unexpected = items_visible & expected_h
            mismatch = (legacy_visible != items_visible)

            case_pass = (not mismatch and not legacy_unexpected and not items_unexpected)

            summary["total_cases"] += 1
            if case_pass:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
            if legacy_unexpected:
                summary["legacy_leak_count"] += len(legacy_unexpected)
            if items_unexpected:
                summary["items_leak_count"] += len(items_unexpected)
            if mismatch:
                summary["mismatch_count"] += 1

            summary["cases"].append({
                "case_id": f"recent_{actor}",
                "actor": actor,
                "path": "recent",
                "legacy_visible": sorted(legacy_visible),
                "items_visible": sorted(items_visible),
                "expected_visible": sorted(expected_v),
                "mismatch": mismatch,
                "legacy_unexpected": sorted(legacy_unexpected),
                "items_unexpected": sorted(items_unexpected),
            })

        # ================================================================
        # C. exclude_privacy tests
        # ================================================================
        for et in EXCLUDE_TESTS:
            # Legacy
            legacy_results = await search_memories(anchor, limit=20, track_recall=False,
                                                   actor=et["actor"], exclude_privacy=et["exclude"])
            legacy_visible = _visible_levels(legacy_results, content_contains)

            # Items
            items_results = await search_memory_items(anchor, limit=20, actor=et["actor"],
                                                      exclude_privacy=et["exclude"])
            items_visible = _visible_levels(items_results, content_contains)

            legacy_unexpected = legacy_visible & et["expected_hidden"]
            items_unexpected = items_visible & et["expected_hidden"]
            mismatch = (legacy_visible != items_visible)

            case_pass = (not mismatch and not legacy_unexpected and not items_unexpected)

            summary["total_cases"] += 1
            if case_pass:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
            if legacy_unexpected:
                summary["legacy_leak_count"] += len(legacy_unexpected)
            if items_unexpected:
                summary["items_leak_count"] += len(items_unexpected)
            if mismatch:
                summary["mismatch_count"] += 1

            summary["cases"].append({
                "case_id": et["case_id"],
                "actor": et["actor"],
                "path": "search_exclude",
                "legacy_visible": sorted(legacy_visible),
                "items_visible": sorted(items_visible),
                "expected_visible": sorted(et["expected_visible"]),
                "mismatch": mismatch,
                "legacy_unexpected": sorted(legacy_unexpected),
                "items_unexpected": sorted(items_unexpected),
            })

    finally:
        ld, lr, id_, ir_ = await cleanup_test_data(legacy_ids, item_ids)
        summary["cleanup"] = {
            "legacy_deleted": ld,
            "items_deleted": id_,
            "legacy_remaining": lr,
            "items_remaining": ir_,
        }
        try:
            await close_pool()
        except Exception:
            pass

    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    cleanup_ok = (summary["cleanup"]["legacy_remaining"] == 0 and
                  summary["cleanup"]["items_remaining"] == 0)
    if summary["failed"] > 0 or not cleanup_ok:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
