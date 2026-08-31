# Event model expansion — investigation RESOLVED, not yet built

**Status 2026-08-31: the four open questions are answered and the field map
is measured.** Nothing is implemented. The previous version of this file was
written from a partial probe and several of its conclusions were wrong; they
are corrected below and the errors are recorded, because two of them were
rebuilt twice before being caught.

The per-game migration (`MIGRATION_PLAN.md`) widened **which events arrive**,
22 play types to 52. This is the other axis: **how much is known about each
one.** We store 63 columns per event.

---

## The namespace map — I had this wrong

There is no single list of "available fields". There are at least four
namespaces, and the first version of this document probed one of them and
treated it as the universe.

```
1. stat tokens        [BodyPart|EVENT]          77 catalogued, 74 live
2. named fields       event.carryLength         233 in equations, ~178 live
3. qualifier flags    event.q33                 227 found by brute force
4. qualifier values   event.qv326               3 confirmed working
5. PREFIXED qualifiers  event.shot_q22          16 seen, count unknown
                        event.assist_q223
                        event.save_q177
```

**`context: ['event']` is a CATEGORY, not a capability flag.** Across 10,511
catalogue stats: `inPossession` 6,228 · `outOfPossession` 1,232 ·
`goalkeeping` combinations 2,579 · **`event` 77**. The 77 are event
*descriptors*; the rest are *aggregates*. Filtering on it finds one namespace,
not everything selectable per event.

**213 of the 233 named fields have no token**, so the token probe could never
have seen them — and that is where the best material turned out to be.

**The prefixed namespaces are a structural dimension nothing else exposes**: a
shot row can carry the qualifiers of the assist that led to it.

### The ceiling, stated honestly

The catalogue only names fields TruMedia wrote an equation against. The
qualifier count already proved the size of that gap — **~70 named in the
catalogue, 227 found by brute-force enumeration.** The same is certainly true
of named fields and the prefixed namespaces. Brute-forcing the numbered spaces
would close part of it; **the user's call on 2026-08-31 was not to** —
"anything that's that hard to find we don't need."

---

## The four open questions — ANSWERED

### 1. Per-event or row-level? PER-EVENT. The worry was an artefact.

`GKx`, `GoalmouthZ`, `xGOT`, `MatchState`, `Starter` were recorded as
"non-null on every row". They are not. Two measurement bugs stacked (see
TRAPS): numeric zero-fill, and 20% of returned rows not being events.
Corrected, `GKx`/`GoalmouthZ`/`xGOT` populate **only on shots, 0.5–1.5%**, and
vary within a game as they should.

Genuine per-game constants: `Formation`, `OppFormation`, `Opponent`,
`Season`, `Team`. Those belong on `games`, not on 8.5M event rows.

### 2. Which of the 77 populate? 74. Three are dead.

`EventID`, `OpenPlaySequence`, `TrueOpenPlaySequence` return nothing on real
events.

### 3. Does it hold outside the Premier League? YES — cleanly.

Man City 2025/26, PL (64,049 events, 38 games) vs UCL (16,588 events, 10
games): **identical token set, identical levels, identical types, identical
categorical value sets** on all 14 categoricals. The only differences are
`Formation`/`OppFormation`, because different teams played.

An earlier claim that UCL gained a `ShotPatternOfPlay` value was **wrong** —
it compared truncated 5-item sample lists, not value sets.

### 4. Is `EventID` a usable join key? NO.

Constant `'0'` across 80,395 rows. `event.optaEventId` is independently EMPTY.
Two methods, same answer. `gameId + gameEventIndex` remains the key.

---

## TRAPS — every one of these produced a confident wrong answer first

1. **The export ZERO-FILLS numerics.** `GKx` is `'0.0'` on a pass, not NULL.
   Counting non-nulls says every numeric shot field is universal.
2. **...but zero is a VALUE on some fields.** Over-correcting and suppressing
   all numeric zeros made `OpponentScore` read 26% when it is 100% populated
   and 0 means "they have not scored". Report non-null **and** non-zero
   separately; never collapse them.
3. **20% of returned rows are not events.** `FROM team BY event` returns
   `Sequence` and `Possession` aggregate rows. `upsert_game_events` drops them
   (flagged neither `is_team` nor `is_opp`) and production holds zero of 8.5M.
   Any probe must select those flags or its denominator is wrong.
4. **Sample lists are not value sets.** Comparing first-5 samples invented a
   competition difference that does not exist.
5. **ANCHOR-SCOPED FIELDS.** `shooterExpectedGoals` populates on all 594
   anchor-team shots and **zero of 369 opponent shots**, and where it
   populates it is identical to `expectedGoals`. It is not player-attributed
   xG; it is raw xG masked by the queried team. In the per-game architecture
   this yields a silently half-empty column. **Excluded.** Same trap class as
   `lookup(team.event.primary, ...)` returning identical values on every row.
6. **The level test cannot discriminate on boolean flags.** A field that is
   only ever `True`-or-NULL shows one distinct value per game and reads as
   "per-game". ~80 fields are affected; read that label as "flag".
7. **`storage_info.historical_bytes`** reported 29.46 GB and a whole plan was
   built on it before its meaning was checked. It was never established what
   that column measures. See `project_motherduck_fragmentation.md` — the same
   view produced a retracted finding three days earlier.

---

## Findings worth acting on

### Pressure — two different fields, and the useful one is not the token

```
Pressure|EVENT  (== qv326)        963 events   1.5%   SHOTS
                                  High / Low / Moderate

remoteEventsPressureReceived   31,129 events  48.6%   passes, take-ons,
                                  high / low / medium  clearances, ball touches
```

They **disagree where both populate** (99 events read `Low` on the token and
`high` on the other), so they are different measures — pressure on the shooter
at the shot, versus pressure on the player receiving the ball. Pressure is
what prompted this work; **`remoteEventsPressureReceived` is the one with
reach.** `qv326` is the token's numeric code — take one, not both.

`remoteEventsPressureCreated` is EMPTY. **There is no presser attribution and
no presser location**, so player-level pressing remains unavailable.

### Stat tokens return DISPLAY-FORMATTED values. Named fields return raw.

```
[xG|EVENT]            0.19                    stored: 100 distinct, 0.01-1.00
event.expectedGoals   0.1945270746946334      948 distinct in ONE team-season
```

`EVENT_LOG_SELECT` line 52 takes `[xG|EVENT]`, `[xA|EVENT]`, `[ShotDist|EVENT]`
— so stored `xG` is 2dp, `xA` 4dp, `ShotDist` 1dp, and `EventX/Y` are
explicitly format-capped. **Switching to the named field is a free precision
fix**: same column, same cost.

### Our xG is unadjusted; TruMedia's own team xG is not

The catalogue defines its own stat as:

```
team:   sum(event.reboundAdjustedExpectedGoals)
player: sum(event.shooterExpectedGoals)
```

`[xG|EVENT]` follows `expectedGoals` (23/23 on rows where they diverge), so
every aggregate we produce is unadjusted.

```
Man City 2025/26, both sides    raw 118.82   rebound-adjusted 116.77
```

1.75% over a season — invisible. But **up to 0.70 on a single shot** (a
rebound goal reads 0.89 raw, 0.19 adjusted), which is a visible step in the
wrong place on a match xG race. `expectedGoals == optaExpectedGoals` exactly
(963/963), so that is a duplicate name, not a third model.

**Not a bug — a definitional choice nobody made.** Take both columns and pick
per chart. **Deferred by the user 2026-08-31; do not change silently.**

---

## What to take — ~53 columns plus the catalogued qualifier flags

| group | n | notable |
|---|---|---|
| shot / chance quality | 16 | `expectedGoals`, `reboundAdjustedExpectedGoals`, `gmY`, **`gmZ`** (shot HEIGHT — charts show where a shot came from, never where it went), `GKx`/`GKy`, `PlyrsBtwn`, `Pressure`, `xGOT`, `BlockX/Y`, `Keeper`, `ShotPatternOfPlay` |
| pressure + passing | 15 | **`remoteEventsPressureReceived`**, `remoteEventsLinesBroken`, `remoteEventsLastLineBroken`, the carry family, `cross`, `chanceCreated` |
| sequence / possession | 8 | `possessionValueAdded`, `sequenceDirectSpeed`, `sequenceFieldLength`, `sequenceReachedPenaltyArea` |
| match context | 14 | `MatchState`, `Starter`, `Position`, formations (→ `games`), `CornerType`, `secondsUntilNextGoal`, win probabilities |

**Excluded:** `optaExpectedGoals` (duplicate) · `shooterExpectedGoals`
(anchor-scoped) · `TimeStamp` (230 MB, all-unique, and the user does not want
time-of-day) · the 3 dead tokens · the 35 EMPTY named fields · case-duplicate
pairs — **field names are case-insensitive**, so `PossessionStartX` and
`possessionStartX` are one field.

**Classified as derivable but NOT tested:** `PassAngle`, `PsDist`,
`PassLength`, `FieldLocation`, and the sequence start/end geometry. That
reasoning is the same kind that got `TimeStamp` wrong. At 20 bytes an event,
taking them is cheaper than trusting it.

---

## Cost — measured, not asserted

Per-column, scaled to 8.5M events, measured against 8.99M real local rows:

```
sparse DOUBLE (shot-only)      3.5-5.5 MB      GKx, gmZ, xGOT
sparse VARCHAR, few values       5-6 MB        Pressure, PlyrsBtwn
dense small INT                  1.7 MB
dense VARCHAR, low cardinality   8.5 MB        MatchState, Starter
dense DOUBLE                      19 MB        possessionValueAdded
all-unique VARCHAR               230 MB        TimeStamp  <- the outlier
```

**~20.5 bytes per event for the recommended set.** Two full seasons of every
league is 16.3M events ≈ 2.27 GB, or 2.61 GB with the new columns, against
**2.49 GB active today and a 10 GB cap**. The column list is not the
constraint; do not spend time curating it hard.

*A first attempt measured 69.9 MB for every column regardless of type — that
was DuckDB's minimum block allocation at 64k rows. Measure at production
scale or not at all.*

---

## Still unknown

- How many prefixed qualifiers (`shot_q*`, `assist_q*`, `save_q*`) exist.
- Whether `[xG|EVENT]` resolves differently at player grain. Probably moot —
  the pipeline only pulls `FROM team BY event`.
- Whether backfilling history is affordable. That depends on what
  `historical_bytes` means, which was never established.
- One team-season drove the field probe. `q171` reads EMPTY there and is known
  to populate elsewhere, so **absence in one sample proves nothing.**

## The probe

`scratchpad/probe_stat_tokens.py` and `probe_named_fields.py` answer, for any
field: does it populate, on what play types, at what level, and what type.
That — not a UI — was the answer to "should the download tool have a mode for
adding stats". Adding a field is two lines (`EVENT_LOG_SELECT` and an
`ALTER TABLE` in `_apply_schema`; `_align_to_table` handles the rest by name).
The hard part is the four judgements, and every one of them fails silently.
