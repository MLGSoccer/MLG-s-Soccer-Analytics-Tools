"""The work list: does it actually see a half-ingested match?

That is the whole point. The current tool cannot - both teams were fetched,
so both look up to date, while the match holds one side.

Built as a fixture rather than found in the wild, so the expected answer is
known: three games in three deliberate states.
"""
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
REPO = Path(__file__).resolve().parent.parent
MIRROR = REPO / "event_db" / "soccer.duckdb"
HERE = Path(os.environ.get("TEMP", ".")) / "worklist_test"
HERE.mkdir(exist_ok=True)
DB = HERE / "wl.duckdb"
sys.path.insert(0, str(REPO / "data_manager"))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
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

src = duckdb.connect(str(MIRROR), read_only=True)
games = src.execute("""
    SELECT g.gameId, g.homeTeamId, g.awayTeamId, g.homeTeam, g.awayTeam
    FROM games g JOIN events e ON e.gameId = g.gameId
    WHERE g.homeTeamId IS NOT NULL AND g.awayTeamId IS NOT NULL
    GROUP BY 1,2,3,4,5 HAVING count(DISTINCT e.teamId) = 2
    ORDER BY g.gameId LIMIT 3""").fetchall()
src.close()
g_complete, g_one_sided, g_missing = [g[0] for g in games]

w = duckdb.connect(str(DB))
w.execute(f"ATTACH '{MIRROR}' AS m (READ_ONLY)")
w.execute("CREATE TABLE games AS SELECT * FROM m.games WHERE gameId IN (?,?,?)",
          [g_complete, g_one_sided, g_missing])
# complete: both sides.  one_sided: home only.  missing: no events at all.
w.execute("CREATE TABLE events AS SELECT * FROM m.events WHERE gameId = ?",
          [g_complete])
w.execute("""INSERT INTO events SELECT e.* FROM m.events e
             JOIN m.games g ON e.gameId = g.gameId
             WHERE e.gameId = ? AND e.teamId = g.homeTeamId""", [g_one_sided])
w.execute("DETACH m")
w.close()

fixtures = pd.DataFrame([
    {"gameId": g, "homeTeamId": h, "awayTeamId": a,
     "homeTeam": hn, "awayTeam": an, "status": "Played"}
    for g, h, a, hn, an in games
] + [{"gameId": "FUTURE-GAME-ID", "homeTeamId": "x", "awayTeamId": "y",
      "homeTeam": "A", "awayTeam": "B", "status": "Scheduled"}])

con = duckdb.connect(str(DB), read_only=True)
work = dl.build_work_list(con, fixtures)
con.close()

print("[1] classification\n")
by_id = {r["gameId"]: r for _, r in work.iterrows()}
for gid, expect in ((g_complete, dl.WORK_COMPLETE),
                    (g_one_sided, dl.WORK_ONE_SIDED),
                    (g_missing, dl.WORK_MISSING),
                    ("FUTURE-GAME-ID", dl.WORK_NOT_PLAYED)):
    got = by_id[gid]
    check(f"{expect:<12} recognised", got["state"] == expect,
          f"got {got['state']}  sides={got['sides_present']} "
          f"events={got['events_stored']:,}")

print("\n[2] the case the CURRENT tool is blind to\n")
one = by_id[g_one_sided]
check("a half-ingested match is flagged as work",
      one["state"] == dl.WORK_ONE_SIDED and one["sides_present"] == 1,
      f"{one['events_stored']:,} events, 1 side - both teams look "
      f"'up to date' by last-game-date")

print("\n[3] summary, for the review step before anything downloads\n")
s = dl.work_list_summary(work)
for k in dl.WORK_ORDER:
    print(f"        {k:<12} {s[k]}")
check("summary counts every fixture", sum(s.values()) == len(work),
      f"{sum(s.values())} == {len(work)}")

print("\n[4] against the REAL mirror, whole database\n")
con = duckdb.connect(str(MIRROR), read_only=True)
allg = con.execute("""
    SELECT gameId, homeTeamId, awayTeamId, homeTeam, awayTeam, 'Played' AS status
    FROM games WHERE homeTeamId IS NOT NULL""").df()
real = dl.build_work_list(con, allg)
con.close()
rs = dl.work_list_summary(real)
tot = sum(rs.values())
for k in dl.WORK_ORDER:
    print(f"        {k:<12} {rs[k]:>6,}  ({100.0*rs[k]/max(tot,1):.1f}%)")
check("mirror is almost entirely complete", rs[dl.WORK_COMPLETE] > tot * 0.9,
      f"{rs[dl.WORK_COMPLETE]:,}/{tot:,}")

print(f"\n{'=' * 62}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
