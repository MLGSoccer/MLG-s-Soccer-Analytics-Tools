# Handover — 2026-08-29 (afternoon): the migration is DONE

Second session of the day. The morning's session (`HANDOFF_2026-08-29.md`)
planned the migration; this one built it, ran it, and shipped it.

**The database is fully migrated and the code is pushed to `main`.**

## Start here

0. **`EVENT_MODEL_EXPANSION.md`** — the NEXT piece of work, specified after
   the migration finished. Take the ~77 `[Abbrev|EVENT]` stat tokens, not raw
   qualifiers. Holds the four-ways-a-field-arrives finding.
1. **`MIGRATION_PLAN.md`** — the living document. Architecture, the tool spec,
   step-by-step status, request costs. Read this before touching anything.
2. `memory/project_per_game_download_architecture.md` — the query shapes and
   the traps.
3. This file — what happened today and what is left.

---

## The result

```
5,295 games   (started at 4,930 — fixture discovery added 365)
  on the new feed  5,295  (100.0%)
  half a match         0  (started at 1,116, i.e. 22.6%)
  old feed             0

52 distinct play types (was 22)

  cards           21,992   none of this existed in the database before
    sendings-off   1,078
  substitutions   95,068
  corners        101,533

  events       8,540,029
  minutes rows   164,025
```

`events` now holds **445 own goals against the API-Football table's 408** —
TruMedia has MORE, exactly as the audit predicted, because 29 of the table's
apparent advantage were own goals in matches only one side of which had ever
been downloaded.

**Production smoke test: 59 OK, 2 EMPTY, 0 ERROR.** The two empties are own
goals and red cards on Marseille v Rennes, which has 4 yellows and 31 play
types but no sending-off and no own goal — verified, not assumed.

---

## What shipped

**Pushed to `origin/main`** (`79d85cb..21f36bd`, 20 commits).

| | |
|---|---|
| **Step 1** fallback readers | events first, old tables as fallback, both products |
| **Step 2** per-game library | fixture discovery, per-game download, minutes, atomic writes |
| **Step 3** the tool | Campaign page, Health coverage, work list |
| **Step 5** re-download | **100% of the database** |

Only ONE file the Streamlit Cloud app executes changed: `shared/motherduck.py`.
Everything else is the local Data Manager, docs, or test scripts. Two chart
pages are affected — **xG Race** and **Match Momentum** — because red cards
and own goals now come from `events` with a real Period rather than one
inferred from a collapsed minute.

**If Cloud shows an ImportError after the deploy:** clear cache + reboot
first. `shared/motherduck.py` gained new symbols, which is exactly the
condition that triggers the known stale-module issue.

---

## THREE BUGS THE USER CAUGHT. All three mattered.

### 1. `old_feed` — would have silently halved the migration

`build_work_list` called a game COMPLETE when both sides were present. True
of every game already in production — but it says nothing about WHICH FEED.
WSL showed "38 complete", and those 38 were `event.toucher` games.

A production campaign would have fetched the 1,116 one-sided games, reported
itself finished, and left **3,814 games on the old feed permanently**.

Found by the user checking the work list for a league he knew. **Do that.**

### 2. "one request each" — the cost estimate was 19x wrong

The Campaign page said "N games · one request each" long after batching
landed. Fixed by deleting the second source of truth: `plan_batches()` now
does the grouping and both the runner and the page call it.

### 3. `Awarded` — three different things wearing one label

Skipping the status would have dropped a complete 94-minute match. See below.

---

## Traps worth carrying forward

**THE ANCHOR FILTERS GAMES.** The event query names a team, and a game
returns events ONLY if that team plays in it. An unrelated anchor returns
zero rows — no error, plausible row count. Batching 20 arbitrary games lost
Manchester United v Arsenal because its batch was anchored on Crystal
Palace. Only counting DISTINCT GAMES caught it. Batches are grouped by HOME
TEAM because every game has exactly one.

**Dropping the anchor does NOT fix this.** With no team predicate you get the
whole match TWICE, once per side's perspective, and `teamId` becomes the
perspective rather than the owner. Tested; rejected.

**`Awarded` covers three things and only the events tell them apart.** All
five in the database: three with 0 events (never played), one with 1,973
(played in full, then awarded), one with 567 ending at 21' (abandoned).
Included in the download set. **`Playing` is excluded** — ingesting a match in
progress would store half a game that then reads as COMPLETE and never
refreshes.

**q171 is a POST-MATCH rescission.** 760 reds measured against minutes
played: 696 settled, ALL sent off, NONE finished the match. So rescinded reds
stay on charts — the player did walk and the team did play a man down. And
there is no class of red where the player stayed on, so an in-match VAR
overturn never reaches the data as a red card event.

**`game.home` / `game.away` are BOOLEAN side flags, not team names.** Names
come from `team.game.fullName` / `opponent.game.fullName`.

**Minutes are fetchable per game, both sides, no team predicate** — verified
identical to the per-team pull, max diff 0.

---

## What is left

| # | Step | State |
|---|---|---|
| 4 | **Team registry** | not started — the one with design in it |
| 6 | **Delete API-Football** | unblocked, pure deletion now |
| — | **Expand the event model** | specified in `EVENT_MODEL_EXPANSION.md`, not built |
| 7 | CBS colour flip | needs 4 |
| — | Retire Bulk Actions + per-team timestamps | deliberately left as the fallback |
| — | Structural hash pull | designed, not built |

**Step 6** is ~450 lines, `TRUMEDIA_TO_API_NAME`'s 186 lines of name-bridging,
and four tables. The step-1 fallback readers already answer from `events`
everywhere, so removing the tables is a deletion rather than a migration.

**Step 4 matters more than it looks.** A new club arriving via per-game ingest
has complete data and **cannot be selected in CBS** — `get_teams_by_league()`
is "built entirely from config.json (no DB query)" and the Campaign page never
writes config.json. DP is fine; `list_teams_for_season` reads `games`.
Until step 4, run **Discover Teams** after a Campaign if a season introduced
clubs that are not already in config.json.

**The structural hash pull** is the only thing that can answer "is my data
still RIGHT" rather than "is it THERE". A game whose source data was revised
reads as `complete`. Deliberately not faked in the work list.

---

## Notes

- **MotherDuck daily compute limit was hit** by the migration. Resets daily.
- **Storage stats are stale** — `md_information_schema.storage_info` reported
  1.78 GB active + 849 MB historical + 222 MB failsafe, but `computed_ts` was
  14:11, before the migration finished. The 849 MB historical is 7-day time
  travel on the overwritten rows and will age out around 5 September. Re-check
  then for the real steady-state figure. **Never use
  `duckdb_tables().estimated_size`** — frozen statistic, ignores INSERT and
  DELETE, a whole finding was once built on it and retracted.
- `data_manager/config.json` shows as modified and predates all of this.
  Left alone throughout.
- PodcastShorts is its own repo with **no remote** — committed, never pushed.

## The routine from here

Campaign is now the incremental mechanism. Run it on active seasons whenever
you want to catch up: `missing` = fixtures played since last run,
`not_played` = upcoming, `complete` = already have it. No dates, no
timestamps, no "which teams did I do" — the database is the progress record.
