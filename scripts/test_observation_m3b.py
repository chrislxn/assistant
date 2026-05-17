"""Phase 1.5-M3b: validate short_term_auto_write → observation_only in resolver."""

import asyncio, os, sys, time, uuid

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr); sys.exit(2)

TS = str(int(time.time()))
TAG = f"[TEST_M3B] m3b_{TS}"

async def main():
    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    from database import resolve_candidate, close_pool

    f = 0; c = 0
    def chk(n, ok, d=""):
        nonlocal f, c; c += 1
        if ok: print(f"  ✅ {n}{' — '+d if d else ''}")
        else: f += 1; print(f"  ❌ {n}{' — '+d if d else ''}")

    async def insert_cand(status, mtype, text, imp=5, trust="assistant_inferred", src_ids=None):
        cid = str(uuid.uuid4())
        sids = src_ids or [str(uuid.uuid4())]
        await pool.execute(
            """INSERT INTO memory_candidates
               (candidate_id, status, memory_type, rendered_text, subject_key,
                source_trust, privacy_level, confidence, importance, source_event_ids)
               VALUES ($1,$2,$3,$4,$5,$6,'personal',0.7,$7,$8::uuid[])""",
            cid, status, mtype, f"{text} — {TAG}", f"eval:m3b:{mtype}", trust, imp, sids)
        return cid

    async def cleanup(ids):
        for cid in ids:
            await pool.execute("DELETE FROM memory_items WHERE source_candidate_id=$1::uuid", cid)
        await pool.execute("DELETE FROM memories WHERE content LIKE $1", f"%{TAG}%")
        for cid in ids:
            await pool.execute("DELETE FROM memory_candidates WHERE candidate_id=$1::uuid", cid)
        await pool.execute("DELETE FROM memory_candidates WHERE subject_key LIKE 'eval:m3b:%'")
        rc = await pool.fetchval("SELECT count(*) FROM memory_candidates WHERE subject_key LIKE 'eval:m3b:%'")
        rm = await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%")
        ri = await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%")
        return rc, rm, ri

    print("Phase 1.5-M3b: short_term_auto_write → observation_only\n")
    ids = {}

    try:
        # Create test candidates
        specs = [
            ("pending", "emotional_observation", "我想她了", 4, "assistant_inferred", "emo_pend"),
            ("pending", "emotional_observation", "今天心情不太好", 5, "assistant_inferred", "emo_pend2"),
            ("requires_review", "emotional_observation", "长期情绪低落需要关注", 6, "assistant_inferred", "emo_req"),
            ("pending", "identity_fact", "用户是内向的人", 7, "user_direct", "ident"),
            ("pending", "relationship_context", "用户和母亲关系紧张", 8, "user_direct", "rela"),
            ("pending", "grade_fact", "CSC165 51", 5, "user_direct", "grade_ud"),
            ("pending", "grade_fact", "PHY131 inferred good", 5, "assistant_inferred", "grade_ai"),
            ("observation_only", "emotional_observation", "already obs", 4, "assistant_inferred", "obs_exist"),
            ("expired", "thought_observation", "already expired", 3, "assistant_inferred", "exp_exist"),
        ]
        for s in specs:
            status, mtype, text, imp, trust, key = s
            cid = await insert_cand(status, mtype, text, imp, trust)
            ids[key] = cid
            if status == "expired":
                await pool.execute("UPDATE memory_candidates SET valid_to=NOW()-INTERVAL'1 day' WHERE candidate_id=$1::uuid", cid)

        # --- A. short_term → observation_only ---
        print("A. short_term_auto_write → observation_only")
        r = await resolve_candidate(ids["emo_pend"])
        chk("A1: action=observation_only", r["action"] == "observation_only", r.get("reason","")[:60])
        st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids["emo_pend"])
        chk("A2: status=observation_only", st == "observation_only", f"status={st}")
        vt = await pool.fetchval("SELECT valid_to FROM memory_candidates WHERE candidate_id=$1::uuid", ids["emo_pend"])
        chk("A3: valid_to set", vt is not None, str(vt)[:30])
        chk("A4: no memory_items", 0 == await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%"))
        chk("A5: no legacy memories", 0 == await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%"))
        print("")

        # --- B. requires_review NOT downgraded ---
        print("B. requires_review preserved → no observation_only")
        r = await resolve_candidate(ids["emo_req"])
        chk("B1: not observation_only", r["action"] != "observation_only", f"action={r['action']}")
        st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids["emo_req"])
        chk("B2: status unchanged", st == "requires_review", f"status={st}")
        print("")

        # --- C. High-stakes unchanged ---
        print("C. identity_fact / relationship_context still requires_review")
        r = await resolve_candidate(ids["ident"])
        chk("C1: identity_fact not observation_only", r["action"] != "observation_only", f"action={r['action']}")
        r = await resolve_candidate(ids["rela"])
        chk("C2: relationship_context not observation_only", r["action"] != "observation_only", f"action={r['action']}")
        print("")

        # --- D. Medium factual NOT auto-committed in M3b ---
        print("D. grade_fact does not auto-commit in M3b")
        r = await resolve_candidate(ids["grade_ud"])
        chk("D1: user_direct grade_fact not committed", r["action"] not in ("auto_commit","observation_only"), f"action={r['action']}")
        st = await pool.fetchval("SELECT status FROM memory_candidates WHERE candidate_id=$1::uuid", ids["grade_ud"])
        chk("D2: grade_fact status unchanged or pending", st in ("pending","pending_auto"), f"status={st}")
        r = await resolve_candidate(ids["grade_ai"])
        chk("D3: assistant_inferred grade_fact not committed", r["action"] not in ("auto_commit","observation_only"), f"action={r['action']}")
        print("")

        # --- E. observation_only / expired blocked ---
        print("E. observation_only / expired rejected by resolver")
        r = await resolve_candidate(ids["obs_exist"])
        chk("E1: observation_only blocked", r["action"] == "error", r.get("reason","")[:60])
        r = await resolve_candidate(ids["exp_exist"])
        chk("E2: expired blocked", r["action"] == "error", r.get("reason","")[:60])
        print("")

        # --- F. Cleanup ---
        print("F. Cleanup")
        rc, rm, ri = await cleanup(list(ids.values()))
        chk("F1: candidates=0", rc == 0, f"rem={rc}")
        chk("F2: memories=0", rm == 0, f"rem={rm}")
        chk("F3: items=0", ri == 0, f"rem={ri}")
        await close_pool()

    finally:
        await pool.close()

    total = c; passed = total - f
    print(f"\nRESULTS: {passed}/{total} PASS")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    asyncio.run(main())
