# Next work item: expand the event model

**Status: sized and specified, not built.** Written 2026-08-29 after the
per-game migration completed. See `MIGRATION_PLAN.md` for that.

The migration widened **which events arrive** — 22 play types to 52. It did
not widen **how much is known about each one**. That is a separate axis, and
this is the spec for it.

---

## FOUR ways a field arrives. This took all evening to work out.

```
1. named event fields     event.playType, event.gameClock, event.carryLengthX
2. qualifier FLAGS        event.q15   (headed), event.q33 (red)
3. qualifier VALUES       event.qv326 (1=Low 2=Moderate 3=High)
4. STAT TOKENS            [BodyPart|EVENT], [xG|EVENT], [GKx|EVENT]
```

We currently take 18 named fields, 9 qualifiers, and 5 stat tokens.

**The trap that cost the most time:** I enumerated *numbered* namespaces
(`q1..500`, `qv1..500`, `assistq1..300`) and kept concluding the space was
small. The valuable fields are **named** — `carryLengthX`,
`remoteEventsPressureReceived`, `onField` — and counting cannot find them.
Three times I told the user something was unavailable when it was.

**The catalogue's equations are the enumeration**, not the field numbering:

```
dp-proxy-show-stats-custom?showTransforms=true&showEquations=true
-> 10,511 stats, each with its equation
```

Parse `event\.([A-Za-z][A-Za-z0-9_]*)` out of every equation and you have the
real field list: **150 named fields**, of which we take 18.

---

## TAKE THE STAT TOKENS, NOT THE RAW QUALIFIERS

Qualifiers are Opta's *tagging* layer. Stat tokens are TruMedia's *resolved*
layer, and they are strictly better to store:

```
q15 + q20 + q72 + q21   ->  [BodyPart|EVENT] = 'Head' / 'Right foot' / ...
q223 + q224 + q225      ->  [CornerType|EVENT] = 'Inswinger' / 'Outswinger' / ...
qv326                   ->  [Pressure|EVENT] = 'High' / 'Moderate' / 'Low'
```

One readable column instead of four booleans you have to interpret. We
already do this for `BodyPart`, `ShotPlayStyle`, `xG`, `xA`, `ShotDist` —
proof of the pattern.

**77 stats carry `context: ['event']`** and are selectable per-event as
`[Abbrev|EVENT]`. That is the shortlist.

---

## What we cannot rebuild ourselves

Everything below was verified populated against a real Premier League
team-season (79,933 events).

### Positional data — no way to reconstruct it

| token | what it is |
|---|---|
| `GKx` / `GKy` | goalkeeper's coordinates at the moment of the shot |
| `PlyrsBtwn` | defenders between shooter and goal |
| `GoalmouthY` / `GoalmouthZ` | where the shot crossed the line, in Y **and Z** |
| `Pressure` | defensive pressure on the shooter, High/Moderate/Low |
| `remoteEventsPressureReceived` | pressure on the receiver, with coordinates |
| `remoteEventsLinesBroken` | line-breaking passes |

`GoalmouthZ` is shot HEIGHT. Today a shot chart can show where a shot was
taken from and never where it went.

### Model outputs

`xGOT` (post-shot xG) · `xPVAdded` (expected possession value added) ·
`WinProb` / `DrawProb` / `LoseProb`

### Context

`MatchState` (Ahead/Behind/Tied *before* the event) · `Formation` /
`OppFormation` · `Position` · **`Starter`** (the flag noted missing during
the API-Football audit) · `TimeStamp` (UTC) · `MfromGoal` · `CarryX` /
`CarryY` / `carryLength` · `1v1Success` / `1v1Next` · `Chance` ·
`CornerType` · `EventID`

**`EventID` is worth testing.** Memory records `event.optaEventId` as always
NULL and "not a usable key" — this is a different token and may work. A real
Opta event id would be a better join key than `gameId + gameEventIndex`.

---

## What NOT to take — we already own the inputs

```
event.x / y                  EventXDecimal / EventYDecimal
event.passEndX / passEndY    PassEndXDecimal / PassEndYDecimal
event.passAngle              atan2 of those two pairs
event.playTypeId             playType
event.next_playTypeID        LEAD() over gameEventIndex
event.sequenceTouchCount     COUNT(*) over sequenceId
event.sequencePassCount      COUNT(*) FILTER over sequenceId
event.sequenceStartX/Y       first event in the sequence
event.possessionTouchCount   same, over possessionSeqNum
event.sequenceStartq2/q5/q6  the sequence's first event's flags
event.success / event.fail   playType semantics
event.onField                player_game_minutes, approximately
```

Storing these would pay TruMedia to duplicate a `COUNT(*)` and a `LEAD()`.
**~132 of the 150 named fields fall here.**

---

## Cost

Measured by building columns into a copy of a real season at varying
densities, then scaling by 13.7 (database / one PL season):

```
sparse boolean    ~0.7 MB per column across the whole database
dense boolean     ~2.1 MB
227 qualifiers    ~0.58 GB     <- if taken raw, which is NOT recommended
~77 stat tokens   well under that, and far more useful per column
```

Against 2.85 GB today and a 10 GB allowance. **Cost is not the constraint.**

---

## How to do it without a second full re-download

`gameEventIndex` is stable across re-fetches at **99.72%**, so this is a
narrow pull of the new columns with a `playType` + `gameClock` guard,
re-downloading only the games that fail the guard. **~7.6% of a full
download.** That option was preserved deliberately before the migration.

---

## Open, before building

1. **Row-level vs event-level.** `GKx`, `GoalmouthZ`, `xGOT`, `MatchState`,
   `Starter` came back non-null on *every* row, including `Sequence` and
   `Possession` aggregate rows. Establish whether they are genuinely
   per-event or carried at row level before storing 8.5M copies of a
   per-match value.
2. **The full 77.** Only a sample was tested. Pull all of them against one
   team-season and check which populate, on what, and with what cardinality.
3. **Other competitions.** Everything was verified on one PL team-season.
   UCL alone showed 3 qualifiers the PL did not, and shootout play types
   (`shootoutgoal`, `missedshootout`) exist and we have none.
4. **`EventID`** — see above.

## What is NOT available, so nobody goes looking

- **Player-level pressing.** `remoteEventsPressureCreated` is named in the
  equations and returns nothing. Pressure is recorded against the player
  RECEIVING it; there is no presser attribution and no presser location.
- **Off-ball positions generally.** `GKx`/`GKy` and `PlyrsBtwn` are the only
  positional data about players other than the actor.
