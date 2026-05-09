"""Phase 1.1-M1: validate get_allowed_privacy_levels() rules.

Extracts and executes only the privacy policy section from database.py
because the full module requires asyncpg (not available in this env).
"""
import re


def extract_privacy_section(path: str) -> str:
    """Extract the Phase 1.1 M1 privacy policy code block from database.py."""
    with open(path) as f:
        src = f.read()

    # Section has double === separators around the header — grab both then everything to EOF
    m = re.search(
        r"(# =+\n# Phase 1\.1 M1 .*\n# =+\n.*)",
        src, re.DOTALL
    )
    if not m:
        raise RuntimeError("Phase 1.1 M1 section not found in database.py")
    return m.group(1)


def run():
    print("Phase 1.1-M1: privacy policy helper validation\n")

    code = extract_privacy_section("kiwi-mem/database.py")
    ns: dict = {}
    exec(code, ns)
    get_allowed_privacy_levels = ns["get_allowed_privacy_levels"]
    _PRIVACY_POLICY = ns["_PRIVACY_POLICY"]

    def check(actor, expected):
        got = get_allowed_privacy_levels(actor)
        assert got == expected, f"{actor}: expected {expected}, got {got}"
        assert "sealed" not in got, f"{actor}: sealed should never be returned"
        print(f"  ✅ {actor} → {got}")

    # 1. Each actor returns expected privacy levels
    print("1. Actor → expected privacy levels:")
    check("local_bot",      ["public_like", "personal", "sensitive", "restricted"])
    check("api_client",     ["public_like", "personal", "sensitive", "restricted"])
    check("telegram_bot",   ["public_like", "personal", "sensitive"])
    check("claude_mcp",     ["public_like", "personal", "sensitive"])
    check("hermes_agent",   ["public_like", "personal"])
    check("dev_agent",      ["public_like", "personal"])

    # 2. sealed never appears for any known actor
    print("\n2. sealed never returned for any known actor:")
    for actor in _PRIVACY_POLICY:
        result = get_allowed_privacy_levels(actor)
        assert "sealed" not in result, f"FAIL: {actor} returned sealed"
    print("  ✅ all actors exclude sealed")

    # 3. unknown actor → default (public_like, personal)
    print("\n3. Unknown actor → default:")
    check("bogus_actor", ["public_like", "personal"])
    check("",            ["public_like", "personal"])
    result_none = get_allowed_privacy_levels(None)
    assert result_none == ["public_like", "personal"], f"None → {result_none}"
    assert "sealed" not in result_none
    print("  ✅ None → public_like, personal (no sealed)")
    result_int = get_allowed_privacy_levels(123)
    assert result_int == ["public_like", "personal"]
    print("  ✅ non-str → public_like, personal")

    # 4. api_client comment exists in source
    print("\n4. api_client comment check:")
    assert "If future lower-trust clients" in code, "api_client comment missing"
    print("  ✅ api_client future lower-trust comment present")

    # 5. immutable return (modifying result doesn't affect policy)
    print("\n5. Return value is a copy:")
    r1 = get_allowed_privacy_levels("claude_mcp")
    r1.append("sealed")
    r2 = get_allowed_privacy_levels("claude_mcp")
    assert "sealed" not in r2, "policy dict was mutated"
    print("  ✅ helper returns a copy, dict not mutated")

    total = 6 + len(_PRIVACY_POLICY) + 5 + 1 + 1
    print(f"\n📊 All {total} checks PASSED")
    print("SUCCESS")


if __name__ == "__main__":
    run()
