"""xG Breakdown - Streamlit page (the path chart, "Active minutes: xG").

One club, one league season, against that league: active minutes in
possession, active minutes per shot and xG per shot for the club and for its
opponents, each against the league average, ending on xG difference.

The stat is built from the event log (shared/active_minutes.py). The league
averages need every team in the competition, so the page reads the whole
league season once - one cached read per season, in-process with an expiry.
Switching club within that season, or switching aspect, never reads again.
"""
import os
import sys
import tempfile

import streamlit as st
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR
from shared.motherduck import get_teams_by_league, get_games_for_team, season_label, is_league_season
from shared.active_minutes import build_active_minutes
from mostly_finished_charts.active_minutes_chart import create_active_minutes_chart

st.set_page_config(page_title="xG Breakdown", page_icon=":material/sports_soccer:", layout="wide")
st.title("xG Breakdown")
st.caption("A club and its opponents against the league: active minutes with the ball, minutes per shot, "
           "xG per shot, to xG difference.")

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

if not team:
    st.info("Pick a league and a team to begin.")
    st.stop()

games = get_games_for_team(team['team_id'])
# League seasons only: the comparison is the league, and a cup has no league table to stand against.
seasons = {}
for g in games:
    sid = g.get('season_id')
    if sid and sid not in seasons and is_league_season(sid):
        seasons[sid] = season_label(sid, g.get('season_name'))
if not seasons:
    st.warning("No league seasons for this team.")
    st.stop()
with c3:
    sid = st.selectbox("Season", options=list(seasons), format_func=seasons.get)   # newest first

with st.spinner("Reading the season..."):
    table, n_games = build_active_minutes(sid)
if table.empty or team['team_id'] not in set(table.teamId):
    st.warning("Not enough data for this team in this season yet.")
    st.stop()

row = table[table.teamId == team['team_id']].iloc[0]
st.caption(f"{int(row.games)} matches for {team['display_name']} | {len(table)} teams | "
           f"{n_games} league matches read")

aspect_choice = st.sidebar.radio("Aspect ratio", options=["Standard (16:9)", "Vertical (9:16)", "Tile (9:8)"],
                                 index=0)
aspect = {"Vertical (9:16)": "9x16", "Tile (9:8)": "9x8"}.get(aspect_choice, "16x9")
competition = st.sidebar.text_input("Competition name", value=seasons[sid])

fig = create_active_minutes_chart(table, team['team_id'], competition, aspect=aspect,
                                  team_name=team['display_name'])
# bbox_inches=None is load-bearing: st.pyplot otherwise crops the frame (see pages/13_Pass_Map.py).
st.pyplot(fig, width='stretch' if aspect == '16x9' else 'content', bbox_inches=None)

name_out = f"xg_breakdown_{team['display_name'].replace(' ', '_')}_{aspect}.png"
path = os.path.join(tempfile.gettempdir(), name_out)
fig.savefig(path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
with open(path, 'rb') as fh:
    st.download_button("Download PNG (300 dpi)", fh, name_out, "image/png")
plt.close(fig)
