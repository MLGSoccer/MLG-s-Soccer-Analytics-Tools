"""Tests for shared/team_profile.py - the Team Profile cube.

A four-team mini-league whose every number is computable by hand. The rules
under test are the ones the chart's honesty rests on: the situations
partition the total, both drill orders agree cell for cell, game state is
read from the PRE-goal score, minutes in state come from the clocks, and
the penalties toggle removes exactly the penalty rows.
Run: py -m pytest test_team_profile.py -q
"""
import math

import pandas as pd
import pytest

from shared import team_profile as tp

A, B, C, D = "teamA", "teamB", "teamC", "teamD"
S = "season1"


def _games():
    # Two games. A beats B 2-1 at home (one pen). C beats D 1-0 at home, by a
    # D own goal.
    return pd.DataFrame([
        dict(seasonId=S, gameId="g1", Date="2025-08-16", homeTeamId=A, awayTeamId=B,
             homeFinalScore=2, awayFinalScore=1),
        dict(seasonId=S, gameId="g2", Date="2025-08-17", homeTeamId=C, awayTeamId=D,
             homeFinalScore=1, awayFinalScore=0),
    ])


def _shot(game, idx, team, opp, pt, style, xg, xgot, blocked, ts, os_, period, clock):
    return dict(seasonId=S, gameId=game, gameEventIndex=idx, teamId=team, opponentId=opp,
                playType=pt, ShotPlayStyle=style, xG=xg, xGOT=xgot, qualifierBlocked=blocked,
                MatchState=None, teamCurrentScore=ts, opponentCurrentScore=os_,
                Period=period, gameClock=clock)


def _shots():
    # g1 timeline (cumulative clock, P1 ends 2800, P2 = 2700..5600):
    #   600  A open-play goal (0-0 before)                 -> A 1-0
    #  1200  B set-piece shot saved, on target (B behind)
    #  3000  B own goal by A? no - B scores from a corner: 1-1 (B behind before)
    #  4000  A penalty goal (level before)                 -> A 2-1
    #  5000  A open-play miss (A ahead)
    return pd.DataFrame([
        _shot("g1", 10, A, B, "Goal", "Open play", 0.30, 0.60, False, 0, 0, 1, 600),
        _shot("g1", 20, B, A, "AttemptSaved", "Corner", 0.10, 0.20, False, 0, 1, 1, 1200),
        _shot("g1", 30, B, A, "Goal", "Corner", 0.20, 0.50, False, 0, 1, 2, 3000),
        _shot("g1", 40, A, B, "PenaltyGoal", "Penalty", 0.79, 0.90, False, 1, 1, 2, 4000),
        _shot("g1", 50, A, B, "Miss", "Fastbreak/Counter", 0.05, 0.0, False, 2, 1, 2, 5000),
        # g2: one blocked shot each, then a D own goal at 5400 (level before)
        # -> C 1-0. OwnGoal rows: teamId = the CONCEDING side, xG = xGOT = 0.
        _shot("g2", 10, C, D, "AttemptSaved", "Open play", 0.08, 0.0, True, 0, 0, 1, 900),
        _shot("g2", 20, D, C, "Miss", "Throw-in", 0.04, 0.0, False, 0, 0, 2, 4500),
        _shot("g2", 30, D, C, "OwnGoal", "Open play", 0.0, 0.0, False, 0, 0, 2, 5400),
    ])


def _period_ends():
    return pd.DataFrame([
        dict(gameId="g1", Period=1, end_clock=2800), dict(gameId="g1", Period=2, end_clock=5600),
        dict(gameId="g2", Period=1, end_clock=2750), dict(gameId="g2", Period=2, end_clock=5500),
    ])


def _restarts():
    # per team-game, already thresholded the way the fetch does it
    return pd.DataFrame([
        dict(seasonId=S, gameId="g1", teamId=A, opponentId=B, corners=5, free_kicks=2, throw_ins=1),
        dict(seasonId=S, gameId="g1", teamId=B, opponentId=A, corners=3, free_kicks=0, throw_ins=0),
        dict(seasonId=S, gameId="g2", teamId=C, opponentId=D, corners=4, free_kicks=1, throw_ins=2),
        dict(seasonId=S, gameId="g2", teamId=D, opponentId=C, corners=2, free_kicks=0, throw_ins=0),
    ])


@pytest.fixture
def cube():
    return tp.build_cube(_games(), _shots(), _period_ends(), _restarts())


def _v(cube, team, headline, situation, component):
    values, _ = tp.cell(cube, headline, situation, component)
    return float(values.loc[(S, team)])


# g1 runs 2800 + 2900 = 5700 s; g2 runs 2750 + 2800 = 5550 s. Everything is
# per 90 MINUTES PLAYED, not per match - one time base across the cube.
N90_G1 = 5700 / 5400
N90_G2 = 5550 / 5400


# -- scoreline and reconciliation ------------------------------------------------

def test_headline_goals_come_from_the_scoreline_and_reconcile(cube):
    assert cube.teams.loc[(S, A), "gf"] == 2 and cube.teams.loc[(S, A), "ga"] == 1
    assert cube.teams.loc[(S, B), "gf"] == 1
    # the own goal is C's goal and D's concession, and replays to the scoreline
    assert cube.teams.loc[(S, C), "gf"] == 1 and cube.teams.loc[(S, D), "ga"] == 1
    assert cube.checks["gf_ok"].all() and cube.checks["ga_ok"].all()
    assert _v(cube, A, "gf", "total", "anchor") == pytest.approx(2.0 / N90_G1)


def test_gd_and_xgd_are_for_minus_against(cube):
    assert _v(cube, A, "gd", "total", "anchor") == pytest.approx(1.0 / N90_G1)
    assert _v(cube, B, "gd", "total", "anchor") == pytest.approx(-1.0 / N90_G1)
    xg_a = 0.30 + 0.79 + 0.05
    xga_a = 0.10 + 0.20
    assert _v(cube, A, "xgd", "total", "anchor") == pytest.approx((xg_a - xga_a) / N90_G1)


# -- the situations partition the total ----------------------------------------

@pytest.mark.parametrize("headline", tp.HEADLINE_ORDER)
def test_phases_and_states_partition_the_total(cube, headline):
    for team in (A, B, C, D):
        tot = _v(cube, team, headline, "total", "anchor")
        op = _v(cube, team, headline, "op", "anchor")
        sp = _v(cube, team, headline, "sp", "anchor")
        assert op + sp == pytest.approx(tot), (team, headline)
        # states are per 90' in state, so compare the RAW totals instead
        raw = sum(tp.cell(cube, headline, s, "anchor")[1].loc[(S, team)]
                  for s in tp.STATE_SITUATIONS)
        assert raw == pytest.approx(tp.cell(cube, headline, "total", "anchor")[1].loc[(S, team)])


def test_game_state_is_the_PRE_goal_score(cube):
    # A's opener came at 0-0 -> level; A's penalty came at 1-1 -> level;
    # B's equaliser came when B was behind 0-1 -> behind.
    assert tp.cell(cube, "gf", "level", "anchor")[1].loc[(S, A)] == 2
    assert tp.cell(cube, "gf", "behind", "anchor")[1].loc[(S, B)] == 1
    assert tp.cell(cube, "gf", "ahead", "anchor")[1].loc[(S, A)] == 0
    # and the same goals seen from the other side: A conceded while ahead
    assert tp.cell(cube, "ga", "ahead", "anchor")[1].loc[(S, A)] == 1


# -- minutes in state ------------------------------------------------------------

def test_minutes_in_state_follow_the_clocks(cube):
    t = cube.teams.loc[(S, A)]
    # P1: level 0-600, ahead 600-2800 (2200). P2: ahead 2700-3000 (300),
    # level 3000-4000 (1000), ahead 4000-5600 (1600).
    assert t["level_s"] == pytest.approx(600 + 1000)
    assert t["ahead_s"] == pytest.approx(2200 + 300 + 1600)
    assert t["behind_s"] == 0
    assert t["total_s"] == pytest.approx(2800 + 2900)
    b = cube.teams.loc[(S, B)]
    assert b["behind_s"] == t["ahead_s"] and b["ahead_s"] == 0
    c = cube.teams.loc[(S, C)]
    # level until the own goal at 5400, ahead for the last 100 s
    assert c["level_s"] == pytest.approx(2750 + 2700) and c["ahead_s"] == pytest.approx(100)
    assert c["total_s"] == pytest.approx(2750 + 2800)


def test_state_rows_are_per_90_in_state_with_a_floor(cube):
    # C and D were level for 5450 s - a match's worth - so they are peers;
    # A was ahead for only 4100 s, below MIN_STATE_SECONDS, so A is NOT a
    # peer on the "ahead" cell and its own gauge shows no standing.
    assert tp.MIN_STATE_SECONDS == 5400
    assert _v(cube, C, "gf", "level", "anchor") == pytest.approx(1.0 / (5450 / 5400))
    assert math.isnan(_v(cube, A, "ga", "ahead", "anchor"))
    assert math.isnan(_v(cube, A, "gf", "behind", "anchor"))    # never behind
    spec = tp.gauge(cube, (S, A), "gf", "behind", "anchor", "rank")
    assert spec.standing.readout == "—"
    # the raw totals are untouched by the floor - the partition test uses them
    assert tp.cell(cube, "ga", "ahead", "anchor")[1].loc[(S, A)] == 1


# -- components ------------------------------------------------------------------

XG_A = 0.30 + 0.79 + 0.05          # A's xG
PSXG_A = 0.60 + 0.90               # A's post-shot xG (the miss is off target)


def test_the_chain_for_side(cube):
    # shots x chance quality = xG; xG + finishing = PSxG
    assert _v(cube, A, "xg", "total", "shots") == pytest.approx(3.0 / N90_G1)
    assert _v(cube, A, "xg", "total", "xg_per_shot") == pytest.approx(XG_A / 3)
    assert _v(cube, A, "gf", "total", "counterpart") == pytest.approx(XG_A / N90_G1)
    assert _v(cube, A, "xg", "total", "counterpart") == pytest.approx(2.0 / N90_G1)
    assert _v(cube, A, "gf", "total", "placement") == pytest.approx((PSXG_A - XG_A) / N90_G1)
    assert _v(cube, A, "gf", "total", "beat_keeper") == pytest.approx((2 - PSXG_A) / N90_G1)
    # the chain closes: goals - xG = placement + beating the keeper
    assert (_v(cube, A, "gf", "total", "placement") + _v(cube, A, "gf", "total", "beat_keeper")
            == pytest.approx((2 - XG_A) / N90_G1))
    # the same links from the same rows on the xG headline
    assert _v(cube, A, "xg", "total", "placement") == _v(cube, A, "gf", "total", "placement")


def test_the_chain_against_side_and_own_goals(cube):
    # B faced A's three shots: placement faced +0.36, PSxG 1.50, conceded 2
    # -> stopping -0.50; the chain closes: GA - xGA = placement faced - stopping
    assert _v(cube, B, "ga", "total", "placement") == pytest.approx((PSXG_A - XG_A) / N90_G1)
    assert _v(cube, B, "ga", "total", "stopping") == pytest.approx((PSXG_A - 2) / N90_G1)
    assert (_v(cube, B, "ga", "total", "placement") - _v(cube, B, "ga", "total", "stopping")
            == pytest.approx((2 - XG_A) / N90_G1))
    assert _v(cube, B, "xga", "total", "counterpart") == pytest.approx(2.0 / N90_G1)
    # D conceded one goal - an own goal, which no shot's PSxG covers. The
    # keeper faced 0 PSxG and 0 goals from shots: stopping is 0, not -1.
    assert _v(cube, D, "ga", "total", "anchor") == pytest.approx(1.0 / N90_G2)
    assert _v(cube, D, "ga", "total", "stopping") == pytest.approx(0.0)
    g = tp.gauge(cube, (S, D), "ga", "total", "stopping", "rank")
    assert g.og == 1 and "excl. 1 own goal" in tp.format_total(g)
    # and the own goal is not C beating a keeper: C's goals from shots are 0
    assert _v(cube, C, "gf", "total", "beat_keeper") == pytest.approx(0.0)
    g = tp.gauge(cube, (S, C), "gf", "total", "beat_keeper", "rank")
    assert g.og == 1 and "excl. 1 own goal" in tp.format_total(g)
    # C's placement is its one blocked shot, charged its full 0.08
    assert _v(cube, C, "gf", "total", "placement") == pytest.approx(-0.08 / N90_G2)


def test_context_slot_resolves_by_situation(cube):
    assert tp.resolve_component("total", "context") == "counterpart"
    assert tp.resolve_component("op", "context") == "counterpart"
    assert tp.resolve_component("ahead", "context") == "minutes_pct"
    # A's open-play xG beside its open-play goals
    assert _v(cube, A, "gf", "op", "context") == pytest.approx((0.30 + 0.05) / N90_G1)
    assert _v(cube, A, "gf", "ahead", "context") == pytest.approx(4100 / 5700)
    # on a difference frame the counterpart is the other family's difference
    assert _v(cube, A, "gd", "total", "context") == pytest.approx((XG_A - 0.30) / N90_G1)
    # the share of the season sits on the phase anchor's line, not on a gauge
    g = tp.gauge(cube, (S, A), "gf", "op", "anchor", "rank")
    assert g.parent_total == 2 and tp.format_total(g) == "1 of 2 (50%)"
    g = tp.gauge(cube, (S, A), "gd", "op", "anchor", "rank")
    assert g.parent_total is None


def test_set_pieces_are_the_set_piece_context(cube):
    assert tp.resolve_component("sp", "context") == "set_pieces"
    # A: 5 corners + 2 attacking-third FKs + 1 box throw-in + 1 penalty = 9
    # taken; faced B's 3. Per 90 like everything else.
    assert _v(cube, A, "gf", "sp", "context") == pytest.approx(9 / N90_G1)
    assert _v(cube, A, "ga", "sp", "context") == pytest.approx(3 / N90_G1)
    assert _v(cube, B, "ga", "sp", "context") == pytest.approx(9 / N90_G1)   # incl. the pen faced
    assert _v(cube, A, "gd", "sp", "context") == pytest.approx(6 / N90_G1)
    labels = {hk: tp.view(cube, (S, A), hk, ("sp",), "situation")[1].label for hk in ("gf", "ga", "gd")}
    assert labels == {"gf": "Set Pieces Taken", "ga": "Set Pieces Faced", "gd": "Set Piece Differential"}
    assert tp.direction(tp.HEADLINES["ga"], "sp", "set_pieces") == -1
    g = tp.gauge(cube, (S, A), "gf", "sp", "context", "rank")
    assert g.fmt == "count" and tp.format_value(g) == "8.5" and g.unit == "per 90 min"
    assert tp.format_total(g) == "9 in 1 (0.11 shots per set piece)"
    g = tp.gauge(cube, (S, A), "gd", "sp", "context", "rank")
    assert g.fmt == "signed_int" and tp.format_total(g) == "+6 in 1"
    # the toggle takes the penalty out of the count too
    np_ = tp.build_cube(_games(), _shots(), _period_ends(), _restarts(), exclude_penalties=True)
    assert _v(np_, A, "gf", "sp", "context") == pytest.approx(8 / N90_G1)
    assert _v(np_, B, "ga", "sp", "context") == pytest.approx(8 / N90_G1)
    # no restart rows (an old fixture): the cell is NaN, the gauge shows a dash
    old = tp.build_cube(_games(), _shots(), _period_ends())
    assert math.isnan(_v(old, A, "gf", "sp", "context"))
    assert tp.gauge(old, (S, A), "gf", "sp", "context", "rank").standing.readout == "—"


def test_both_drill_orders_agree_cell_for_cell(cube):
    for hk in tp.HEADLINE_ORDER:
        h = tp.HEADLINES[hk]
        comps = tp.components_of(h)
        for sit in tp.SITUATION_ORDER:
            by_sit = {g.key: g for g in tp.view(cube, (S, A), hk, (sit,), "situation")}
            for c in comps:
                by_comp = {g.key: g for g in tp.view(cube, (S, A), hk, (c,), "component")}
                key = f"{hk}.{sit}.{tp.resolve_component('total', c) if c == 'context' else c}"
                if key in by_sit and key in by_comp:
                    a, b = by_sit[key], by_comp[key]
                    assert (a.value == b.value) or (math.isnan(a.value) and math.isnan(b.value)), key


def test_anchor_equals_the_gauge_one_level_up(cube):
    top = {g.key: g for g in tp.view(cube, (S, A), None)}
    for hk in tp.HEADLINE_ORDER:
        lvl2 = tp.view(cube, (S, A), hk, (), "situation")
        assert lvl2[0].value == top[f"{hk}.total.anchor"].value
        lvl3 = tp.view(cube, (S, A), hk, ("sp",), "situation")
        assert lvl3[0].value == lvl2[2].value


# -- standing ------------------------------------------------------------------------

def test_rank_and_percentile(cube):
    # per 90: A 2/1.056, C 1/1.028, B 1/1.056, D 0 - C's game was shorter
    values, _ = tp.cell(cube, "gf", "total", "anchor")
    st = tp.standing(values, (S, A), +1, "rank")
    assert st.readout == "1st/4" and st.needle == 1.0
    st = tp.standing(values, (S, C), +1, "rank")
    assert st.readout == "2nd/4" and not st.tied
    st = tp.standing(values, (S, D), +1, "rank")
    assert st.readout == "4th/4" and st.needle == 0.0
    # ties share the higher place and wear the mark
    tied = pd.Series({(S, A): 2.0, (S, B): 1.0, (S, C): 1.0, (S, D): 0.0})
    st = tp.standing(tied, (S, C), +1, "rank")
    assert st.readout == "2nd=/4" and st.tied
    # lower is better flips it
    st = tp.standing(values, (S, A), -1, "rank")
    assert st.readout == "4th/4" and st.needle == 0.0
    p = tp.standing(values, (S, A), +1, "pctl")
    assert p.position == pytest.approx(87.5) and p.readout == "88"
    assert tp.calculate_percentile(2, [2, 1, 0, 0]) == 87.5


def test_labels_are_names_and_carry_the_side(cube):
    against = [g.label for g in tp.view(cube, (S, A), "xga", (), "component")]
    assert against == ["xG Against", "Goals Against", "Shots Faced", "Chance Quality Faced",
                       "Placement Faced", "Shot-Stopping"]
    for_side = [g.label for g in tp.view(cube, (S, A), "gf", (), "component")]
    assert for_side == ["Goals For", "xG For", "Shots", "Chance Quality", "Shot Placement",
                        "Beating the Keeper"]
    diff = [g.label for g in tp.view(cube, (S, A), "gd", (), "component")]
    assert diff == ["Goal Difference", "xG Difference", "Goals For", "Goals Against",
                    "Shot Differential", "vs Expected"]
    # under a state the context slot is the time spent there, and the anchor
    # of a situation-first level 3 names its situation
    behind = [g.label for g in tp.view(cube, (S, A), "gf", ("behind",), "situation")]
    assert behind[:2] == ["Goals For When Behind", "Time Behind"]
    assert tp.view(cube, (S, A), "ga", ("sp",), "situation")[0].label == "Set-Piece Goals Against"
    assert tp.view(cube, (S, A), "xg", ("op",), "situation")[0].label == "Open-Play xG For"
    assert tp.view(cube, (S, A), "xg", ("total",), "situation")[0].label == "xG For"


def test_directions():
    assert tp.direction(tp.HEADLINES["ga"], "total", "anchor") == -1
    assert tp.direction(tp.HEADLINES["ga"], "total", "counterpart") == -1     # xGA
    assert tp.direction(tp.HEADLINES["ga"], "total", "shots") == -1
    # the strike is good for whoever struck it; the last link is good for
    # the team on both sides
    assert tp.direction(tp.HEADLINES["gf"], "total", "placement") == 1
    assert tp.direction(tp.HEADLINES["ga"], "total", "placement") == -1
    assert tp.direction(tp.HEADLINES["gf"], "total", "beat_keeper") == 1
    assert tp.direction(tp.HEADLINES["ga"], "total", "stopping") == 1
    assert tp.direction(tp.HEADLINES["xgd"], "total", "net") == 1
    # time in a state is context, never a verdict - on every situation
    assert tp.direction(tp.HEADLINES["xga"], "ahead", "minutes_pct") == 0
    assert tp.direction(tp.HEADLINES["xga"], "behind", "minutes_pct") == 0
    assert tp.direction(tp.HEADLINES["gd"], "total", "against") == -1
    assert tp.direction(tp.HEADLINES["xgd"], "total", "counterpart") == 1


# -- the penalties toggle --------------------------------------------------------------

def test_exclude_penalties_removes_exactly_the_penalty_rows():
    base = tp.build_cube(_games(), _shots(), _period_ends())
    np_ = tp.build_cube(_games(), _shots(), _period_ends(), exclude_penalties=True)
    assert _v(base, A, "gf", "total", "anchor") == pytest.approx(2.0 / N90_G1)
    assert _v(np_, A, "gf", "total", "anchor") == pytest.approx(1.0 / N90_G1)   # scoreline minus the pen
    assert _v(np_, B, "ga", "total", "anchor") == pytest.approx(1.0 / N90_G1)
    assert _v(np_, A, "xg", "total", "anchor") == pytest.approx((0.30 + 0.05) / N90_G1)
    assert _v(np_, A, "xg", "total", "shots") == pytest.approx(2.0 / N90_G1)
    assert _v(np_, A, "gf", "sp", "anchor") == 0.0
    # partition still holds with the pen gone
    assert (_v(np_, A, "gf", "op", "anchor") + _v(np_, A, "gf", "sp", "anchor")
            == pytest.approx(1.0 / N90_G1))
    # and with the pen IN, the set-piece line says it is there - inside the
    # one parenthesis the share already opened
    g = tp.gauge(base, (S, A), "gf", "sp", "anchor", "rank")
    assert g.pens == 1 and tp.format_total(g) == "1 of 2 (50%, 1 pen goal)"
    g = tp.gauge(base, (S, A), "xg", "sp", "anchor", "rank")
    assert g.pens == pytest.approx(0.79) and tp.format_total(g) == "0.8 of 1.1 (69%, 0.8 from pens)"
    g = tp.gauge(base, (S, A), "xg", "sp", "shots", "rank")
    assert tp.format_total(g) == "1 in 1 (1 pen taken)"
    g = tp.gauge(np_, (S, A), "gf", "sp", "anchor", "rank")
    assert g.pens is None
    # match STATE is untouched: the pen still put A 2-1 up for the clock
    assert np_.teams.loc[(S, A), "ahead_s"] == base.teams.loc[(S, A), "ahead_s"]


def test_format_helpers(cube):
    g = tp.gauge(cube, (S, A), "gf", "total", "anchor", "rank", "Goals For")
    assert tp.format_value(g) == "1.89" and tp.format_total(g) == "2 in 1"
    assert g.unit == "per 90 min"
    g = tp.gauge(cube, (S, A), "xg", "total", "xg_per_shot", "rank")
    assert tp.format_value(g) == "0.380" and g.unit == "xG per shot"
    # a state anchor says its share of the season's minutes
    g = tp.gauge(cube, (S, C), "gf", "level", "anchor", "rank")
    assert tp.format_total(g) == "1 in 91 min (98% of time)"
    assert g.unit == "per 90 min when drawing"
    g = tp.gauge(cube, (S, C), "gf", "level", "shots", "rank")
    assert tp.format_total(g) == "1 in 91 min"
    # the named links carry no formula - the name and the page's help do
    g = tp.gauge(cube, (S, A), "gf", "behind", "placement", "rank")
    assert g.unit == "per 90 min when behind" and g.fmt == "signed"
    # a real minus sign on negative numbers, everywhere a number prints
    g = tp.gauge(cube, (S, B), "ga", "total", "stopping", "rank")
    assert tp.format_value(g) == "−0.47" and g.unit == "per 90 min"
    assert tp.format_total(g) == "−0.5 in 1"
    # every difference is signed, integers on the goals family
    g = tp.gauge(cube, (S, A), "gd", "total", "anchor", "rank")
    assert g.fmt == "signed_int" and tp.format_value(g) == "+0.95" and tp.format_total(g) == "+1 in 1"
    g = tp.gauge(cube, (S, A), "xgd", "total", "anchor", "rank")
    assert g.fmt == "signed" and tp.format_value(g) == "+0.80" and tp.format_total(g) == "+0.8 in 1"
    g = tp.gauge(cube, (S, A), "gd", "total", "net", "rank")
    assert tp.format_value(g) == "+0.15" and g.label == "vs Expected"
    assert tp.format_number("signed", 0.04) == "+0.04"
    assert tp.format_number("xg", -0.06) == "−0.06"
    g = tp.gauge(cube, (S, A), "gf", "ahead", "context", "rank")
    assert tp.format_value(g) == "72%" and tp.format_total(g) == "68 min (72%)"
    assert g.label == "Time Ahead" and g.direction == 0
