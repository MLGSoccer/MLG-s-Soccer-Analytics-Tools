# Migration master plan

One document for the whole thing: what changes in the **database**, the
**download tool** and the **charts**, in what order, and why that order.

Written 2026-08-29. Supersedes the ordering sections of
`HANDOFF_2026-08-29.md`, which stays as the record of *why* each decision was
taken.

---

## The goal, in one line

Move from a **per-team** download filtered to `event.toucher` (22 play types,
API-Football patching the holes) to a **per-game** download on `event.primary`
(47 play types, TruMedia as the only source).

---

# THE ARCHITECTURE

## What changes, structurally

|  | today | new |
|---|---|---|
| **driver** | hand-maintained 455-team list in `config.json` | the season list. Fixtures are discovered |
| **unit of work** | one team-season | one **game** |
| **requests** | 2 per team-season (events, then minutes+cards) | 1 per game |
| **event filter** | `AND ((event.toucher))` → 22 play types | none → 47 play types |
| **who each event belongs to** | assumed: the anchor team | read from the row itself |
| **write scope** | `DELETE WHERE (gameId, teamId)` | `DELETE WHERE gameId` |
| **provenance** | a match can be half-ingested and look complete | a match is in or out |
| **missing data** | patched from API-Football by name+date matching | none needed |

## How it runs

**Step 1 — discover the fixtures.** Once per season, no team involved:

```sql
SELECT game.gameId, game.gameDate, game.home, game.away, game.status,
       game.week, game.venueName, game.attendance,
       game.gameMainMatchOfficialName
FROM team BY game
WHERE ((season.seasonId IN ('<season>')))
```

Returns **two rows per game**, one per side, distinguished by the `home` /
`away` booleans — so this hands you the fixture list *and* both teamIds for
every match. `config.json`'s team list stops being the driver; adding a league
needs no team enumeration.

> `FROM season BY game` and `FROM game BY game` both return **HTTP 400**. The
> `FROM team BY <grain>` shape is required even when no team is named.

**Step 2 — one request per game.** Scoped to the game, with **no event
predicate and NO TEAM PREDICATE** — see A4 below. Naming a team makes it the
anchor and corrupts every team-scoped column on the opponent's rows:

```sql
SELECT game.gameId, event.gameEventIndex, event.playType,
       event.period, event.gameClock,
       lookup(event.primary, abbrevName) AS primaryPlayer,
       event.primaryPlayerId,
       team.event.primary      AS is_team,      -- RAW boolean
       opponent.event.primary  AS is_opp,       -- RAW boolean
       event.q31, event.q32, event.q33, event.q171,
       ...
FROM team BY event
WHERE ((season.seasonId IN ('<season>')))
  AND (game.gameId IN ('<gameId>'))
ORDER BY event.gameEventIndex ASC
```

With no team named, each event returns ONCE PER SIDE - about 2.5x the rows -
each copy carrying that side's own values. Keep the `is_team` copies.

Dropping `AND ((event.toucher))` is what widens 22 play types to 47 — cards,
substitutions, corners, ball recoveries, aerials.

**Step 3 — keep the `is_team` rows.** Each is the copy written from its own
team's point of view, so `teamId` and every other team-scoped column is
already correct and needs no reconstruction. The first version of this
rebuilt `teamId` and `teamFullName` from the fixture and left 21 other
columns describing the anchor — that was A4.

**Step 4 — write atomically per match.** `DELETE FROM events WHERE gameId = ?`
then INSERT. A match is ingested or it isn't; there is no half state to
describe.

## The trap that must not be re-discovered

`lookup(team.event.primary, abbrevName)` and
`lookup(opponent.event.primary, abbrevName)` return **identical values on
every row**. `lookup()` resolves "the actor of this event" and discards the
namespace prefix. It looks like it works and does not.

**The namespaces only discriminate as RAW booleans.** Select predicate fields
raw to test membership; use `lookup()` only to resolve an actor to a name.

## What this buys

- **Fewer requests than the per-team path** — 538 against 904, once batching
  stopped being grouped by home team (which only existed to satisfy the
  anchor). NOT "halves the load": that framing assumed the anchored shape.
- **Kills the 22.6% half-matches** — though see A4: those were a COVERAGE gap,
  not a defect in the per-team model.
- **Cards, own goals and subs arrive as events**, with `playerId` and the
  correct side — so API-Football goes.
- **Match metadata currently thrown away**: referee crew, venue, attendance,
  `p1Start`/`p1End`/`p2Start`/`p2End`, `matchLength`, announced injury time,
  neutral site, stage, week, captain. Period boundaries matter — xG race and
  momentum currently *infer* them from `gameClock`.
- **`game.starter` / `game.gameStarted` exist**, so starter-vs-sub comes free.

## How refresh works afterwards

**Not `lastModified`.** Measured useless: a season that ended in May had 325
of its 380 games "modified" in August, median lag 224 days. It tracks
TruMedia's own pipeline touches, not data changes.

**A structural hash pull instead.** Pull every event with a minimal column set
and hash per game:

```sql
SELECT game.optaMatchId,        -- INTEGER; gameId is 25 chars and dominates
       event.gameEventIndex, event.gameClock, event.playType, [xG|EVENT]
```

Measured on Chelsea, 34,357 events: **1.21 MB against 15.9 MB — 7.6% of a full
download**, ~20 minutes for the whole database. Hash per game, compare to the
same hash computed locally, re-download only what moved.

It detects every insertion, deletion and retiming with certainty — the class
that shifts `gameEventIndex` and makes a backfill misattribute. It does not
catch an in-place coordinate nudge, which is harmless by construction. **So:
stale values remain possible, silently wrong ones do not.**

## THE TOOL, SPECIFIED — what actually gets built

Not a second tool. **The Data Manager, with its download engine replaced.**
You end up with one app, at the same place, doing the job differently.

### What it is for

Keep the MotherDuck database **complete and current** for the seasons you care
about. "Complete" is a new idea in this tool: today it tracks *when a team was
last fetched*; it has never been able to answer *is this match whole?* — which
is why 1,116 games are sitting there half-ingested without anything flagging
it.

### The core loop

```
1  AUTHENTICATE      paste a cURL                        (unchanged)
2  SCOPE             pick seasons, or "everything"
3  ENUMERATE         1 request per season -> every fixture,
                     both teamIds, status, date
4  COMPARE           fixtures vs what the database holds
                        -> THE WORK LIST
5  REVIEW            counts shown BEFORE anything downloads
6  RUN               1 request per game
                     DELETE WHERE gameId -> INSERT, atomically
                     progress recorded per game
7  REPORT            downloaded / failed / still outstanding
```

### The work list — the heart of it

Step 4 is what the tool does not have today. For every fixture in scope it
classifies:

| classification | meaning | action |
|---|---|---|
| **missing** | no events for this gameId | download |
| **one-sided** | only one team's events present | download — *this is the 1,116* |
| **stale** | structural hash differs from source | download |
| **not played** | fixture exists, no result yet | skip |
| **complete** | both sides present, hash matches | skip |

Today's equivalent is "incremental since this team's last game date," which
cannot see a one-sided match at all — every team looks up to date because each
was fetched, just never together.

Staleness comes from the **structural hash pull** (id + timecode per event,
7.6% of a full download), *not* from `game.lastModified`, which is measurably
useless.

### Resumability

Progress is keyed by **gameId**, not by team. Close the browser mid-campaign,
reopen, and the work list recomputes — the games already written are complete
and simply drop out of it. There is no separate "resume" state to corrupt,
because the database *is* the progress record.

This is what the queued pause/resume tracker was for, and it falls out of the
design rather than being bolted on.

### The screens when it is done

| screen | does |
|---|---|
| **Home** | auth · scope · the work list · Run · results |
| **Health** | coverage per season: complete / one-sided / missing / stale / not played |
| **Targeted Refresh** | one game, on demand — the same primitive Home runs in bulk |
| **Teams** | the registry: name, TruMedia colour, override, alternate, provenance |
| **Add Season** | season id + league. No API-Football wiring |

### What is genuinely new versus what is being moved

**New:** the work list and its completeness check; per-game progress; hash-based
staleness; the teams registry.

**Moved, not written:** the per-game download-and-write itself already exists
as `refresh_game()`. Bulk Actions currently loops teams calling the per-team
path; afterwards it loops the work list calling the per-game path. The
authentication, retry, cookie-parsing, type-coercion and Supabase code is
untouched.

**Deleted:** every API-Football call site, the fixture-matching by name and
date, `TRUMEDIA_TO_API_NAME`, the Add Season league lookup, and the per-team
last-updated timestamps.

---

## The Data Manager, file by file

Everything above is the library (`downloader.py`, 1,905 lines). The app you
actually use is **~2,380 lines of Streamlit** across five files, and the plan
is incomplete without it.

**The good news, and it is significant: the per-game model already exists in
this codebase.** `pages/2_Targeted_Refresh.py:refresh_game()` downloads both
teams for one gameId, wipes the whole game, and inserts both sides — in three
phases, explicitly so the game is never left half-empty. Its own comment says
*"the wide DELETE is necessary"*.

So A1 is not inventing a pattern. It is **promoting `refresh_game` from a
repair tool to the main ingest path**, and collapsing its two requests into
one.

| surface | today | after |
|---|---|---|
| **Authentication** (`app.py`) | paste a cURL | **unchanged** |
| **Player Pools** (`app.py`) | Supabase pools | **unchanged** — separate deferred workstream |
| **Bulk Actions** (`app.py`) | `_run_downloads(teams, season, mode=incremental/full/range)` — loops **teams × seasons** | **REBUILT.** Loops **fixtures**. Becomes the campaign runner: enumerate a season's games, download what is missing or stale, resumable |
| **Targeted Refresh** (`pages/2`) | per-game repair; 2 requests; phase 3 re-calls API-Football | **PROMOTED to the primitive.** 1 request; phase 3 deleted. Reads of `game_fixtures`/`player_minutes`/`own_goals`/`cards` go with it |
| **Health** (`pages/1`) | freshness per **team**, season wiring | **REWORKED** around per-**game** coverage. This is where the 22.6% half-match number should have been visible all along |
| **Discover Teams** (`pages/3`) | scans seasons for teams missing from `config.json` | **REPLACED.** Teams arrive as an ingest by-product, so the page stops being a scanner and becomes the **team registry UI** — this is where Track B1 lives |
| **Add Season** (`pages/4`) | season id, league, + API-Football league lookup | **HALVED.** The API-Football league lookup and `SEASON_TO_API_LEAGUE` wiring are deleted |

### State that has to change with it

`app.py` tracks progress in `load_last_updated()` / `load_minutes_last_updated()`,
**keyed by team**. Under per-game ingest that key is wrong. It becomes
per-game coverage — which is also what makes a campaign resumable, and what
lets Health answer "which games are missing?" as a query rather than a guess.

The **pause/resume campaign tracker** noted as queued in memory belongs here,
in the Bulk Actions rebuild, not as a later addition.

### What this means for effort

The library change is the smaller half. Roughly:

- `downloader.py` — ~400 lines change grain, ~450 delete with API-Football
- `app.py` Bulk Actions + progress state — the biggest single rewrite
- `pages/2` — mostly deletion; it is already the right shape
- `pages/1` — rework around a different question
- `pages/3` — becomes a different page entirely (Track B)
- `pages/4` — deletion

## Proven vs still open

**Proven live** — `data_manager/probe_per_game_architecture.py`

- Fixture enumeration with no team predicate: all 10 games of PL 26/27 week 1.
- One request attributing both sides: Brighton v Aston Villa, 1,951 rows —
  `is_team` true on 567 (Villa's exact count), `is_opp` on 991 (Brighton's
  exact), 393 neither. Cards split 4/2, correct.
- `event.primary` a strict superset of `event.toucher`: 3 PL teams, full
  season, **0 lost, +29.7% gained, 47 types against 22**.
- Structural hash cost, above.

**Open — decide during A1**

1. **The 393 "neither" rows** are `Sequence` / `Possession` aggregates. Store
   them or drop them? They are absent from `events` today.
2. **Does the minutes/cards call stay per-team-season?** It is a second
   request today (`FROM team BY game`, `MINUTES_SELECT`) feeding
   `player_game_minutes`. Per-game ingest does not obviously require moving
   it, but leaving it per-team keeps one per-team code path alive.
3. **Re-ingest is still a full re-download.** Per-game does not avoid that —
   sequence-embedded new types (`BallRecovery`, `Pickup`, `Aerial`) change
   sequence composition. What it buys is that the re-ingest becomes
   incremental, resumable and auditable rather than a big bang.

---

## Where we are

**Done and committed**

| | |
|---|---|
| Chart fixes | 7 of them — play-type pins, teamId-not-name, one ranking rule |
| Downloader repairs | column-named inserts, practice mode (`DATA_MANAGER_LOCAL_DB`) |
| Discovery | per-game download proven live; `event.primary` proven a strict superset |
| Decoupling | 25/26 snapshotted to the local mirror, so the sequence model is off the critical path |

**Already changed in production, partially**

- `events` is **63 columns** — the ALTERs ran when the Data Manager last
  connected.
- **1,299 events already carry card qualifiers.** Teams downloaded since the
  columns landed have them; everything else is NULL. The database is already
  in a mixed state, which is the thing the re-download resolves.
- **1,116 of 4,930 games (22.6%) hold only ONE side's events.** Not a
  migration side-effect — it is true right now, and every chart reading those
  games is reading half a match.

---

## The dependency picture

Two tracks. They only meet at the end.

```
TRACK A — DATA                          TRACK B — IDENTITY
                                        (independent until the very end)
 A0  fallback readers ......... prep     B1  team registry + overrides
      |                                      |
 A1  per-game download rework            B2  CBS switches to TruMedia-first
      |                                      |
 A2  re-download everything  [DONE]      B3  rebuild alternates  [DEFERRED]
      |                                          ^
 A3  delete API-Football     [DONE]              |
      |                                          |
      +------------------------------------------+
              (B3 wants A2 done first)
```

**The one thing that unblocks everything: A0.** Write the chart readers to
take cards and own goals from `events` first and fall back to the old tables
when `events` has nothing. Charts then work before, during and after the
migration, and A1/A2 stop being a synchronised big-bang.

---

## TRACK A — the data

### A0. Fallback readers — **DONE 2026-08-29** · *charts only · no risk*

Committed, not pushed. CBS `4a97b5d` + `fd8f998`, DP `8c2decd` + `d41cc29`.
Seven call sites across both products now go through shared helpers.
`test_fallback_readers.py` covers all three states including the post-A3
world where the tables are gone. Own goals read from events today; cards
fall back until the predicate swap.

Two things verified on live data while doing it, both worth carrying into
step A1a:

- **`game.home` / `game.away` are BOOLEAN side flags, not team names.** The
  fixture-discovery query above must take names from `team.game.fullName` /
  `opponent.game.fullName`.
- **`((event.q33) OR (event.q32))` and `((event.q171))` work as WHERE
  predicates**, so targeted scans cost a few KB rather than full seasons.

And one question closed: a red card in the data ALWAYS means the player left
the pitch — 760 measured, 696 settled against minutes played, 0 played to
the end. Rescinded reds are included, deliberately.

*(original spec below)*

Six call sites read `own_goals.credited_team`; one reads `cards`. Rewrite
each to read `events` first, fall back to the old table.

- **Own goals work immediately** — `OwnGoal` playType is already there, with
  `teamId` and `gameClock` complete on every row.
- **Cards mostly won't yet** — the qualifier columns are NULL except for
  those 1,299 rows. They start returning as A2 progresses. That is what
  "fallback" means; nothing breaks in the meantime.

**Read `teamId`, not `credited_team`.** TruMedia's `OwnGoal` event carries the
conceding side's id — verified 329 same / 0 opposite. Keeping the name would
carry forward exactly the bug class fixed on 2026-08-29.

*Touches:* `shared/motherduck.py`, `PodcastShorts/pipeline/chart_data.py`
*Verify:* `smoke_chart_data.py`, plus own-goal counts unchanged per game
*Undo:* git revert — charts only, no data written

### A1. Per-game download rework · *tool only · branch*

On `per-game-ingest`, with `DATA_MANAGER_LOCAL_DB` set the whole time.

1. **Fixture discovery** — `FROM team BY game` with no team predicate. Returns
   two rows per game, so both teamIds come free.
2. **DELETE grain** `(gameId, teamId)` → `(gameId)`. This is what kills the
   half-match problem.
3. **Attribute both sides from one request** — select
   `team.event.primary` / `opponent.event.primary` as RAW booleans. They do
   not discriminate through `lookup()`.
4. **Swap** `event.toucher` → `event.primary`.
5. **`teams` table** as an ingest by-product — feeds B1.

*Needs:* a fresh TruMedia cURL (~4h lifetime — capture when ready to run)
*Verify:* download one season locally; `smoke_chart_data.py` against it;
`build_local_fullfeed.py` A/B against production
*Undo:* delete the branch. Production is never written to.

### A2. Re-download everything — **DONE 2026-08-29** · *database*

**Every season, 100%.** Run by the user through the Campaign page, season by
season, checking the work list before each.

```
5,295 games   (started at 4,930 - fixture discovery added 365)
  on the new feed  5,295  (100.0%)
  half a match         0  (started at 1,116)
  old feed             0

52 distinct play types (was 22)
  cards           21,992     none of this existed in the database before
    sendings-off   1,078
  substitutions   95,068
  corners        101,533

  events       8,540,029
  minutes rows   164,025
```

A confirmation worth keeping: `events` now holds **445 own goals against the
API-Football table's 408**. TruMedia has MORE, which is what the audit
predicted - 29 of the table's apparent advantage were own goals sitting in
matches only one side of which had ever been downloaded.

Note: rewriting 8.5M rows exhausted the MotherDuck daily compute allowance.
Expected for a one-off; worth planning around if a second full pass is ever
needed.

**GATE PASSED 2026-08-29.** A full Premier League 25/26 season was ingested
per-game into a practice file and smoke-tested:

```
380 games · 20 batches · 41 requests · 2.2 minutes
621,984 events · 11,492 minute rows · 48 play types · 0 half-written

smoke_chart_data.py:  OK 58   EMPTY 3   ERROR 0   SKIP 9
```

Zero errors across every CBS and DP entry point on the wider feed. Better
than the baseline: `build_stat_poster_payload`, `build_v_compare_payload`
and `build_v8_compare_payload` ERROR against the local mirror and pass here,
because the per-game download carries `qualifierBlocked`.

The 3 EMPTY are goal scorers, own goals and red cards on a match the harness
sampled that finished 0-0 with no sending-off. Verified rather than assumed:
the same functions return correctly on a game that has them.

Request cost is at parity with the path being replaced - 41 against ~40 for
a 20-team season - because batches are grouped by home team. One discovery
call per SEASON, then one events and one minutes call per home team.

**AND the hostile shape, UEFA Champions League 25/26** - the most
structurally different competition in the database, chosen precisely because
a 20-team domestic league is the tidiest case and proving only that would
have proved little:

```
226 games · 56 clubs · 9 stages · 1 neutral site · 56 batches · 113 requests
373,586 events · 6,987 minute rows · 49 play types · 0 half-written · 1.8 min
minutes/game 1,870-2,640   <- 2,640 = 120 min x 22, extra time, unhandled and correct

smoke_chart_data.py:  OK 55  EMPTY 2  ERROR 4  SKIP 9
```

The 4 errors are all `player_id not found in pool 'europe'` - the Supabase
player pools, fetched from `storage/v1/object/player-pools/*.csv` and
entirely outside the database. Per-game ingest cannot affect them and the
same error occurs today. That is the deferred Supabase workstream, already
recorded as outside this migration's coverage. **Zero migration-caused
errors.**

Every season in config was also run through the WORK LIST (read-only, one
request each): all 26 discovered cleanly, no failures. Full re-download is
~1,250 requests, ~45 minutes.

## Recommended production sequence

1. A small season first - WSL (132) or Frauen-Bundesliga (182). First write
   to production, cheapest possible.
2. Check Health: feed vintage should jump from 0 to that season's count, and
   "half a match" should fall.
3. Big-5, one season per run.
4. Cups and the rest.

Resumable throughout, so stopping between seasons costs nothing.

Per-game, resumable, auditable. Roughly 900 requests.

Fixes, as a by-product: the 22.6% half-matches, the 1,299-row mixed card
state, and the one game with NULL `qualifierBlocked` that breaks
`stat_poster` / `v_compare` / `v8_compare` today.

*Verify:* every game has both sides; card qualifiers populated everywhere;
`smoke_chart_data.py` green
*Undo:* **none — this is the irreversible step.** Everything before it is
practice. Do not start until A0 and A1 are both signed off.

### A3. Delete API-Football — **DONE 2026-09-01** · *tool + database*

**813 lines** across `downloader.py`, `app.py` and pages 1/2/4.
`TRUMEDIA_TO_API_NAME`'s 186 lines of name bridging, `SEASON_TO_API_LEAGUE`,
the four DDLs, `_apifootball_get`, `search_api_football_leagues`,
`fetch_fixture_id`, `compute_player_minutes`, `fetch_and_store_fixture_data`,
`get_games_missing_fixture_data`, Targeted Refresh's Phase 3, Bulk Actions'
enrichment pass, and Add Season's league-id widget.

**Re-verified against production before cutting**, not taken from the
2026-08-29 audit — that audit predated both the per-game re-download and the
column expansion, so its numbers were about a different database:

```
             legacy rows/games      events rows/games   ONLY legacy   ONLY events
own goals      408 / 394              454 / 438              2             46
red cards      916 / 793            1,087 / 929              0            136
```

**`ONLY legacy` is the only number that can cost anything** — a game where an
annotation disappears. Both are provider artefacts the audit had already
identified: Leverkusen v PSV is credited to "Bayer Leverkusen U19", a youth
fixture matched to a senior gameId, so dropping it is a *fix*; West Brom v
Wrexham is a genuine disagreement where TruMedia has I. Price scoring at 26'
and API-Football has a Wrexham own goal — same scoreline, different
attribution.

**Dropped: `game_fixtures`, `own_goals`, `cards`.**
**KEPT: `player_minutes`** — twelve `One_Offs/` scripts (sequence_value_model
v1-v4, backward_xg, marginal_zone, seq_nn_*) read it from MotherDuck by name.
Superseded by `player_game_minutes`, but superseded is a claim about what
should run, not a guarantee. Nothing recreates it now the DDL is gone, so it
is frozen rather than maintained.

All four are archived to `event_db/apifootball_archive.duckdb` — 178,018 rows,
2.9 MB, row counts AND column shapes verified against the live tables before
the DROP ran.

**`CALENDAR_YEAR_LEAGUES` survived, re-keyed.** It drives Health's "in-season
league gone quiet" finding and was only keyed by API-Football league id
because that is where it was first needed. Whether MLS runs Feb-Nov is a
property of MLS. It now lives in `pages/1` keyed by league name.

`betting_dashboard/` has its own self-contained API-Football client and does
not import from `downloader`. Untouched; the key stays in `secrets.env`.

*Verified:* `test_practice_mode` 13 pass (and now asserts the four tables are
NOT created), `test_expanded_columns` 9, `test_fallback_readers` 19 —
including the case where the fallback table does not exist. Post-drop, the
live readers were called against production: both answer from `events`, with
real teamIds and playerIds.

---

## TRACK B — identity and colour

Runs in parallel. Nothing here blocks Track A or vice versa, until B3.

### B1. Team registry

**A NEW CLUB IS NOT REACHABLE UNTIL THIS EXISTS.** Confirmed 2026-08-29 while
the migration was running, because `missing` fixtures will start surfacing
clubs the old team-list download never asked for.

What already works for a club that arrives via per-game ingest:

| | |
|---|---|
| `events` rows with teamId, teamFullName, newestTeamColor | works |
| `games` rows with both team ids | works |
| minutes, cards, substitutions | works |
| **DP team picker** | works - `list_teams_for_season` reads `games` |
| colour | works - falls back to TruMedia's newestTeamColor |

What does not:

- **The CBS team picker.** `get_teams_by_league()` is, in its own words,
  "built entirely from config.json (no DB query)". The Campaign page never
  writes config.json - that was Discover Teams' job, and it is driven by
  player pools rather than fixtures. So the club has complete data and no way
  to be selected.
- **A new season** needs a `season_leagues` entry or it lands in "Other".
- **Alternate colour** - a new club has none, so a clash has nothing to swap
  to.

This is the same blind spot recorded during testing: every test reads the
DATABASE, so anything a chart takes from config.json passes every test while
being entirely unexercised.

Nothing breaks mid-migration - every club currently in the data is already in
config.json. It bites the first time a genuinely new club appears.

```
teamId  |  name  |  primary_override  |  alternate  |  provenance
```

Both products read it; the alternate dicts are a straight copy today
(149 shared keys, 0 conflicts), so this removes a duplicate rather than adding
a system. **The resolvers stay per-product** — CBS works against `#1A2332`,
DP against `#0D1117` with furniture guards. Registry owns the data; each
renderer owns its decision.

`primary_override` is empty by default. It exists because TruMedia is wrong
for some clubs (Real Madrid) and gives pure `#000000` to 8 more, which is
invisible on both backgrounds. **Do not seed it from the palette** — that
re-implements palette-first and would override Aston Villa away from its
correct claret.

*Can start now* — seed from existing `events` (teamId, name, colour). A1's
`teams` table refines it rather than enabling it.

### B2. CBS switches to TruMedia-colour-first

**Hard constraint: B1 first.** ~38 clubs go visibly wrong otherwise, with no
way to correct them.

### B3. Rebuild alternates · **DEFERRED** (user, 2026-08-29)

Wants A2 done. Scoping idea not yet explored: an alternate is only needed
where two clubs actually appear together AND clash — computable from real
fixtures, likely turning "author 204" into a few dozen.

Settled and **not to be re-proposed**: TruMedia cannot serve as the backup
colour. 19 clubs are byte-identical to the palette, 80% of pairs fall inside
CBS's own clash threshold, and 61% of clubs have no palette entry at all.
Alternates are irreducibly authored.

---

## The order, flattened

| # | Step | Touches | Reversible | Status |
|---|---|---|---|---|
| 1 | ~~A0 fallback readers~~ | charts | yes | **DONE** 2026-08-29 |
| 2 | ~~A1a per-game **library**~~ | tool | yes | **DONE** 2026-08-29 |
| 3 | ~~A1b per-game **app**~~ (Campaign) | tool | yes | **DONE** 2026-08-29 |
| 5 | ~~**A2 re-download**~~ | **database** | — | **DONE 2026-08-29**, redone 09-02 |
| 6 | ~~A3 delete API-Football~~ | tool + db | archived | **DONE** 2026-09-01 |
| — | ~~**A4 the ANCHOR BUG**~~ | tool + db | — | **DONE 2026-09-02** — see below |
| 4 | B1 team registry | tool + charts | yes | **NEXT** — smaller than it was |
| 7 | B2 CBS colour flip | charts | yes | needs 4 |
| 8 | B3 alternates | data | deferred | deferred |

---

## A4. The anchor bug — DONE 2026-09-02, and it was serious

A1a shipped a per-game query that still NAMED A TEAM, inherited from the
per-team download:

    FROM team BY event WHERE ((team.teamId = 'X')) AND game.gameId IN (...)

Naming a team makes it the ANCHOR, and TruMedia answers every team-scoped
column from that team's point of view — **on the opponent's rows too**.
Batches were grouped by home team, so every away side wore the home side's
identity. **21 columns on 4,276,373 rows**, live 08-29 to 09-01:

    teamAbbrevName wrong on 49.3% of ALL rows
    MatchState inverted on 2,275,864 away rows
    every away team in the database showed ZERO assists (5,624 home, 0 away,
      against 6,462 away goals actually scored)

**The fix was deleting the predicate.** With no team named TruMedia returns
each event once per side, each copy carrying that side's own values; keep the
`is_team` copies. Shipped `5162101`, whole database re-downloaded, 0 games
still anchored.

Three consequences for this plan:

- **Batching by home team is gone.** It existed only because the anchor
  filtered. Any games can now share a request, and that turned out to be
  cheaper: **538 requests against 904**, because a club has at most ~25 home
  games so batches were capped there.
- **The 22.6% half-match justification was overstated.** Those were a
  COVERAGE gap — 29 of 29 examined had the missing side be a team never
  downloaded — not a defect in the per-team model, which was correct on all
  21 columns. Per-game's real win is atomic writes and fixture-driven work
  lists, not correctness.
- **`WORK_ANCHORED`** detects the damage — a two-sided game with one distinct
  `teamAbbrevName` — so the repair was self-tracking and resumable.

Full detail in `memory/project_anchor_bug.md`. `data_manager/test_no_anchor.py`
asserts the cheap version of the check that would have caught it.

---

## OPEN DATA WORK, as of 2026-09-03

Nothing below is started.

**Next up**

| | |
|---|---|
| **B1 team registry** | Smaller than originally scoped: colour and abbreviation now arrive CORRECT on the event rows, so the registry is for OVERRIDES and ALTERNATES, not correctness. Still blocks B2. |
| **3 clubs invisible in CBS** | Elversberg, Paderborn, Schalke 04 have complete data and cannot be selected — `get_teams_by_league()` is built from config.json's `teams`, which only Discover Teams writes, and that scans PLAYER POOLS rather than fixtures. B1 fixes it. |
| **89 clubs have no TruMedia colour** | Only 24 are covered by the CBS palette; the rest would fall to grey. A decision, not a bug. NB the earlier figure of "6 clubs" was wrong — it was measured with MAX over all rows, which picked up opponents' colours from away games. |

**Known-imperfect, deliberately left**

| | |
|---|---|
| `check_color_similarity` | Still measures RGB distance, and still runs on the RAW colours BEFORE lightening, so it never sees what is drawn. Its remedy is swapping to an alternate, and 77% of alternates are pure white or black. Revisit with B1. |
| 4 clubs with no home game | Colour is underivable for them by construction; transient, resolves when they host. |
| `ChanceCreated` semantics | The catalogue defines it as "Key Passes (Not Including Assists)", so a chance created is `ChanceCreated OR IsAssist`. The column name misleads. |
| xG definitional question | `xG` is the display-rounded token (100 distinct values); `xGRaw`/`xGRebound` are full floats and TruMedia's own team xG is the rebound-adjusted sum. Both stored, nothing changed. **Do not change silently.** |

**Sized but not built**

| | |
|---|---|
| Batch size | 40 games is PROVEN (70,974 rows exact, all gameIds present) but produced a 182,618-row response against `LIMIT 200000` — 91%. Default is 20. `LIMIT 200000` is OUR number with no provenance; if it can be raised, requests roughly halve again. Two requests would settle it. |
| Server-side `is_team` filter | The no-anchor response carries ~2.5x rows because each event returns once per side and we discard half. Filtering server-side would halve the payload — but adding a predicate is exactly what caused A4, so it needs the full "exact row sums, no gameId lost" check. |
| Structural hash pull | Designed, not built. Staleness detection without `lastModified`, which is measurably useless. |
| Retire Bulk Actions | Plus the per-team timestamps. Campaign has been used in anger now. |

**Operational limits**

- **MotherDuck Lite is 30 min compute/day.** Reset mechanism undocumented.
  Diagnostics belong on a local snapshot, not production.
- Cookies last ~4h; a full re-download is longer, so plan a refresh mid-run.
- MotherDuck internal errors happen. Resumability handled it; a failed
  ROLLBACK used to mask the real error and no longer does (`6be8127`).

A1a and A1b are split deliberately: the library can be finished and proven
against a local file before any UI is touched, using `refresh_game` as the
existing worked example of the target shape.

---

## NEXT: capture the Opta qualifiers (sized 2026-08-29)

Widening `event.toucher` -> `event.primary` changed WHICH EVENTS arrive. It
did not change HOW MUCH IS KNOWN about each one. Those are independent axes
and only the first has been widened.

**Qualifiers are opt-in, one named column at a time.** Today we take nine:
q6/q107/q124 folded into `PassType`, q82 as `qualifierBlocked`, and
q31/q32/q33/q171 as the card columns. Everything else Opta tags an event with
is simply not selected - a Corner arrives with no delivery type, a
substitution with no reason, a shot with no body part.

### Sized, from the catalogue

`dp-proxy-show-stats-custom?showTransforms=true&showEquations=true` returns
**10,511 stats** with their equations. Referenced across them:

```
79 distinct qualifiers    77 flags, 2 that only look value-bearing (q9, q10)
```

**THAT NUMBER IS WRONG, and the method was answering the wrong question.**
The catalogue tells you which qualifiers TruMedia built STATS on top of. It
says nothing about what the raw Opta feed carries. The list itself contains
q322 and q488, which gives it away.

Probed empirically instead - select q1..q500 and see which ever come back
POPULATED. Acceptance proves nothing: all 500 were accepted, because a
non-existent field is accepted and returns NULL forever
(`event.optaEventId` is the standing example).

```
catalogue (stat library)      79
one game                     124
ONE TEAM'S FULL SEASON       227   <- the real floor, 79,933 events
whole database          probably 250-300
```

### Typed, because the cost depends on it

```
220  booleans
  6  numerics
  1  categorical   q56, 4 values: Back / Right / Left / Center
```

220 x ~2.1 MB + 6 x ~20 MB + 1 dictionary-encoded ~= **0.58 GB**. The
boolean-based estimate held because only 7 of 227 are anything else.

`data_manager/opta_qualifiers_typed.json` has type, non-null count and
cardinality for each.

**DECISION: take all 227.** 0.6 GB buys never having to predict which
qualifier you will want. The alternative is choosing a subset now and paying
a **full re-download — 904 requests** — every time the guess is wrong. (This
line used to say "7.6% of a full download". That was a bytes figure being
read as a cost; see the backfill section below.)

**SUPERSEDED 2026-08-31 on the column set**, though not on the principle:
the expansion that shipped takes **52 columns** chosen from a measured probe
of the stat-token AND named-field namespaces, not 227 raw qualifiers. Raw
qualifier flags are still reachable as `event.qNNN` if wanted later. See
`EVENT_MODEL_EXPANSION.md`.

**227 is still a floor** - one team, one competition. Competition-specific and
rare qualifiers will not have appeared. `data_manager/opta_qualifiers_populated.txt`
has the 227 with occurrence counts.

Storage measured by building boolean columns into a copy of a real season at
varying densities:

```
TRUE on  1.7%   0.7 MB/col across the whole database
TRUE on  5.0%   1.0 MB/col
TRUE on 20.0%   2.1 MB/col
TRUE on 58.0%   1.2 MB/col

ALL 227 QUALIFIERS ~0.58 GB   (~20% growth, 6% of the allowance)
```

**Density barely matters at this width.** 8.5M rows x 79 booleans is 671 MB
uncompressed and columnar compression takes that to a fraction. The intuition
that dense qualifiers are expensive is wrong here - a boolean column is a
boolean column.

### Decision: take all of them

Selective depth per league was considered and rejected. It would save ~100 MB
and **reintroduce the vintage-invisibility trap** - a game would read as
`complete`, be complete, and quietly lack the qualifiers a chart needs. That
is the same shape as the `old_feed` bug, for a trivial saving.

### How — SETTLED 2026-08-31: a full re-download, not a narrow pull

The narrow-pull plan below was **rejected on measurement.** It priced the
backfill in BYTES, and the binding cost is REQUESTS - a request costs the
same whether it selects 5 columns or 120. Every game has to be asked for
again either way, so the narrow pull saves bandwidth nobody was paying for
while adding an UPDATE path that has to be right about row alignment 8.5
million times. With the guard failures it is also MORE requests, not fewer.

~~`gameEventIndex` is stable across re-fetches at 99.72%, so this is a
narrow pull of the new columns with a `playType` + `gameClock` guard,
re-downloading only the games that fail the guard, at ~7.6% of a full
download.~~

**What it actually costs:** 5,295 games = **452 batches = 904 requests**,
events and minutes. Minutes are kept deliberately - skipping them halves
the count but leaves `player_game_minutes` stale against refreshed events
if the source revised the game, which is the per-90 trap this file already
warns about. `run_campaign(with_minutes=False)` exists if that ever changes.

Driven from Campaign's **"re-download games already stored"** checkbox,
season by season. `COMPLETE` is selectable but never pre-selected.

The 79 ids are in `data_manager/opta_qualifiers_referenced.json`, each mapped
to the stats that reference it - which is what says what it MEANS. Names are
readable from those: q2 passes, q1 long ball, q5 cross, q15 headed, q20/q72
right/left foot, q25 from corner, q26 from free kick, q214 big chance,
q211 duels, q264 aerial, q218 assist.

**Do NOT guess qualifier names.** Fields that do not exist are accepted by the
query and return NULL forever - `event.optaEventId` is the standing example.
Enumerate from the catalogue.

---

## Beyond qualifiers: see EVENT_MODEL_EXPANSION.md

**The conclusion changed after this section was written.** Do not take 227
raw qualifiers - take the ~77 `context: ['event']` STAT TOKENS instead. They
are TruMedia's resolved layer: `[BodyPart|EVENT]` instead of q15+q20+q72+q21,
`[CornerType|EVENT]` instead of q223+q224+q225. One readable column instead
of four booleans to interpret.

And there is a fourth way fields arrive that none of the sections below knew
about - stat tokens - which is where the genuinely irreplaceable data lives:
`GKx`/`GKy`, `GoalmouthY`/`GoalmouthZ` (shot placement in 3D), `PlyrsBtwn`,
`xGOT`, `xPVAdded`, `MatchState`, `Starter`.

`EVENT_MODEL_EXPANSION.md` is the spec. What follows is kept for the sizing
work and the derivable/not-derivable split, both of which still hold.

## (superseded) Beyond qualifiers: what else the feed carries

Widening the predicate changed WHICH EVENTS arrive. Qualifiers are one axis of
"how much is known about each one" - but not the only one. The catalogue's
equations reference **150 named `event.*` fields**, of which we select 18.

**Most of the other 132 are NOT worth taking, because we can build them.**

```
DERIVABLE from what we already store
  event.x / y                    EventXDecimal / EventYDecimal
  event.passEndX / passEndY      PassEndXDecimal / PassEndYDecimal
  event.passAngle                atan2 of those two pairs
  event.playTypeId               playType
  event.next_playTypeID          LEAD() over gameEventIndex
  event.sequenceTouchCount       COUNT(*) over sequenceId
  event.sequencePassCount        COUNT(*) FILTER over sequenceId
  event.sequenceStartX / StartPlayTypeId   first event in the sequence
  event.possessionTouchCount / StartX      same, over possessionSeqNum
  event.sequenceStartq2/q5/q6/q107/q124    the sequence's first event's flags
  event.success / event.fail     playType semantics
  event.onField                  player_game_minutes, approximately
```

Storing those would pay TruMedia to duplicate a `COUNT(*)` and a `LEAD()`.

### The irreducible list

```
event.remoteEventsPressureReceived    77 stats
event.remoteEventsLinesBroken         81 stats
event.remoteEventsLastLineBroken      18 stats
event.qv326    defensive pressure at time of shot (1=Low 2=Mod 3=High)
event.carryLength / carryLengthX / carryStartType   39 stats, arguably
```

**The `remoteEvents*` family is computed from where the other 21 players
were.** There is no off-ball positional data in this feed and no way to
reconstruct it, so this is the one category where "we could build it
ourselves" is false. Not coincidentally it is also the most analytically
interesting - pressure on the ball, and passes that break a defensive line.

`carryLength` is an Opta construct with its own start-type definition. It
could be approximated from consecutive coordinates by the same player, but
that invents a definition rather than recovering theirs.

**So the real ask is ~233 fields: the 227 qualifier flags plus five or six
measured ones.** Every one carries information that cannot be reconstructed.
The rest of the catalogue is convenience over inputs already held.

### The trap this took three passes to escape

I probed NUMBERED namespaces - `q1..500`, `qv1..500`, `assistq1..300` - and
kept concluding the space was small. `event.remoteEventsPressureReceived` and
`event.carryLengthX` are NAMED fields; counting cannot find them. The
catalogue's equations are the enumeration, not the field numbering.

Also: `qv` is a real and separate namespace from `q` - qualifier VALUES
against qualifier FLAGS. 10 populated, including the pressure one.

---

## Standing rules

- **Practice mode for all download work.** `DATA_MANAGER_LOCAL_DB=<path>`.
  Git reverts code; nothing reverts a bad write to the cloud.
- **Never join on team name.** `teamId` is stable across renames; TruMedia has
  already renamed Bournemouth and Angers.
- **`smoke_chart_data.py` before and after any schema or ingest change.**
- **No push to `main` without explicit approval.**
