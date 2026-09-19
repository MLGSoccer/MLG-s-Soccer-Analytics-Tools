"""Tests for the Team Profile's click-on-image layer (shared/gauge_click.py).

The page lays one region over each gauge's grid cell and a click returns the
gauge's key. The regions come off the same figure the PNG was saved from,
so the tests check that mapping end to end: every gauge's axes sits inside
its region, the regions tile the grid without overlap, the top-left flip is
right, and the saved PNG has ink inside every region (the figure-fraction
to image-fraction identity that `bbox_inches=None` guarantees).
Run: py -m pytest test_team_profile_click.py -q
"""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

from shared import team_profile as tp
from shared.gauge_click import regions_from_boxes
from shared.styles import BG_COLOR
from mostly_finished_charts.team_profile_chart import create_team_profile
import test_team_profile as fx


def _profile():
    cube = tp.build_cube(fx._games(), fx._shots(), fx._period_ends(), fx._restarts())
    return {
        "cube": cube, "subject": (fx.S, fx.A), "team_name": "Team A",
        "team_color": "#C8102E", "season_label": "2025/26", "season_years": "2025/26",
        "competition": "Test League", "pool_mode": "rank", "pool_label": "Test League",
        "pool_n": 4, "season_ids": (fx.S,), "exclude_penalties": False, "checks": {},
    }


FRAMES = [
    (None, (), "situation"),                 # level 1
    ("gf", (), "situation"),                 # level 2 by situation
    ("ga", (), "component"),                 # level 2 by component, against side
    ("gd", ("behind",), "situation"),        # level 3
    ("xgd", ("net",), "component"),          # level 3, component first
]


@pytest.mark.parametrize("aspect", ["default", "9x16", "9x8"])
@pytest.mark.parametrize("headline,path,order", FRAMES)
def test_regions_are_the_cells_the_reader_sees(aspect, headline, path, order):
    prof = _profile()
    fig = create_team_profile(prof, headline=headline, path=path, order=order, aspect=aspect)
    regions = regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs, prof["pool_mode"])
    assert len(regions) == 6
    assert [r["key"] for r in regions] == [s.key for s in fig.tp_specs]
    assert all(r["label"].startswith(s.label) for r, s in zip(regions, fig.tp_specs))

    # Each gauge's axes (matplotlib figure fraction, origin bottom-left) lies
    # inside its region (image fraction, origin top-left).
    gauges = [ax for ax in fig.axes if ax.get_aspect() == 1.0]
    assert len(gauges) == 6
    for ax, r in zip(gauges, regions):
        bb = ax.get_position()
        top, bottom = 1.0 - bb.y1, 1.0 - bb.y0
        assert r["x"] - 1e-9 <= bb.x0 and bb.x1 <= r["x"] + r["w"] + 1e-9, (r, bb)
        assert r["y"] - 1e-9 <= top and bottom <= r["y"] + r["h"] + 1e-9, (r, bb)

    # The grid: inside the frame, no two regions overlap, reading order runs
    # left to right then down.
    for r in regions:
        assert 0 <= r["x"] and r["x"] + r["w"] <= 1 + 1e-9
        assert 0 <= r["y"] and r["y"] + r["h"] <= 1 + 1e-9
    for i, a in enumerate(regions):
        for b in regions[i + 1:]:
            dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert dx <= 1e-9 or dy <= 1e-9, (a, b)
    for a, b in zip(regions, regions[1:]):
        assert (b["y"] > a["y"] + 1e-9) or (abs(b["y"] - a["y"]) < 1e-9 and b["x"] > a["x"])

    # The saved PNG has ink inside every region: the figure fraction IS the
    # image fraction. Ink alone would not catch a wrong flip (the header is
    # ink too), so the frame's first ink row - the kicker - must also sit
    # ABOVE the top row of regions.
    buf = io.BytesIO()
    fig.savefig(buf, dpi=60, facecolor=BG_COLOR, edgecolor="none", format="png")
    plt.close(fig)
    img = np.asarray(Image.open(buf).convert("RGB"))
    H, W = img.shape[:2]
    bg = np.array([int(BG_COLOR[i:i + 2], 16) for i in (1, 3, 5)])
    for r in regions:
        x0, x1 = int(r["x"] * W), int((r["x"] + r["w"]) * W)
        y0, y1 = int(r["y"] * H), int((r["y"] + r["h"]) * H)
        crop = img[y0:y1, x0:x1]
        ink = np.any(np.abs(crop.astype(int) - bg).sum(axis=2) > 30, axis=None)
        assert ink, r
    first_ink_row = int(np.argmax(np.abs(img.astype(int) - bg).sum(axis=2).max(axis=1) > 30))
    assert first_ink_row < int(regions[0]["y"] * H)


def test_click_keys_map_to_the_drill():
    """What the page does with a returned key, per level and order."""
    prof = _profile()
    fig = create_team_profile(prof, headline=None, path=(), order="situation")
    keys = [r["key"] for r in regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs)]
    plt.close(fig)
    assert [k.split(".")[0] for k in keys] == list(tp.HEADLINE_ORDER)

    fig = create_team_profile(prof, headline="gf", path=(), order="situation")
    keys = [r["key"] for r in regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs)]
    plt.close(fig)
    assert [k.split(".")[1] for k in keys] == list(tp.SITUATION_ORDER)

    fig = create_team_profile(prof, headline="gf", path=(), order="component")
    keys = [r["key"] for r in regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs)]
    plt.close(fig)
    # Every dial is fixed per frame now - no context slot to resolve - so
    # the key carries the component name the page drills with directly.
    comps = [k.split(".")[2] for k in keys]
    assert comps == ["anchor", "shots", "shot_dist", "on_target_pct", "blocked_pct", "missed_pct"]
    assert comps == list(tp.components_of(tp.HEADLINES["gf"]))
    l3 = tp.view(prof["cube"], prof["subject"], "gf", ("on_target_pct",), "component", "rank")
    assert [s.key.split(".")[1] for s in l3] == list(tp.SITUATION_ORDER)


def test_pctl_label_says_so():
    prof = _profile()
    fig = create_team_profile(prof, headline=None, path=(), order="situation")
    rank = regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs, "rank")
    pctl = regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs, "pctl")
    plt.close(fig)
    assert not rank[0]["label"].endswith("pctl")
    assert pctl[0]["label"].endswith("pctl")
