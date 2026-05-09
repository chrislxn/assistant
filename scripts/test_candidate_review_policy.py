"""Phase 1.5-M2: validate classify_candidate_review_policy() rules.

Extracts the classifier function and its dependency sets from database.py
via regex (stdlib only — no asyncpg dependency on dev machine).
"""

import re
import sys


def extract_section(path: str, marker: str) -> str:
    """Extract code from `marker` to end of file."""
    with open(path) as f:
        src = f.read()
    idx = src.find(marker)
    if idx == -1:
        raise RuntimeError(f"Marker not found: {marker}")
    return src[idx:]


def run():
    code = extract_section("kiwi-mem/database.py",
                           "# Phase 1.5 M2 — dry-run candidate review policy classifier")
    ns: dict = {}
    exec(code, ns)
    classify = ns["classify_candidate_review_policy"]

    failures = 0
    checks = 0

    def check(case_id, candidate, expected_action, expected_review=None,
              expected_long_term=None, expected_ttl_min=None, expected_ttl_max=None):
        nonlocal failures, checks
        checks += 1
        result = classify(candidate)
        actual = result["recommended_action"]

        ok = True
        if actual != expected_action:
            print(f"  ❌ {case_id}: expected {expected_action}, got {actual} ({result['reason']})")
            ok = False
        elif expected_review is not None and result["review_required"] != expected_review:
            print(f"  ❌ {case_id}: expected review_required={expected_review}, got {result['review_required']}")
            ok = False
        elif expected_long_term is not None and result["should_commit_long_term"] != expected_long_term:
            print(f"  ❌ {case_id}: expected should_commit_long_term={expected_long_term}, got {result['should_commit_long_term']}")
            ok = False
        elif expected_ttl_min is not None and (result["suggested_ttl_days"] or 0) < expected_ttl_min:
            print(f"  ❌ {case_id}: expected ttl >= {expected_ttl_min}, got {result['suggested_ttl_days']}")
            ok = False
        elif expected_ttl_max is not None and (result["suggested_ttl_days"] or 0) > expected_ttl_max:
            print(f"  ❌ {case_id}: expected ttl <= {expected_ttl_max}, got {result['suggested_ttl_days']}")
            ok = False

        if ok:
            print(f"  ✅ {case_id}: {actual} (reason={result['reason'][:50]}...)")
        else:
            failures += 1

    print("Phase 1.5-M2: candidate review policy classifier\n")

    # ==================================================================
    # Short-term emotional observations
    # ==================================================================
    print("1. Short-term emotional observations → short_term_auto_write")
    check("我想她了",
          {"memory_type": "emotional_observation", "rendered_text": "我想她了", "source_trust": "assistant_inferred", "importance": 4},
          "short_term_auto_write", expected_review=False, expected_long_term=False, expected_ttl_min=14, expected_ttl_max=14)
    check("我现在很难受",
          {"memory_type": "emotional_observation", "rendered_text": "我现在很难受", "source_trust": "assistant_inferred", "importance": 4},
          "short_term_auto_write", expected_review=False, expected_long_term=False)
    check("这件事让我自我否定",
          {"memory_type": "emotional_observation", "rendered_text": "这件事让我自我否定", "source_trust": "assistant_inferred", "importance": 5},
          "short_term_auto_write", expected_review=False, expected_long_term=False)
    check("今天又想起她 — low importance short-term",
          {"memory_type": "thought_observation", "rendered_text": "今天又想起她了", "source_trust": "assistant_inferred", "importance": 3},
          "short_term_auto_write", expected_review=False, expected_ttl_min=7, expected_ttl_max=7)
    check("session_observation",
          {"memory_type": "session_observation", "rendered_text": "用户今天看起来状态不太好", "source_trust": "assistant_inferred", "importance": 3},
          "short_term_auto_write", expected_review=False)
    print("")

    # ==================================================================
    # Medium factual auto-commit
    # ==================================================================
    print("2. Medium factual → medium_factual_auto_commit")
    check("CSC165 51",
          {"memory_type": "grade_fact", "rendered_text": "CSC165 51", "source_trust": "user_direct",
           "source_event_ids": ["uuid-1"], "importance": 5},
          "medium_factual_auto_commit", expected_review=False, expected_long_term=True)
    check("STA237 68",
          {"memory_type": "academic_fact", "rendered_text": "STA237 68", "source_trust": "user_direct",
           "source_event_ids": ["uuid-2"], "importance": 5},
          "medium_factual_auto_commit", expected_review=False, expected_long_term=True)
    check("project_state",
          {"memory_type": "project_state", "rendered_text": "正在搭建 n8n workflow", "source_trust": "user_direct",
           "source_event_ids": ["uuid-3"], "importance": 5},
          "medium_factual_auto_commit", expected_review=False)
    check("device_inventory",
          {"memory_type": "device_inventory", "rendered_text": "Arch Linux + i3 工作环境", "source_trust": "user_direct",
           "source_event_ids": ["uuid-4"], "importance": 5},
          "medium_factual_auto_commit", expected_review=False)
    print("")

    # ==================================================================
    # Manual review — high-stakes types
    # ==================================================================
    print("3. High-stakes types → manual_review")
    check("identity_fact",
          {"memory_type": "identity_fact", "rendered_text": "用户是内向的人", "source_trust": "assistant_inferred", "importance": 7},
          "manual_review", expected_review=True)
    check("relationship_context",
          {"memory_type": "relationship_context", "rendered_text": "用户和 Ellie 分手了", "source_trust": "user_direct", "importance": 8},
          "manual_review", expected_review=True)
    check("health_baseline",
          {"memory_type": "health_baseline", "rendered_text": "用户有慢性偏头痛", "source_trust": "user_direct", "importance": 8},
          "manual_review", expected_review=True)
    check("risk_flag",
          {"memory_type": "risk_flag", "rendered_text": "用户提到自我伤害的想法", "source_trust": "assistant_inferred", "importance": 9},
          "manual_review", expected_review=True)
    check("policy_rule",
          {"memory_type": "policy_rule", "rendered_text": "用户不允许 AI 自动修改 core_blocks", "source_trust": "user_direct", "importance": 8},
          "manual_review", expected_review=True)
    check("health_pattern",
          {"memory_type": "health_pattern", "rendered_text": "用户每周偏头痛发作两次", "source_trust": "user_direct", "importance": 7},
          "manual_review", expected_review=True)
    print("")

    # ==================================================================
    # Manual review — durable relationship claim from emotional type
    # ==================================================================
    print("4. Durable claim from emotional type → promoted to manual_review")
    check("长期自我否定",
          {"memory_type": "emotional_observation", "rendered_text": "用户长期因为 Ellie 自我否定，这种关系模式持续影响人格",
           "source_trust": "assistant_inferred", "importance": 7},
          "manual_review", expected_review=True)
    print("")

    # ==================================================================
    # Auto-reject — negative inference
    # ==================================================================
    print("5. Negative capability inference → auto_reject_or_expire")
    check("学习能力差",
          {"memory_type": "thought_observation", "rendered_text": "用户学习能力差", "source_trust": "assistant_inferred", "importance": 5},
          "auto_reject_or_expire", expected_review=False, expected_long_term=False)
    check("学不会",
          {"memory_type": "thought_observation", "rendered_text": "用户学不会数学", "source_trust": "assistant_inferred"},
          "auto_reject_or_expire", expected_review=False)
    print("")

    # ==================================================================
    # Auto-reject — third-party inference
    # ==================================================================
    print("6. Third-party inference → auto_reject_or_expire")
    check("third_party_preference",
          {"memory_type": "preference", "rendered_text": "用户喜欢早起", "source_trust": "third_party_doc", "importance": 5},
          "auto_reject_or_expire", expected_review=False)
    check("third_party_identity",
          {"memory_type": "identity_fact", "rendered_text": "用户是一个完美主义者", "source_trust": "third_party_doc", "importance": 6},
          "manual_review", expected_review=True)  # identity_fact overrides third_party gate (high-stakes gate fires first)
    print("")

    # ==================================================================
    # Auto-reject — missing provenance
    # ==================================================================
    print("7. Missing provenance for long-term commit → auto_reject_or_expire")
    check("no_source_events",
          {"memory_type": "grade_fact", "rendered_text": "MAT237 85", "source_trust": "user_direct",
           "source_event_ids": [], "importance": 5},
          "auto_reject_or_expire", expected_review=False)
    print("")

    # ==================================================================
    # Diagnosis keywords
    # ==================================================================
    print("8. Diagnosis-like content → manual_review")
    check("diagnosis_keyword",
          {"memory_type": "emotional_observation", "rendered_text": "用户可能患有 chronic anxiety disorder",
           "source_trust": "assistant_inferred", "importance": 7},
          "manual_review", expected_review=True)
    print("")

    # ==================================================================
    # Keep pending — unknown type fallback
    # ==================================================================
    print("9. Unknown type → keep_pending (conservative fallback)")
    check("unknown_type",
          {"memory_type": "some_future_type", "rendered_text": "something new", "source_trust": "assistant_inferred", "importance": 5},
          "keep_pending", expected_review=True)
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
    run()
