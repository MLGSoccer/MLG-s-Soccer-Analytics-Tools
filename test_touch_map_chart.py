"""Tests for mostly_finished_charts/touch_map_chart.py.

The chart makes three claims a reader will act on, and each is pinned here:
the key ("each shade holds a tenth of the touches"), that the bands can be
told apart whatever the club colour, and that a compare frame's panels are
labelled from their own data. Synthetic touches only - no database.
Run: py -m pytest test_touch_map_chart.py -q
"""
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "mockups" / "aspect_variants"))

from mostly_finished_charts import touch_map_chart as tm
from layout_lint import lint


def _touches(n=2000, seed=3, games=36):
    """Two clusters (a half-space and a deep right-back zone) plus scatter,
    shaped like build_touch_map's rows."""
    rng = np.random.default_rng(seed)
    k = [int(n * 0.45), int(n * 0.35)]
    k.append(n - sum(k))
    x = np.concatenate([rng.normal(68, 8, k[0]), rng.normal(35, 7, k[1]), rng.uniform(5, 95, k[2])])
    y = np.concatenate([rng.normal(25, 8, k[0]), rng.normal(10, 5, k[1]), rng.uniform(2, 98, k[2])])
    g = rng.integers(0, games, n)
    return pd.DataFrame({
        "EventX": np.clip(x, 0, 100), "EventY": np.clip(y, 0, 100),
        "gameId": [f"g{i}" for i in g], "Date": "2026-02-08",
        "opponent_name": "Manchester City", "is_home": True, "team_score": 1, "opp_score": 2,
    })


INFO = {"team_name": "Liverpool", "total_matches": 36, "season_span": "2025/26",
        "date_range": "AUG 15 - MAY 24, 2026"}
CLUBS = ["#C8102E", "#132257", "#FFFFFF", "#241F20", "#018749", "#FDB913",
         "#6CABDD", "#034694", "#670E36", "#FFF200", "#888888", None]


# -- the key: each band is a tenth ------------------------------------------

def test_each_band_holds_a_tenth_of_the_smoothed_field():
    g, xs, ys = tm.density(_touches())
    lv = tm.band_levels(g)
    bounds = lv + [g.max() * 2]
    mass = [g[(g >= a) & (g < b)].sum() for a, b in zip(bounds[:-1], bounds[1:])]
    assert np.allclose(mass, 0.1, atol=0.005), mass
    assert g[g < lv[0]].sum() == pytest.approx(0.1, abs=0.005)       # the unpainted ground


def test_raw_touches_land_close_to_a_tenth_per_band():
    # Smoothing moves a point or two; the key's claim must survive that.
    shares = tm.band_shares(_touches())
    assert all(0.06 <= s <= 0.14 for s in shares), shares
    assert sum(shares) == pytest.approx(0.9, abs=0.03)


def test_levels_are_strictly_increasing_even_for_a_cameo():
    for n in (1, 3, 12):
        lv = tm.band_levels(tm.density(_touches(n=n, seed=n))[0])
        assert all(b > a for a, b in zip(lv, lv[1:])), (n, lv)


def test_the_kernel_widens_as_the_sample_shrinks_and_is_clamped():
    lo, hi = tm.SIGMA_RANGE_M
    s = lambda n: float(np.clip(tm.SIGMA_M * (tm.SIGMA_N / n) ** (1 / 6), lo, hi))
    assert s(60) > s(1400) > s(27000)
    assert s(3) == hi and s(10 ** 7) == lo


# -- the bands can be told apart ----------------------------------------------

@pytest.mark.parametrize("club", CLUBS)
def test_nine_bands_step_evenly_in_lightness_for_every_club_colour(club):
    L = [tm.lightness(c) for c in tm.band_colours(club)]
    steps = np.diff(L)
    assert len(L) == tm.N_BANDS
    assert steps.min() >= 5.5, (club, [round(v, 1) for v in L])      # dark -> light, never flat


# -- the frame ------------------------------------------------------------------

def test_compare_labels_come_from_each_panels_own_data():
    t = _touches()
    scope = t[t.gameId == "g0"].copy()
    other = t[t.gameId != "g0"]
    info = dict(INFO, total_matches=1, date_range="FEB 08, 2026")
    (lname, ldetail), (rname, rdetail) = tm.panel_labels_for(scope, other, info, "Premier League")
    assert "MANCHESTER CITY" in lname and "1-2" in lname
    assert ldetail.endswith(f"{len(scope):,} TOUCHES") and "FEB 08, 2026" in ldetail
    assert rname == f"HIS OTHER {other.gameId.nunique()} MATCHES"
    # the count, then the RATE: each panel is shaded against its own total, so
    # the rate is what lets a reader compare volume across the two
    rate = round(len(other) / other.gameId.nunique())
    assert rdetail == f"{len(other):,} TOUCHES{tm.SEP}{rate:,} A MATCH", rdetail
    assert "A MATCH" not in ldetail              # one match: the count IS the rate


def test_the_field_never_paints_outside_the_pitch():
    fig, ax = plt.subplots()
    cs = tm.draw_field(ax, _touches(), "#C8102E")
    for path in cs.get_paths():
        v = path.vertices
        if len(v):
            assert v[:, 0].min() >= 0 and v[:, 0].max() <= 100
            assert v[:, 1].min() >= 0 and v[:, 1].max() <= 100
    plt.close(fig)


@pytest.mark.parametrize("aspect", ["default", "9x16", "9x8"])
@pytest.mark.parametrize("mode", ["field", "marks", "compare", "team"])
def test_every_frame_renders_lint_clean(aspect, mode):
    t = _touches()
    kw = dict(touches=t, info=INFO, team_color="#C8102E", competition="Premier League",
              aspect=aspect, subject_name="Dominik Szoboszlai")
    if mode == "marks":
        kw["view"] = "marks"
    elif mode == "compare":
        kw.update(touches=t[t.gameId == "g0"], baseline=t[t.gameId != "g0"],
                  info=dict(INFO, total_matches=1, date_range="FEB 08, 2026"))
    elif mode == "team":
        kw.update(subject_name=None, pronoun="their")
    fig = tm.create_touch_map(**kw)
    bad = [f for f in lint(fig, delivery="laptop" if aspect == "default" else "phone")
           if not any(s in str(f) for s in ("CBS SPORTS", "DATA: OPTA"))]
    plt.close(fig)
    assert not bad, [str(b)[:140] for b in bad]


def test_the_module_is_ascii():
    data = (ROOT / "mostly_finished_charts" / "touch_map_chart.py").read_bytes()
    assert all(b < 128 for b in data)
