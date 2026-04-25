"""
Match Momentum Chart - Streamlit Page
Rolling window momentum using shots, corners, and final-third entries.
"""
import os
import sys
import tempfile

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.transforms import blended_transform_factory
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.motherduck import (
    get_teams_by_league, get_games_for_team, get_momentum_events,
    get_goal_scorers_for_game, get_own_goals_for_game, get_red_cards_for_game,
)
from shared.styles import (
    BG_COLOR, SPINE_COLOR,
    TEXT_PRIMARY, TEXT_SECONDARY,
    add_cbs_footer, BROADCAST_FIGSIZE,
)
from shared.colors import ensure_contrast_with_background, check_colors_need_fix
from pages.streamlit_utils import custom_title_inputs

st.set_page_config(page_title="Match Momentum", page_icon="", layout="wide")


# ── CSV parsing ────────────────────────────────────────────────────────────────

@st.cache_data
def _parse_momentum_csv(file_content):
    """Parse a TruMedia event log CSV into (events_df, match_info, goal_scorers).

    events_df has columns: minute, team_side, event_type
    match_info mirrors get_momentum_events() output
    goal_scorers mirrors get_goal_scorers_for_game() output
    """
    import io
    df = pd.read_csv(io.BytesIO(file_content))

    if df.empty:
        return pd.DataFrame(), {}, []

    # ── Match metadata from first row ─────────────────────────────────────────
    r0 = df.iloc[0]
    home_team = str(r0.get("homeTeam", "Home Team") or "Home Team")
    away_team = str(r0.get("awayTeam", "Away Team") or "Away Team")

    try:
        home_score = int(float(r0.get("homeFinalScore", 0) or 0))
        away_score = int(float(r0.get("awayFinalScore", 0) or 0))
    except (ValueError, TypeError):
        home_score, away_score = 0, 0

    try:
        from datetime import datetime as _dt
        date_str = str(r0.get("Date", ""))[:10]
        date_display = _dt.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        date_display = str(r0.get("Date", ""))

    # ── Team colors ───────────────────────────────────────────────────────────
    home_color, away_color = "#4A90D9", "#E05C5C"
    if "newestTeamColor" in df.columns and "Team" in df.columns:
        home_rows = df[df["Team"].str.strip().str.lower() == home_team.strip().lower()]
        away_rows = df[df["Team"].str.strip().str.lower() == away_team.strip().lower()]
        if not home_rows["newestTeamColor"].dropna().empty:
            home_color = home_rows["newestTeamColor"].dropna().iloc[0]
        if not away_rows["newestTeamColor"].dropna().empty:
            away_color = away_rows["newestTeamColor"].dropna().iloc[0]

    # ── Determine team_side per row ───────────────────────────────────────────
    team_col = "Team" if "Team" in df.columns else (
        "teamFullName" if "teamFullName" in df.columns else None
    )
    if team_col is None:
        return pd.DataFrame(), {}, []

    df["_team_norm"] = df[team_col].fillna("").str.strip().str.lower()
    home_norm = home_team.strip().lower()
    away_norm = away_team.strip().lower()
    df["team_side"] = df["_team_norm"].apply(
        lambda t: "home" if t == home_norm else ("away" if t == away_norm else None)
    )
    df = df[df["team_side"].notna()].copy()

    # ── Event type classification ─────────────────────────────────────────────
    shot_types = {"AttemptSaved", "Miss", "Post", "Goal", "PenaltyGoal", "OwnGoal"}

    play_col = "playType" if "playType" in df.columns else None
    pass_col  = "PassType"  if "PassType"  in df.columns else None
    x_col     = "EventXDecimal" if "EventXDecimal" in df.columns else None

    def _classify(row):
        if play_col and row.get(play_col) in shot_types:
            return "shot"
        if pass_col and row.get(pass_col) == "Corner":
            return "corner"
        if x_col and pd.notna(row.get(x_col)) and float(row.get(x_col, 0)) > 66:
            return "final_third"
        return None

    df["event_type"] = df.apply(_classify, axis=1)
    df = df[df["event_type"].notna()].copy()

    # ── Minute ────────────────────────────────────────────────────────────────
    clock_col = "gameClock" if "gameClock" in df.columns else None
    if clock_col:
        df["minute"] = pd.to_numeric(df[clock_col], errors="coerce").fillna(0) / 60.0
    else:
        df["minute"] = 0.0

    # ── Half-time minute ──────────────────────────────────────────────────────
    # Real HT = last Period 1 event's minute. Default 45.0 if no Period column
    # or no Period 1 events (e.g., partial CSV).
    if "Period" in df.columns:
        p1_minutes = pd.to_numeric(
            df.loc[df["Period"] == 1, "minute"], errors="coerce"
        ).dropna()
        ht_minute = float(p1_minutes.max()) if len(p1_minutes) else 45.0
    else:
        ht_minute = 45.0

    events_df = df[["minute", "team_side", "event_type"]].reset_index(drop=True)

    # ── Goal scorers ──────────────────────────────────────────────────────────
    goal_scorers = []
    if play_col and "shooter" in df.columns:
        goals_df = df[df[play_col].isin({"Goal", "PenaltyGoal"}) & df["shooter"].notna() & (df["shooter"] != "")].copy()
        for _, grow in goals_df.iterrows():
            try:
                minute = int(float(grow.get(clock_col or "gameClock", 0) or 0) / 60)
            except Exception:
                minute = 0
            team = grow[team_col]
            goal_scorers.append({
                "minute":   minute,
                "player":   str(grow["shooter"]),
                "team":     team,
                "team_id":  None,
                "pen":      grow[play_col] == "PenaltyGoal",
            })
        goal_scorers.sort(key=lambda x: x["minute"])

    match_info = {
        "home_team":    home_team,
        "away_team":    away_team,
        "home_score":   home_score,
        "away_score":   away_score,
        "home_team_id": None,
        "away_team_id": None,
        "home_color":   home_color,
        "away_color":   away_color,
        "date":         date_display,
        "ht_minute":    ht_minute,
    }

    return events_df, match_info, goal_scorers


# ── Momentum computation ───────────────────────────────────────────────────────

def _compute_momentum(events_df, w_shots, w_corners, w_ft, window=5):
    """
    Compute per-minute momentum (0-100) using a rolling window.
    50 = neutral, >50 = home dominant, <50 = away dominant.
    Weights are normalised internally so they don't need to sum to 100.
    """
    if events_df.empty:
        return pd.Series(dtype=float)

    max_min = int(events_df["minute"].max()) + 1
    minutes = range(0, max_min + 1)

    weight_map = {"shot": w_shots, "corner": w_corners, "final_third": w_ft}
    total_w = w_shots + w_corners + w_ft
    if total_w == 0:
        return pd.Series(50.0, index=minutes)

    home_score = pd.Series(0.0, index=minutes)
    away_score = pd.Series(0.0, index=minutes)

    events_df = events_df.copy()
    events_df["minute_bin"] = events_df["minute"].astype(int).clip(0, max_min)
    events_df["weight"] = events_df["event_type"].map(weight_map).fillna(0)

    for side, series in [("home", home_score), ("away", away_score)]:
        sub = events_df[events_df["team_side"] == side]
        grouped = sub.groupby("minute_bin")["weight"].sum()
        for m, v in grouped.items():
            if m in series.index:
                series[m] = v

    # Trailing window — pressure builds forward into events, not averaged around them.
    home_roll = home_score.rolling(window=window, min_periods=1).sum()
    away_roll = away_score.rolling(window=window, min_periods=1).sum()

    # Net difference: home minus away. Rising = home increasing pressure, falling = away.
    net = home_roll - away_roll

    # Normalise symmetrically to 0-100 (50 = neutral) using the most dominant
    # period in the match as the ceiling — so the full scale is always used.
    max_abs = net.abs().max()
    if max_abs < 1e-6:
        return pd.Series(50.0, index=net.index)
    momentum = 50.0 + (net / max_abs) * 50.0

    return momentum


# ── Chart rendering ─────────────────────────────────────────────────────────────────────────

# Goal/RC label vertical levels (axes fraction). Labels stack here when minutes
# would otherwise collide horizontally.
_Y_LEVELS = (1.04, 1.12, 1.20)
_NEAR_EDGE = 6     # prefer left side if within this many minutes of chart end
_LABEL_W = 12      # estimated label width in minutes (matches xG race spec)
_RC_COLOR = "#E53935"


def _place_event_labels(events, chart_max):
    """Assign x_side ('left'/'right') and y_level (0/1/2) to each event so
    labels don't collide. Mutates events in place. Mirrors xG race's
    collision-avoidance logic.
    """
    def _label_range(minute, side):
        return (minute, minute + _LABEL_W) if side == "right" else (minute - _LABEL_W, minute)

    def _overlaps(m_new, s_new, placed):
        lo_new, hi_new = _label_range(m_new, s_new)
        for m_p, s_p, _lv in placed:
            lo_p, hi_p = _label_range(m_p, s_p)
            if max(lo_new, lo_p) < min(hi_new, hi_p):
                return True
        return False

    placed = []  # (minute, x_side, level)
    for ev in events:
        near_right = (chart_max - ev["minute"]) < _NEAR_EDGE
        near_left = ev["minute"] < _NEAR_EDGE
        if near_left:
            sides = ["right"]
        elif near_right:
            sides = ["left", "right"]
        else:
            sides = ["right", "left"]

        chosen_side, chosen_level = None, None
        # Step 1: try level 0
        for s in sides:
            same_lv = [(mp, sp, lp) for mp, sp, lp in placed if lp == 0]
            if not _overlaps(ev["minute"], s, same_lv):
                chosen_side, chosen_level = s, 0
                break

        # Step 2: try flipping a placed level-0 label
        flip_target = None
        if chosen_side is None and not near_left:
            for j, (mp, sp, lp) in enumerate(placed):
                if lp != 0:
                    continue
                alt_s = "left" if sp == "right" else "right"
                others_lv0 = [(mk, sk, lk) for k, (mk, sk, lk) in enumerate(placed)
                              if k != j and lk == 0]
                if _overlaps(mp, alt_s, others_lv0):
                    continue
                tentative = others_lv0 + [(mp, alt_s, 0)]
                for s in sides:
                    if not _overlaps(ev["minute"], s, tentative):
                        flip_target = (j, mp, alt_s)
                        chosen_side, chosen_level = s, 0
                        break
                if chosen_side is not None:
                    break

        # Step 3: elevate to higher levels
        if chosen_side is None:
            for lv in range(1, len(_Y_LEVELS)):
                for s in sides:
                    same_lv = [(mp, sp, lp) for mp, sp, lp in placed if lp == lv]
                    if not _overlaps(ev["minute"], s, same_lv):
                        chosen_side, chosen_level = s, lv
                        break
                if chosen_side is not None:
                    break

        if chosen_side is None:
            chosen_side, chosen_level = sides[0], len(_Y_LEVELS) - 1

        if flip_target is not None:
            j, mp, alt_s = flip_target
            placed[j] = (mp, alt_s, 0)
            events[j]["x_side"] = alt_s

        placed.append((ev["minute"], chosen_side, chosen_level))
        ev["x_side"] = chosen_side
        ev["y_level"] = chosen_level


def _draw_momentum_chart(momentum, match_info, goal_scorers,
                         own_goals=None, red_cards=None,
                         competition="", custom_title=None, custom_subtitle=None):
    home_name = match_info["home_team"]
    away_name = match_info["away_team"]
    home_team_id = match_info.get("home_team_id")
    home_score = match_info["home_score"]
    away_score = match_info["away_score"]
    home_color = ensure_contrast_with_background(match_info["home_color"], BG_COLOR)
    away_color = ensure_contrast_with_background(match_info["away_color"], BG_COLOR)

    # Apply alternate color if the two team colors clash
    fix = check_colors_need_fix(home_color, away_color, home_name, away_name)
    if fix["needs_fix"] and fix["suggested_fix"]:
        sf = fix["suggested_fix"]
        if sf["team"] == home_name:
            home_color = sf["color"]
        else:
            away_color = sf["color"]

    ht_minute = float(match_info.get("ht_minute", 45.0))
    date = match_info["date"]

    mins = np.array(momentum.index, dtype=float)
    vals = np.array(momentum.values, dtype=float)
    chart_max = float(max(max(mins) if len(mins) else 90, 90))

    # ── Figure + axes ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=BROADCAST_FIGSIZE, facecolor=BG_COLOR)
    fig.subplots_adjust(top=0.72, bottom=0.10, left=0.06, right=0.98)
    ax.set_facecolor(BG_COLOR)

    # ── Header (xG race conventions) ──────────────────────────────────────────────
    fig.text(0.5, 0.973, "MATCH MOMENTUM", fontsize=11, fontweight="bold",
             color=TEXT_SECONDARY, ha="center", va="center")

    score_title = f"{home_name.upper()} {home_score}-{away_score} {away_name.upper()}"
    title_text = custom_title or score_title
    title_obj = fig.text(0.5, 0.942, title_text, fontsize=22, fontweight="bold",
                         color=TEXT_PRIMARY, ha="center", va="center")

    # Accent bar matched to title text width — same approach as xG race so
    # the bar always anchors under the title regardless of name length.
    fig.canvas.draw()
    sb = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    sb_fig = sb.transformed(fig.transFigure.inverted())
    bar_y = 0.912
    bar_h = 0.005
    mid = sb_fig.x0 + sb_fig.width / 2
    fig.patches.append(mpatches.Rectangle(
        (sb_fig.x0, bar_y), sb_fig.width / 2, bar_h,
        transform=fig.transFigure, facecolor=home_color,
        edgecolor='none', zorder=10, figure=fig))
    fig.patches.append(mpatches.Rectangle(
        (mid, bar_y), sb_fig.width / 2, bar_h,
        transform=fig.transFigure, facecolor=away_color,
        edgecolor='none', zorder=10, figure=fig))

    subtitle_parts = [p for p in [competition.upper() if competition else "", date] if p]
    auto_subtitle = " | ".join(subtitle_parts)
    display_subtitle = custom_subtitle or auto_subtitle
    if display_subtitle:
        fig.text(0.5, 0.885, display_subtitle, ha="center",
                 color=TEXT_SECONDARY, fontsize=11)

    # ── Filled momentum areas + white trajectory line ────────────────────────────────────────
    ax.fill_between(mins, vals, 50, where=(vals >= 50),
                    color=home_color, alpha=0.55, interpolate=True, linewidth=0)
    ax.fill_between(mins, vals, 50, where=(vals <= 50),
                    color=away_color, alpha=0.55, interpolate=True, linewidth=0)
    ax.plot(mins, vals, color="white", linewidth=2.0, alpha=0.9)

    # 50-line + HT marker (real half-time, not hardcoded 45)
    ax.axhline(50, color=SPINE_COLOR, linewidth=1.0, alpha=0.6)
    ax.axvline(ht_minute, color=SPINE_COLOR, linewidth=0.8,
               linestyle="--", alpha=0.45)
    ax.text(ht_minute / chart_max, 1.005, "HT", transform=ax.transAxes,
            color=TEXT_SECONDARY, fontsize=8, ha="center", va="bottom",
            alpha=0.75)

    # ── Build all events (goals + own goals + red cards) ───────────────────────────────────
    import difflib as _dl

    def _is_home(team_name, team_id=None):
        if team_id and home_team_id:
            return str(team_id) == str(home_team_id)
        hs = _dl.SequenceMatcher(None, (team_name or "").lower(), home_name.lower()).ratio()
        as_ = _dl.SequenceMatcher(None, (team_name or "").lower(), away_name.lower()).ratio()
        return hs >= as_

    all_events = []
    for g in (goal_scorers or []):
        all_events.append({
            "type": "goal", "minute": g["minute"], "team": g["team"],
            "team_id": g.get("team_id"),
            "label": g["player"] + (" (P)" if g.get("pen") else ""),
            "og": False,
        })
    for og in (own_goals or []):
        all_events.append({
            "type": "goal", "minute": og["minute"], "team": og["team"],
            "team_id": None, "label": "OG", "og": True,
        })
    for rc in (red_cards or []):
        if rc.get("card_type") and rc.get("card_type") not in ("red", "second_yellow"):
            continue
        all_events.append({
            "type": "rc", "minute": rc["minute"], "team": rc["team"],
            "team_id": rc.get("team_id"),
            "label": rc.get("player", ""),
        })
    all_events.sort(key=lambda x: x["minute"])

    h, a = 0, 0
    for ev in all_events:
        side = "home" if _is_home(ev["team"], ev.get("team_id")) else "away"
        ev["side"] = side
        if ev["type"] == "goal":
            if side == "home":
                h += 1
            else:
                a += 1
            ev["score"] = f"{h}-{a}"

    _place_event_labels(all_events, chart_max)

    # ── Render events (goals, OGs, red cards) ────────────────────────────────────────────
    label_transform = blended_transform_factory(ax.transData, ax.transAxes)

    for ev in all_events:
        flip_left = ev["x_side"] == "left"
        label_y = _Y_LEVELS[ev["y_level"]]
        label_x = ev["minute"] - 0.6 if flip_left else ev["minute"] + 0.6
        label_ha = "right" if flip_left else "left"
        side_color = home_color if ev["side"] == "home" else away_color

        if ev["type"] == "goal":
            ax.axvline(ev["minute"], color=side_color, linewidth=1.2,
                       linestyle=":", alpha=0.8)
            if ev["og"]:
                ax.plot(ev["minute"], 1.005, "o", transform=label_transform,
                        markerfacecolor='none', markeredgecolor=side_color,
                        markersize=8, markeredgewidth=1.6,
                        clip_on=False, zorder=5)
            else:
                ax.plot(ev["minute"], 1.005, "o", transform=label_transform,
                        color=side_color, markersize=8,
                        markeredgecolor="white", markeredgewidth=1.2,
                        clip_on=False, zorder=5)
            text = f"{ev['label']} ({ev['minute']}')\n{ev['score']}"
            ax.text(label_x, label_y, text, transform=label_transform,
                    color=side_color, fontsize=13, fontweight="bold",
                    va="bottom", ha=label_ha,
                    fontstyle="italic" if ev["og"] else "normal",
                    clip_on=False)

        else:  # rc
            ax.axvline(ev["minute"], color=_RC_COLOR, linewidth=1.0,
                       linestyle="-.", alpha=0.7)
            # Card-shaped marker: vertical red rectangle at chart top edge,
            # ~1:1.5 ratio, distinct from circular goal markers.
            card_w_min = 0.7
            card_h_axes = 0.028
            card = mpatches.Rectangle(
                (ev["minute"] - card_w_min / 2, 1.0),
                card_w_min, card_h_axes,
                facecolor=_RC_COLOR, edgecolor='white', linewidth=1.5,
                transform=label_transform, clip_on=False, zorder=6,
            )
            ax.add_patch(card)
            player = ev.get("label", "")
            text = (f"{player} ({ev['minute']}')\nRED CARD"
                    if player else f"RED CARD ({ev['minute']}')")
            ax.text(label_x, label_y, text, transform=label_transform,
                    color=side_color, fontsize=13, fontweight="bold",
                    va="bottom", ha=label_ha, clip_on=False)

    # ── Team labels at top-left and bottom-left of plot ─────────────────────────────────────
    ax.text(0.005, 0.96, home_name.upper(), transform=ax.transAxes,
            color=home_color, fontsize=12, fontweight="bold", va="top",
            alpha=0.95)
    ax.text(0.005, 0.04, away_name.upper(), transform=ax.transAxes,
            color=away_color, fontsize=12, fontweight="bold", va="bottom",
            alpha=0.95)

    # ── Axes ───────────────────────────────────────────────────────────────────
    for spine_name, spine in ax.spines.items():
        if spine_name in ("top", "right"):
            spine.set_visible(False)
        else:
            spine.set_color(SPINE_COLOR)
            spine.set_linewidth(0.8)
    # Y-tick positions kept (they anchor the grid lines) but numeric labels
    # hidden — the team labels at top/bottom of the plot tell the reader who
    # is pressuring; explicit 0/25/50/75/100 isn't meaningful since the
    # momentum scale is normalized to the match's own peak.
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels([])
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", colors=TEXT_SECONDARY, labelsize=9)
    ax.set_xlabel("Minute", color=TEXT_SECONDARY, fontsize=10)
    ax.set_ylabel("Momentum", color=TEXT_SECONDARY, fontsize=10)
    ax.set_ylim(-2, 102)
    ax.set_xlim(0, chart_max)
    ax.yaxis.grid(True, color=SPINE_COLOR, alpha=0.18, linewidth=0.5)
    ax.set_axisbelow(True)

    # ── Footer (standard convention) ──────────────────────────────────────────────
    add_cbs_footer(fig)

    return fig

# ── Page ───────────────────────────────────────────────────────────────────────

st.title("Match Momentum")
st.markdown("Rolling momentum balance using shots, corners, and final-third entries.")

# ── Data source toggle ────────────────────────────────────────────────────────
data_source = st.radio(
    "Data source",
    options=["Database", "Upload CSV"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

# Sidebar weights (shared by both modes)
st.sidebar.header("Momentum Weights")
w_shots   = st.sidebar.slider("Shots",               0, 100, 50, step=5)
w_corners = st.sidebar.slider("Corners",             0, 100, 30, step=5)
w_ft      = st.sidebar.slider("Final Third Entries", 0, 100, 20, step=5)
total_w   = w_shots + w_corners + w_ft
st.sidebar.caption(
    f"Effective weights: Shots {w_shots/total_w*100:.0f}% | Corners {w_corners/total_w*100:.0f}% | Final Third {w_ft/total_w*100:.0f}%"
    if total_w > 0 else "Set at least one weight > 0"
)
window = st.sidebar.slider("Rolling window (minutes)", 3, 10, 5, step=1)


def _own_goals_sidebar(home_team, away_team, auto_ogs, key_prefix):
    """Render own goals sidebar and return list of {minute, team} dicts."""
    import difflib as _dl
    st.sidebar.header("Own Goals")
    num_own_goals = st.sidebar.number_input(
        "Number of own goals", min_value=0, max_value=5,
        value=len(auto_ogs), key=f"num_og_{key_prefix}"
    )
    own_goals = []
    for i in range(num_own_goals):
        st.sidebar.markdown(f"**Own Goal {i+1}**")
        og_col1, og_col2 = st.sidebar.columns(2)
        if i < len(auto_ogs):
            default_minute = auto_ogs[i]["minute"]
            cr = auto_ogs[i]["credited_team"]
            hs = _dl.SequenceMatcher(None, cr.lower(), home_team.lower()).ratio()
            as_ = _dl.SequenceMatcher(None, cr.lower(), away_team.lower()).ratio()
            default_scorer_idx = 0 if hs >= as_ else 1
        else:
            default_minute = 45
            default_scorer_idx = 0
        with og_col1:
            og_minute = st.number_input(
                "Minute", min_value=1, max_value=120,
                value=default_minute, key=f"og_min_{key_prefix}_{i}"
            )
        with og_col2:
            scoring_team = st.selectbox(
                "Scored by", options=[home_team, away_team],
                index=default_scorer_idx, key=f"og_team_{key_prefix}_{i}"
            )
        credited_team = away_team if scoring_team == home_team else home_team
        own_goals.append({"minute": og_minute, "team": credited_team})
        st.sidebar.caption(f"Goal credited to {credited_team}")
    return own_goals


def _render_and_store(events_df, match_info, goal_scorers, own_goals, red_cards,
                      competition, custom_title, custom_subtitle, session_key):
    """Build chart, save to session state. Returns True on success."""
    if events_df.empty:
        st.warning("No event data found.")
        return False
    if total_w == 0:
        st.warning("Please set at least one weight above 0.")
        return False

    momentum = _compute_momentum(events_df, w_shots, w_corners, w_ft, window=window)
    fig = _draw_momentum_chart(
        momentum, match_info, goal_scorers,
        own_goals=own_goals,
        red_cards=red_cards,
        competition=competition,
        custom_title=custom_title,
        custom_subtitle=custom_subtitle,
    )

    home_slug = match_info["home_team"].replace(" ", "_")
    away_slug = match_info["away_team"].replace(" ", "_")
    fname = f"momentum_{home_slug}_vs_{away_slug}.png"

    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, fname)
        fig.savefig(fp, dpi=300, bbox_inches="tight",
                    facecolor=BG_COLOR, edgecolor="none")
        with open(fp, "rb") as f:
            img_bytes = f.read()
    plt.close(fig)

    st.session_state[session_key] = {
        "img": img_bytes, "filename": fname,
        "caption": f"{match_info['home_team']} vs {match_info['away_team']}"
    }
    return True


def _show_chart(session_key):
    if st.session_state.get(session_key):
        chart = st.session_state[session_key]
        st.image(chart["img"], caption=chart["caption"])
        st.download_button(
            label="Download Chart",
            data=chart["img"],
            file_name=chart["filename"],
            mime="image/png",
        )


# ── Database mode ─────────────────────────────────────────────────────────────
if data_source == "Database":
    try:
        with st.spinner("Loading teams..."):
            teams_by_league = get_teams_by_league()
    except Exception as e:
        st.error(f"Could not connect to database: {e}")
        st.stop()

    league_names = list(teams_by_league.keys())
    col1, col2, col3 = st.columns(3)

    with col1:
        selected_league = st.selectbox("League", options=[""] + league_names)

    with col2:
        if selected_league:
            team_options  = teams_by_league[selected_league]
            team_labels   = [t["display_name"] for t in team_options]
            selected_team_name = st.selectbox("Team", options=[""] + team_labels)
            selected_team = next(
                (t for t in team_options if t["display_name"] == selected_team_name), None
            )
        else:
            st.selectbox("Team", options=[], disabled=True)
            selected_team = None

    with col3:
        if selected_team:
            games = get_games_for_team(selected_team["team_id"])
            if games:
                season_options = {}
                for g in games:
                    if g.get("season_id") and g.get("season_name"):
                        season_options[g["season_id"]] = g["season_name"]
                if len(season_options) > 1:
                    season_labels = list(season_options.values())
                    selected_season_name = st.selectbox("Season", options=season_labels)
                    selected_season_id = next(k for k, v in season_options.items() if v == selected_season_name)
                    games = [g for g in games if g.get("season_id") == selected_season_id]
                game_labels = [g["label"] for g in games]
                selected_game_label = st.selectbox("Game", options=[""] + game_labels)
                selected_game = next(
                    (g for g in games if g["label"] == selected_game_label), None
                )
            else:
                st.selectbox("Game", options=["No games found"], disabled=True)
                selected_game = None
        else:
            st.selectbox("Game", options=[], disabled=True)
            selected_game = None

    competition = st.text_input(
        "Competition Name",
        value=selected_league if selected_league else "",
        help="Auto-filled from league - edit if needed"
    )

    if selected_game:
        _dt = (f"{selected_game['home_team'].upper()} "
               f"{selected_game['home_score']}-{selected_game['away_score']} "
               f"{selected_game['away_team'].upper()}")
        _ds = f"{competition} | {selected_game['date_display']}" if competition else selected_game['date_display']
        custom_title_m, custom_subtitle_m = custom_title_inputs("momentum_db", _dt, _ds)

        try:
            auto_ogs = get_own_goals_for_game(selected_game["game_id"])
        except Exception:
            auto_ogs = []

        own_goals = _own_goals_sidebar(
            selected_game["home_team"], selected_game["away_team"],
            auto_ogs, key_prefix=selected_game["game_id"]
        )

        if st.button("Generate Chart", type="primary"):
            st.session_state["momentum_chart_db"] = None
            with st.spinner("Building momentum chart..."):
                events_df, match_info = get_momentum_events(selected_game["game_id"])
                try:
                    goal_scorers = get_goal_scorers_for_game(selected_game["game_id"])
                except Exception:
                    goal_scorers = []
                try:
                    red_cards = get_red_cards_for_game(selected_game["game_id"])
                except Exception:
                    red_cards = []
                _render_and_store(
                    events_df, match_info, goal_scorers, own_goals, red_cards,
                    competition, custom_title_m, custom_subtitle_m, "momentum_chart_db"
                )

        _show_chart("momentum_chart_db")


# ── CSV upload mode ───────────────────────────────────────────────────────────
else:
    competition = st.text_input(
        "Competition Name",
        value="",
        help="e.g., Premier League, Champions League (optional)"
    )

    uploaded_file = st.file_uploader(
        "Upload TruMedia Event Log CSV",
        type=["csv"],
        help="Single-match event log"
    )

    if uploaded_file is not None:
        file_content = uploaded_file.getvalue()
        try:
            with st.spinner("Parsing match data..."):
                events_df, match_info, goal_scorers = _parse_momentum_csv(file_content)

            if events_df.empty:
                st.error("No momentum events found in CSV. Check that the file contains shot, corner, or final-third entry events.")
            else:
                home_team = match_info["home_team"]
                away_team = match_info["away_team"]

                st.success(f"**{home_team}** vs **{away_team}**  —  {match_info.get('date', '')}")

                col1, col2, col3 = st.columns(3)
                col1.metric("Date", match_info.get("date", "Unknown"))
                col2.metric("Home Team", home_team)
                col3.metric("Away Team", away_team)

                _dt = (f"{home_team.upper()} "
                       f"{match_info['home_score']}-{match_info['away_score']} "
                       f"{away_team.upper()}")
                _ds = f"{competition} | {match_info.get('date', '')}" if competition else match_info.get("date", "")
                custom_title_m, custom_subtitle_m = custom_title_inputs("momentum_csv", _dt, _ds)

                own_goals = _own_goals_sidebar(
                    home_team, away_team, [], key_prefix="csv"
                )

                if st.button("Generate Chart", type="primary"):
                    st.session_state["momentum_chart_csv"] = None
                    with st.spinner("Building momentum chart..."):
                        _render_and_store(
                            events_df, match_info, goal_scorers, own_goals, [],
                            competition, custom_title_m, custom_subtitle_m, "momentum_chart_csv"
                        )

                _show_chart("momentum_chart_csv")

        except Exception as e:
            st.error(f"Error processing file: {e}")
            import traceback
            st.code(traceback.format_exc())

    else:
        st.info("Upload a TruMedia Event Log CSV for a single match")

        with st.expander("Required CSV columns"):
            st.markdown("""
            **TruMedia Event Log** (one row per event):

            | Column | Required | Description |
            |--------|----------|-------------|
            | `Date` | Yes | Match date |
            | `homeTeam` | Yes | Home team name |
            | `awayTeam` | Yes | Away team name |
            | `Team` | Yes | Team that performed the action |
            | `playType` | Yes | Event type (AttemptSaved, Miss, Post, Goal, PenaltyGoal, etc.) |
            | `gameClock` | Yes | Time of event in seconds |
            | `PassType` | Recommended | Used to detect corners (value: "Corner") |
            | `EventXDecimal` | Recommended | X coordinate — values > 66 counted as final-third entries |
            | `shooter` | Recommended | Player name — used for goal labels |
            | `homeFinalScore` | Recommended | Final score |
            | `awayFinalScore` | Recommended | Final score |
            | `newestTeamColor` | Optional | Hex team color |

            **Note:** Own goals are not in TruMedia event data — add them manually in the sidebar after uploading.
            """)
