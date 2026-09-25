"""Touch Map - Streamlit page.

SCOPE picks the games and the subject (a player, or the whole team) and runs in
SQL. TOUCHES picks which kinds of touch count, from shared.touch_types - the
same registry that writes the chart's deck line, so the sidebar and the chart
cannot describe two different selections. Every touch belongs to exactly one
checkbox, so the counts beside them always add up to the total on the chart.

One cached read per club-season: the page asks build_touch_map for the union
of the selected seasons' games and the comparison window's, then splits scope
and baseline in pandas. Stepping through matches, toggling the comparison or
changing the touch types never reads the database again.
"""
import datetime as dt
import os
import sys
import tempfile

import streamlit as st
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR
from shared.motherduck import (
    get_teams_by_league, get_games_for_team, season_label, build_touch_map,
    touch_map_info, season_competitions, get_player_full_names,
    is_league_season, is_womens_competition,
)
from shared import touch_types as tt
from mostly_finished_charts.touch_map_chart import create_touch_map
from pages.streamlit_utils import custom_title_inputs

st.set_page_config(page_title="Touch Map", page_icon=":material/sports_soccer:",
                   layout="wide")
st.title("Touch Map")
st.caption("Where a player, or a team, touched the ball.")

MIN_BASELINE_MATCHES = 3


@st.cache_data(show_spinner=False)
def _annotated(fetch_ids, team_id):
    """build_touch_map's rows, classified. Cached on the same key as the read."""
    df, info, colour = build_touch_map(fetch_ids, team_id)
    return (tt.annotate(df) if not df.empty else df), info, colour


def _day(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# -- Scope: which games ---------------------------------------------------------

try:
    teams_by_league = get_teams_by_league()
except Exception as exc:
    st.error(f"Could not connect to database: {exc}")
    st.stop()

c1, c2, c3 = st.columns(3)
with c1:
    league = st.selectbox("League", options=[""] + list(teams_by_league))
with c2:
    if league:
        options = teams_by_league[league]
        name = st.selectbox("Team", options=[""] + [t['display_name'] for t in options])
        team = next((t for t in options if t['display_name'] == name), None)
    else:
        st.selectbox("Team", options=[], disabled=True)
        team = None
with c3:
    mode = st.selectbox("Scope", options=["Season", "Single match", "Last N matches"],
                        disabled=not team)

if not team:
    st.info("Pick a league and a team to begin.")
    st.stop()

games = get_games_for_team(team['team_id'])
if not games:
    st.warning("No games found for this team.")
    st.stop()

seasons = {g['season_id']: season_label(g['season_id'], g.get('season_name'))
           for g in games if g.get('season_id')}
labels = list(seasons.values())
# The DOMESTIC LEAGUE by default (user, 2026-09-25). The games arrive newest
# first, so the pass map's default - the newest season - is a cup whenever the
# club's last match was one. Cups are one click away.
league_first = [sid for sid in seasons if is_league_season(sid)]
default = [seasons[league_first[0]]] if league_first else labels[:1]
s1, s2 = st.columns([2, 3])
with s1:
    picked = st.multiselect(
        "Season / competition", options=labels, default=default,
        help="The domestic league by default. Add a cup to put its matches on "
             "the same map.")
season_ids = [k for k, v in seasons.items() if v in picked]
if not season_ids:
    st.info("Pick at least one season.")
    st.stop()
in_season = [g for g in games if g.get('season_id') in season_ids]

if mode == "Single match":
    with s2:
        label = st.selectbox("Match", options=[""] + [g['label'] for g in in_season])
    scope_games = [g for g in in_season if g['label'] == label]
elif mode == "Last N matches":
    with s2:
        n_last = st.slider("How many of the most recent matches", 1,
                           max(len(in_season), 1), min(5, len(in_season)))
    scope_games = in_season[:n_last]
else:
    scope_games = in_season
if not scope_games:
    st.info("Pick a match.")
    st.stop()
scope_ids = {g['game_id'] for g in scope_games}

# -- View and the comparison window ----------------------------------------------

st.sidebar.header("View")
view = st.sidebar.radio("Show", ["Field", "Marks"], horizontal=True,
                        help="Field: nine shades, each holding a tenth of the "
                             "touches. Marks: one dot per touch.")
compare = st.sidebar.toggle("Compare with the other matches", value=False)

season_days = [d for d in (_day(g['date']) for g in in_season) if d]
window = (min(season_days), max(season_days)) if season_days else (None, None)
include_others = False
dated = False
if compare:
    basis = st.sidebar.radio("Other matches from", ["The rest of the season", "Choose dates"],
                             help="The rest of the season by default. Dates can "
                                  "span more than one season.")
    if basis == "Choose dates":
        dated = True
        all_days = [d for d in (_day(g['date']) for g in games) if d]
        picked_dates = st.sidebar.date_input(
            "From - to", value=window, min_value=min(all_days), max_value=max(all_days))
        if isinstance(picked_dates, (list, tuple)) and len(picked_dates) == 2:
            window = tuple(picked_dates)
    include_others = st.sidebar.checkbox(
        "Include other competitions", value=False,
        help="League matches only by default. Tick to add the cups in the same "
             "window.")

base_games = []
if compare and window[0]:
    base_games = [g for g in games
                  if g['game_id'] not in scope_ids
                  and _day(g['date']) and window[0] <= _day(g['date']) <= window[1]
                  and (include_others or is_league_season(g.get('season_id')))]
base_ids = {g['game_id'] for g in base_games}

fetch = tuple(sorted({g['game_id'] for g in in_season} | scope_ids | base_ids))
with st.spinner("Loading touches..."):
    rows, info_all, team_color = _annotated(fetch, team['team_id'])
if rows is None or rows.empty:
    st.warning("No touches found for that scope.")
    st.stop()
team_name = info_all.get('team_name') or team['display_name']

# -- Subject: a player, or the whole team ------------------------------------------

scope_all = rows[rows['gameId'].isin(scope_ids)]
by_player = (scope_all[scope_all['has_toucher']]
             .groupby(['player_id', 'player']).size().sort_values(ascending=False))
names, ids = [], {}
for (pid, pname), n in by_player.items():
    label = pname if pname not in ids else f"{pname} ({n:,})"
    names.append(label)
    ids[label] = pid
player = st.selectbox("Player (leave empty for the whole team)", options=[""] + names)
pid = ids.get(player)

scope_rows = scope_all if pid is None else scope_all[scope_all['player_id'] == pid]
base_rows = rows[rows['gameId'].isin(base_ids)]
if pid is not None:
    base_rows = base_rows[base_rows['player_id'] == pid]

# -- Touches: which kinds count -----------------------------------------------------

st.sidebar.header("Touches")
present = set(scope_rows['touch_type']) | set(base_rows['touch_type'])
n_now = tt.counts(scope_rows)


def _set_group(members, key):
    on = st.session_state[key]
    for tid in members:
        st.session_state[f"tm_t_{tid}"] = on


for t in tt.TYPES:
    st.session_state.setdefault(f"tm_t_{t.id}", t.default)
for g in tt.GROUPS:
    members = [t for t in tt.TYPES if t.group == g
               and (t.id in present or (g == tt.EXTRAS and t.id != 'other'))]
    if not members:
        continue
    with st.sidebar.expander(g, expanded=False):
        gkey = f"tm_grp_{g}"
        st.session_state[gkey] = all(st.session_state[f"tm_t_{t.id}"] for t in members)
        st.checkbox(f"All {g.lower()}", key=gkey, on_change=_set_group,
                    args=([t.id for t in members], gkey))
        for t in members:
            st.checkbox(f"{t.label} ({n_now.get(t.id, 0):,})", key=f"tm_t_{t.id}",
                        help=t.note)

chosen = [t.id for t in tt.TYPES if st.session_state.get(f"tm_t_{t.id}")]
_present = set(scope_rows['touch_type'])
st.sidebar.caption(tt.describe(chosen, present=_present) or "TruMedia's standard touch count.")
# The chart's deck: exclusions only - the count already names any extras.
filter_text = tt.describe(chosen, present=_present, extras=False)

shown = tt.select(scope_rows, chosen)
baseline = None
baseline_name = None
if compare:
    base_shown = tt.select(base_rows, chosen)
    n_base = int(base_shown['gameId'].nunique()) if not base_shown.empty else 0
    if n_base < MIN_BASELINE_MATCHES:
        st.info(f"Only {n_base} other match{'es' if n_base != 1 else ''} in that window - "
                f"the comparison needs {MIN_BASELINE_MATCHES}.")
    else:
        baseline = base_shown
        who = 'THEIR' if pid is None else (
            'HER' if is_womens_competition(season_ids, team_name) else 'HIS')
        suffix = []
        if dated:
            suffix.append(f"{window[0]:%b %Y} - {window[1]:%b %Y}".upper())
        if include_others:
            suffix.append("ALL COMPETITIONS")
        if suffix:
            baseline_name = f"{who} OTHER {n_base} MATCHES, " + ", ".join(suffix)

if shown.empty:
    st.warning("No touches of those kinds in this scope.")
    st.stop()

# -- Chart -------------------------------------------------------------------------

subject_name = None
if pid is not None:
    subject_name = get_player_full_names((pid,)).get(pid) or player
pronoun = 'their' if pid is None else (
    'her' if is_womens_competition(season_ids, team_name) else 'his')

st.sidebar.header("Chart")
aspect_choice = st.sidebar.radio(
    "Aspect ratio", options=["Standard (16:9)", "Vertical (9:16)", "Tile (9:8)"], index=0)
aspect = {"Vertical (9:16)": "9x16", "Tile (9:8)": "9x8"}.get(aspect_choice, "default")
competition = st.sidebar.text_input("Competition name",
                                    value=season_competitions(season_ids) or league or "")
title, subtitle = custom_title_inputs(
    "touch_map", (subject_name or team_name).upper(), "")

info = touch_map_info(shown, team_name)
fig = create_touch_map(shown, info, team_color, view=view.lower(), baseline=baseline,
                       baseline_name=baseline_name, subject_name=subject_name,
                       pronoun=pronoun, competition=competition,
                       filter_text=filter_text, custom_title=title,
                       custom_subtitle=subtitle, aspect=aspect)
# bbox_inches=None: st.pyplot's default 'tight' crops the figure - see the pass
# map page for the measurement.
st.pyplot(fig, width=('stretch' if aspect == 'default' else 'content'),
          bbox_inches=None)

slug = (subject_name or team_name).replace(' ', '_').replace('/', '-')
name = f"touch_map_{slug}{'' if aspect == 'default' else '_' + aspect}.png"
path = os.path.join(tempfile.gettempdir(), name)
fig.savefig(path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
with open(path, 'rb') as fh:
    st.download_button("Download PNG (300 dpi)", fh, name, "image/png")
plt.close(fig)
