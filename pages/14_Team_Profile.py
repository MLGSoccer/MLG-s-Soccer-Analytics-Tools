"""Team Profile - Streamlit page.

One team against its league or a wider pool, as six gauges, three levels
deep. The page is the analysis tool; every level it shows is also a finished
CBS graphic, downloadable as drawn.

The drill is a cube (shared/team_profile.py): headline x situation x
component. Level 2 shows one dimension of the picked headline, level 3 the
other, and WHICH comes first is the user's call - the "Drill by" control
under level 2. Both orders read the same numbers, so a cell reached either
way is the same cell.

The gauges are the controls: each frame at levels 1-2 is shown through
shared.gauge_click, a static component of ours that lays a hoverable region
over every grid cell and returns the clicked gauge's KEY. The page maps the
key to the same drill the button rows did, in session state, and the level
below renders in the same run. The frame is rendered once at 150 dpi for
the screen; the 300 dpi file is rendered when its download is clicked.
"""
import io
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR
from shared.motherduck import (
    get_teams_by_league, get_games_for_team, season_label, season_competition,
    is_league_season, pool_for_season, get_team_profile,
)
from shared import team_profile as tp
from shared.gauge_click import gauge_click, regions_from_boxes
from mostly_finished_charts.team_profile_chart import create_team_profile
from mostly_finished_charts.team_profile_table import create_league_ranking
from pages.streamlit_utils import custom_title_inputs

st.set_page_config(page_title="Team Profile", page_icon="🎯", layout="wide")
st.title("Team Profile")
st.caption("Six gauges: where one team stands against its league (rank) or the "
           "wider pool (percentile). Click a gauge to open it; click a gauge on "
           "the frame that appears to go one layer further.")

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


# The click tokens the page has acted on, one per clickable frame. NOT inside
# tp_state: a component's value survives a scope change, and a reset that
# forgot the token would replay the last click onto the new team.
seen_clicks = st.session_state.setdefault('tp_seen_clicks', {})

# 150 dpi for the screen: 2,400px across for the 16:9, one device pixel per
# image pixel in a 1,200px column on a 2x display, 170 KB, half a second.
# The 300 dpi file (390 KB, 4,800px) is made when its download is clicked.
SCREEN_DPI = 150
FILE_DPI = 300


@st.cache_data(show_spinner=False)
def _frame(_profile, scope_key, headline, path, order, aspect, competition, title, subtitle, dpi):
    """(PNG bytes, click regions, height/width) for one frame. `_profile` is
    not hashed - the scope key stands for it - so a click renders only the
    level it opens. The regions come off the figure the PNG was saved from,
    so they are the cells the reader sees, whatever the frame's layout."""
    import matplotlib.pyplot as plt
    fig = create_team_profile(_profile, headline=headline, path=tuple(path), order=order,
                              competition=competition, custom_title=title,
                              custom_subtitle=subtitle, aspect=aspect)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=dpi, facecolor=BG_COLOR, edgecolor='none', format='png')
    w, h = fig.get_size_inches()
    regions = regions_from_boxes(fig.tp_gauge_boxes, fig.tp_specs, _profile['pool_mode'])
    plt.close(fig)
    return buf.getvalue(), regions, float(h) / float(w)


def _take_click(slot):
    """The gauge key of a NEW click on the frame in `slot`, else None. Read
    BEFORE the frame is drawn, so the drill it opens - and the outline on the
    clicked gauge - land in this run. The component's value is its LAST
    click and persists across reruns; the token tells a new click from the
    same one seen again."""
    click = st.session_state.get(f'tp_click_{slot}')
    if not click or click.get('t') == seen_clicks.get(slot):
        return None
    seen_clicks[slot] = click.get('t')
    return click.get('key')


def _sentence(phrase):
    """"Game Situation" -> "Game situation": the frame line's title case is
    not a control's."""
    return phrase[:1] + phrase[1:].lower()


@st.cache_data(show_spinner=False)
def _ranking(_profile, scope_key, headline, situation, component, aspect, competition, dpi):
    """PNG bytes of the pool ranked on one stat. Same cache discipline as
    `_frame`: the scope key stands for the profile."""
    import matplotlib.pyplot as plt
    fig = create_league_ranking(_profile, headline=headline, situation=situation,
                                component=component, aspect=aspect, competition=competition)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=dpi, facecolor=BG_COLOR, edgecolor='none', format='png')
    plt.close(fig)
    return buf.getvalue()


def _show(headline, path, order, filename, *, slot=None, selected=None, ranking=None):
    """One frame and its download. With a `slot` the frame is clickable and
    `selected` names the gauge already open beneath it. `ranking` is the
    (headline, situation, component) the frame was reached by clicking; it
    hangs the league ranking on that stat under the frame."""
    args = (profile, scope_key, headline, tuple(path), order, aspect, competition, title, subtitle)
    png, regions, ratio = _frame(*args, SCREEN_DPI)
    # The portrait cuts are 9in wide against the 16:9's 16in; letting the
    # column stretch them blows them up past any size they will be seen at.
    max_width = None if aspect == 'default' else (540 if aspect == '9x16' else 640)
    if slot:
        gauge_click(png, regions, ratio=ratio, max_width=max_width, selected=selected,
                    key=f'tp_click_{slot}')
    elif max_width:
        st.image(png, width=max_width)
    else:
        st.image(png, width='stretch')
    st.download_button("Download PNG (300 dpi)", lambda: _frame(*args, FILE_DPI)[0],
                       filename, "image/png", key=f"dl_{filename}")
    if ranking:
        hk, sit, comp = ranking
        # Twenty rows fit a tall frame; the tile cannot hold a league.
        r_aspect = '9x16' if aspect == '9x8' else aspect
        spec = tp.gauge(profile['cube'], profile['subject'], hk, sit, comp, profile['pool_mode'])
        name = spec.label if not (comp == 'anchor' and sit == 'total') else tp.HEADLINES[hk].label
        if comp == 'anchor' and sit != 'total':
            name = tp.situation_label(tp.HEADLINES[hk], sit)
        with st.expander(f"League ranking: {name}"):
            r_args = (profile, scope_key, hk, sit, comp, r_aspect, competition)
            png = _ranking(*r_args, SCREEN_DPI)
            st.image(png, width=540 if r_aspect == '9x16' else 'stretch')
            r_file = filename.replace('team_profile_', 'ranking_')
            st.download_button("Download ranking PNG (300 dpi)", lambda: _ranking(*r_args, FILE_DPI),
                               r_file, "image/png", key=f"dl_rank_{r_file}")


safe = team['display_name'].replace(' ', '_').replace('/', '-')
suffix = '' if aspect == 'default' else f"_{aspect}"

# ── Level 1 ──────────────────────────────────────────────────────────────────

st.subheader("Overview")
clicked = _take_click('l1')
if clicked:
    _set(headline=clicked.split('.')[0], pick=None)
headline = tp_state.get('headline')
_show(None, (), 'situation', f"team_profile_{safe}{suffix}.png", slot='l1',
      selected=f"{headline}.total.anchor" if headline else None)

# ── Level 2 ──────────────────────────────────────────────────────────────────

if headline:
    h = tp.HEADLINES[headline]
    st.divider()
    lc, rc = st.columns([3, 2])
    with lc:
        st.subheader(h.label)
    with rc:
        # The two splits in a reader's words, and the six names each one
        # opens listed beneath, so the choice is concrete before it is made.
        split_options = [_sentence(tp.order_phrase('situation', short=True)),
                         _sentence(tp.order_phrase('component', h, short=True))]
        order_label = st.radio(
            "Split by", options=split_options, horizontal=True,
            index=0 if tp_state.get('order') == 'situation' else 1,
            key=f"order_{scope_key}_{headline}",
            help="Game situation: the same number in each part of the game - "
                 "open play, set pieces, and with the team ahead, drawing or "
                 "behind. The other split takes the number apart, and what it "
                 "shows depends on which one you opened. A goals headline "
                 "stays clear of xG: how many shots, from how far out, and how "
                 "they ended - on target, blocked, or missed. An xG headline "
                 "walks the chain: the goals beside the xG, the gap between "
                 "them, and the two things that make the gap - where the shots "
                 "were placed and what beat the keeper. A difference compares "
                 "the two ends of the game, netting only what can honestly be "
                 "netted. Whichever you open first, the next click opens the "
                 "other.")
        new_order = 'component' if order_label == split_options[1] else 'situation'
        if new_order != tp_state.get('order'):
            _set(order=new_order, pick=None)
        if new_order == 'situation':
            names = [tp.SITUATIONS[s] for s in tp.SITUATION_ORDER]
        else:
            names = [tp.component_label(h, tp.resolve_component('total', c, h.side))
                     for c in tp.components_of(h)]
            names[0] = h.label
        st.caption(" \u00b7 ".join(names))
    order = tp_state['order']
    clicked = _take_click('l2')
    if clicked:
        # By situation the frame's keys vary in the situation slot
        # ("gf.behind.anchor"); by component in the component slot
        # ("gf.total.shots"). The pick is whichever varies.
        _set(pick=clicked.split('.')[1] if order == 'situation' else clicked.split('.')[2])
    pick = tp_state.get('pick')
    if pick:
        selected = f"{headline}.{pick}.anchor" if order == 'situation' else f"{headline}.total.{pick}"
    else:
        selected = None
    _show(headline, (), order, f"team_profile_{safe}_{headline}_by_{order}{suffix}.png",
          ranking=(headline, 'total', 'anchor'),
          slot='l2', selected=selected)
    # on_click runs before the script body, so the level closes in the same
    # run as the click - no st.rerun() and no second pass.
    st.button("Back to overview", key="back1", on_click=_set,
              kwargs={'headline': None, 'pick': None})

    # ── Level 3 ──────────────────────────────────────────────────────────────

    if pick:
        st.divider()
        if order == 'situation':
            crumb = f"{h.label} › {tp.SITUATION_PHRASE[pick]}"
        else:
            crumb = f"{h.label} › {tp.component_label(h, pick)}"
        st.subheader(crumb)
        _show(headline, (pick,), order,
              f"team_profile_{safe}_{headline}_{pick}{suffix}.png",
              ranking=((headline, pick, 'anchor') if order == 'situation'
                       else (headline, 'total', pick)))
        st.button(f"Back to {h.label}", key="back2", on_click=_set, kwargs={'pick': None})
