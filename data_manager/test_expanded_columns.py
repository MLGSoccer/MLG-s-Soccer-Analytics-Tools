"""The expanded event model has to pass THREE gates. Missing one is silent.

A column only reaches the database if it appears in all of:

    1. EVENT_LOG_SELECT      or TruMedia never sends it
    2. EVENTS_MD_COLS        or upsert_game_events filters it out of the frame
    3. the events schema      or _align_to_table has nothing to insert into

Gate 2 is the trap. It is an allowlist applied BEFORE _align_to_table, and
_align_to_table then NULL-fills anything absent - so a widened SELECT whose
columns are missing from the allowlist ingests cleanly, reports the right row
count, and stores nothing. That is exactly what happened on the first attempt
at this expansion: 1,581 rows written, all 52 new columns NULL, "INGEST OK".

All three are now derived from EXPANDED_EVENT_FIELDS so they cannot drift.
This asserts that they have not been un-derived by hand.

    py test_expanded_columns.py          (no network, no database)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb  # noqa: E402
from downloader import (EXPANDED_EVENT_FIELDS, EVENT_LOG_SELECT,  # noqa: E402
                        EVENTS_MD_COLS, _apply_schema,
                        build_game_event_statement)

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


names = [n for _e, n, _t in EXPANDED_EVENT_FIELDS]
print(f"[1] {len(names)} expanded fields declared")
check("names are unique", len(names) == len(set(names)),
      f"{len(names) - len(set(names))} duplicates")
check("no name collides with an existing stored column",
      not (set(names) & set(EVENTS_MD_COLS[:-len(names)])))

print("\n[2] gate 1 - every field is in the SELECT")
missing = [n for n in names if f" AS {n}" not in EVENT_LOG_SELECT]
check("all present in EVENT_LOG_SELECT", not missing, str(missing[:5]))

print("\n[3] gate 2 - every field survives the ingest allowlist")
missing = [n for n in names if n not in EVENTS_MD_COLS]
check("all present in EVENTS_MD_COLS", not missing, str(missing[:5]))

print("\n[4] gate 3 - every field exists in the events schema")
con = duckdb.connect(":memory:")
_apply_schema(con)
cols = {r[0] for r in con.execute("DESCRIBE events").fetchall()}
missing = [n for n in names if n not in cols]
check("all present after _apply_schema", not missing, str(missing[:5]))
check("_apply_schema is idempotent",
      (_apply_schema(con) or True) and len(
          {r[0] for r in con.execute("DESCRIBE events").fetchall()}) == len(cols))

print("\n[5] the per-game statement carries them too")
stmt = build_game_event_statement(["SEASONID"], ["GAMEID"])
missing = [n for n in names if f" AS {n}" not in stmt]
check("all present in build_game_event_statement", not missing,
      str(missing[:5]))
# The statement deliberately names NO team: naming one makes it the anchor,
# and TruMedia then answers every team-scoped column from that team's point of
# view, including on the opponent's rows. See test_no_anchor.py.
check("statement carries both side flags and names no team",
      "team.event.primary AS is_team" in stmt
      and "opponent.event.primary AS is_opp" in stmt
      and "team.teamId" not in stmt)

print("\n[6] types are ones DuckDB accepts")
bad = [t for _e, _n, t in EXPANDED_EVENT_FIELDS
       if t not in ("DOUBLE", "INTEGER", "BIGINT", "VARCHAR", "BOOLEAN")]
check("every declared type is known", not bad, str(bad))

print(f"\n{'=' * 60}\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
