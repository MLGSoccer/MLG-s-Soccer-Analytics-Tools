"""Prove practice mode round-trips WITHOUT touching production.

Builds a CSV in the downloader's own event-log shape from real mirror data,
pushes it through the real `upsert_events_to_motherduck`, and checks the
resulting local database is structurally identical to what production would
have got.

No TruMedia session needed - this exercises everything after the fetch.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRROR = os.path.join(REPO, "event_db", "soccer.duckdb")
# Scratch, NOT the repo: this writes a database and a CSV, and neither should
# ever be mistaken for real data sitting in data_manager/.
HERE = os.path.join(os.environ.get("TEMP", "."), "dm_practice_test")
os.makedirs(HERE, exist_ok=True)
LOCAL_DB = os.path.join(HERE, "practice.duckdb")
CSV = os.path.join(HERE, "practice_events.csv")

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "data_manager"))

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


for p in (LOCAL_DB, CSV):
    if os.path.exists(p):
        os.remove(p)

# ── Build a CSV shaped like a real event-log export ─────────────────────────
mirror = duckdb.connect(MIRROR, read_only=True)
have = {r[0] for r in mirror.execute("DESCRIBE events").fetchall()}
# what the upsert reads: the events columns plus the games-derivation ones
want = list(dict.fromkeys(
    dl.EVENTS_MD_COLS +
    ['gameId', 'optaMatchId', 'Date', 'homeTeam', 'awayTeam',
     'homeFinalScore', 'awayFinalScore', 'teamFullName', 'teamId',
     'opponentId', 'seasonId']))
present = [c for c in want if c in have]
missing = [c for c in want if c not in have]
print(f"mirror supplies {len(present)}/{len(want)} of the columns the upsert reads")
if missing:
    print(f"  absent from the mirror (upsert must cope): {missing}")

gid = mirror.execute(
    "SELECT gameId FROM events GROUP BY 1 ORDER BY count(*) DESC, gameId "
    "LIMIT 1").fetchone()[0]
tid = mirror.execute(
    "SELECT teamId FROM events WHERE gameId = ? GROUP BY 1 "
    "ORDER BY count(*) DESC, teamId LIMIT 1", [gid]).fetchone()[0]
cols = ", ".join(f'"{c}"' for c in present)
mirror.execute(
    f"COPY (SELECT {cols} FROM events WHERE gameId = ? AND teamId = ?) "
    f"TO '{CSV}' (HEADER, DELIMITER ',')", [gid, tid])
n_src = mirror.execute(
    "SELECT count(*) FROM events WHERE gameId = ? AND teamId = ?",
    [gid, tid]).fetchone()[0]
mirror.close()
print(f"\nexported {n_src} events for one (game, team) -> CSV\n")

# ── Practice mode ───────────────────────────────────────────────────────────
print("[1] practice mode routes away from production")
os.environ[dl.LOCAL_DB_ENV] = LOCAL_DB
con = dl.get_motherduck_connection(token="THIS-TOKEN-IS-INVALID-ON-PURPOSE")
check("connects with a deliberately invalid token",
      con is not None, "so it cannot have reached MotherDuck")
check("local file was created", os.path.exists(LOCAL_DB))

tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
expected = {"games", "events", "game_fixtures", "player_minutes",
            "own_goals", "cards", "player_game_minutes"}
check("every production table exists locally", expected <= tables,
      f"missing: {sorted(expected - tables)}" if not expected <= tables
      else f"{len(tables)} tables")

ev_cols = [r[0] for r in con.execute("DESCRIBE events").fetchall()]
check("events has all 6 columns added this migration",
      {"primaryPlayer", "primaryPlayerId", "qualifierYellow",
       "qualifierSecondYellow", "qualifierRed",
       "qualifierCardRescinded"} <= set(ev_cols),
      f"{len(ev_cols)} columns")
con.close()

# ── The real upsert ─────────────────────────────────────────────────────────
print("\n[2] the real upsert writes to the local file")
try:
    n = dl.upsert_events_to_motherduck(token=None, csv_path=CSV)
    check("upsert_events_to_motherduck completed", True, f"{n} rows")
except Exception as e:
    check("upsert_events_to_motherduck completed", False,
          f"{type(e).__name__}: {e}")

def peek(sql):
    """Read the practice db and CLOSE. DuckDB refuses a read-only handle
    alongside a writable one on the same file, so nothing may be held open
    across an upsert."""
    v = duckdb.connect(LOCAL_DB, read_only=True)
    try:
        return v.execute(sql).fetchone()
    finally:
        v.close()


got_ev = peek("SELECT count(*) FROM events")[0]
got_g = peek("SELECT count(*) FROM games")[0]
check("events landed", got_ev == n_src, f"{got_ev} vs {n_src} exported")
check("games row derived from the event log", got_g == 1, f"{got_g}")
hm, aw, hid, aid = peek(
    "SELECT homeTeam, awayTeam, homeTeamId, awayTeamId FROM games")
check("homeTeamId/awayTeamId derived", bool(hid) and bool(aid),
      f"{hm} ({hid}) v {aw} ({aid})")

nulled = peek("SELECT count(*) FROM events WHERE primaryPlayer IS NULL")[0]
check("columns absent from the CSV land as NULL, not as a failure",
      nulled == got_ev, f"{nulled}/{got_ev} rows NULL in primaryPlayer")

# idempotence: the same CSV twice must not double the rows
dl.upsert_events_to_motherduck(token=None, csv_path=CSV)
again = peek("SELECT count(*) FROM events")[0]
check("re-running the same CSV is idempotent", again == got_ev,
      f"{again} after second run vs {got_ev}")

# ── Fidelity: did naming the columns misalign anything? ────────────────────
# The whole point of the rewrite was to stop inserting positionally. So check
# every column that came from the mirror arrives holding the SAME values -
# a misalignment would put each value in its neighbour's column, same types,
# no error.
print("\n[2b] values land in the columns they came from")
src = duckdb.connect(MIRROR, read_only=True)
dst = duckdb.connect(LOCAL_DB, read_only=True)
compared = mismatched = skipped = []
compared, mismatched, skipped = [], [], []
for c in present:
    if c not in {r[0] for r in dst.execute("DESCRIBE events").fetchall()}:
        skipped.append(c)
        continue
    a = src.execute(
        f'SELECT "{c}" FROM events WHERE gameId = ? AND teamId = ? '
        f'ORDER BY eventGuid', [gid, tid]).fetchall()
    b = dst.execute(
        f'SELECT "{c}" FROM events ORDER BY eventGuid').fetchall()
    compared.append(c)
    if a != b:
        mismatched.append(c)
src.close()
dst.close()
check(f"all {len(compared)} shared columns identical to source",
      not mismatched, f"mismatched: {mismatched}")
if skipped:
    print(f"        (not in the events table, skipped: {skipped})")

# ── Production is untouched ─────────────────────────────────────────────────
print("\n[3] the default is still production")
del os.environ[dl.LOCAL_DB_ENV]
try:
    dl.get_motherduck_connection(token="THIS-TOKEN-IS-INVALID-ON-PURPOSE")
    check("invalid token is REJECTED when practice mode is off", False,
          "it connected - the default is not production")
except Exception as e:
    check("invalid token is REJECTED when practice mode is off", True,
          f"{type(e).__name__} - so the default really is the cloud")

print(f"\n{'=' * 60}\n{ok} passed, {fail} failed")
print(f"practice db: {LOCAL_DB}")
sys.exit(1 if fail else 0)
