"""Pull Touch Map fixtures ONCE, through the real builder, and pickle them.

Reads PRODUCTION: one build_touch_map per club-season (every event with a
toucher, plus the opt-in extras' rows - about 30k rows for a Premier League
season). Skips any fixture whose pickle already exists; delete the file to
re-pull one.

    py mockups/review_harnesses/cache_touch_map_fixtures.py

Fixtures land in mockups/review_harnesses/touch_map_fixtures/ as the
builder's own (df, info, team_color) - the same objects the page hands the
chart - and are consumed by render_touch_map_variants.py.
"""
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from shared.motherduck import build_touch_map, get_games_for_team  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "touch_map_fixtures")

# (name, team_id, season_id) - a Premier League, a Championship and a WSL
# season: three different feeds, three different colour-resolution paths.
FIXTURES = [
    ("lfc_pl2526", "c8h9bw1l82s06h77xxrelzhur", "51r6ph2woavlbbpk8f29nynf8"),
    ("swa_ch2526", "410jti6axb01yhvbc0axsp8li", "bmmk637l2a33h90zlu36kx8no"),
    ("che_wsl", "spi3g782q7m4evjxn1sv6cfa", "221phckhkd7y6rg3uyava3ifo"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, team_id, season_id in FIXTURES:
        path = os.path.join(OUT, f"{name}.pkl")
        if os.path.exists(path):
            print(f"skip  {name} (exists)")
            continue
        games = [g['game_id'] for g in get_games_for_team(team_id)
                 if g.get('season_id') == season_id]
        df, info, colour = build_touch_map(tuple(games), team_id)
        with open(path, "wb") as fh:
            pickle.dump({"df": df, "info": info, "team_color": colour,
                         "team_id": team_id, "season_id": season_id}, fh)
        print(f"pull  {name}: {len(games)} games, {len(df):,} rows, "
              f"{int(df['has_toucher'].sum()):,} touches, colour {colour}, "
              f"team {info.get('team_name')!r}")


if __name__ == "__main__":
    main()
