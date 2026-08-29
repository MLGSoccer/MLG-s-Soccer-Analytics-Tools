"""Smoke-test every chart data entry point against a DuckDB file.

PURPOSE
-------
Before changing how events are ingested, answer the functional question:
does every chart still GET data? Not "is the xG identical" — that is fidelity,
and it is not what breaks. What breaks is "the chart asks for a team and
nothing comes back", or "red cards are suddenly unreadable because the table
they lived in is gone".

So each entry point is called and classified:

    OK      returned something non-empty
    EMPTY   returned cleanly but with nothing in it  <-- the dangerous one
    ERROR   raised
    SKIP    harness could not construct arguments

EMPTY is the interesting column. A crash is obvious in production; a silently
empty chart is not.

USAGE
-----
    py smoke_chart_data.py <path-to-duckdb>          # e.g. event_db/soccer.duckdb
    py smoke_chart_data.py <path> --verbose          # show tracebacks

Run it against the CURRENT local mirror first to establish a baseline — that
mirror has events/games/player_game_minutes but no cards or own_goals, so it
demonstrates exactly what a missing-table scenario looks like before any
schema change is made.
"""
from __future__ import annotations

import argparse
import inspect
import os
import sys
import traceback
import warnings

warnings.filterwarnings("ignore")

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


SHOT_TYPES = "('Goal','PenaltyGoal','AttemptSaved','Miss','Post')"


def sample_ids(db_path):
    """Build a COHERENT sample: every value co-occurs with every other.

    Picking the most-common team and the most-common season independently
    produces combinations that never happened, and the season-scoped shot
    maps then fail with "no shots for team X in season Y" — a harness
    artefact that looks exactly like a real regression. So derive everything
    downward from one game that actually contains shots.
    """
    import duckdb
    # read_only is right for a FILE - it guarantees the harness cannot alter
    # what it is measuring. It is wrong for MotherDuck: the chart modules open
    # the same database read-write from this same process, and DuckDB refuses
    # two connections to one database with different configurations. Asking
    # for read-only here made every builder fail with a ConnectionException
    # that looked like a data problem and was not.
    is_file = not str(db_path).startswith("md:")
    con = duckdb.connect(db_path, read_only=True) if is_file \
        else duckdb.connect(db_path)
    out = {}

    def q(sql, params=None):
        try:
            return con.execute(sql, params or []).fetchall()
        except Exception:
            return []

    # 1. A game with plenty of shots AND both sides present. A partial test
    #    database may hold only one team's events for a fixture, and a
    #    half-populated game makes match-scoped charts look broken when they
    #    are merely under-fed. Require >= 2 distinct teamIds.
    r = q(f"""SELECT e.gameId, count(*) AS shots
              FROM events e WHERE e.playType IN {SHOT_TYPES}
              GROUP BY e.gameId
              HAVING count(DISTINCT e.gameId) > 0
                 AND (SELECT count(DISTINCT teamId) FROM events x
                      WHERE x.gameId = e.gameId) >= 2
              ORDER BY shots DESC LIMIT 1""")
    if not r:
        # Fall back to any game with shots, but say so — results will be
        # one-sided and match charts may legitimately look thin.
        r = q(f"""SELECT e.gameId, count(*) AS shots
                  FROM events e WHERE e.playType IN {SHOT_TYPES}
                  GROUP BY e.gameId ORDER BY shots DESC LIMIT 1""")
        if r:
            out["_warning"] = "no fully-populated game found; using a one-sided fixture"
    if not r:
        con.close()
        return out
    out["game_id"] = r[0][0]

    # 2. That game's season, from the games table.
    r = q("SELECT seasonId FROM games WHERE gameId = ?", [out["game_id"]])
    if r and r[0][0]:
        out["season_id"] = r[0][0]

    # 3. A team that actually took shots in that game.
    r = q(f"""SELECT teamId, count(*) AS n FROM events
              WHERE gameId = ? AND playType IN {SHOT_TYPES}
                AND teamId IS NOT NULL
              GROUP BY teamId ORDER BY n DESC LIMIT 1""", [out["game_id"]])
    if r:
        out["team_id"] = r[0][0]

    # 4. A shooter IN that game — required by the player shot maps, which
    #    take a NAME, and must be someone with shots in this exact fixture.
    r = q(f"""SELECT shooter, shooterId, count(*) AS n FROM events
              WHERE gameId = ? AND playType IN {SHOT_TYPES}
                AND shooter IS NOT NULL
              GROUP BY shooter, shooterId ORDER BY n DESC LIMIT 1""",
          [out["game_id"]])
    if r:
        out["player_name"] = r[0][0]
        out["player"] = r[0][0]
        if r[0][1]:
            out["player_id"] = r[0][1]

    # 5. Games for that team in that season, for the multi-game builders.
    if "team_id" in out and "season_id" in out:
        r = q("""SELECT DISTINCT e.gameId FROM events e
                 JOIN games g ON g.gameId = e.gameId
                 WHERE e.teamId = ? AND g.seasonId = ? LIMIT 8""",
              [out["team_id"], out["season_id"]])
        if r:
            out["game_ids_tuple"] = tuple(x[0] for x in r)

    # Sanity: confirm the season-scoped combination really has shots, since
    # that is exactly what the season/conceded maps assert on.
    if "team_id" in out and "season_id" in out:
        r = q(f"""SELECT count(*) FROM events e JOIN games g USING (gameId)
                  WHERE e.teamId = ? AND g.seasonId = ?
                    AND e.playType IN {SHOT_TYPES}""",
              [out["team_id"], out["season_id"]])
        out["_team_season_shots"] = r[0][0] if r else 0

    con.close()
    return out


def build_args(fn, pool):
    """Fill a signature from the sample pool by parameter name."""
    alias = {
        "shooter_name": "player_name",
        "shooter_id": "player_id",
        "game_ids": "game_ids_tuple",
        "season_ids": "season_id",
    }
    args, missing = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        key = alias.get(name, name)
        if key in pool:
            args[name] = pool[key]
        elif p.default is not inspect.Parameter.empty:
            continue                      # optional, let the default stand
        else:
            missing.append(name)
    return args, missing


def is_empty(v):
    if v is None:
        return True
    try:
        import pandas as pd
        if isinstance(v, pd.DataFrame):
            return v.empty
    except Exception:
        pass
    if isinstance(v, (list, tuple, dict, str, set)):
        return len(v) == 0
    return False


def run_module(mod, label, pool, verbose, prefixes):
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    # @st.cache_data replaces the function with a wrapper object, so
    # inspect.isfunction() on the module attribute is False for most of them.
    # Unwrap first, then test.
    resolved, skipped_undiscoverable = [], []
    for n, f in vars(mod).items():
        if not n.startswith(prefixes) or n == "get_connection":
            continue
        target = f
        for _ in range(5):                       # peel nested decorators
            nxt = getattr(target, "__wrapped__", None) or \
                  getattr(target, "func", None) or \
                  getattr(target, "_cached_func", None)
            if nxt is None or nxt is target:
                break
            target = nxt
        if not (inspect.isfunction(target) or inspect.ismethod(target)):
            if callable(f):
                skipped_undiscoverable.append(n)
            continue
        if getattr(target, "__module__", "") != mod.__name__:
            continue
        resolved.append((n, target))

    if skipped_undiscoverable:
        print(f"  (could not unwrap {len(skipped_undiscoverable)}: "
              f"{', '.join(skipped_undiscoverable[:6])})")

    tally = {"OK": 0, "EMPTY": 0, "ERROR": 0, "SKIP": 0}
    rows = []
    for name, fn in sorted(resolved):
        args, missing = build_args(fn, pool)
        if missing:
            rows.append((name, "SKIP", f"no sample for: {', '.join(missing)}"))
            tally["SKIP"] += 1
            continue
        try:
            val = fn(**args)
        except Exception as e:
            rows.append((name, "ERROR", f"{type(e).__name__}: {e}"[:110]))
            tally["ERROR"] += 1
            if verbose:
                traceback.print_exc()
            continue
        if is_empty(val):
            rows.append((name, "EMPTY", "returned nothing"))
            tally["EMPTY"] += 1
        else:
            n = len(val) if hasattr(val, "__len__") else "-"
            rows.append((name, "OK", f"{type(val).__name__}, len {n}"))
            tally["OK"] += 1

    for name, status, detail in rows:
        flag = {"OK": " ", "EMPTY": "!", "ERROR": "X", "SKIP": "."}[status]
        print(f" {flag} {name:<36} {status:<6} {detail}")
    print(f"\n   OK {tally['OK']}   EMPTY {tally['EMPTY']}   "
          f"ERROR {tally['ERROR']}   SKIP {tally['SKIP']}")
    return tally


def _production_dsn():
    """MotherDuck DSN from .streamlit/secrets.toml, for sample_ids().

    The chart modules authenticate themselves via st.secrets; this is only so
    the harness can open its own connection to pick sample ids.
    """
    import pathlib as _p
    import re as _re
    t = _p.Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"
    m = _re.search(r'MOTHERDUCK_TOKEN\s*=\s*"([^"]+)"', t.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("MOTHERDUCK_TOKEN not found in .streamlit/secrets.toml")
    return f"md:soccer?motherduck_token={m.group(1)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="path to a DuckDB file, or 'production' to run "
                               "against MotherDuck")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    # `production` runs the harness against the real database. It only ever
    # READS - every builder here is a payload builder - but it is the one
    # target where a mistake would be visible to users, so it has to be named
    # explicitly rather than reached by leaving an environment variable unset.
    if a.db in ("production", "md", "motherduck"):
        os.environ.pop("SOCCER_DB_PATH", None)
        db_arg = _production_dsn()
        print("target = PRODUCTION MotherDuck  (read-only)")
    else:
        if not os.path.exists(a.db):
            print(f"no such database: {a.db}")
            return 1
        os.environ["SOCCER_DB_PATH"] = os.path.abspath(a.db)
        db_arg = os.environ["SOCCER_DB_PATH"]
        print(f"SOCCER_DB_PATH = {db_arg}")

    pool = sample_ids(db_arg)
    print("\nsample values discovered:")
    for k, v in pool.items():
        s = str(v)
        print(f"   {k:<18} {s[:60]}{'...' if len(s) > 60 else ''}")
    if not pool:
        print("   none — is this an empty database?")
        return 1

    totals = {"OK": 0, "EMPTY": 0, "ERROR": 0, "SKIP": 0}

    try:
        from shared import motherduck as cbs
        t = run_module(cbs, "CBS chart builder — shared/motherduck.py",
                       pool, a.verbose, ("get_", "build_"))
        for k in totals:
            totals[k] += t[k]
    except Exception as e:
        print(f"\nCBS module unavailable: {type(e).__name__}: {e}")
        if a.verbose:
            traceback.print_exc()

    try:
        sys.path.insert(0, os.path.join(REPO, "PodcastShorts"))
        from pipeline import chart_data as dp
        t = run_module(dp, "DP chart builder — PodcastShorts chart_data.py",
                       pool, a.verbose, ("build_",))
        for k in totals:
            totals[k] += t[k]
    except Exception as e:
        print(f"\nDP module unavailable ({type(e).__name__}: {e})")
        print("  Expected if not running in the PodcastShorts venv "
              "(C:\\Users\\mlgpo\\venvs\\podcastshorts).")

    print(f"\n{'=' * 78}")
    print(f"TOTAL   OK {totals['OK']}   EMPTY {totals['EMPTY']}   "
          f"ERROR {totals['ERROR']}   SKIP {totals['SKIP']}")
    print("=" * 78)
    print("EMPTY is the column that matters — a chart that silently renders")
    print("nothing is worse than one that raises.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
