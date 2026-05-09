"""Phase 1.3-M2: minimal retrieval eval runner.

Reads evals/retrieval_safety_minimal.jsonl, creates anchored test memories,
runs query-based retrieval checks against the kiwi-mem HTTP API, and outputs
a machine-readable JSON summary.

Usage:
    ACCESS_TOKEN=xxx python3 scripts/eval_retrieval_minimal.py
    ACCESS_TOKEN=xxx KIWI_BASE=http://localhost:8080 python3 scripts/eval_retrieval_minimal.py
"""

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

KIWI_BASE = os.getenv("KIWI_BASE", "https://agent.xeon.im").rstrip("/")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

_HEADERS = {"User-Agent": "kiwi-eval-runner/1.0"}
_SSL_CONTEXT = ssl.create_default_context()

EVAL_FILE = os.path.join(os.path.dirname(__file__), "..", "evals", "retrieval_safety_minimal.jsonl")

LEVELS = ("public_like", "personal", "sensitive", "restricted", "sealed")

# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def _resolve_token() -> str:
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
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _get(path: str, **params) -> dict:
    qs_parts = []
    for k, v in params.items():
        if v is not None and v != "":
            qs_parts.append(f"{k}={urllib.request.quote(str(v))}")
    url = f"{KIWI_BASE}{path}?token={_TOKEN}"
    if qs_parts:
        url += "&" + "&".join(qs_parts)
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
    url = f"{KIWI_BASE}{path}?token={_TOKEN}"
    data = json.dumps(body).encode()
    headers = {**_HEADERS, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": b[:200]}
    except Exception as e:
        return {"error": str(e)}


def _delete(path: str) -> dict:
    url = f"{KIWI_BASE}{path}?token={_TOKEN}"
    req = urllib.request.Request(url, headers=_HEADERS, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}", "detail": b[:200]}
    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# Test memory management
# ---------------------------------------------------------------------------

def generate_anchor() -> str:
    return f"kiwi_eval_alpha_{int(time.time())}"


def build_level_to_tag(anchor: str) -> dict[str, str]:
    return {level: f"{anchor}_{level}" for level in LEVELS}


def expand_template(template: str, anchor: str, level_to_tag: dict[str, str]) -> str:
    s = template.replace("{anchor}", anchor)
    for level, tag in level_to_tag.items():
        s = s.replace(f"{{{level}_tag}}", tag)
    return s


def _test_content(anchor: str, level: str, tag: str) -> str:
    domains = {
        "public_like": "synthetic public-domain note",
        "personal": "synthetic private-note note",
        "sensitive": "synthetic secret-domain note; no real secret",
        "restricted": "synthetic private-domain note; no real health data",
        "sealed": "synthetic sealed-domain note; no real financial data",
    }
    return f"{anchor} — {level}: {domains.get(level, 'synthetic note')}  tag:{tag}"


def setup_test_memories(anchor: str, level_to_tag: dict[str, str]) -> list[int]:
    """Create 5 test memories. Returns list of memory IDs."""
    ids = []
    for level in LEVELS:
        tag = level_to_tag[level]
        resp = _post("/debug/memories", {
            "title": f"[EVAL] retrieval_minimal {level}",
            "content": _test_content(anchor, level, tag),
            "importance": 1,
            "privacy_level": level,
            "source": "eval_retrieval_minimal",
            "source_trust": "system_generated",
            "memory_type": "test_eval",
            "status": "active",
            "subject_key": f"eval:retrieval_minimal:{level}",
        })
        if "error" in resp:
            print(f"ERROR creating [{level}] test memory: {resp.get('error')}", file=sys.stderr)
            continue
        mem_id = resp.get("memory_id")
        if mem_id:
            ids.append(mem_id)
        else:
            print(f"WARN: [{level}] created but no memory_id in response", file=sys.stderr)
    return ids


def cleanup_test_memories(memory_ids: list[int]) -> tuple[int, int]:
    """Delete test memories. Returns (deleted, remaining)."""
    deleted = 0
    for mid in memory_ids:
        resp = _delete(f"/debug/memories/{mid}")
        if resp.get("status") == "deleted":
            deleted += 1

    # Fallback: search by [EVAL] title prefix
    for level in LEVELS:
        resp = _get("/debug/memories", title=f"[EVAL] retrieval_minimal {level}", limit=5)
        for r in resp.get("results", []):
            mid = r["id"]
            if mid not in memory_ids:
                _delete(f"/debug/memories/{mid}")
                deleted += 1

    # Verify
    remaining = 0
    for level in LEVELS:
        resp = _get("/debug/memories", title=f"[EVAL] retrieval_minimal {level}", limit=5)
        remaining += len(resp.get("results", []))
    return deleted, remaining

# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def search(query: str, actor: str, exclude_privacy: str = "", limit: int = 20) -> list[dict]:
    """Call /debug/memories?q=...&actor=... to exercise search_memories()."""
    params: dict = {"q": query, "actor": actor, "limit": limit}
    if exclude_privacy:
        params["exclude_privacy"] = exclude_privacy
    resp = _get("/debug/memories", **params)
    return resp.get("results", [])


def level_visible(level: str, results: list[dict], level_to_tag: dict[str, str]) -> bool:
    """Check if a test memory for `level` appears in results by tag match."""
    tag = level_to_tag[level]
    for r in results:
        text = (r.get("title", "") or "") + " " + (r.get("content", "") or "")
        if tag in text:
            return True
    return False

# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run():
    global _TOKEN
    _TOKEN = _resolve_token()
    if not _TOKEN:
        print("ERROR: ACCESS_TOKEN not set", file=sys.stderr)
        sys.exit(2)

    anchor = generate_anchor()
    level_to_tag = build_level_to_tag(anchor)
    cases = load_cases(EVAL_FILE)

    if not cases:
        print("ERROR: no eval cases loaded", file=sys.stderr)
        sys.exit(2)

    summary = {
        "total": len(cases),
        "passed": 0,
        "failed": 0,
        "leak_count": 0,
        "missing_expected_count": 0,
        "cases": [],
        "cleanup_deleted": 0,
        "cleanup_remaining": -1,
    }

    memory_ids: list[int] = []
    try:
        # Setup
        memory_ids = setup_test_memories(anchor, level_to_tag)
        if len(memory_ids) < 5:
            print(f"WARN: only {len(memory_ids)}/5 test memories created", file=sys.stderr)

        # Run cases
        for case in cases:
            case_id = case["case_id"]
            query = expand_template(case["query_template"], anchor, level_to_tag)
            actor = case["actor"]
            exclude = case.get("exclude_privacy", "")
            expected_visible = set(case["expected_visible"])
            expected_hidden = set(case["expected_hidden"])

            results = search(query, actor=actor, exclude_privacy=exclude)

            actual_visible = {level for level in LEVELS
                              if level_visible(level, results, level_to_tag)}

            unexpected = actual_visible & expected_hidden
            missing = expected_visible - actual_visible

            case_pass = len(unexpected) == 0 and len(missing) == 0

            case_result = {
                "case_id": case_id,
                "pass": case_pass,
                "expected_visible": sorted(expected_visible),
                "expected_hidden": sorted(expected_hidden),
                "actual_visible": sorted(actual_visible),
                "unexpected_visible": sorted(unexpected),
                "missing_expected": sorted(missing),
            }
            summary["cases"].append(case_result)

            if case_pass:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
                summary["leak_count"] += len(unexpected)
                summary["missing_expected_count"] += len(missing)

    finally:
        deleted, remaining = cleanup_test_memories(memory_ids)
        summary["cleanup_deleted"] = deleted
        summary["cleanup_remaining"] = remaining

    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    if summary["failed"] > 0 or summary["cleanup_remaining"] != 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    _TOKEN = ""  # resolved in run()
    run()
