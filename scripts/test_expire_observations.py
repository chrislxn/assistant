"""Phase 1.5-M3c: validate expire_observations.py behavior."""

import asyncio, json, os, sys, time, uuid

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr); sys.exit(2)

TS = str(int(time.time()))
TAG = f"[TEST_M3C] m3c_{TS}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SPECS = [
    ("observation_only", "NOW()-INTERVAL'1 day'", "obs_expired"),
    ("observation_only", "NOW()+INTERVAL'1 day'", "obs_active"),
    ("observation_only", None, "obs_null"),
    ("pending",          "NOW()-INTERVAL'1 day'", "pend_exp"),
    ("requires_review",  "NOW()-INTERVAL'1 day'", "req_exp"),
    ("expired",          "NOW()-INTERVAL'1 day'", "exp_exp"),
    ("committed",        "NOW()-INTERVAL'1 day'", "com_exp"),
    ("rejected",         "NOW()-INTERVAL'1 day'", "rej_exp"),
]


def run_expire_script(env_extra=None):
    """Run expire_observations.py via subprocess with clean env."""
    import subprocess
    env = {"DATABASE_URL": DATABASE_URL, "PATH": os.environ.get("PATH", "/usr/bin")}
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        ["python3", os.path.join(SCRIPT_DIR, "expire_observations.py")],
        capture_output=True, text=True, env=env)
    if r.returncode == 0:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"error": "JSON parse", "raw": r.stdout[:200]}
    return {"error": f"rc={r.returncode}", "stderr": r.stderr}


async def main():
    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    f = c = 0; ids = {}

    def chk(n, ok, d=""):
        nonlocal f, c; c += 1
        if ok: print(f"  ✅ {n}{' — '+d if d else ''}")
        else: f += 1; print(f"  ❌ {n}{' — '+d if d else ''}")

    print("Phase 1.5-M3c: expire observations\n")

    try:
        # Baseline check: refuse APPLY if real expired observation_only rows exist
        baseline_ids = [str(r["candidate_id"]) for r in await pool.fetch(
            """SELECT candidate_id FROM memory_candidates
               WHERE status = 'observation_only'
                 AND valid_to IS NOT NULL
                 AND valid_to < NOW()""")]
        baseline_n = len(baseline_ids)

        for status, vto_expr, key in SPECS:
            cid = str(uuid.uuid4())
            if vto_expr:
                await pool.execute(
                    f"""INSERT INTO memory_candidates
                        (candidate_id, status, rendered_text, subject_key,
                         source_trust, privacy_level, importance, valid_to)
                        VALUES ($1,$2,$3,$4,'system_generated','personal',5,{vto_expr})""",
                    cid, status, f"{TAG} {status} candidate", f"eval:m3c:{key}")
            else:
                await pool.execute(
                    """INSERT INTO memory_candidates
                       (candidate_id, status, rendered_text, subject_key,
                        source_trust, privacy_level, importance)
                       VALUES ($1,$2,$3,$4,'system_generated','personal',5)""",
                    cid, status, f"{TAG} {status} candidate", f"eval:m3c:{key}")
            ids[key] = cid

        # --- A. Dry-run ---
        print("A. Dry-run")
        r = run_expire_script({"DRY_RUN": "1"})
        chk("A1: status=ok", r.get("status") == "ok", f"resp={r}")
        chk("A2: dry_run=true", r.get("dry_run") is True)
        expected_matched = baseline_n + 1
        chk("A3: matched_count", r.get("matched_count") == expected_matched, f"expected {expected_matched}, got {r.get('matched_count')}")
        chk("A4: expired_count=0", r.get("expired_count") == 0, f"got {r.get('expired_count')}")

        for s in SPECS:
            key = s[2]; expected = s[0]
            st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids[key])
            chk(f"A5: {key} status unchanged", st == expected, f"status={st}")

        # --- B. Apply ---
        print("\nB. Apply")
        if baseline_n > 0:
            chk("B0: skip apply (pre-existing expired obs rows would be mutated)", False,
                f"baseline={baseline_n} rows, ids={baseline_ids[:5]}")
            print("  ⚠️  Skipping apply tests to avoid mutating real data. Clean up expired observation_only rows first.")
        else:
            r = run_expire_script({"APPLY": "1"})
            chk("B1: status=ok", r.get("status") == "ok")
            chk("B2: dry_run=false", r.get("dry_run") is False)
            chk("B3: matched_count=1", r.get("matched_count") == 1)
            chk("B4: expired_count=1", r.get("expired_count") == 1, f"got {r.get('expired_count')}")

            st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids["obs_expired"])
            chk("B5: obs_expired → expired", st == "expired", f"status={st}")
            rv = await pool.fetchval("SELECT reviewed_by FROM memory_candidates WHERE candidate_id=$1::uuid", ids["obs_expired"])
            chk("B6: reviewed_by=expiry_job", rv == "expiry_job", f"reviewed_by={rv}")
            ra = await pool.fetchval("SELECT reviewed_at FROM memory_candidates WHERE candidate_id=$1::uuid", ids["obs_expired"])
            chk("B7: reviewed_at set", ra is not None)

            for key in ("obs_active", "obs_null", "pend_exp", "req_exp"):
                expected = [s[0] for s in SPECS if s[2] == key][0]
                st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids[key])
                chk(f"B8: {key} unchanged", st == expected, f"status={st}")

            for key in ("exp_exp", "com_exp", "rej_exp"):
                expected = [s[0] for s in SPECS if s[2] == key][0]
                st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids[key])
                chk(f"B9: {key} unchanged", st == expected, f"status={st}")

        mc = await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%")
        chk("B10: no memory_items", mc == 0, f"rows={mc}")
        mc = await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%")
        chk("B11: no memories", mc == 0, f"rows={mc}")
        print("")

    finally:
        print("C. Cleanup")
        for cid in ids.values():
            await pool.execute("DELETE FROM memory_candidates WHERE candidate_id=$1::uuid", cid)
        await pool.execute("DELETE FROM memory_candidates WHERE subject_key LIKE 'eval:m3c:%'")
        rc = await pool.fetchval("SELECT count(*) FROM memory_candidates WHERE subject_key LIKE 'eval:m3c:%'")
        rm = await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%")
        ri = await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%")
        chk("C1: candidates=0", rc == 0, f"rem={rc}")
        chk("C2: memories=0", rm == 0, f"rem={rm}")
        chk("C3: items=0", ri == 0, f"rem={ri}")
        await pool.close()

    total = c; passed = total - f
    print(f"\nRESULTS: {passed}/{total} PASS")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    asyncio.run(main())
