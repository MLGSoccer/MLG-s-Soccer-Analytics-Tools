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

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR
from shared.motherduck import get_teams_by_league, get_games_for_team, season_label, is_league_season
from shared.active_minutes import build_active_minutes
from mostly_finished_charts.active_minutes_chart import create_active_minutes_chart, MINUS

st.set_page_config(page_title="xG Breakdown", page_icon=":material/sports_soccer:", layout="wide")
st.title("xG Breakdown")
st.caption("A club and its opponents against the league: active minutes with the ball, minutes per shot, "
           "xG per shot, to xG difference.")

try:
    teams_by_league = get_teams_by_league()
except Exception as exc:
    st.error(f"Could not connect to database: {exc}")
    st.stop()

# A club picked in the league table at the foot of the page: its chart
# replaces this one, same league season. Applied before the pickers are
# drawn, since a widget's value can only be set before it is created. The
# pickers' keys carry the league / club, so a stale choice never outlives them.
jump = st.session_state.pop('xgb_jump', None)
if jump:
    st.session_state['xgb_league'] = jump['league']
    st.session_state[f"xgb_team_{jump['league']}"] = jump['name']
    st.session_state[f"xgb_season_{jump['team_id']}"] = jump['season']

c1, c2, c3 = st.columns(3)
with c1:
    league = st.selectbox("League", options=[""] + list(teams_by_league), key='xgb_league')
with c2:
    if league:
        options = teams_by_league[league]
        name = st.selectbox("Team", options=[""] + [t['display_name'] for t in options], key=f"xgb_team_{league}")
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
    sid = st.selectbox("Season", options=list(seasons), format_func=seasons.get,   # newest first
                       key=f"xgb_season_{team['team_id']}")

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


# ---- the league table: every club on the chart's statistics, for and against ----
# Sorting a column ranks the league by it (no rank columns: the row order is
# the rank). One league-average row: across a league, opponents' totals are
# the clubs' own, so one average serves both sides - the chart's own
# reference (active_minutes_chart.compute: minutes the mean club, per-shot
# figures the league's totals). Minutes per shot in decimal minutes, not the
# chart's "2m 06s": a column must hold numbers to sort as numbers.
# Short headers so all eleven columns fit the page; the full name is the
# header's hover help.
COLS = {'Active min in possession': ('Active min', "Active minutes in possession, per game"),
        'Active min per shot': ('Min / shot', "Active minutes in possession per shot (decimal minutes)"),
        'xG per shot': ('xG / shot', "xG per shot"),
        'xG per game': ('xG / game', "xG per game"),
        'Opp. active min in possession': ('Opp. active min', "Opponents' active minutes in possession, per game"),
        'Opp. active min per shot': ('Opp. min / shot', "Opponents' active minutes per shot (decimal minutes)"),
        'Opp. xG per shot': ('Opp. xG / shot', "Opponents' xG per shot"),
        'Opp. xG per game': ('Opp. xG / game', "Opponents' xG per game (xG against)"),
        'xG difference': ('xG diff.', "xG per game minus opponents' xG per game")}


def league_table(table):
    n = len(table)
    tot_min, tot_shots, tot_xg = table.mip.sum(), table.shots.sum(), table.xg.sum()
    t = pd.DataFrame({
        'teamId': table.teamId, 'Club': table.team, 'Games': table.games.astype(float),
        'Active min in possession': table.mip, 'Active min per shot': table.mps,
        'xG per shot': table.xps, 'xG per game': table.xg,
        'Opp. active min in possession': table.opp_mip, 'Opp. active min per shot': table.opp_mps,
        'Opp. xG per shot': table.opp_xps, 'Opp. xG per game': table.opp_xg,
        # the chart's printed difference: of the printed ends, so the row agrees with the frame
        'xG difference': table.xg.round(2) - table.opp_xg.round(2)})
    avg = {'teamId': None, 'Club': 'League average', 'Games': table.games.mean(),
           'Active min in possession': tot_min / n, 'Active min per shot': tot_min / tot_shots,
           'xG per shot': tot_xg / tot_shots, 'xG per game': tot_xg / n}
    avg.update({'Opp. active min in possession': avg['Active min in possession'],
                'Opp. active min per shot': avg['Active min per shot'],
                'Opp. xG per shot': avg['xG per shot'], 'Opp. xG per game': avg['xG per game']})
    avg['xG difference'] = 0.0
    t = pd.concat([t, pd.DataFrame([avg])], ignore_index=True)
    t = t.sort_values('xG difference', ascending=False, kind='stable').reset_index(drop=True)
    return t.rename(columns={k: v[0] for k, v in COLS.items()})


def _signed(v):
    return ('+%.2f' % v) if v > 0.004 else (MINUS + '%.2f' % -v) if v < -0.004 else '0.00'


lt = league_table(table)
FMTS = {'Games': '{:.0f}', 'Active min': '{:.1f}', 'Opp. active min': '{:.1f}',
        'Min / shot': '{:.2f}', 'Opp. min / shot': '{:.2f}', 'xG / shot': '{:.3f}', 'Opp. xG / shot': '{:.3f}',
        'xG / game': '{:.2f}', 'Opp. xG / game': '{:.2f}', 'xG diff.': _signed}


def _row_style(r):
    if r.teamId == team['team_id']:
        return ['font-weight: 700; background-color: rgba(128, 128, 128, 0.18)'] * len(r)
    if r.teamId is None:
        return ['font-style: italic; color: #B8C5D6'] * len(r)
    return [''] * len(r)


st.subheader("League table")
st.caption("Click a column to sort the league by it. Select a club to see its chart.")
shown = [c for c in lt.columns if c != 'teamId']
picked = st.dataframe(
    lt.style.apply(_row_style, axis=1).format(FMTS), column_order=shown, hide_index=True,
    column_config={short: st.column_config.Column(short, help=full) for short, full in COLS.values()},
    height=(len(lt) + 1) * 35 + 3, on_select='rerun', selection_mode='single-row',
    key=f"xgb_table_{sid}_{team['team_id']}")
rows = picked.selection.rows if picked else []
if rows:
    tid = lt.teamId.iloc[rows[0]]
    if tid and tid != team['team_id']:
        # the club's entry in the pickers: this league first, else wherever it is listed
        where = [(lg, t) for lg in [league] + [x for x in teams_by_league if x != league]
                 for t in teams_by_league[lg] if t['team_id'] == tid]
        if where:
            lg, t = where[0]
            st.session_state['xgb_jump'] = {'league': lg, 'name': t['display_name'],
                                            'team_id': tid, 'season': sid}
            st.session_state['xgb_scroll_top'] = True
            st.rerun()
        else:
            st.warning(f"{lt.Club.iloc[rows[0]]} isn't in the team list, so its chart can't be opened from here.")

# After a club is picked from the table the page comes back scrolled to the
# table; take it to the top, where the new chart is.
if st.session_state.pop('xgb_scroll_top', False):
    components.html("<script>const m = window.parent.document.querySelector('[data-testid=\"stMain\"]');"
                    "if (m) m.scrollTo({top: 0}); window.parent.scrollTo(0, 0);</script>", height=0)
