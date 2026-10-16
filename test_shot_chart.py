"""Unit tests for shot_chart pure functions.

Run: pytest test_shot_chart.py -v
"""
import pandas as pd
import pytest

from mostly_finished_charts.shot_chart import (
    GOAL_TYPES,
    PenStats,
    TeamGoalBreakdown,
    _lighten_hex,
    compute_pen_stats,
    compute_ylim_floor,
    ensure_bg_readable,
    ensure_pitch_contrast,
    reconcile_team_goals,
)


# ---------------------------------------------------------------------------
# reconcile_team_goals — the function that caused the OG/pen bugs

def _df(rows):
    """Tiny helper to build a shots_df from a list of dicts."""
    return pd.DataFrame(rows)


def test_reconcile_no_pen_no_og():
    """Simple match: 2 shot goals, final 2. No OG, no pen."""
    shots = _df([
        {'playType': 'Goal', 'xG': 0.3, 'ShotPlayStyle': 'Open play'},
        {'playType': 'Goal', 'xG': 0.15, 'ShotPlayStyle': 'Open play'},
        {'playType': 'Miss', 'xG': 0.08, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots, final_score=2,
        pen_stats=PenStats(shots=0, goals=0, xg=0.0),
        exclude_penalties=False,
    )
    assert result == TeamGoalBreakdown(
        shot_goals=2, own_goals=0, pen_shots=0, pen_goals=0, pen_xg=0.0
    )


def test_reconcile_with_og_no_pen():
    """Team has 1 shot goal + 1 OG benefiting them. Final 2."""
    shots = _df([
        {'playType': 'Goal', 'xG': 0.3, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots, final_score=2,
        pen_stats=PenStats(shots=0, goals=0, xg=0.0),
        exclude_penalties=False,
    )
    assert result.shot_goals == 1
    assert result.own_goals == 1
    assert result.pen_goals == 0


def test_reconcile_pen_filtered_out():
    """Team scored 1 pen goal (only goal). Filter removed it from shots_df.
    Final score 1. Old bug: would count 1 OG. Correct: 0 OG, 1 pen noted.
    """
    shots_filtered = _df([  # pen removed upstream
        {'playType': 'Miss', 'xG': 0.08, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots_filtered, final_score=1,
        pen_stats=PenStats(shots=1, goals=1, xg=0.76),
        exclude_penalties=True,
    )
    assert result.shot_goals == 0
    assert result.own_goals == 0, "Filtered pen goal must NOT be counted as OG"
    assert result.pen_goals == 1
    assert result.pen_xg == pytest.approx(0.76)


def test_reconcile_pen_plus_og_filtered():
    """Team has 1 pen goal + 1 OG benefiting them. Final 2. Filter on.
    Old double-counting bug: would show 2 OG. Correct: 1 OG + 1 pen noted.
    """
    shots_filtered = _df([
        {'playType': 'Miss', 'xG': 0.05, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots_filtered, final_score=2,
        pen_stats=PenStats(shots=1, goals=1, xg=0.76),
        exclude_penalties=True,
    )
    assert result.shot_goals == 0
    assert result.own_goals == 1
    assert result.pen_goals == 1


def test_reconcile_pen_included_no_filter():
    """No filter — shot_goals already includes pen goal. OG correction is 0."""
    shots = _df([
        {'playType': 'Goal', 'xG': 0.76, 'ShotPlayStyle': 'Penalty'},
        {'playType': 'Miss', 'xG': 0.08, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots, final_score=1,
        pen_stats=PenStats(shots=1, goals=1, xg=0.76),
        exclude_penalties=False,
    )
    assert result.shot_goals == 1
    assert result.own_goals == 0  # score matches shot count, no OG


def test_reconcile_own_goals_never_negative():
    """If data is messed up (shot goals exceed final score), OG clamps to 0."""
    shots = _df([
        {'playType': 'Goal', 'xG': 0.3, 'ShotPlayStyle': 'Open play'},
        {'playType': 'Goal', 'xG': 0.2, 'ShotPlayStyle': 'Open play'},
    ])
    result = reconcile_team_goals(
        shots, final_score=1,  # less than shot goals — bad data
        pen_stats=PenStats(shots=0, goals=0, xg=0.0),
        exclude_penalties=False,
    )
    assert result.own_goals == 0  # clamped, not negative


# ---------------------------------------------------------------------------
# compute_pen_stats

def test_compute_pen_stats_empty_df():
    result = compute_pen_stats(pd.DataFrame())
    assert result == PenStats(shots=0, goals=0, xg=0.0)


def test_compute_pen_stats_no_shotplaystyle_column():
    shots = _df([{'playType': 'Goal', 'xG': 0.3}])
    result = compute_pen_stats(shots)
    assert result == PenStats(shots=0, goals=0, xg=0.0)


def test_compute_pen_stats_one_pen_scored():
    shots = _df([
        {'playType': 'Goal', 'xG': 0.76, 'ShotPlayStyle': 'Penalty'},
        {'playType': 'Miss', 'xG': 0.05, 'ShotPlayStyle': 'Open play'},
    ])
    result = compute_pen_stats(shots)
    assert result['shots'] == 1
    assert result['goals'] == 1
    assert result['xg'] == pytest.approx(0.76)


def test_compute_pen_stats_pen_missed():
    shots = _df([
        {'playType': 'Miss', 'xG': 0.76, 'ShotPlayStyle': 'Penalty'},
    ])
    result = compute_pen_stats(shots)
    assert result['shots'] == 1
    assert result['goals'] == 0


def test_compute_pen_stats_multiple_pens():
    shots = _df([
        {'playType': 'Goal', 'xG': 0.76, 'ShotPlayStyle': 'Penalty'},
        {'playType': 'AttemptSaved', 'xG': 0.76, 'ShotPlayStyle': 'Penalty'},
        {'playType': 'Goal', 'xG': 0.12, 'ShotPlayStyle': 'Open play'},
    ])
    result = compute_pen_stats(shots)
    assert result['shots'] == 2
    assert result['goals'] == 1
    assert result['xg'] == pytest.approx(1.52)


# ---------------------------------------------------------------------------
# Color helpers

def test_ensure_pitch_contrast_black_passes_through():
    """Black has enough distance from dark green — should not shift."""
    assert ensure_pitch_contrast('#000000') == '#000000'


def test_ensure_pitch_contrast_normal_color_passes_through():
    assert ensure_pitch_contrast('#EF0107') == '#EF0107'  # Arsenal red
    assert ensure_pitch_contrast('#0066B2') == '#0066B2'  # Bayern blue


def test_ensure_pitch_contrast_green_team_lightens():
    """Werder Bremen green blends with pitch — must be lightened."""
    result = ensure_pitch_contrast('#006633')
    assert result != '#006633'  # must have shifted


def test_ensure_bg_readable_bright_color_passes_through():
    assert ensure_bg_readable('#FF0000') == '#FF0000'


def test_ensure_bg_readable_very_dark_lightens():
    """Black text on dark navy is unreadable — must be lightened."""
    result = ensure_bg_readable('#000000')
    assert result != '#000000'


def test_lighten_hex_clamps_at_255():
    """Near-white should not overflow when lightened."""
    result = _lighten_hex('#F0F0F0', amount=100)
    # Should still be a valid hex, not overflow past FF
    assert result.startswith('#')
    assert len(result) == 7


def test_lighten_hex_black_by_60():
    assert _lighten_hex('#000000', amount=60) == '#3C3C3C'


# ---------------------------------------------------------------------------
# compute_ylim_floor

def test_ylim_floor_empty_df_returns_default():
    assert compute_ylim_floor(pd.DataFrame()) == 60


def test_ylim_floor_all_shots_near_goal():
    """All shots in the attacking third — crop at default floor."""
    shots = _df([
        {'EventX': 85, 'EventY': 50},
        {'EventX': 92, 'EventY': 60},
    ])
    assert compute_ylim_floor(shots) == 60


def test_ylim_floor_shot_near_halfway_expands():
    """A long-range shot near the halfway line should expand the floor down."""
    shots = _df([
        {'EventX': 55, 'EventY': 50},  # well below default floor of 60
        {'EventX': 90, 'EventY': 60},
    ])
    floor = compute_ylim_floor(shots)
    assert floor < 60, "Floor should expand when shot lives below default"
    assert floor >= 50, "Floor should never go below halfway"


def test_ylim_floor_respects_flip():
    """When flip_coords=True, EventX values are mirrored (x = 100 - x)."""
    shots = _df([{'EventX': 10, 'EventY': 50}])  # flips to 90
    assert compute_ylim_floor(shots, flip_coords=True) == 60


def test_no_loop_overwrites_a_parameter():
    """No loop in shared/motherduck.py may rebind a parameter that is read
    after it. build_shot_chart_multi unpacked each row's teamId into
    `team_id` - its own parameter, the subject - so every season
    shots-against map took the name and colour of the subject's last
    opponent (Gotham read "Angel City", 2026-10-16)."""
    import ast
    from pathlib import Path
    src = (Path(__file__).parent / "shared" / "motherduck.py").read_text(encoding="utf-8")
    bad = []
    for fn in [n for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for loop in [n for n in ast.walk(fn) if isinstance(n, ast.For)]:
            rebound = {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)} & params
            read_after = {n.id for n in ast.walk(fn)
                          if isinstance(n, ast.Name) and n.id in rebound
                          and isinstance(n.ctx, ast.Load) and n.lineno > loop.end_lineno}
            if read_after:
                bad.append(f"{fn.name}: loop at line {loop.lineno} rebinds {sorted(read_after)}")
    assert not bad, bad
