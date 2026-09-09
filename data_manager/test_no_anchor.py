"""The test that would have caught the anchor bug. No network needed.

Between 2026-08-29 and 2026-09-01 `build_game_event_statement` named a team.
Naming one made it the ANCHOR, and TruMedia answered every team-scoped column
from that team's point of view - on the opponent's rows too. 21 columns held
the home side's values on 4.3M away rows: teamAbbrevName, newestTeamColor,
MatchState, Formation, the score columns, the assist and chance flags.

Nothing caught it for three days. Every check that ran asked "did the right
ROWS arrive?" - counts, play types, no half-matches - and all of them pass on
data with the wrong team stamped on half of it.

The check that catches it is one line: in a two-sided match, a team column must
hold TWO distinct values, not one. That assertion is the last section here, and
it runs against a fixture rather than the network so it costs nothing.

    py test_no_anchor.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb                                                     # noqa: E402
import pandas as pd                                              # noqa: E402
import downloader as dl                                          # noqa: E402

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


print("\n[1] the statement must NOT name a team")
stmt = dl.build_game_event_statement(["S1"], ["G1", "G2"])
check("no team.teamId predicate", "team.teamId" not in stmt,
      "naming a team is what corrupts the opponent's rows")
check("still scoped to the games asked for",
      "'G1'" in stmt and "'G2'" in stmt)
check("still scoped to the season", "'S1'" in stmt)
check("carries the side flags",
      "team.event.primary AS is_team" in stmt
      and "opponent.event.primary AS is_opp" in stmt)
# `event.toucher` legitimately appears in the SELECT list, resolving the
# toucher to a name. What must not appear is the PREDICATE form, which is what
# filtered out cards, substitutions and 23 other play types.
_where = stmt[stmt.index("WHERE"):stmt.index("ORDER BY")]
check("no event.toucher PREDICATE", "toucher" not in _where,
      f"WHERE is: {_where.strip()}")

print("\n[2] batches are no longer grouped by team")
todo = pd.DataFrame({
    "gameId": [f"g{i}" for i in range(45)],
    "homeTeamId": [f"t{i % 9}" for i in range(45)],
    "awayTeamId": [f"t{(i + 1) % 9}" for i in range(45)],
})
batches = dl.plan_batches(todo, batch_size=20)
sizes = [len(b) for b in batches]
check("chunks by size, not by club", sizes == [20, 20, 5], str(sizes))
covered = [r["gameId"] for b in batches for _, r in b]
check("every game appears exactly once",
      sorted(covered) == sorted(todo["gameId"]) and len(covered) == 45)
check("default batch size is half the proven ceiling",
      dl.MAX_GAMES_PER_REQUEST == 20,
      "40 returned 182,618 rows against LIMIT 200000 - too close")

print("\n[3] anchor-written games are still DETECTED")
# WORK_ANCHORED was removed on 2026-09-09. It was a work-list state meaning
# "select these and re-download", and after the full rebuild that stopped being
# the right response: measured across all 5,619 production games, zero are
# anchored, so the branch could never be taken. If one ever appears the answer
# is to fix `build_game_event_statement`, not to re-fetch under it.
#
# The DETECTION had to survive the state's removal, because the bug was silent
# - plausible row counts, no error, 21 wrong columns. It lives in
# `count_anchored_games` and is surfaced on the Health page.
check("the state is gone", not hasattr(dl, "WORK_ANCHORED"))
check("the detector remains", hasattr(dl, "count_anchored_games"))

_probe = duckdb.connect()
_probe.execute("CREATE TABLE events (gameId VARCHAR, teamId VARCHAR, "
               '"teamAbbrevName" VARCHAR)')
_probe.execute("INSERT INTO events VALUES "
               "('clean','home','CRY'),('clean','away','ARS')")
check("clean game is not flagged", dl.count_anchored_games(_probe) == 0)
_probe.execute("INSERT INTO events VALUES "
               "('anchored','home','CRY'),('anchored','away','CRY')")
check("anchored game IS flagged", dl.count_anchored_games(_probe) == 1,
      "two sides sharing one abbreviation")
_probe.close()

print("\n[4] THE ASSERTION THAT WOULD HAVE CAUGHT IT")
# A two-sided match where both teams carry the home side's identity - exactly
# what an anchored request produced, and what every other check called healthy.
anchored = pd.DataFrame({
    "gameId": ["g1"] * 4,
    "teamId": ["home", "home", "away", "away"],
    "teamAbbrevName": ["CRY", "CRY", "CRY", "CRY"],
    "newestTeamColor": ["#1B458F"] * 4,
    "teamFinalScore": [1, 1, 1, 1],
})
clean = anchored.copy()
clean["teamAbbrevName"] = ["CRY", "CRY", "ARS", "ARS"]
clean["newestTeamColor"] = ["#1B458F", "#1B458F", "#DB0007", "#DB0007"]
clean["teamFinalScore"] = [1, 1, 2, 2]

TEAM_SCOPED = ["teamAbbrevName", "newestTeamColor", "teamFinalScore"]


def anchor_contaminated(df):
    """True if any team-scoped column holds ONE value across a two-sided game."""
    for _gid, g in df.groupby("gameId"):
        if g["teamId"].nunique() < 2:
            continue
        for col in TEAM_SCOPED:
            if col in g.columns and g[col].nunique() < 2:
                return True
    return False


check("flags anchor-written rows", anchor_contaminated(anchored) is True,
      "both sides sharing one abbreviation/colour/score")
check("passes clean rows", anchor_contaminated(clean) is False,
      "each side carries its own")

print("\n" + "=" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
