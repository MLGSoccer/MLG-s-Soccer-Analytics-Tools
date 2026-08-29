"""A real campaign against live TruMedia, into a local file.

Discover -> classify -> run -> RECLASSIFY. The last step is the one that
matters: after running, the games just written must fall out of the work list
on their own. That is what makes the campaign resumable without any resume
state to keep.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
REPO = Path(__file__).resolve().parent.parent
HERE = Path(os.environ.get("TEMP", ".")) / "campaign_test"
HERE.mkdir(exist_ok=True)
DB = HERE / "campaign.duckdb"
if len(sys.argv) < 2:
    print(__doc__)
    print("Usage: py test_campaign.py <curl-file>")
    sys.exit(1)
CURL = Path(sys.argv[1])

os.environ["DATA_MANAGER_LOCAL_DB"] = str(DB)
os.environ["SOCCER_DB_PATH"] = str(DB)
sys.path.insert(0, str(REPO / "data_manager"))
sys.path.insert(0, str(REPO))

import duckdb  # noqa: E402
import downloader as dl  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}   {detail}")


if DB.exists():
    DB.unlink()
session = dl.create_session(dl.parse_cookies_from_curl(
    CURL.read_text(encoding="utf-8", errors="replace")))
SEASON = "51r6ph2woavlbbpk8f29nynf8"

print("[1] discover + classify against an EMPTY database")
fx = dl.discover_fixtures(session, [SEASON])
con = dl.get_motherduck_connection("INVALID")   # creates the schema locally
work = dl.build_work_list(con, fx)
con.close()
s = dl.work_list_summary(work)
print(f"        {s}")
check("an empty database is all 'missing'",
      s[dl.WORK_MISSING] == len(fx), f"{s[dl.WORK_MISSING]}/{len(fx)}")

# Keep it to five games - this is a mechanism test, not a download.
subset = work.head(5).copy()
print(f"\n[2] running a 5-game campaign")
seen = []
with tempfile.TemporaryDirectory() as tmp:
    con = dl.get_motherduck_connection("INVALID")
    try:
        written, failed, skipped = dl.run_campaign(
            session, "INVALID", fx, subset, tmp, [SEASON],
            states=(dl.WORK_MISSING,), con=con,
            progress=lambda d, t, g, st_, n: seen.append((d, g, n)))
    finally:
        con.close()
check("all five written", written == 5 and failed == 0,
      f"written={written} failed={failed} skipped={skipped}")
for d, g, n in seen:
    print(f"        {d}/5  {g}  {n}")

print("\n[3] RECLASSIFY - do the written games leave the work list?")
con = duckdb.connect(str(DB), read_only=True)
work2 = dl.build_work_list(con, fx)
con.close()
s2 = dl.work_list_summary(work2)
print(f"        {s2}")
check("five games moved to complete", s2[dl.WORK_COMPLETE] == 5,
      f"complete={s2[dl.WORK_COMPLETE]}")
check("the rest are still missing",
      s2[dl.WORK_MISSING] == len(fx) - 5,
      f"missing={s2[dl.WORK_MISSING]} of {len(fx)}")
done_ids = set(subset["gameId"])
still = set(work2[work2["state"] == dl.WORK_MISSING]["gameId"])
check("no written game is still on the list", not (done_ids & still))

print("\n[4] the stop callback lands on a BATCH boundary")
# batch_size=1 so the stop is testable at all: batches are grouped by home
# team, so four games can share one batch and stop() is then polled once.
# That is the documented behaviour, not a bug - the interrupt still lands
# between whole matches. Exercised at batch_size=1 to isolate the mechanism.
subset2 = work2[work2["state"] == dl.WORK_MISSING].head(4).copy()
calls = {"n": 0}


def _stop():
    calls["n"] += 1
    return calls["n"] > 2        # stop before the 3rd batch


with tempfile.TemporaryDirectory() as tmp:
    con = dl.get_motherduck_connection("INVALID")
    try:
        w2, f2, sk2 = dl.run_campaign(
            session, "INVALID", fx, subset2, tmp, [SEASON],
            states=(dl.WORK_MISSING,), con=con, stop=_stop, batch_size=1)
    finally:
        con.close()
check("stopped early", w2 == 2 and sk2 == 2, f"wrote={w2} skipped={sk2}")

con = duckdb.connect(str(DB), read_only=True)
partial = dl.build_work_list(con, fx)
sides = con.execute("SELECT gameId, count(DISTINCT teamId) FROM events "
                    "GROUP BY 1 HAVING count(DISTINCT teamId) <> 2").fetchall()
con.close()
check("NO game was left half-written", not sides, str(sides))
check("interrupted campaign is resumable",
      dl.work_list_summary(partial)[dl.WORK_COMPLETE] == 5 + w2,
      f"complete={dl.work_list_summary(partial)[dl.WORK_COMPLETE]}, "
      f"expected 5 from the first run + {w2} from the interrupted one")

print(f"\n{'=' * 62}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
