"""The Team Profile cube: one team against a pool, three drillable levels.

Pure pandas. No database, no Streamlit. `shared/motherduck.get_team_profile`
fetches three small frames and hands them here; the chart reads only what
`view()` returns. Keeping the arithmetic in one dependency-free module is
what makes it testable against a fixture and - the reason it is a separate
file - what makes it the SPEC a PodcastShorts twin re-implements. The pass
map went that way (matching definitions, independent code, a parity script);
the player comparison went the other way and imports this repo's chart
module, which is the coupling not to repeat.

THE CUBE
    headline  x  situation  x  component

    headline   gf ga gd xg xga xgd     - what the top-level gauge rates
    situation  total op sp ahead level behind
    component  for:     anchor context shots xg_per_shot finishing psxg
               against: anchor context shots xg_per_shot psxg stopping
               diff:    anchor context for against shots_diff psxg_diff

THE CHAIN. Components are the links of one accounting chain, in order:
    shots  x  chance quality (xG per shot)      =  xG
    xG   +  SHOT PLACEMENT (PSxG - xG)          =  post-shot xG
    PSxG +  BEATING THE KEEPER (G - PSxG)       =  goals          (for)
    PSxGA - SHOT-STOPPING (PSxGA - GA)          =  goals against  (against)
xG prices a chance before the strike; post-shot xG prices it after, where
the ball actually went. So the strike itself is PSxG - xG (the user's
correction of a first build that used G - xG, which bundles the shooter with
the keeper who faced him). Both links are gauged on both sides, so the
chain CLOSES: goals - xG = placement + beating the keeper, and goals
against - xGA = placement faced - shot-stopping. A second build gauged the
PSxG LEVEL instead of the last link; two cold readers took it for xG
printed twice, and Liverpool's +7.0 goals over PSxG - the biggest number on
the frame - was nowhere while a -5.5 "Finishing" was. Own goals sit in the
scoreline and in no shot, so the keeper links leave them out and say so.

THE CONTEXT SLOT (second gauge) is the one that changes with the situation:
the other family's quantity beside the anchor under Total and Open Play (xG
beside Goals, Goals beside xG, xGD beside GD), the time spent there under a
game state, and - pending - set pieces under Set Piece. A phase's share of
the season lives on the anchor's value line ("44 of 63 (70%)"), not on a
gauge of its own.

Every cell is computable from the same shot rows, which is why level 2 and
level 3 are the two ORDERS of walking the same two dimensions rather than a
tree: `situation-first` shows the six situations of a headline and drills
into a situation's components; `component-first` shows the six components
(the Total context list) and drills into one component across the six
situations. The two orders must agree cell for cell - a check the critique
can run, and `tests` below do.

FACTS THE RULES REST ON (probed on PL 2025/26, 2026-09-15)
- `MatchState` and `teamCurrentScore`/`opponentCurrentScore` on a goal row
  are the PRE-goal state (100% of 1,045 goals). State is read from the
  scores, not the label, so a row's state is always from the ACTING team's
  point of view and the other side's is its mirror.
- Goal events (Goal + PenaltyGoal + the opponent's OwnGoal) replay to the
  `games` scoreline in 380/380 games. The scoreline is still the headline
  source; the replay is reported as a check, never silently trusted.
- `OwnGoal` rows: `teamId` is the CONCEDING side, they carry `ShotPlayStyle`
  (the phase of play), and xG = xGOT = 0. So goals partition exactly into
  open play + set piece, and own goals never leak into xG.
- `xGOT` is populated on every shot: 0 when off target or blocked, > 0 when
  on target. SUM(xGOT) is post-shot xG.
- `gameClock` is cumulative seconds; period 2 starts at 2700 whatever the
  first half ran to.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

# -- Vocabulary ----------------------------------------------------------------

SHOT_TYPES = ("Goal", "PenaltyGoal", "AttemptSaved", "Miss", "Post")
GOAL_TYPES = ("Goal", "PenaltyGoal")
EVENT_TYPES = SHOT_TYPES + ("OwnGoal",)          # what the fetch asks for

OPEN_PLAY_STYLES = ("Open play", "Fastbreak/Counter")
SET_PIECE_STYLES = ("Corner", "Throw-in", "Direct Free Kick",
                    "Free Kick Set Piece", "Penalty")

# Cumulative-clock start of each period, seconds. Extra time never occurs in
# the league seasons this chart is scoped to, but the arithmetic is generic.
PERIOD_START = {1: 0, 2: 2700, 3: 5400, 4: 6300}

# SET PIECES - the count beside the set-piece shots. A set piece is every
# corner, a free kick taken in the attacking third, a throw-in taken level
# with or inside the penalty area, and a penalty. Coordinates are Opta's
# attack-normalised 0-100 (the penalty area's edge is x = 83). The lines
# were set from the PL 2025/26 yields (2026-09-16 probe): corners give 0.44
# shots each, attacking-third free kicks 0.73, box-level throw-ins 0.26 -
# while attacking-THIRD throw-ins give 0.11 and would have been 47% of every
# team's count, so "set pieces" would mostly have counted routine wide
# restarts. `q5` (free kick taken) is on pass rows only; `PassType` Corner
# also flags a shot assisted by the corner, so restarts are PASS rows only.
SET_PIECE_FK_MIN_X = 100.0 * 2.0 / 3.0    # attacking third
SET_PIECE_THROW_MIN_X = 83.0              # level with the penalty area


@dataclass(frozen=True)
class Headline:
    key: str
    label: str
    family: str      # "goals" | "xg"
    side: str        # "for" | "against" | "diff"


HEADLINES = {
    "gf":  Headline("gf",  "Goals For",        "goals", "for"),
    "ga":  Headline("ga",  "Goals Against",    "goals", "against"),
    "gd":  Headline("gd",  "Goal Difference",  "goals", "diff"),
    "xg":  Headline("xg",  "xG For",           "xg",    "for"),
    "xga": Headline("xga", "xG Against",       "xg",    "against"),
    "xgd": Headline("xgd", "xG Difference",    "xg",    "diff"),
}
HEADLINE_ORDER = ("gf", "ga", "gd", "xg", "xga", "xgd")
# The other family's headline on the same side: what sits beside the anchor.
COUNTERPART = {"gf": "xg", "xg": "gf", "ga": "xga", "xga": "ga", "gd": "xgd", "xgd": "gd"}

SITUATIONS = {
    "total":  "Total",
    "op":     "Open Play",
    "sp":     "Set Piece",
    "ahead":  "Ahead",
    "level":  "Drawing",
    "behind": "Behind",
}
SITUATION_ORDER = ("total", "op", "sp", "ahead", "level", "behind")
STATE_SITUATIONS = ("ahead", "level", "behind")

# The context slot is the one that changes with the situation: the other
# family beside the anchor, the set pieces the shots came from, the time
# spent in the state.
CONTEXT_FOR_SITUATION = {
    "total": "counterpart", "op": "counterpart", "sp": "set_pieces",
    "ahead": "minutes_pct", "level": "minutes_pct", "behind": "minutes_pct",
}

# Names, not formulas. The first build put the user's shorthand on the
# gauges verbatim ("GOALS - XG", "MINUTES %", "FOR") and the frames read as
# a worksheet. The formula lives in the unit line under the number.
COMPONENT_LABELS = {
    "anchor": "",                      # takes the parent's label
    "shots": "Shots",
    "xg_per_shot": "Chance Quality",
    "placement": "Shot Placement",
    "beat_keeper": "Beating the Keeper",
    "stopping": "Shot-Stopping",
    "set_pieces": "Set Pieces Taken",
    # difference headlines
    "shots_diff": "Shot Differential",
    # GD - xGD: the four links summed. Not "Finishing" - to a fan that word
    # means goals against chances, and it sat beside numbers saying the
    # opposite.
    "net": "vs Expected",
}

# On an AGAINST headline the same components are the opponents' numbers, and
# a body that says only "SHOTS" was read as the team's own attack by two
# cold readers out of two - the word "against" lived in the eyebrow alone.
# The label carries the side.
COMPONENT_LABELS_AGAINST = {
    "shots": "Shots Faced",
    "xg_per_shot": "Chance Quality Faced",
    "placement": "Placement Faced",
    "set_pieces": "Set Pieces Faced",
}


# What a derived stat IS, for the small line under its name. The user's
# rule: the name may be explanatory, but the specific stat must be on the
# chart too. Plain quantities (goals, xG, shots, time) need none.
COMPONENT_FORMULA = {
    "xg_per_shot": "xG per shot",
    "placement": "PSxG \u2212 xG",
    "beat_keeper": "Goals \u2212 PSxG",
    "stopping": "PSxGA \u2212 GA",
    "net": "GD \u2212 xGD",
    "set_pieces": "corners, FKs, throw-ins, pens",
}
COMPONENT_FORMULA_AGAINST = {
    "xg_per_shot": "xGA per shot",
    "placement": "PSxGA \u2212 xGA",
}


def component_formula(headline: "Headline", comp: str) -> str:
    if headline.side == "against" and comp in COMPONENT_FORMULA_AGAINST:
        return COMPONENT_FORMULA_AGAINST[comp]
    if headline.side == "diff" and comp == "set_pieces":
        return "taken \u2212 faced"
    return COMPONENT_FORMULA.get(comp, "")


def component_label(headline: "Headline", comp: str, situation: str = "total") -> str:
    """The label a component wears on this headline in this situation.

    Time in state names its state: "Time Behind 21%" carries its own
    direction (less is better) where "Minutes % 21%" did not - the same
    label was ranked ascending on Behind, descending on Ahead and left
    neutral on Drawing, and two cold readers inverted it.
    """
    if comp == "minutes_pct" and situation in STATE_SITUATIONS:
        return {"ahead": "Time Ahead", "level": "Time Level", "behind": "Time Behind"}[situation]
    if comp == "counterpart":
        return HEADLINES[COUNTERPART[headline.key]].label
    if comp == "set_pieces" and headline.side == "diff":
        return "Set Piece Differential"
    if comp in ("for", "against"):
        # On a difference frame the halves wear their full names: a bare
        # "FOR" over a gauge was the shorthand the user objected to.
        return next(h.label for h in HEADLINES.values()
                    if h.family == headline.family and h.side == comp)
    if headline.side == "against" and comp in COMPONENT_LABELS_AGAINST:
        return COMPONENT_LABELS_AGAINST[comp]
    return COMPONENT_LABELS.get(comp, comp)


# Reading order: the anchor, its context, then the chain forward - one order
# on every frame. (The first build ran the chain backward from a goals
# headline and forward from an xG one; two orders for one chain was a
# second thing to learn.)
COMPONENT_ORDER = {
    "for":     ("anchor", "context", "shots", "xg_per_shot", "placement", "beat_keeper"),
    "against": ("anchor", "context", "shots", "xg_per_shot", "placement", "stopping"),
    "diff":    ("anchor", "context", "for", "against", "shots_diff", "net"),
}


def situation_label(headline: Headline, situation: str) -> str:
    """The anchor's label on a situation-first level-3 frame: "Set-Piece
    Goals For", "Goals For When Behind". A bare "GOALS FOR 9th" on the set
    piece frame was read as the season rank by two cold readers - the
    qualifier lived only in the subject line above."""
    if situation == "op":
        return f"Open-Play {headline.label}"
    if situation == "sp":
        return f"Set-Piece {headline.label}"
    if situation in STATE_SITUATIONS:
        return f"{headline.label} When {SITUATIONS[situation]}"
    return headline.label


def components_of(headline: Headline) -> tuple:
    return COMPONENT_ORDER[headline.side]


# -- Direction: +1 higher is better, -1 lower is better, 0 no direction --------

def direction(headline: Headline, situation: str, component: str) -> int:
    """The gauge rates goodness, so every cell needs to know which way is up.

    Time in state has none: it is the context the rate beside it is read
    in. Judged on Ahead and Behind and left neutral on Drawing (the first
    build), the same label was ranked in opposite directions on adjacent
    frames and read as a bug.
    """
    if component == "minutes_pct":
        return 0
    if component in ("beat_keeper", "stopping"):
        # The last link, on either side: more is better for the team.
        return 1
    if headline.side == "diff":
        return -1 if component == "against" else 1
    return 1 if headline.side == "for" else -1


# -- Step 1: shot facts, two rows per event -----------------------------------

def _state_from_scores(team_score, opp_score) -> pd.Series:
    t = pd.to_numeric(team_score, errors="coerce")
    o = pd.to_numeric(opp_score, errors="coerce")
    out = pd.Series(np.where(t > o, "ahead", np.where(t < o, "behind", "level")),
                    index=t.index, dtype="object")
    out[t.isna() | o.isna()] = None
    return out


_MIRROR = {"ahead": "behind", "behind": "ahead", "level": "level"}


def shot_facts(shots: pd.DataFrame) -> pd.DataFrame:
    """Long table: every event twice, once for the side it counts FOR and once
    for the side it counts AGAINST, each with the state from THAT side's view.

    Columns: seasonId gameId teamId side phase state is_pen shots goals og
             xg xgot
    An own goal is a goal for the opponent and against the conceding team,
    in the phase the feed recorded, and is never a shot. It is counted
    separately (`og`) because no shot's post-shot xG covers it: shot-stopping
    is PSxG against goals the keeper actually faced.
    """
    s = shots.copy()
    pt = s["playType"].astype(str)
    style = s["ShotPlayStyle"].astype("string")
    is_og = pt.eq("OwnGoal")
    is_shot = pt.isin(SHOT_TYPES)
    is_goal = pt.isin(GOAL_TYPES) | is_og
    is_pen = style.eq("Penalty").fillna(False) | pt.eq("PenaltyGoal")
    phase = pd.Series(np.where(style.isin(SET_PIECE_STYLES), "sp",
                      np.where(style.isin(OPEN_PLAY_STYLES), "op", None)),
                      index=s.index, dtype="object")
    state_actor = _state_from_scores(s["teamCurrentScore"], s["opponentCurrentScore"])

    base = pd.DataFrame({
        "seasonId": s["seasonId"].values,
        "gameId": s["gameId"].values,
        "phase": phase.values,
        "is_pen": is_pen.values,
        "shots": is_shot.astype(int).values,
        "goals": is_goal.astype(int).values,
        "og": is_og.astype(int).values,
        "xg": pd.to_numeric(s["xG"], errors="coerce").fillna(0.0).values,
        "xgot": pd.to_numeric(s["xGOT"], errors="coerce").fillna(0.0).values,
    })
    actor = s["teamId"].values
    other = s["opponentId"].values
    mirrored = state_actor.map(_MIRROR)

    # The acting team's row counts FOR it - unless it is an own goal, which
    # counts for the other side. Against is always the reverse.
    for_team = np.where(is_og, other, actor)
    ag_team = np.where(is_og, actor, other)
    for_state = np.where(is_og, mirrored, state_actor)
    ag_state = np.where(is_og, state_actor, mirrored)

    f = base.copy(); f["teamId"] = for_team; f["side"] = "for"; f["state"] = for_state
    a = base.copy(); a["teamId"] = ag_team; a["side"] = "against"; a["state"] = ag_state
    return pd.concat([f, a], ignore_index=True)


# -- Step 2: minutes in each state, per team-game ------------------------------

def minutes_in_state(goals: pd.DataFrame, period_ends: pd.DataFrame,
                     games: pd.DataFrame) -> pd.DataFrame:
    """Seconds each team spent ahead / level / behind, from the goal clocks.

    goals: the Goal / PenaltyGoal / OwnGoal rows (gameId teamId opponentId
           playType Period gameClock gameEventIndex).
    period_ends: gameId Period end_clock  (MAX(gameClock) per period).
    games: seasonId gameId homeTeamId awayTeamId.

    Returns seasonId teamId ahead_s level_s behind_s total_s, summed over the
    team's games. A game with no goal contributes all its time to level.
    Penalties are goals here whatever the exclude-penalties toggle says - the
    toggle removes them from the team's OUTPUT, not from the match state the
    other numbers are conditioned on.
    """
    g = goals[goals["playType"].isin(GOAL_TYPES + ("OwnGoal",))].copy()
    g["scorer"] = np.where(g["playType"].eq("OwnGoal"), g["opponentId"], g["teamId"])
    g = g.sort_values(["gameId", "Period", "gameClock", "gameEventIndex"])
    by_game = {gid: grp for gid, grp in g.groupby("gameId", sort=False)}
    ends = {(r.gameId, int(r.Period)): float(r.end_clock)
            for r in period_ends.itertuples(index=False)}
    periods_of = {}
    for (gid, p) in ends:
        periods_of.setdefault(gid, []).append(p)

    rows = []
    for gm in games.itertuples(index=False):
        gid, home, away = gm.gameId, gm.homeTeamId, gm.awayTeamId
        periods = sorted(periods_of.get(gid, ()))
        if not periods:
            continue
        gl = by_game.get(gid)
        hs = as_ = 0                   # running score entering the period
        acc = {"ahead": 0.0, "level": 0.0, "behind": 0.0}   # home's view
        goal_iter = list(gl.itertuples(index=False)) if gl is not None else []
        gi = 0
        for p in periods:
            start = PERIOD_START.get(p, 0)
            end = ends[(gid, p)]
            clock = float(start)
            while gi < len(goal_iter) and int(goal_iter[gi].Period) == p:
                ev = goal_iter[gi]
                t = min(max(float(ev.gameClock), clock), end)
                key = "ahead" if hs > as_ else "behind" if hs < as_ else "level"
                acc[key] += t - clock
                clock = t
                if ev.scorer == home:
                    hs += 1
                elif ev.scorer == away:
                    as_ += 1
                gi += 1
            key = "ahead" if hs > as_ else "behind" if hs < as_ else "level"
            acc[key] += max(end - clock, 0.0)
        total = acc["ahead"] + acc["level"] + acc["behind"]
        rows.append((gm.seasonId, home, acc["ahead"], acc["level"], acc["behind"], total))
        rows.append((gm.seasonId, away, acc["behind"], acc["level"], acc["ahead"], total))
    out = pd.DataFrame(rows, columns=["seasonId", "teamId", "ahead_s", "level_s", "behind_s", "total_s"])
    return out.groupby(["seasonId", "teamId"], as_index=False).sum()


# -- Step 3: the cube ----------------------------------------------------------

_Q = ["goals", "og", "xg", "shots", "xgot"]


@dataclass
class Cube:
    """Everything `view()` needs, for every team in the pool.

    teams:  index (seasonId, teamId): gp gf ga ahead_s level_s behind_s total_s
            gf_pen ga_pen  (penalty goals, so the headline can drop them)
    cells:  index (seasonId, teamId, side, situation): goals og xg shots xgot
    checks: per team-season reconciliation of goal events vs scoreline
    exclude_penalties: what the cells were built with
    """
    teams: pd.DataFrame
    cells: pd.DataFrame
    checks: pd.DataFrame
    exclude_penalties: bool
    pens: pd.DataFrame | None = None   # (seasonId, teamId, side) -> penalty goals xg shots xgot
    # teams also carries sp_for / sp_against - set pieces taken and faced,
    # NaN when the restart rows were not supplied (an old fixture).

    @property
    def index(self):
        return self.teams.index


def team_games(games: pd.DataFrame) -> pd.DataFrame:
    """Two rows per game -> gp, gf, ga per (seasonId, teamId) from the scoreline."""
    g = games.dropna(subset=["homeFinalScore", "awayFinalScore"])
    h = pd.DataFrame({"seasonId": g["seasonId"], "teamId": g["homeTeamId"],
                      "gf": g["homeFinalScore"].astype(int), "ga": g["awayFinalScore"].astype(int)})
    a = pd.DataFrame({"seasonId": g["seasonId"], "teamId": g["awayTeamId"],
                      "gf": g["awayFinalScore"].astype(int), "ga": g["homeFinalScore"].astype(int)})
    both = pd.concat([h, a], ignore_index=True)
    out = both.groupby(["seasonId", "teamId"]).agg(gp=("gf", "size"), gf=("gf", "sum"), ga=("ga", "sum"))
    return out


def set_piece_counts(restarts: pd.DataFrame | None, facts: pd.DataFrame,
                     index: pd.MultiIndex, *, exclude_penalties: bool) -> pd.DataFrame:
    """sp_for / sp_against per (seasonId, teamId): corners + attacking-third
    free kicks + box-level throw-ins from the restart rows (already
    thresholded per team-game by the fetch), plus penalties taken / faced
    from the shot rows unless the toggle removed them.

    restarts: seasonId gameId teamId opponentId corners free_kicks throw_ins
    """
    out = pd.DataFrame(index=index, columns=["sp_for", "sp_against"], dtype=float)
    if restarts is None or restarts.empty:
        return out
    r = restarts.copy()
    r["n"] = r[["corners", "free_kicks", "throw_ins"]].fillna(0).sum(axis=1)
    taken = r.groupby(["seasonId", "teamId"])["n"].sum()
    faced = r.groupby(["seasonId", "opponentId"])["n"].sum()
    faced.index = faced.index.set_names(["seasonId", "teamId"])
    out["sp_for"] = taken.reindex(index).fillna(0.0)
    out["sp_against"] = faced.reindex(index).fillna(0.0)
    if not exclude_penalties:
        pens = facts[facts["is_pen"] & (facts["shots"] > 0)]
        p = pens.groupby(["seasonId", "teamId", "side"])["shots"].sum().unstack("side")
        p = p.reindex(columns=["for", "against"]).reindex(index).fillna(0)
        out["sp_for"] += p["for"].astype(float)
        out["sp_against"] += p["against"].astype(float)
    return out


def build_cube(games: pd.DataFrame, shots: pd.DataFrame, period_ends: pd.DataFrame,
               restarts: pd.DataFrame | None = None, *, exclude_penalties: bool = False) -> Cube:
    games = games.copy()
    games["gameId"] = games["gameId"].astype(str)
    shots = shots.copy()
    shots["gameId"] = shots["gameId"].astype(str)
    period_ends = period_ends.copy()
    period_ends["gameId"] = period_ends["gameId"].astype(str)

    teams = team_games(games)
    facts = shot_facts(shots)

    # Penalty goals per side, kept on the team frame so the headline can be
    # "scoreline minus penalties" rather than "events" when the toggle is on.
    pens = (facts[facts["is_pen"]].groupby(["seasonId", "teamId", "side"])["goals"].sum()
            .unstack("side").reindex(columns=["for", "against"]).fillna(0).astype(int))
    teams["gf_pen"] = pens["for"].reindex(teams.index).fillna(0).astype(int)
    teams["ga_pen"] = pens["against"].reindex(teams.index).fillna(0).astype(int)

    pen_cells = (facts[facts["is_pen"]].groupby(["seasonId", "teamId", "side"])[_Q].sum())

    # Reconciliation BEFORE any filtering: do the goal events replay to the
    # scoreline? Reported, not enforced.
    ev_goals = (facts.groupby(["seasonId", "teamId", "side"])["goals"].sum()
                .unstack("side").reindex(columns=["for", "against"]).fillna(0).astype(int))
    checks = teams[["gp", "gf", "ga"]].join(ev_goals.rename(columns={"for": "gf_events", "against": "ga_events"}), how="left")
    checks[["gf_events", "ga_events"]] = checks[["gf_events", "ga_events"]].fillna(0).astype(int)
    checks["gf_ok"] = checks["gf"] == checks["gf_events"]
    checks["ga_ok"] = checks["ga"] == checks["ga_events"]

    if exclude_penalties:
        facts = facts[~facts["is_pen"]]

    # Situations: total, the two phases, the three states.
    parts = []
    tot = facts.groupby(["seasonId", "teamId", "side"])[_Q].sum()
    tot["situation"] = "total"; parts.append(tot.reset_index())
    ph = facts.dropna(subset=["phase"]).groupby(["seasonId", "teamId", "side", "phase"])[_Q].sum()
    parts.append(ph.reset_index().rename(columns={"phase": "situation"}))
    stt = facts.dropna(subset=["state"]).groupby(["seasonId", "teamId", "side", "state"])[_Q].sum()
    parts.append(stt.reset_index().rename(columns={"state": "situation"}))
    cells = pd.concat(parts, ignore_index=True)

    # Every (team, side, situation) present, zero-filled, so a team with no
    # set-piece shot is 0 - not missing - and ranks last rather than nowhere.
    full_index = pd.MultiIndex.from_tuples(
        [(s, t, side, sit) for (s, t) in teams.index
         for side in ("for", "against") for sit in SITUATION_ORDER],
        names=["seasonId", "teamId", "side", "situation"])
    cells = (cells.set_index(["seasonId", "teamId", "side", "situation"])
             .reindex(full_index).fillna(0.0))
    cells[["goals", "og", "shots"]] = cells[["goals", "og", "shots"]].astype(int)

    mins = minutes_in_state(shots, period_ends, games).set_index(["seasonId", "teamId"])
    teams = teams.join(mins, how="left")
    teams[["ahead_s", "level_s", "behind_s", "total_s"]] = teams[["ahead_s", "level_s", "behind_s", "total_s"]].fillna(0.0)
    if restarts is not None:
        restarts = restarts.copy()
        restarts["gameId"] = restarts["gameId"].astype(str)
    teams = teams.join(set_piece_counts(restarts, shot_facts(shots), teams.index,
                                        exclude_penalties=exclude_penalties), how="left")
    return Cube(teams=teams, cells=cells, checks=checks, exclude_penalties=exclude_penalties,
                pens=pen_cells)


# -- Step 4: a cell, for every team at once ------------------------------------

def _q(cube: Cube, side: str, situation: str, col: str) -> pd.Series:
    """One quantity for one side and situation, indexed (seasonId, teamId)."""
    s = cube.cells.xs((side, situation), level=("side", "situation"))[col]
    return s.reindex(cube.index)


def _headline_goals(cube: Cube, side: str) -> pd.Series:
    """Goals for the Total situation come from the scoreline - exact, own
    goals included - minus penalty goals when the toggle is on."""
    t = cube.teams
    if side == "for":
        return (t["gf"] - (t["gf_pen"] if cube.exclude_penalties else 0)).astype(float)
    return (t["ga"] - (t["ga_pen"] if cube.exclude_penalties else 0)).astype(float)


def _quantity(cube: Cube, family: str, side: str, situation: str) -> pd.Series:
    """The headline family's quantity (goals or xG) for one side + situation."""
    if family == "goals":
        if situation == "total":
            return _headline_goals(cube, side)
        return _q(cube, side, situation, "goals").astype(float)
    return _q(cube, side, situation, "xg").astype(float)


def _denominator(cube: Cube, situation: str) -> pd.Series:
    """Per 90 minutes - played, or spent in the state. Zero where there is
    no exposure; `_per90` turns 0/0 into a rate of zero.

    ONE time base for the whole cube. The first build normalised Total, Open
    Play and Set Piece per MATCH and the game states per 90' in state, and a
    cold analyst caught what that does: a Premier League match runs ~101
    minutes with stoppages, so "1.66 per match" sat on the same frame as
    "1.63 per 90' ahead" and read as HIGHER when the like-for-like figure
    (1.48 per 90') is lower. The matches still show in the line beneath.
    """
    t = cube.teams
    secs = t[f"{situation}_s"] if situation in STATE_SITUATIONS else t["total_s"]
    return secs / 5400.0                        # 90 minutes = 5400 s


def _per90(q: pd.Series, den: pd.Series) -> pd.Series:
    """q per 90, and ZERO where there was no exposure. Every team is a peer
    on every cell: a side that has never trailed has scored zero when behind,
    which is a fact about it and not a missing value. The first build kept a
    90-minute floor so a side behind for twenty minutes all season could not
    rank 1st on one goal; two games into a season that floor was dropping
    teams to "18th/19", and the user ruled that a full pool wins. The thin
    exposure stays visible on the line beneath ("1 in 15 min")."""
    return (q / den).where(den > 0, 0.0)


def resolve_component(situation: str, component: str) -> str:
    return CONTEXT_FOR_SITUATION[situation] if component == "context" else component


def cell(cube: Cube, headline: str, situation: str, component: str) -> tuple[pd.Series, pd.Series | None]:
    """(normalised value, raw total-or-None) for every team in the pool.

    The normalised value is what the gauge ranks; the raw total is what the
    chart prints beneath it ("70 in 38"). Ratios and shares have no total.
    """
    h = HEADLINES[headline]
    comp = resolve_component(situation, component)
    den = _denominator(cube, situation)

    if comp == "minutes_pct":
        t = cube.teams
        return (t[f"{situation}_s"] / t["total_s"]).where(t["total_s"] > 0), None

    if h.side in ("for", "against"):
        side = h.side
        goals = _quantity(cube, "goals", side, situation)
        xg = _quantity(cube, "xg", side, situation)
        shots = _q(cube, side, situation, "shots").astype(float)
        psxg = _q(cube, side, situation, "xgot").astype(float)
        og = _q(cube, side, situation, "og").astype(float)
        anchor = goals if h.family == "goals" else xg
        if comp == "anchor":
            return _per90(anchor, den), anchor
        if comp == "counterpart":
            other = xg if h.family == "goals" else goals
            return _per90(other, den), other
        if comp == "shots":
            return _per90(shots, den), shots
        if comp == "xg_per_shot":
            # No shots yet in the situation: chance quality of zero, so the
            # team stays a peer (the same rule as the states).
            return (xg / shots).where(shots > 0, 0.0), None
        if comp == "placement":
            # The strike: what the shot added to (or took from) the chance.
            # Off-target and blocked shots carry PSxG 0, so both cost the
            # full xG - a miss is a placement failure; a block is arguably
            # part defensive pressure, and is charged here all the same.
            # On the against side the same number is the opponents' strike.
            return _per90(psxg - xg, den), psxg - xg
        if comp == "beat_keeper":
            # The last link for the attack: goals FROM SHOTS beyond what
            # their placement was worth - the opposing keeper's failure and
            # the deflections. Own goals are in the scoreline and in no
            # shot's PSxG, so they are left out and the line says so.
            from_shots = goals - og
            return _per90(from_shots - psxg, den), from_shots - psxg
        if comp == "stopping":
            # The keeper's link: PSxG faced minus goals conceded FROM SHOTS.
            faced = goals - og
            return _per90(psxg - faced, den), psxg - faced
        if comp == "set_pieces":
            n = cube.teams[f"sp_{side}"].astype(float)
            return _per90(n, den), n
        raise KeyError(comp)

    # difference headlines
    qf = _quantity(cube, h.family, "for", situation)
    qa = _quantity(cube, h.family, "against", situation)
    if comp == "anchor":
        return _per90(qf - qa, den), qf - qa
    if comp == "counterpart":
        other = "xg" if h.family == "goals" else "goals"
        d = _quantity(cube, other, "for", situation) - _quantity(cube, other, "against", situation)
        return _per90(d, den), d
    if comp == "for":
        return _per90(qf, den), qf
    if comp == "against":
        return _per90(qa, den), qa
    if comp == "shots_diff":
        d = _q(cube, "for", situation, "shots") - _q(cube, "against", situation, "shots")
        return _per90(d.astype(float), den), d.astype(float)
    if comp == "net":
        # GD - xGD: placement + beating the keeper - placement faced +
        # shot-stopping, own goals included on both sides.
        gd = _quantity(cube, "goals", "for", situation) - _quantity(cube, "goals", "against", situation)
        xgd = _quantity(cube, "xg", "for", situation) - _quantity(cube, "xg", "against", situation)
        return _per90(gd - xgd, den), gd - xgd
    if comp == "set_pieces":
        d = cube.teams["sp_for"].astype(float) - cube.teams["sp_against"].astype(float)
        return _per90(d, den), d
    raise KeyError(comp)


# -- Step 5: where the subject stands ------------------------------------------

def calculate_percentile(value: float, peer_values) -> float:
    """(below + half the ties) / n x 100. Same definition as
    mostly_finished_charts/player_comparison_chart.py:381 - copied, not
    imported: five lines are not worth pulling a 2,300-line chart module in."""
    peers = [v for v in peer_values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not peers:
        return 50.0
    below = sum(1 for v in peers if v < value)
    equal = sum(1 for v in peers if v == value)
    return (below + 0.5 * equal) / len(peers) * 100.0


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


@dataclass
class Standing:
    mode: str            # "rank" | "pctl"
    position: float      # rank (1 = best) or percentile
    n: int               # pool size with a value
    needle: float        # 0..1 along the arc, 1 = best (or highest, if neutral)
    tied: bool = False

    @property
    def readout(self) -> str:
        if self.n == 0 or self.position is None or (isinstance(self.position, float) and math.isnan(self.position)):
            return "\u2014"
        if self.mode == "rank":
            r = int(self.position)
            return f"{ordinal(r)}{'=' if self.tied else ''}/{self.n}"
        return f"{self.position:.0f}"


def standing(values: pd.Series, subject, direction_sign: int, mode: str) -> Standing:
    """Rank or percentile of `subject` within `values` (indexed by team key).

    direction_sign +1: higher is better; -1: lower is better; 0: no direction,
    ranked by value with the highest first and drawn on a neutral arc.
    Teams without a value are not peers - which, with zero exposure counted
    as zero, is only a team whose data is missing (no restart rows for the
    set-piece count).
    """
    v = pd.to_numeric(values, errors="coerce").dropna()
    n = int(len(v))
    if subject not in v.index or n == 0:
        return Standing(mode, float("nan"), n, 0.5)
    x = float(v.loc[subject])
    sign = 1 if direction_sign >= 0 else -1
    if mode == "rank":
        ranked = (v * sign).rank(method="min", ascending=False)
        r = int(ranked.loc[subject])
        tied = int((ranked == r).sum()) > 1
        needle = (n - r) / (n - 1) if n > 1 else 0.5
        return Standing(mode, r, n, needle, tied)
    p = calculate_percentile(x * sign, list(v * sign))
    return Standing(mode, p, n, p / 100.0)


# -- Step 6: a frame's six gauges ----------------------------------------------

@dataclass
class GaugeSpec:
    key: str
    label: str
    value: float                 # normalised (what the gauge ranks)
    total: float | None          # raw total, or None for ratios
    unit: str                    # "per match" | "per 90' <state>" | "" for ratios
    fmt: str                     # how to print value
    direction: int
    standing: Standing
    pool_values: list            # every peer's normalised value (for arc ticks)
    minutes: float | None = None   # minutes in state, on state gauges
    minutes_pct: float | None = None
    gp: int | None = None
    component: str = ""         # the resolved component key
    situation: str = ""
    n_shots: float | None = None   # the subject's shots in this cell (ratios' denominator)
    minutes_total: float | None = None
    pens: float | None = None      # penalties inside a Set Piece cell (goals / xG / shots)
    parent_total: float | None = None   # the season figure a phase anchor is a share of
    og: float | None = None        # own goals left out of a shot-stopping cell
    shots_per_sp: float | None = None   # on the set-pieces gauge: the link to the shots gauge
    formula: str = ""              # what the stat IS, under the name ("PSxG - xG")


def _fmt_for(family: str, comp: str, side: str = "for") -> str:
    if comp == "minutes_pct":
        return "pct"
    if comp == "xg_per_shot":
        # 3 dp: at 2 dp "0.11 - median 0.11" carried a 7th/20 beside it, a
        # rank the displayed digits could not support.
        return "xg3"
    if comp == "shots_diff" or (comp == "set_pieces" and side == "diff"):
        return "signed_int"
    if comp in ("placement", "beat_keeper", "stopping", "net"):
        return "signed"
    if comp in ("shots", "set_pieces"):
        return "count"
    # The counterpart is the OTHER family's quantity.
    goals_like = (family == "goals") != (comp == "counterpart")
    if side == "diff" and comp in ("anchor", "counterpart"):
        # Every difference is signed: "+10 in 38" beside "+154 in 38". An
        # unsigned "10 in 38" was read as ten goals.
        return "signed_int" if goals_like else "signed"
    if comp in ("anchor", "counterpart", "for", "against") and goals_like:
        return "goals"
    return "xg"


def _unit_for(situation: str, comp: str) -> str:
    """The words after the number. No formulas: the link's NAME carries
    its meaning and the page's help text its definition. A formula in the
    unit ("PSxG - xG per 90 min when behind") pushed the bold value 150px
    off the dial's centre. The "when <state>" tail is what the chart's
    short-unit layouts drop."""
    if comp == "minutes_pct":
        return ""
    if comp == "xg_per_shot":
        return ""                  # the definition line under the name says it
    # "when behind", not "behind": the bare word read as a preposition
    # missing its object ("per 90 minutes... behind what?").
    when = f" when {SITUATIONS[situation].lower()}" if situation in STATE_SITUATIONS else ""
    return "per 90 min" + when


def gauge(cube: Cube, subject, headline: str, situation: str, component: str,
          mode: str, label: str | None = None) -> GaugeSpec:
    h = HEADLINES[headline]
    comp = resolve_component(situation, component)
    values, totals = cell(cube, headline, situation, component)
    d = direction(h, situation, comp)
    st = standing(values, subject, d, mode)
    t = cube.teams.loc[subject] if subject in cube.index else None
    v = float(values.loc[subject]) if subject in values.index else float("nan")
    tot = None
    if totals is not None and subject in totals.index:
        tot = float(totals.loc[subject])
    spec = GaugeSpec(
        key=f"{headline}.{situation}.{comp}",
        formula=component_formula(h, comp),
        label=label if label is not None else component_label(h, comp, situation),
        value=v, total=tot, unit=_unit_for(situation, comp), fmt=_fmt_for(h.family, comp, h.side),
        direction=d, standing=st,
        pool_values=[float(x) for x in pd.to_numeric(values, errors="coerce").dropna().values],
        gp=int(t["gp"]) if t is not None else None,
        component=comp, situation=situation,
    )
    if t is not None and situation in STATE_SITUATIONS:
        secs = float(t[f"{situation}_s"])
        spec.minutes = secs / 60.0
        spec.minutes_total = float(t["total_s"]) / 60.0
        spec.minutes_pct = secs / float(t["total_s"]) if float(t["total_s"]) > 0 else None
    if t is not None and h.side in ("for", "against"):
        spec.n_shots = float(_q(cube, h.side, situation, "shots").loc[subject])
        if situation == "sp" and not cube.exclude_penalties and cube.pens is not None:
            col = {"anchor": "goals" if h.family == "goals" else "xg",
                   "counterpart": "xg" if h.family == "goals" else "goals",
                   "shots": "shots"}.get(comp)
            key = (subject[0], subject[1], h.side)
            if col and key in cube.pens.index:
                spec.pens = float(cube.pens.loc[key, col])
        if comp in ("stopping", "beat_keeper"):
            og = float(_q(cube, h.side, situation, "og").loc[subject])
            spec.og = og if og > 0 else None
        if comp == "set_pieces" and tot and tot > 0:
            # The link to the Shots gauge beside it. No penalty note here:
            # the Shots line carries it, and with both the line pushed the
            # pool median off every 16:9 Set Piece frame.
            spec.shots_per_sp = spec.n_shots / tot
    if (t is not None and h.side in ("for", "against") and situation in ("op", "sp")
            and comp in ("anchor", "counterpart")):
        # A phase's share of the season, for the line beneath: "44 of 63".
        # Not on a difference: +5 open play of +3 overall is not a share.
        _, season = cell(cube, headline, "total", comp)
        if season is not None and subject in season.index:
            spec.parent_total = float(season.loc[subject])
    return spec


def view(cube: Cube, subject, headline: str | None, path: tuple = (),
         order: str = "situation", mode: str = "rank") -> list[GaugeSpec]:
    """The six gauges of one frame.

    level 1  headline None, path ()            -> the six headlines
    level 2  path ()   order situation         -> the six situations of `headline`
             path ()   order component         -> the six components (Total context list)
    level 3  path (situation,)  [situation-first] -> that situation's components
             path (component,)  [component-first] -> that component across situations
    """
    if headline is None:
        return [gauge(cube, subject, hk, "total", "anchor", mode, HEADLINES[hk].label)
                for hk in HEADLINE_ORDER]
    h = HEADLINES[headline]
    comps = components_of(h)

    if not path:
        if order == "situation":
            return [gauge(cube, subject, headline, sit, "anchor", mode, SITUATIONS[sit])
                    for sit in SITUATION_ORDER]
        return [gauge(cube, subject, headline, "total", c, mode,
                      h.label if c == "anchor" else None) for c in comps]

    (pick,) = path
    if order == "situation":
        sit = pick
        return [gauge(cube, subject, headline, sit, c, mode,
                      situation_label(h, sit) if c == "anchor" else None) for c in comps]
    comp = resolve_component("total", pick)
    return [gauge(cube, subject, headline, sit, comp, mode, SITUATIONS[sit])
            for sit in SITUATION_ORDER]


# -- Formatting -----------------------------------------------------------------

def _minus(s: str) -> str:
    """A real minus sign (escaped, not typed), not the hyphen Python prints."""
    return s.replace("-", "\u2212", 1) if s.startswith("-") else s


def _signed(s: str) -> str:
    """A signed number that rounds to zero is zero: "+0.0" and "-0.0" both
    printed on the xGD frames."""
    if s[0] in "+-" and not any(ch in "123456789" for ch in s):
        return s[1:]
    return _minus(s)


def format_number(fmt: str, v, *, total: bool = False) -> str:
    """One number in a cell's format - the value, the pool median beneath it,
    or (total=True) the raw total on the last line."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "\u2014"
    if fmt == "pct":
        return f"{v * 100:.0f}%"
    if fmt == "signed":
        return _signed(f"{v:+.1f}" if total else f"{v:+.2f}")
    if fmt == "signed_int":
        return _signed(f"{v:+.0f}" if total else f"{v:+.2f}")
    if fmt in ("goals", "count"):
        return _minus(f"{v:.0f}" if total else (f"{v:.2f}" if fmt == "goals" else f"{v:.1f}"))
    if fmt == "xg3":
        return _minus(f"{v:.3f}")
    return _minus(f"{v:.1f}" if total else f"{v:.2f}")


def format_value(spec: GaugeSpec) -> str:
    return format_number(spec.fmt, spec.value)


def format_total(spec: GaugeSpec) -> str:
    """The line beneath the value: what the rate was made from."""
    if spec.total is None:
        # Only the minutes gauge explains itself with the minutes line; a
        # ratio under a game state (xG per shot while behind) has no total.
        if spec.component == "minutes_pct" and spec.minutes is not None and spec.minutes_pct is not None:
            return f"{spec.minutes:,.0f} min ({spec.minutes_pct * 100:.0f}%)"
        return ""
    t = spec.total
    body = format_number(spec.fmt, t, total=True)
    out = body
    notes = []
    if spec.minutes is not None:
        out = f"{body} in {spec.minutes:,.0f} min"
        if spec.component == "anchor" and spec.minutes_pct is not None:
            # The state's share of the season beside the anchor, so the six
            # situations visibly compose the whole on the by-situation frame.
            notes.append(f"{spec.minutes_pct * 100:.0f}% of time")
    elif spec.parent_total is not None and spec.parent_total > 0:
        # A phase's share of the season: "44 of 63 (70%)".
        out = f"{body} of {format_number(spec.fmt, spec.parent_total, total=True)}"
        notes.append(f"{t / spec.parent_total * 100:.0f}%")
    elif spec.gp:
        out = f"{body} in {spec.gp}"
    if spec.pens:
        # Say what the number IS: "(3.4 pens)" on a post-shot-xG line put a
        # metric amount in the slot that carries a count on the goals line.
        if spec.fmt == "goals":
            notes.append(f"{spec.pens:.0f} pen goal{'s' if spec.pens != 1 else ''}")
        elif spec.fmt == "count":
            notes.append(f"{spec.pens:.0f} pen{'s' if spec.pens != 1 else ''} taken")
        else:
            notes.append(f"{spec.pens:.1f} from pens")
    if spec.og:
        notes.append(f"excl. {spec.og:.0f} own goal{'s' if spec.og != 1 else ''}")
    if spec.shots_per_sp is not None:
        # "each" what was not stated, and 0.37 read as a per-shot value.
        notes.insert(0, f"{spec.shots_per_sp:.2f} shots per set piece")
    if notes:
        # One parenthesis, so the chart's fit ladder sheds it whole.
        out += f" ({', '.join(notes)})"
    return out
