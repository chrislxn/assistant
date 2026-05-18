"""Phase 1.5-M3c: expire observation_only candidates with valid_to < NOW().

Default dry-run. Set APPLY=1 to actually update.

Usage:
    DATABASE_URL=... python3 scripts/expire_observations.py         # dry-run
    DATABASE_URL=... DRY_RUN=1 python3 scripts/expire_observations.py # dry-run
    DATABASE_URL=... APPLY=1 python3 scripts/expire_observations.py  # apply
"""

import asyncio, json, os, sys, time

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    print(json.dumps({"status": "error", "reason": "DATABASE_URL not set"})); sys.exit(2)

APPLY = os.getenv("APPLY", "") == "1"
DRY_RUN = os.getenv("DRY_RUN", "") == "1" or not APPLY  # default dry-run


async def run():
    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=1)

    async with pool.acquire() as conn:
        # Count matching
        matched = [dict(r) for r in await conn.fetch(
            """SELECT candidate_id, valid_to, rendered_text FROM memory_candidates
               WHERE status = 'observation_only'
                 AND valid_to IS NOT NULL
                 AND valid_to < NOW()
               ORDER BY valid_to ASC""")]
        mc = len(matched)
        cids = [str(r["candidate_id"]) for r in matched]
        oldest = str(matched[0]["valid_to"]) if matched else None
        newest = str(matched[-1]["valid_to"]) if matched else None

        ec = 0
        if APPLY and mc > 0:
            result = await conn.execute(
                """UPDATE memory_candidates
                   SET status = 'expired',
                       reviewed_at = NOW(),
                       reviewed_by = 'expiry_job'
                   WHERE status = 'observation_only'
                     AND valid_to IS NOT NULL
                     AND valid_to < NOW()""")
            ec = int(result.split()[-1]) if result else 0

    await pool.close()

    out = {
        "status": "ok",
        "dry_run": DRY_RUN,
        "matched_count": mc,
        "expired_count": ec,
        "candidate_ids": cids[:20],
        "oldest_valid_to": oldest,
        "newest_valid_to": newest,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    asyncio.run(run())
