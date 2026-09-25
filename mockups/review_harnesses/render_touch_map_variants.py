"""Render the Touch Map review set from the fixtures (no touch reads).

Fixtures come from cache_touch_map_fixtures.py - the builder's own rows. This
script classifies them with shared.touch_types (as the page does), cuts the
subjects the critique needs, and renders each at the aspects given, plus a
phone-size copy (400px wide - how the frame is seen on a phone) for the viewer
lens. Player full names are the one lookup (a tiny read, as the page makes).

    py mockups/review_harnesses/render_touch_map_variants.py [default 9x16 9x8]
"""
import os
import pickle
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mockups", "aspect_variants"))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

from shared import touch_types as tt  # noqa: E402
from shared.motherduck import touch_map_info, get_player_full_names  # noqa: E402
from mostly_finished_charts.touch_map_chart import create_touch_map  # noqa: E402
from layout_lint import lint  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "touch_map_fixtures")
OUT = os.path.join(os.path.expanduser("~"), "Downloads", "touch_map", "review")
PHONE = os.path.join(OUT, "_phone")

SZOB = "avh60ensgiwz1qalvn1bbqkfd"
MCI = "3ragu84a8rxxqwkao5j5m4mqc"          # 8 Feb 2026: deep and wide right
CHE = "1pow6zy0g8vyp6ag9wvnubkes"          # 4 Oct 2025: left in the first half, right in the second


def load(name):
    with open(os.path.join(FIX, f"{name}.pkl"), "rb") as fh:
        d = pickle.load(fh)
    d["rows"] = tt.annotate(d["df"])
    return d


def std(df, extra=()):
    return tt.select(df, list(tt.STANDARD) + list(extra))


def cases():
    lfc, swa, che = load("lfc_pl2526"), load("swa_ch2526"), load("che_wsl")
    r = lfc["rows"]
    sz = r[r.player_id == SZOB]
    # the cameo: the Liverpool player-match with the fewest standard touches above 8
    pm = std(r).groupby(["player_id", "gameId"]).size()
    cam_pid, cam_game = pm[pm >= 8].sort_values().index[0]
    alisson = (std(r)[std(r).touch_type.isin(["save", "claim", "punch"])]
               .groupby("player_id").size().idxmax())
    top_che = std(che["rows"]).groupby("player_id").size().idxmax()
    che_game = che["rows"][che["rows"].player_id == top_che]["gameId"].value_counts().index[-1]
    names = get_player_full_names((SZOB, cam_pid, alisson, top_che))

    def player(d, pid, games=None, exclude=None):
        rows = d["rows"][d["rows"].player_id == pid]
        if games is not None:
            rows = rows[rows.gameId.isin(games)]
        if exclude is not None:
            rows = rows[~rows.gameId.isin(exclude)]
        return rows

    L = lfc
    yield ("szob_season", L, std(sz), dict(subject_name=names[SZOB]))
    yield ("szob_season_no_restarts", L,
           tt.select(sz, [t for t in tt.STANDARD if t not in ("corner", "kickoff")]),
           dict(subject_name=names[SZOB],
                filter_text=tt.describe([t for t in tt.STANDARD if t not in ("corner", "kickoff")])))
    yield ("szob_mci_compare", L, std(player(L, SZOB, [MCI])),
           dict(subject_name=names[SZOB], baseline=std(player(L, SZOB, exclude=[MCI]))))
    yield ("szob_che_match", L, std(player(L, SZOB, [CHE])), dict(subject_name=names[SZOB]))
    yield ("szob_che_marks", L, std(player(L, SZOB, [CHE])),
           dict(subject_name=names[SZOB], view="marks"))
    yield ("cameo", L, std(player(L, cam_pid, [cam_game])), dict(subject_name=names[cam_pid]))
    yield ("cameo_marks", L, std(player(L, cam_pid, [cam_game])),
           dict(subject_name=names[cam_pid], view="marks"))
    yield ("keeper_season", L, std(player(L, alisson)), dict(subject_name=names[alisson]))
    yield ("lfc_team", L, std(r), dict(pronoun="their"))
    yield ("szob_carries", L, tt.select(sz, list(tt.STANDARD) + ["carry_start"]),
           dict(subject_name=names[SZOB], filter_text=tt.describe(list(tt.STANDARD) + ["carry_start"], extras=False)))
    yield ("swa_team", swa, std(swa["rows"]), dict(pronoun="their"))
    yield ("che_player_compare", che, std(player(che, top_che, [che_game])),
           dict(subject_name=names[top_che], pronoun="her",
                baseline=std(player(che, top_che, exclude=[che_game]))))


def main(aspects):
    os.makedirs(PHONE, exist_ok=True)
    total = 0
    for name, d, touches, kw in cases():
        info = touch_map_info(touches, d["info"]["team_name"])
        comp = {"lfc": "Premier League", "swa": "Championship", "che": "WSL"}[
            [k for k in ("lfc", "swa", "che") if d["season_id"] and k][0]
            if False else ("lfc" if "Liverpool" in info["team_name"] else
                           "swa" if "Swansea" in info["team_name"] else "che")]
        for aspect in aspects:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fig = create_touch_map(touches, info, d["team_color"], competition=comp,
                                       aspect=aspect, **kw)
            fn = f"{name}_{aspect}.png"
            fig.savefig(os.path.join(OUT, fn), dpi=100, facecolor=fig.get_facecolor(),
                        bbox_inches=None)
            bad = [f for f in lint(fig, delivery="laptop" if aspect == "default" else "phone")
                   if not any(s in str(f) for s in ("CBS SPORTS", "DATA: OPTA"))]
            total += len(bad)
            plt.close(fig)
            im = Image.open(os.path.join(OUT, fn))
            im.resize((400, round(im.height * 400 / im.width)), Image.LANCZOS).save(
                os.path.join(PHONE, fn))
            print(f"{fn:36s} n={len(touches):6,d} lint={len(bad)} "
                  f"{[str(b)[:90] for b in bad[:2]]}")
    print("lint findings:", total, "->", OUT)


if __name__ == "__main__":
    main(sys.argv[1:] or ["default", "9x16", "9x8"])
