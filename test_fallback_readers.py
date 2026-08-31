"""A0 fallback readers: do they read events first and fall back correctly?

Three states have to work:
  1. events HAS the data          -> read from events, ids populated
  2. events lacks it, table has it -> fall back, ids None
  3. the fallback table is GONE    -> return [], do not raise

State 3 is the one that matters at step A3, and it cannot be tested against
production. It is tested here by building a local database with the table
deliberately absent.
"""
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
MIRROR = os.path.join(REPO, "event_db", "soccer.duckdb")
HERE = os.path.join(os.environ.get("TEMP", "."), "a0_test")
os.makedirs(HERE, exist_ok=True)
DB = os.path.join(HERE, "a0.duckdb")

sys.path.insert(0, REPO)

import duckdb  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}   {detail}")


src = duckdb.connect(MIRROR, read_only=True)
og_game = src.execute(
    "SELECT gameId FROM events WHERE playType = 'OwnGoal' "
    "GROUP BY 1 ORDER BY gameId LIMIT 1").fetchone()[0]
plain_game = src.execute(
    "SELECT gameId FROM events WHERE gameId NOT IN "
    "(SELECT gameId FROM events WHERE playType = 'OwnGoal') "
    "GROUP BY 1 ORDER BY gameId LIMIT 1").fetchone()[0]
src.close()


def build(path, legacy_tables):
    """A scratch db holding two games. `legacy_tables` False = the post-A3
    world, where own_goals and cards no longer exist."""
    if os.path.exists(path):
        os.remove(path)
    w = duckdb.connect(path)
    w.execute(f"ATTACH '{MIRROR}' AS m (READ_ONLY)")
    for t in ("games", "events"):
        w.execute(f"CREATE TABLE {t} AS SELECT * FROM m.{t} "
                  f"WHERE gameId IN (?, ?)", [og_game, plain_game])
    w.execute("DETACH m")
    if legacy_tables:
        w.execute("CREATE TABLE own_goals (gameId VARCHAR, minute INTEGER, "
                  "credited_team VARCHAR)")
        w.execute("INSERT INTO own_goals VALUES (?, 33, 'Some Club FC')",
                  [plain_game])
        # deliberately WRONG, to prove events wins when both are present
        w.execute("INSERT INTO own_goals VALUES (?, 99, 'Wrong Club')",
                  [og_game])
        w.execute("CREATE TABLE cards (gameId VARCHAR, minute INTEGER, "
                  "playerName VARCHAR, teamName VARCHAR, card_type VARCHAR)")
        w.execute("INSERT INTO cards VALUES (?, 67, 'A. Player', "
                  "'Some Club FC', 'red')", [plain_game])
    w.close()


DB_NO_LEGACY = os.path.join(HERE, "a0_post_a3.duckdb")
DB_LEGACY = os.path.join(HERE, "a0_today.duckdb")
build(DB_NO_LEGACY, legacy_tables=False)
build(DB_LEGACY, legacy_tables=True)

os.environ["SOCCER_DB_PATH"] = DB_NO_LEGACY
import shared.motherduck as md  # noqa: E402

own_goals = md.get_own_goals_for_game.__wrapped__
red_cards = md.get_red_cards_for_game.__wrapped__

print("[1] events HAS own goals -> read from events")
res = own_goals(og_game)
check("returns rows", len(res) > 0, f"{len(res)} own goal(s)")
check("source is events", all(r["source"] == "events" for r in res))
check("teamId populated", all(r["teamId"] for r in res),
      str([(r["minute"], r["teamId"][:8], r["period"]) for r in res]))
check("period is real, not inferred",
      all(r["period"] in (1, 2, 3, 4) for r in res))

print("\n[2] the fallback table does NOT exist -> empty, no exception")
res = own_goals(plain_game)
check("own goals: returns [] rather than raising", res == [], str(res))
try:
    rc = red_cards(og_game)
    check("red cards: returns [] rather than raising", rc == [], str(rc))
except Exception as e:
    check("red cards: returns [] rather than raising", False,
          f"{type(e).__name__}: {e}")

# ── Same readers, against the database that still has the legacy tables ───
print("\n[3] fallback table present, events empty -> falls back")
os.environ["SOCCER_DB_PATH"] = DB_LEGACY
# get_connection is @st.cache_resource, and that cache is global - a module
# reload alone reuses the old handle and the test silently reads the wrong
# database. Clear it explicitly.
md.get_connection.clear()

res = own_goals(plain_game)
check("own goals: falls back", len(res) == 1 and res[0]["source"] == "own_goals",
      str(res))
check("fallback carries no teamId", res and res[0]["teamId"] is None)
check("fallback keeps credited_team for old callers",
      res and res[0]["credited_team"] == "Some Club FC")

rc = red_cards(plain_game)
check("red cards: falls back", len(rc) == 1 and rc[0]["source"] == "cards",
      str(rc))

print("\n[4] events still WINS when both are present")
res = own_goals(og_game)
check("events preferred over the legacy table",
      res and all(r["source"] == "events" for r in res),
      f"sources={[r['source'] for r in res]}")
check("the wrong legacy row ('Wrong Club', min 99) is not returned",
      all(r["minute"] != 99 for r in res),
      str([(r["minute"], r["source"]) for r in res]))

print("\n[5] own_goal_conceding_side — the crash that shipped with the migration")
# The events path returns credited_team=None by design. Every Streamlit page
# called .lower() on it unconditionally, so after the migration ANY game with
# an own goal raised AttributeError on page load. Four sites, one duplicated
# block; the resolution now lives here instead.
side = md.own_goal_conceding_side
home_id, away_id = md.get_game_team_ids.__wrapped__(og_game)
og_row = own_goals(og_game)[0]

check("events row still has credited_team=None",
      og_row["credited_team"] is None)
check("resolves a side from teamId alone (no name)",
      side(og_game, og_row["teamId"], None, "Home FC", "Away FC")
      in ("home", "away"))
check("teamId maps to the right side",
      side(og_game, home_id, None, "Home FC", "Away FC") == "home"
      and side(og_game, away_id, None, "Home FC", "Away FC") == "away")
check("legacy path still matches on name when there is no id",
      side(plain_game, None, "Home FC", "Home FC", "Away FC") == "home"
      and side(plain_game, None, "Away FC", "Home FC", "Away FC") == "away")
check("id WINS over a contradicting name",
      side(og_game, home_id, "Away FC", "Home FC", "Away FC") == "home")
check("neither id nor name returns None rather than raising",
      side(og_game, None, None, "Home FC", "Away FC") is None)
check("unknown teamId falls through instead of guessing",
      side(og_game, "not-a-real-team-id", None, "Home FC", "Away FC") is None)

print(f"\n{'=' * 60}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
