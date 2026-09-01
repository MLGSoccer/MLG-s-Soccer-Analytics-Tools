"""Does a backfill OVERWRITE correctly? An empty practice file cannot say.

The practice target starts empty, so a trial run there only proves "insert
into nothing". The backfill's actual risk is different:

  * _apply_schema runs 52 ALTER TABLE statements against a POPULATED table
  * games must be REPLACED, not duplicated
  * the new columns must go NULL -> populated on rows that already existed
  * DELETE must stay scoped to gameId, so games NOT in the run survive

So seed a practice database from the LOCAL event_db/soccer.duckdb - real
data in the old schema, which is exactly the state production is in - and
run the real download and ingest against it.

    py test_backfill_overwrite.py <path-to-file-containing-a-curl>

Reads MotherDuck not at all. Writes only to a temp file.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import duckdb  # noqa: E402
from downloader import (create_session, parse_cookies_from_curl,  # noqa: E402
                        EXPORT_URL, build_game_event_statement,
                        discover_fixtures, upsert_game_events, _apply_schema,
                        EXPANDED_EVENT_FIELDS)

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

SRC = os.path.join(REPO, "event_db", "soccer.duckdb")
if not os.path.exists(SRC):
    print(f"no local seed database at {SRC}")
    sys.exit(2)

SEASON = sys.argv[2] if len(sys.argv) > 2 else "51r6ph2woavlbbpk8f29nynf8"
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


tmp = tempfile.mkdtemp(prefix="backfill_test_")
DB = os.path.join(tmp, "practice.duckdb")
con = duckdb.connect(DB)

print("[1] seed a practice database from the local pre-expansion file")
# Build through the REAL DDL - a CTAS copy loses the primary key that
# INSERT OR REPLACE depends on, which is a property of the test rig, not of
# the thing under test. Then drop the expanded columns to recreate the state
# production is actually in, so step 2 alters a populated table for real.
_apply_schema(con)
for _e, _n, _t in EXPANDED_EVENT_FIELDS:
    con.execute(f'ALTER TABLE events DROP COLUMN IF EXISTS "{_n}"')

con.execute(f"ATTACH '{SRC}' AS s (READ_ONLY)")
seeded = [r[0] for r in con.execute(
    "SELECT gameId FROM s.games WHERE seasonId = ? LIMIT 6",
    [SEASON]).fetchall()]
ph = ",".join("?" * len(seeded))
for tbl in ("games", "events"):
    mine = [r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()]
    theirs = [r[0] for r in con.execute(f"DESCRIBE s.{tbl}").fetchall()]
    shared = [c for c in mine if c in theirs]
    cols = ", ".join(f'"{c}"' for c in shared)
    key = "gameId" if tbl == "games" else "gameId"
    con.execute(f"INSERT INTO {tbl} ({cols}) SELECT {cols} FROM s.{tbl} "
                f"WHERE {key} IN ({ph})", seeded)
con.execute("DETACH s")

before = {g: n for g, n in con.execute(
    "SELECT gameId, COUNT(*) FROM events GROUP BY 1").fetchall()}
n_cols_before = len(con.execute("DESCRIBE events").fetchall())
check("seeded real games", len(seeded) >= 2, f"{len(seeded)} games")
check("seeded real events", sum(before.values()) > 1000,
      f"{sum(before.values()):,} events across {len(before)} games")
check("games kept its primary key (INSERT OR REPLACE needs it)",
      any("PRIMARY" in str(r).upper() or r[3] for r in
          con.execute("PRAGMA table_info('games')").fetchall()))
print(f"        schema before: {n_cols_before} columns")

print("\n[2] _apply_schema against a POPULATED table")
rows_before = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
_apply_schema(con)
n_cols_after = len(con.execute("DESCRIBE events").fetchall())
cols = {r[0] for r in con.execute("DESCRIBE events").fetchall()}
check("all 52 columns added",
      all(n in cols for _e, n, _t in EXPANDED_EVENT_FIELDS),
      f"{n_cols_before} -> {n_cols_after} columns")
check("existing rows untouched by the ALTERs",
      con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == rows_before,
      f"{rows_before:,} rows")
nulls = con.execute(
    'SELECT COUNT(*) FROM events WHERE "MatchState" IS NOT NULL').fetchone()[0]
check("new columns are NULL on pre-existing rows", nulls == 0)

print("\n[3] re-download ONE game and ingest over the top")
session = create_session(parse_cookies_from_curl(
    open(sys.argv[1], encoding="utf-8", errors="replace").read()))
fx = discover_fixtures(session, [SEASON])
target = next((g for g in seeded if g in set(fx["gameId"])), None)
if target is None:
    print("  none of the seeded games came back from discovery — cannot test")
    sys.exit(1)
row = fx[fx["gameId"] == target].iloc[0]
untouched = [g for g in seeded if g != target]
print(f"        replacing {target}  ({row.get('homeTeam')} v {row.get('awayTeam')})")
print(f"        leaving {len(untouched)} other games alone")

stmt = build_game_event_statement([SEASON], [target])
r = session.post(EXPORT_URL, json={
    "format": "MIXED", "statement": stmt, "export": "csv",
    "pageDescriptorName": "pageSoccerTeamEventLogOverall",
    "exportOptions": {"includeCalculations": False,
                      "includeVideoData": False}}, timeout=300)
csv_path = os.path.join(tmp, "e.csv")
open(csv_path, "wb").write(r.content)
fixtures = {target: {"homeTeamId": row["homeTeamId"],
                     "awayTeamId": row["awayTeamId"],
                     "homeTeam": row.get("homeTeam"),
                     "awayTeam": row.get("awayTeam")}}
games_w, rows_w = upsert_game_events(None, csv_path, fixtures, con=con)
print(f"        wrote {games_w} game, {rows_w:,} rows")

print("\n[4] the replaced game")
after = con.execute("SELECT COUNT(*) FROM events WHERE gameId = ?",
                    [target]).fetchone()[0]
check("not duplicated - old rows gone, new rows in", after == rows_w,
      f"{before[target]:,} before -> {after:,} after")
dupes = con.execute(
    "SELECT COUNT(*) - COUNT(DISTINCT eventGuid) FROM events WHERE gameId = ?",
    [target]).fetchone()[0]
check("eventGuid still unique within the game", dupes == 0)
filled = con.execute(
    'SELECT COUNT(*) FROM events WHERE gameId = ? AND "MatchState" IS NOT NULL',
    [target]).fetchone()[0]
check("new columns went NULL -> populated", filled > 0,
      f"{filled:,} rows carry MatchState")
pr = con.execute(
    'SELECT COUNT(*) FROM events WHERE gameId = ? AND "PressureReceived" '
    'IS NOT NULL', [target]).fetchone()[0]
check("PressureReceived populated on the replaced game", pr > 0, f"{pr:,} rows")
sides = con.execute("SELECT COUNT(DISTINCT teamId) FROM events WHERE gameId = ?",
                    [target]).fetchone()[0]
check("both sides present after replacement", sides == 2, f"{sides} sides")

print("\n[5] the games NOT in the run — DELETE scoped to gameId")
for g in untouched:
    now = con.execute("SELECT COUNT(*) FROM events WHERE gameId = ?",
                      [g]).fetchone()[0]
    check(f"{g[:12]} untouched", now == before[g],
          f"{before[g]:,} -> {now:,}")
still_null = con.execute(
    f'SELECT COUNT(*) FROM events WHERE gameId IN '
    f'({",".join("?" * len(untouched))}) AND "MatchState" IS NOT NULL',
    untouched).fetchone()[0] if untouched else 0
check("untouched games still have NULL in the new columns", still_null == 0)

print(f"\n{'=' * 62}\n{ok} passed, {fail} failed")
print(f"practice db: {DB}")
con.close()
sys.exit(1 if fail else 0)
