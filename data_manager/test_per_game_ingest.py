"""Per-game ingest, end to end against live TruMedia, writing ONLY locally.

Discover fixtures -> download one game -> attribute both sides from the raw
is_team/is_opp booleans -> write atomically at gameId -> check what landed.

Production is never opened: both local overrides are set before any
connection is made, and the token passed is deliberately invalid.

Usage:
    py test_per_game_ingest.py <path-to-file-containing-a-curl> [seasonId]

Needs a live TruMedia session (~4h). Capture: DevTools -> Network, filter
dp-proxy, right-click a request -> Copy -> Copy as cURL, paste into a file.
"""
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)
CURL = Path(sys.argv[1])
if not CURL.exists():
    print(f"ERROR: no such file: {CURL}")
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
HERE = Path(os.environ.get("TEMP", ".")) / "a1a_test"
HERE.mkdir(exist_ok=True)
DB = HERE / "pergame.duckdb"
CSV = HERE / "pergame_events.csv"

# BOTH overrides, and they point at different layers. Setting only the first
# is not obviously wrong - the downloader writes locally while the chart
# readers quietly connect to production - and the test then passes for the
# wrong reason. That happened.
os.environ["DATA_MANAGER_LOCAL_DB"] = str(DB)   # downloader writes here
os.environ["SOCCER_DB_PATH"] = str(DB)          # chart readers read here
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


for p in (DB, CSV):
    if p.exists():
        p.unlink()

session = dl.create_session(dl.parse_cookies_from_curl(
    CURL.read_text(encoding="utf-8", errors="replace")))
SEASON = sys.argv[2] if len(sys.argv) > 2 else "51r6ph2woavlbbpk8f29nynf8"

# ── 1. Fixture discovery ───────────────────────────────────────────────────
print("[1] discover_fixtures - one request, no team predicate")
fx = dl.discover_fixtures(session, [SEASON])
check("returns fixtures", len(fx) > 0, f"{len(fx)} games")
check("one row per game", fx["gameId"].is_unique)
check("both teamIds present",
      fx["homeTeamId"].notna().all() and fx["awayTeamId"].notna().all())
check("home and away are different clubs",
      (fx["homeTeamId"] != fx["awayTeamId"]).all())
check("match metadata captured",
      {"referee", "venueName", "attendance", "p1Start", "matchLength"}
      <= set(fx.columns),
      f"{len(fx.columns)} columns")
played = fx[fx["status"] == "Played"]
print(f"        {len(played)} played, {len(fx) - len(played)} not played")

row = played.iloc[0]
gid, home_id, away_id = row["gameId"], row["homeTeamId"], row["awayTeamId"]
print(f"        test game: {row['homeTeam']} v {row['awayTeam']}  "
      f"{str(row['gameDate'])[:10]}")

fixtures = {r["gameId"]: {"homeTeamId": r["homeTeamId"],
                          "awayTeamId": r["awayTeamId"],
                          "homeTeam": r["homeTeam"], "awayTeam": r["awayTeam"]}
            for _, r in played.iterrows()}

# ── 2. One request, both sides ─────────────────────────────────────────────
print("\n[2] download_game_events - ONE request for the whole match")
rows, kb = dl.download_game_events(session, [SEASON], [gid], str(CSV))
check("downloaded", rows > 0, f"{rows:,} rows, {kb:.0f} KB")

import pandas as pd  # noqa: E402
raw = pd.read_csv(CSV)
t = raw["is_team"].fillna(False).astype(bool)
o = raw["is_opp"].fillna(False).astype(bool)
check("is_team / is_opp are usable booleans",
      t.any() and o.any(), f"is_team={t.sum()}  is_opp={o.sum()}  "
                           f"neither={(~t & ~o).sum()}")
check("no row is both sides", not (t & o).any())
print(f"        play types: {raw['playType'].nunique()} distinct")
cards = raw[raw["playType"].isin(["Booking", "Dismissal"])]
print(f"        cards in the feed: {len(cards)}  "
      f"(the toucher predicate hid these entirely)")

# ── 3. Atomic write to the practice database ───────────────────────────────
print("\n[3] upsert_game_events - DELETE scoped to gameId")
n_games, n_rows = dl.upsert_game_events(
    token="INVALID-ON-PURPOSE", csv_path=str(CSV), fixtures=fixtures)
check("wrote the game", n_games == 1 and n_rows > 0,
      f"{n_games} game, {n_rows:,} rows")


def peek(sql, params=None):
    c = duckdb.connect(str(DB), read_only=True)
    try:
        return c.execute(sql, params or []).fetchall()
    finally:
        c.close()


sides = peek("SELECT teamId, count(*) FROM events WHERE gameId = ? "
             "GROUP BY 1 ORDER BY 2 DESC", [gid])
check("BOTH sides landed", len(sides) == 2, str([(s[0][:8], s[1]) for s in sides]))
check("sides are the fixture's two clubs",
      {s[0] for s in sides} == {home_id, away_id})

names = peek("SELECT DISTINCT teamId, teamFullName FROM events WHERE gameId = ?",
             [gid])
check("teamFullName follows teamId, not the anchor",
      len({n[1] for n in names}) == 2, str(names))

g = peek("SELECT homeTeam, awayTeam, homeTeamId, awayTeamId FROM games "
         "WHERE gameId = ?", [gid])
check("games row from the FIXTURE, ids not names",
      g and g[0][2] == home_id and g[0][3] == away_id, str(g))

pt = peek("SELECT count(DISTINCT playType) FROM events WHERE gameId = ?", [gid])
check("full play-type vocabulary stored", pt[0][0] > 22,
      f"{pt[0][0]} distinct play types (per-team feed carries 22)")

cardrows = peek("SELECT playType, primaryPlayer, teamId, qualifierYellow, "
                "qualifierRed FROM events WHERE gameId = ? AND "
                "(qualifierYellow OR qualifierRed OR qualifierSecondYellow)",
                [gid])
check("cards stored with a player and a side", len(cardrows) > 0,
      f"{len(cardrows)} card events")
for c in cardrows[:4]:
    print(f"        {c[0]:<10} {str(c[1]):<18} team={str(c[2])[:8]} "
          f"Y={c[3]} R={c[4]}")

# ── 4. Idempotence ─────────────────────────────────────────────────────────
print("\n[4] re-running the same game replaces rather than duplicates")
before = peek("SELECT count(*) FROM events WHERE gameId = ?", [gid])[0][0]
dl.upsert_game_events("INVALID-ON-PURPOSE", str(CSV), fixtures)
after = peek("SELECT count(*) FROM events WHERE gameId = ?", [gid])[0][0]
check("idempotent", before == after, f"{before} -> {after}")

print(f"\n{'=' * 62}\n{ok} passed, {fail} failed")
print(f"practice db: {DB}")
sys.exit(1 if fail else 0)
