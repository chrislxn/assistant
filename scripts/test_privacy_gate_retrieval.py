"""Phase 1.1-M3: end-to-end retrieval privacy gate tests.

Validates that SQL-layer actor privacy gate correctly filters legacy memories
returned by search_memories(), get_recent_memories(), and the /debug/memories
HTTP API (including title path and exclude_privacy intersection).

Uses the kiwi-mem HTTP API — no asyncpg or direct DB access needed.
Token is read from ACCESS_TOKEN env var, not hardcoded.

Usage:
    ACCESS_TOKEN=xxx python3 scripts/test_privacy_gate_retrieval.py
    ACCESS_TOKEN=xxx KIWI_BASE=http://localhost:8080 python3 scripts/test_privacy_gate_retrieval.py
"""

import os
import sys
import json
import urllib.request
import urllib.error
import ssl

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KIWI_BASE = os.getenv("KIWI_BASE", "https://agent.xeon.im").rstrip("/")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")


def _load_token_from_dotenv() -> str:
    """Fallback: read ACCESS_TOKEN from ../.env (relative to project root)."""
    for path in (".env", "../.env"):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("ACCESS_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


if not ACCESS_TOKEN:
    ACCESS_TOKEN = _load_token_from_dotenv()

if not ACCESS_TOKEN:
    print("ERROR: ACCESS_TOKEN not set. Export it or create .env file.")
    sys.exit(2)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "kiwi-mem-test/1.0 (Phase-1.1-M3)"}
_SSL_CONTEXT = ssl.create_default_context()


def _get(path: str, **params) -> dict:
    """GET a kiwi-mem endpoint, return parsed JSON."""
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items() if v is not None)
    url = f"{KIWI_BASE}{path}?token={ACCESS_TOKEN}"
    if qs:
        url += "&" + qs
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:200]}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict) -> dict:
    """POST to a kiwi-mem endpoint, return parsed JSON."""
    url = f"{KIWI_BASE}{path}?token={ACCESS_TOKEN}"
    data = json.dumps(body).encode()
    headers = {**_HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:200]}
    except Exception as e:
        return {"error": str(e)}


def _delete(path: str) -> dict:
    """DELETE a kiwi-mem resource."""
    url = f"{KIWI_BASE}{path}?token={ACCESS_TOKEN}"
    req = urllib.request.Request(url, headers=_HEADERS, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:200]}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

TEST_TITLE = "[TEST] privacy gate"        # prefix, actual: "[TEST] privacy gate <level>"
TEST_CONTENT_PREFIX = "[TEST] privacy_gate_"  # actual: "[TEST] privacy_gate_<level>"
LEVELS = ("public_like", "personal", "sensitive", "restricted", "sealed")
ACTORS = {
    "hermes_agent": {
        "visible":  {"public_like", "personal"},
        "hidden":   {"sensitive", "restricted", "sealed"},
    },
    "claude_mcp": {
        "visible":  {"public_like", "personal", "sensitive"},
        "hidden":   {"restricted", "sealed"},
    },
    "api_client": {
        "visible":  {"public_like", "personal", "sensitive", "restricted"},
        "hidden":   {"sealed"},
    },
    "telegram_bot": {
        "visible":  {"public_like", "personal", "sensitive"},
        "hidden":   {"restricted", "sealed"},
    },
    "unknown_actor": {
        "visible":  {"public_like", "personal"},
        "hidden":   {"sensitive", "restricted", "sealed"},
    },
    "local_bot": {
        "visible":  {"public_like", "personal", "sensitive", "restricted"},
        "hidden":   {"sealed"},
    },
}


def _parse_results(resp: dict) -> list[dict]:
    """Extract result list from /debug/memories response (handles both search & title paths)."""
    if "error" in resp:
        return []
    # search/recent returns "results", title path also returns "results"
    return resp.get("results", [])


def _level_from_result(r: dict) -> str:
    """Extract privacy_level from a result dict."""
    return r.get("privacy_level", "") or "personal"


# ---------------------------------------------------------------------------
# Setup / cleanup
# ---------------------------------------------------------------------------

_test_memory_ids: list[int] = []


def setup_test_memories():
    """Create one test memory per privacy_level. Stores IDs in _test_memory_ids.

    Uses POST response memory_id directly (not title lookup) because sealed
    memories are invisible via the default actor title lookup.
    """
    global _test_memory_ids
    _test_memory_ids = []

    print("--- Setup: creating test memories ---")
    for level in LEVELS:
        resp = _post("/debug/memories", {
            "title": f"{TEST_TITLE} {level}",
            "content": f"{TEST_CONTENT_PREFIX}{level} — Phase 1.1-M3 automated verification. privacy_level={level}",
            "importance": 1,
            "privacy_level": level,
            "source": "privacy_gate_test",
            "source_trust": "system_generated",
            "memory_type": "test_privacy_gate",
            "status": "active",
            "subject_key": "test:privacy_gate",
        })
        if "error" in resp:
            print(f"  ❌ Failed to create [{level}]: {resp.get('error')} {resp.get('detail','')}")
            continue
        mem_id = resp.get("memory_id")
        if mem_id:
            _test_memory_ids.append(mem_id)
            print(f"  ✅ [{level}] id={mem_id}")
        else:
            print(f"  ⚠️  [{level}] created but no memory_id in response: {resp}")

    print(f"  Created {len(_test_memory_ids)}/{len(LEVELS)} test memories\n")


def cleanup_test_memories():
    """Delete all test memories. Must be called even on failure."""
    print("--- Cleanup ---")
    deleted = 0
    for rid in _test_memory_ids:
        result = _delete(f"/debug/memories/{rid}")
        status = result.get("status", result.get("error", "?"))
        if status == "deleted":
            deleted += 1
        print(f"  DELETE id={rid}: {status}")

    # Fallback: also search by title for any missed test memories
    for level in LEVELS:
        resp = _get("/debug/memories", title=f"{TEST_TITLE} {level}", limit=5)
        for r in _parse_results(resp):
            rid = r["id"]
            if rid not in _test_memory_ids:
                result = _delete(f"/debug/memories/{rid}")
                if result.get("status") == "deleted":
                    deleted += 1
                print(f"  DELETE id={rid} ({level}) [fallback]")

    # Count remaining test memories via title for non-sealed levels
    remaining = 0
    for level in LEVELS:
        resp = _get("/debug/memories", title=f"{TEST_TITLE} {level}", limit=5)
        remaining += len(_parse_results(resp))
    if remaining == 0:
        print(f"  ✅ cleanup confirmed: 0 test memories remaining (deleted {deleted})\n")
    else:
        print(f"  ❌ cleanup FAILED: {remaining} test memories still exist (deleted {deleted})\n")


# ---------------------------------------------------------------------------
# Search / recent / title retrieval helpers
# ---------------------------------------------------------------------------

def search_memories(query: str, actor: str, limit: int = 10, exclude_privacy: str = "") -> list[dict]:
    """Call /debug/memories?q=...&actor=... — exercises search_memories() code path."""
    params: dict = {"q": query, "actor": actor, "limit": limit}
    if exclude_privacy:
        params["exclude_privacy"] = exclude_privacy
    resp = _get("/debug/memories", **params)
    return _parse_results(resp)


def recent_memories(actor: str, limit: int = 20, exclude_privacy: str = "") -> list[dict]:
    """Call /debug/memories?actor=... (no q) — exercises get_recent_memories() code path.

    exclude_privacy: comma-separated string (HTTP API format; DB helper uses set[str]).
    """
    params: dict = {"actor": actor, "limit": limit}
    if exclude_privacy:
        params["exclude_privacy"] = exclude_privacy
    resp = _get("/debug/memories", **params)
    return _parse_results(resp)


def title_lookup(title: str, actor: str, exclude_privacy: str = "") -> list[dict]:
    """Call /debug/memories?title=...&actor=... — exercises title exact-match path."""
    params: dict = {"title": title, "actor": actor, "limit": 5}
    if exclude_privacy:
        params["exclude_privacy"] = exclude_privacy
    resp = _get("/debug/memories", **params)
    return _parse_results(resp)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

_failures = 0
_checks = 0


def _result_matches_level(r: dict, level: str) -> bool:
    """Check if a result dict corresponds to a given privacy_level test memory.

    Uses title substring match because search_memories / get_recent_memories
    SELECT queries do not currently include privacy_level in their column list
    (privacy_level filtering happens in WHERE, not in the returned columns).
    """
    title = r.get("title", "") or ""
    content = r.get("content", "") or ""
    # Title: "[TEST] privacy gate <level>"
    if f"privacy gate {level}" in title:
        return True
    # Content: "[TEST] privacy_gate_<level> — ..."
    if f"privacy_gate_{level}" in content:
        return True
    # Direct privacy_level field (present in title-path results)
    if r.get("privacy_level") == level:
        return True
    return False


def check_visible(actor: str, level: str, results: list[dict], path: str):
    """Assert `level` appears in results."""
    global _failures, _checks
    _checks += 1
    found = any(_result_matches_level(r, level) for r in results)
    if found:
        print(f"    ✅ [{level}] visible")
    else:
        _failures += 1
        print(f"    ❌ [{level}] MISSING (expected visible) [{path}]")


def check_hidden(actor: str, level: str, results: list[dict], path: str):
    """Assert `level` does NOT appear in results."""
    global _failures, _checks
    _checks += 1
    found = any(_result_matches_level(r, level) for r in results)
    if not found:
        print(f"    ✅ [{level}] hidden")
    else:
        _failures += 1
        print(f"    ❌ [{level}] LEAKED (should be hidden) [{path}]")


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run():
    global _failures, _checks
    _failures = 0
    _checks = 0

    setup_test_memories()
    if len(_test_memory_ids) < 5:
        print("⚠️  Not all test memories created — some checks may not be meaningful\n")

    try:
        # ==================================================================
        # 1. SEARCH path (search_memories via HTTP)
        # ==================================================================
        print("=" * 60)
        print("1. SEARCH path (/debug/memories?q=)")

        search_query = "[TEST] privacy_gate"

        for actor, rules in ACTORS.items():
            print(f"\n  Actor: {actor}")
            results = search_memories(search_query, actor=actor, limit=20)
            for level in sorted(rules["visible"]):
                check_visible(actor, level, results, f"search:{actor}")
            for level in sorted(rules["hidden"]):
                check_hidden(actor, level, results, f"search:{actor}")

        # ==================================================================
        # 2. RECENT path (get_recent_memories via HTTP)
        # ==================================================================
        print(f"\n{'=' * 60}")
        print("2. RECENT path (/debug/memories, no q)")

        for actor, rules in ACTORS.items():
            print(f"\n  Actor: {actor}")
            results = recent_memories(actor=actor, limit=50)
            for level in sorted(rules["visible"]):
                check_visible(actor, level, results, f"recent:{actor}")
            for level in sorted(rules["hidden"]):
                check_hidden(actor, level, results, f"recent:{actor}")

        # ==================================================================
        # 3. TITLE path (/debug/memories?title=)
        # ==================================================================
        print(f"\n{'=' * 60}")
        print("3. TITLE path (/debug/memories?title=)")

        for actor, rules in ACTORS.items():
            print(f"\n  Actor: {actor}")
            for level in LEVELS:
                results = title_lookup(f"{TEST_TITLE} {level}", actor=actor)
                if level in rules["visible"]:
                    check_visible(actor, level, results, f"title:{actor}")
                else:
                    check_hidden(actor, level, results, f"title:{actor}")

        # ==================================================================
        # 4. SEALED global — no actor should ever see sealed
        # ==================================================================
        print(f"\n{'=' * 60}")
        print("4. SEALED global (all actors)")
        all_actors = list(ACTORS.keys()) + ["local_bot", "dev_agent", "hermes_agent"]
        for actor in sorted(set(all_actors)):
            results = search_memories(search_query, actor=actor, limit=20)
            check_hidden(actor, "sealed", results, f"sealed-global:{actor}")
            results = recent_memories(actor=actor, limit=50)
            check_hidden(actor, "sealed", results, f"sealed-global-recent:{actor}")

        # ==================================================================
        # 5. EXCLUDE_PRIVACY intersection
        # ==================================================================
        print(f"\n{'=' * 60}")
        print("5. EXCLUDE_PRIVACY intersection (actor gate ∩ exclude)")

        # Case A: api_client + exclude_privacy=restricted
        #   visible: public_like, personal, sensitive
        #   hidden: restricted, sealed
        print("\n  Case A: actor=api_client, exclude_privacy=restricted")
        for level in LEVELS:
            results = title_lookup(f"{TEST_TITLE} {level}", actor="api_client", exclude_privacy="restricted")
            if level in ("public_like", "personal", "sensitive"):
                check_visible("api_client", level, results, "exclude:A")
            else:
                check_hidden("api_client", level, results, "exclude:A")

        # Search path with exclude
        print("\n  Case A (search): actor=api_client, exclude_privacy=restricted")
        results = search_memories(search_query, actor="api_client", limit=20, exclude_privacy="restricted")
        for level in ("public_like", "personal", "sensitive"):
            check_visible("api_client", level, results, "exclude:A-search")
        for level in ("restricted", "sealed"):
            check_hidden("api_client", level, results, "exclude:A-search")

        # Recent path with exclude
        print("\n  Case A (recent): actor=api_client, exclude_privacy=restricted")
        results_list = recent_memories(actor="api_client", limit=50, exclude_privacy="restricted")
        for level in ("public_like", "personal", "sensitive"):
            check_visible("api_client", level, results_list, "exclude:A-recent")
        for level in ("restricted", "sealed"):
            check_hidden("api_client", level, results_list, "exclude:A-recent")

        # Case B: hermes_agent + exclude_privacy=sealed,restricted
        #   visible: public_like, personal
        #   hidden: sensitive, restricted, sealed
        print("\n  Case B: actor=hermes_agent, exclude_privacy=sealed,restricted")
        for level in LEVELS:
            results = title_lookup(f"{TEST_TITLE} {level}", actor="hermes_agent", exclude_privacy="sealed,restricted")
            if level in ("public_like", "personal"):
                check_visible("hermes_agent", level, results, "exclude:B")
            else:
                check_hidden("hermes_agent", level, results, "exclude:B")

        # ==================================================================
        # 6. Summary
        # ==================================================================
        print(f"\n{'=' * 60}")
        print(f"RESULTS: {_checks - _failures}/{_checks} PASS")
        if _failures:
            print(f"         {_failures} FAILURES")
        else:
            print("         ALL PASSED ✅")

    finally:
        cleanup_test_memories()

    return 1 if _failures > 0 else 0


if __name__ == "__main__":
    sys.exit(run())
