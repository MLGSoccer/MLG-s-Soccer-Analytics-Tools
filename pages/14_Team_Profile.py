"""Team Profile - Streamlit page.

One team against its league or a wider pool, as six gauges, three levels
deep. The page is the analysis tool; every level it shows is also a finished
CBS graphic, downloadable as drawn.

The drill is a cube (shared/team_profile.py): headline x situation x
component. Level 2 shows one dimension of the picked headline, level 3 the
other, and WHICH comes first is the user's call - the "Drill by" control
under level 2. Both orders read the same numbers, so a cell reached either
way is the same cell.

Interaction is buttons beside the chart (the Zone Passing pattern): a click
sets session state and the level below re-renders. Nothing on the image is
clickable yet - that layer goes on top of this one, over the same figures.
"""
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR
from shared.motherduck import (
    get_teams_by_league, get_games_for_team, season_label, season_competition,
    is_league_season, pool_for_season, get_team_profile,
)
from shared import team_profile as tp
from mostly_finished_charts.team_profile_chart import create_team_profile
from pages.streamlit_utils import custom_title_inputs

st.set_page_config(page_title="Team Profile", page_icon="🎯", layout="wide")
st.title("Team Profile")
st.caption("Six gauges: where one team stands against its league (rank) or the "
           "wider pool (percentile). Click a metric to open it - by game situation "
           "or by component - and click again for the layer beneath.")

# ── Scope: which team, which league season, against whom ─────────────────────

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
# ONE league season. A cup is not a comparable pool - a Champions League
# league phase is eight games against eight different opponents - and the
# chart compares within a season, so the cups a club plays alongside its
# league are not offered here. The rolling xG and pass map pages are where a
# club's whole campaign goes on one chart.
seasons = {}
for g in games:
    sid = g.get('season_id')
    if sid and sid not in seasons and is_league_season(sid):
        seasons[sid] = season_label(sid, g.get('season_name'))
if not seasons:
    st.warning("No league seasons found for this team.")
    st.stop()
with c3:
    labels = list(seasons.values())
    picked = st.selectbox("League season", options=labels, index=0)
    season_id = next(k for k, v in seasons.items() if v == picked)

pool_info = pool_for_season(season_id)
p1, p2 = st.columns([2, 3])
with p1:
    pool_options = ["League"] + ([pool_info['label']] if pool_info else [])
    pool_choice = st.radio(
        "Compare against", options=pool_options, horizontal=True,
        help="League: rank among the teams in this season. The wider pool: "
             "percentile among every club in those leagues this campaign.")
    pool = "pool" if (pool_info and pool_choice == pool_info['label']) else "league"
with p2:
    exclude_penalties = st.toggle(
        "Exclude penalties", value=False,
        help="Drops penalty goals, xG and shots from every level. Game state "
             "still counts them - a penalty still put the team ahead.")

# ── Sidebar: chart controls ───────────────────────────────────────────────────

st.sidebar.header("Chart")
aspect_choice = st.sidebar.radio(
    "Aspect ratio",
    options=["Standard (16:9)", "Vertical (9:16)", "Tile (9:8)"], index=0,
    help="16:9 is the editorial chart, three gauges by two. 9:16 stacks them "
         "two by three. 9:8 keeps three by two at tile size, with labels set "
         "solid and the pool median dropped to fit.")
aspect = {"Vertical (9:16)": "9x16", "Tile (9:8)": "9x8"}.get(aspect_choice, "default")
competition = st.sidebar.text_input(
    "Competition name", value=season_competition([season_id]) or league or "")
title, subtitle = custom_title_inputs("team_profile", team['display_name'].upper(), "")

# ── Data ─────────────────────────────────────────────────────────────────────

with st.spinner("Loading the pool..."):
    try:
        profile = get_team_profile(team['team_id'], season_id, pool=pool,
                                   exclude_penalties=exclude_penalties)
    except Exception as exc:
        st.error(f"Could not build the profile: {exc}")
        st.stop()

checks = profile.get('checks') or {}
if checks and not (checks.get('gf_ok', True) and checks.get('ga_ok', True)):
    st.warning(
        f"Goal events do not reconcile with the scoreline for this team "
        f"(scoreline {checks.get('gf')}-{checks.get('ga')}, events "
        f"{checks.get('gf_events')}-{checks.get('ga_events')}). The headline "
        f"uses the scoreline; the situation splits use the events.")
if profile['pool_n'] < 2:
    st.warning("Only one team in this pool - nothing to rank against.")
    st.stop()

# ── Drill state ──────────────────────────────────────────────────────────────
# One dict, reset whenever the scope changes: a drill into Liverpool's goals
# must not survive switching to Arsenal.

scope_key = f"{team['team_id']}|{season_id}|{pool}|{int(exclude_penalties)}"
tp_state = st.session_state.get('tp')
if not tp_state or tp_state.get('scope') != scope_key:
    tp_state = {'scope': scope_key, 'headline': None, 'order': 'situation', 'pick': None}
    st.session_state['tp'] = tp_state


def _set(**kw):
    tp_state.update(kw)
    st.session_state['tp'] = tp_state


@st.cache_data(show_spinner=False)
def _render(_profile, scope_key, headline, path, order, aspect, competition, title, subtitle):
    """PNG bytes for one frame, 300 dpi. `_profile` is not hashed - the
    scope key stands for it - so a click re-renders only the level it opens."""
    import matplotlib.pyplot as plt
    fig = create_team_profile(_profile, headline=headline, path=tuple(path), order=order,
                              competition=competition, custom_title=title,
                              custom_subtitle=subtitle, aspect=aspect)
    tmp = os.path.join(tempfile.gettempdir(), f"team_profile_{abs(hash((scope_key, headline, path, order, aspect)))}.png")
    fig.savefig(tmp, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    plt.close(fig)
    with open(tmp, 'rb') as fh:
        return fh.read()


def _show(headline, path, order, filename):
    png = _render(profile, scope_key, headline, tuple(path), order, aspect,
                  competition, title, subtitle)
    # The portrait cuts are 9in wide against the 16:9's 16in; letting the
    # column stretch them blows them up past any size they will be seen at.
    if aspect == 'default':
        st.image(png, width='stretch')
    else:
        st.image(png, width=540 if aspect == '9x16' else 640)
    st.download_button("Download PNG (300 dpi)", png, filename, "image/png",
                       key=f"dl_{filename}")


def _button_row(specs, key_prefix, on_click):
    """Six buttons under a frame, one per gauge, in the frame's own order."""
    cols = st.columns(len(specs))
    for i, spec in enumerate(specs):
        with cols[i]:
            read = spec.standing.readout + ('' if spec.standing.mode == 'rank' else ' pctl')
            if st.button(f"{spec.label}\n{read}", key=f"{key_prefix}_{spec.key}",
                         width='stretch'):
                on_click(spec)


cube, subject, mode = profile['cube'], profile['subject'], profile['pool_mode']
safe = team['display_name'].replace(' ', '_').replace('/', '-')
suffix = '' if aspect == 'default' else f"_{aspect}"

# ── Level 1 ──────────────────────────────────────────────────────────────────

st.subheader("Overview")
_show(None, (), 'situation', f"team_profile_{safe}{suffix}.png")
top_specs = tp.view(cube, subject, None, mode=mode)


def _pick_headline(spec):
    _set(headline=spec.key.split('.')[0], pick=None)


_button_row(top_specs, "l1", _pick_headline)

# ── Level 2 ──────────────────────────────────────────────────────────────────

headline = tp_state.get('headline')
if headline:
    h = tp.HEADLINES[headline]
    st.divider()
    lc, rc = st.columns([3, 2])
    with lc:
        st.subheader(h.label)
    with rc:
        order_label = st.radio(
            "Drill by", options=["Situation", "Component"], horizontal=True,
            index=0 if tp_state.get('order') == 'situation' else 1,
            key=f"order_{scope_key}_{headline}",
            help="Situation: total, open play, set piece, ahead, drawing, behind. "
                 "Component: the links of the chain the metric is made of - "
                 "shots x chance quality (xG per shot) = xG; shot placement is "
                 "post-shot xG minus xG (how well the shot was struck); beating "
                 "the keeper is goals minus post-shot xG; on the against side "
                 "placement faced and shot-stopping (post-shot xGA minus goals "
                 "against) are the same two links seen from the defence. Own "
                 "goals sit in the scoreline and in no shot, so the keeper links "
                 "leave them out. The second gauge is the context: the other "
                 "family (xG beside goals), time in the game state, or under Set "
                 "Piece the set pieces themselves - corners, attacking-third free "
                 "kicks, throw-ins level with the box, penalties. Whichever you "
                 "open first, the next click opens the other.")
        new_order = 'component' if order_label == "Component" else 'situation'
        if new_order != tp_state.get('order'):
            _set(order=new_order, pick=None)
    order = tp_state['order']
    _show(headline, (), order, f"team_profile_{safe}_{headline}_by_{order}{suffix}.png")
    lvl2_specs = tp.view(cube, subject, headline, (), order, mode)

    def _pick_cell(spec):
        _set(pick=spec.key.split('.')[1] if order == 'situation' else spec.key.split('.')[2])

    _button_row(lvl2_specs, f"l2_{order}", _pick_cell)
    if st.button("Back to overview", key="back1"):
        _set(headline=None, pick=None)
        st.rerun()

    # ── Level 3 ──────────────────────────────────────────────────────────────

    pick = tp_state.get('pick')
    if pick:
        st.divider()
        if order == 'situation':
            crumb = f"{h.label} › {tp.SITUATIONS[pick]}"
        else:
            crumb = f"{h.label} › {tp.component_label(h, pick)}"
        st.subheader(crumb)
        _show(headline, (pick,), order,
              f"team_profile_{safe}_{headline}_{pick}{suffix}.png")
        if st.button(f"Back to {h.label}", key="back2"):
            _set(pick=None)
            st.rerun()
