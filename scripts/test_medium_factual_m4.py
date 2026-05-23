"""Phase 1.5-M4: validate medium_factual_auto_commit in resolver."""

import asyncio, os, sys, time, uuid

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL: print("ERROR: DATABASE_URL not set", file=sys.stderr); sys.exit(2)

TS = str(int(time.time())); TAG = f"[TEST_M4] m4_{TS}"

async def main():
    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
    from database import resolve_candidate, close_pool
    f = c = 0
    def chk(n, ok, d=""):
        nonlocal f, c; c += 1
        if ok: print(f"  ✅ {n}{' — '+d if d else ''}")
        else: f += 1; print(f"  ❌ {n}{' — '+d if d else ''}")

    async def insert(status, mtype, text, trust, imp=5, sids=None, priv="personal", vto=None):
        cid = str(uuid.uuid4()); ev = sids if sids is not None else [str(uuid.uuid4())]
        if vto:
            await pool.execute(
                f"INSERT INTO memory_candidates(candidate_id,status,memory_type,rendered_text,subject_key,source_trust,privacy_level,importance,source_event_ids,valid_to) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::uuid[],{vto})",
                cid, status, mtype, f"{text} — {TAG}", f"eval:m4:{mtype}", trust, priv, imp, ev)
        else:
            await pool.execute(
                "INSERT INTO memory_candidates(candidate_id,status,memory_type,rendered_text,subject_key,source_trust,privacy_level,importance,source_event_ids) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::uuid[])",
                cid, status, mtype, f"{text} — {TAG}", f"eval:m4:{mtype}", trust, priv, imp, ev)
        return cid

    async def cleanup(ids):
        for cid in ids: await pool.execute("DELETE FROM memory_items WHERE source_candidate_id=$1::uuid", cid)
        await pool.execute("DELETE FROM memories WHERE content LIKE $1", f"%{TAG}%")
        for cid in ids: await pool.execute("DELETE FROM memory_candidates WHERE candidate_id=$1::uuid", cid)
        await pool.execute("DELETE FROM memory_candidates WHERE subject_key LIKE 'eval:m4:%'")
        return (await pool.fetchval("SELECT count(*) FROM memory_candidates WHERE subject_key LIKE 'eval:m4:%'"),
                await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%"),
                await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%"))

    print("Phase 1.5-M4: medium factual auto-commit\n"); ids = {}
    try:
        specs = [
            ("pending", "grade_fact", "CSC165 51", "user_direct", True, "grade_ud"),
            ("pending", "academic_fact", "STA237 68", "user_confirmed", True, "acad_uc"),
            ("pending", "technical_environment", "Arch Linux + i3wm", "user_direct", True, "tech_ud"),
            ("pending", "project_state", "n8n deployed", "system_generated", True, "proj_sg"),
            ("pending", "grade_fact", "PHY131 inferred", "system_generated", True, "grade_sg"),
            ("pending", "grade_fact", "inferred grade", "assistant_inferred", True, "grade_ai"),
            ("pending", "grade_fact", "3rd party grade", "third_party_doc", True, "grade_3p"),
            ("pending", "grade_fact", "unknown grade", "unknown", True, "grade_uk"),
            ("pending", "grade_fact", "no events", "user_direct", False, "grade_noev"),
            ("pending", "grade_fact", "sealed grade", "user_direct", True, "grade_seal", "sealed"),
            ("pending", "academic_fact", "chronic disorder diagnosis", "user_direct", True, "diag"),
            ("pending", "thought_observation", "用户学习能力差", "assistant_inferred", True, "neg_inf"),
            ("pending", "identity_fact", "introvert", "user_direct", True, "ident"),
            ("pending", "health_baseline", "migraine", "user_direct", True, "health"),
            ("requires_review", "grade_fact", "requires review grade", "user_direct", True, "grade_req"),
            ("observation_only", "grade_fact", "already obs grade", "user_direct", True, "grade_obs", "personal", "NOW()+INTERVAL'7 days'"),
            ("expired", "grade_fact", "already exp grade", "user_direct", True, "grade_exp", "personal", "NOW()-INTERVAL'1 day'"),
            ("pending", "emotional_observation", "我想她了", "assistant_inferred", True, "emo"),
        ]
        for s in specs:
            status, mtype, text, trust, has_ev, key = s[0], s[1], s[2], s[3], s[4], s[5]
            priv = s[6] if len(s) > 6 else "personal"; vto = s[7] if len(s) > 7 else None
            ev_ids = [str(uuid.uuid4())] if has_ev else []
            cid = await insert(status, mtype, text, trust, sids=ev_ids, priv=priv, vto=vto)
            ids[key] = cid

        def act(r): return r["action"]

        # --- Positive ---
        print("P. Positive auto-commit")
        for k, name in [("grade_ud","grade_fact user_direct"),("acad_uc","academic_fact user_confirmed"),("tech_ud","tech_env user_direct"),("proj_sg","project_state system_generated")]:
            r = await resolve_candidate(ids[k])
            chk(f"P: {name} committed", act(r) in ("auto_commit","medium_factual_auto_commit","committed"), f"action={act(r)}")
        mc = await pool.fetchval("SELECT count(*) FROM memory_items WHERE rendered_text LIKE $1", f"%{TAG}%")
        chk("P: memory_items rows exist", mc >= 4, f"rows={mc}")
        mc = await pool.fetchval("SELECT count(*) FROM memories WHERE content LIKE $1", f"%{TAG}%")
        chk("P: legacy memories rows exist", mc >= 4, f"rows={mc}")
        print("")

        # --- Negative: source_trust ---
        print("N. Negative: source_trust")
        for k, name in [("grade_sg","system_generated"),("grade_ai","assistant_inferred"),("grade_3p","third_party_doc"),("grade_uk","unknown")]:
            r = await resolve_candidate(ids[k])
            chk(f"N: {name} grade_fact not committed", act(r) != "auto_commit", f"action={act(r)}")
        print("")

        # --- Negative: safety ---
        print("S. Negative: safety")
        r = await resolve_candidate(ids["grade_noev"]); chk("S: no source_event_ids", act(r) != "auto_commit")
        r = await resolve_candidate(ids["grade_seal"]); chk("S: sealed", act(r) != "auto_commit")
        r = await resolve_candidate(ids["diag"]); chk("S: diagnosis-like", act(r) != "auto_commit")
        r = await resolve_candidate(ids["neg_inf"]); chk("S: negative inference", act(r) != "auto_commit")
        r = await resolve_candidate(ids["ident"]); chk("S: identity_fact", act(r) != "auto_commit")
        r = await resolve_candidate(ids["health"]); chk("S: health_baseline", act(r) != "auto_commit")
        print("")

        # --- Negative: status ---
        print("T. Negative: terminal status")
        r = await resolve_candidate(ids["grade_req"]); chk("T: requires_review not downgraded", act(r) != "auto_commit")
        r = await resolve_candidate(ids["grade_obs"]); chk("T: observation_only blocked", act(r) == "error")
        r = await resolve_candidate(ids["grade_exp"]); chk("T: expired blocked", act(r) == "error")
        print("")

        # --- Regression ---
        print("R. Regression")
        r = await resolve_candidate(ids["emo"]); chk("R: emotional → observation_only", act(r) == "observation_only")
        print("")

    finally:
        print("C. Cleanup")
        rc, rm, ri = await cleanup(list(ids.values()))
        chk("C1: candidates=0", rc == 0, f"rem={rc}")
        chk("C2: memories=0", rm == 0, f"rem={rm}")
        chk("C3: items=0", ri == 0, f"rem={ri}")
        await close_pool()

    total = c; passed = total - f
    print(f"\nRESULTS: {passed}/{total} PASS"); sys.exit(1 if f else 0)

if __name__ == "__main__": asyncio.run(main())
