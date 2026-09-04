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
import matplotlib.patheffects as mpe
from matplotlib.transforms import blended_transform_factory
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.motherduck import (
    get_teams_by_league, get_games_for_team, get_momentum_events, season_label,
    get_goal_scorers_for_game, get_own_goals_for_game, get_red_cards_for_game,
    own_goal_conceding_side,
)
from shared.styles import (
    BG_COLOR, SPINE_COLOR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    add_cbs_footer, render_two_team_score_header, resolve_figsize,
    fit_fontsize,
)
from shared.colors import check_color_similarity, ensure_line_contrast
# The xG race is this chart's chronological sibling: same axis, same event
# annotations, same conventions. Import its machinery rather than carrying
# parallel copies - this page's own copies drifted on FOUR conventions
# (floor-not-broadcast minutes, a fixed-50 period split, char-count label
# widths, no retro-flip guard) before being replaced 2026-09-04.
from mostly_finished_charts.xg_race_chart import (
    _event_period, _place_goal_labels, _separate_using_secondary,
    format_broadcast_minute,
)
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
    # The two defaults below are a generic blue and a generic red. They are the
    # last resort only: the registry is asked first, then the feed value on the
    # row, so a colour corrected in the Data Manager reaches this chart too.
    from shared.motherduck import resolve_single_team_colour

    feed_home = feed_away = None
    if "newestTeamColor" in df.columns and "Team" in df.columns:
        home_rows = df[df["Team"].str.strip().str.lower() == home_team.strip().lower()]
        away_rows = df[df["Team"].str.strip().str.lower() == away_team.strip().lower()]
        if not home_rows["newestTeamColor"].dropna().empty:
            feed_home = home_rows["newestTeamColor"].dropna().iloc[0]
        if not away_rows["newestTeamColor"].dropna().empty:
            feed_away = away_rows["newestTeamColor"].dropna().iloc[0]

    home_color = resolve_single_team_colour(home_team, feed_home) or "#4A90D9"
    away_color = resolve_single_team_colour(away_team, feed_away) or "#E05C5C"

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

    # ── Minute (before any event filtering - the HT derivation needs it) ─────
    clock_col = "gameClock" if "gameClock" in df.columns else None
    if clock_col:
        df["minute"] = pd.to_numeric(df[clock_col], errors="coerce").fillna(0) / 60.0
    else:
        df["minute"] = 0.0

    # ── Half-time minute ──────────────────────────────────────────────────────
    # When the first half ENDED: the last Period-1 event of ANY type, from the
    # UNFILTERED frame. Deriving it from momentum events only ran early by
    # >1min in 14% of matches (measured on the DB path, same subset). Clamped
    # to 45 so a partial CSV degrades to "no stoppage", not a negative
    # period-2 shift that drags the second half backwards over the first.
    if "Period" in df.columns:
        p1_minutes = pd.to_numeric(
            df.loc[df["Period"] == 1, "minute"], errors="coerce"
        ).dropna()
        ht_minute = max(45.0, float(p1_minutes.max())) if len(p1_minutes) else 45.0
    else:
        ht_minute = 45.0

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

    # Keep period alongside minute so downstream code can compute a
    # chronological match-time x-axis (period 2+ events plot AFTER period 1
    # ends, even when broadcast minute would put them earlier on a naive
    # axis - e.g. 46' in second half vs 45+4 in first half).
    if "Period" in df.columns:
        events_df = df[["minute", "Period", "team_side", "event_type"]].copy()
        events_df = events_df.rename(columns={"Period": "period"}).reset_index(drop=True)
        events_df["period"] = pd.to_numeric(events_df["period"], errors="coerce").fillna(1).astype(int)
    else:
        events_df = df[["minute", "team_side", "event_type"]].copy()
        events_df["period"] = 1
        events_df = events_df.reset_index(drop=True)

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
            try:
                period = int(grow.get("Period") or 1)
            except (ValueError, TypeError):
                period = 1
            goal_scorers.append({
                "minute":   minute,
                "period":   period,
                "player":   str(grow["shooter"]),
                "team":     team,
                "team_id":  None,
                "pen":      grow[play_col] == "PenaltyGoal",
            })
        # Sort by (period, minute) so first-half stoppage (45+4) precedes
        # second-half early goals (46') instead of inverting on minute alone.
        goal_scorers.sort(key=lambda x: (x.get("period", 1), x["minute"]))

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

def _chrono_minute(minute, period, ht_minute):
    """Translate a (broadcast_minute, period) pair into chronological match
    time since kickoff.

    Period 1: clock ran continuously from kickoff, so broadcast minute
    already equals elapsed match minutes.
    Period 2+: period 2's broadcast clock restarts at minute=45 even
    though period 1 actually ran to ht_minute. Shift forward by the
    Period 1 stoppage time (ht_minute - 45) so events line up
    sequentially after Period 1 ends. Same logic extends to extra time.
    """
    try:
        p = int(period)
    except (ValueError, TypeError):
        p = 1
    if p <= 1:
        return float(minute)
    return float(minute) + (float(ht_minute) - 45.0)


def _compute_momentum(events_df, w_shots, w_corners, w_ft, ht_minute=45.0, window=5):
    """
    Compute per-minute momentum (0-100) using a rolling window.
    50 = neutral, >50 = home dominant, <50 = away dominant.
    Weights are normalised internally so they don't need to sum to 100.

    Bins on chronological match-minute (period-aware) so the returned
    Series index is monotonic across the half-time boundary - the chart
    line plots without a backward jump when Period 2 starts.
    """
    if events_df.empty:
        return pd.Series(dtype=float)

    events_df = events_df.copy()
    if "period" not in events_df.columns:
        events_df["period"] = 1
    events_df["chrono_minute"] = events_df.apply(
        lambda r: _chrono_minute(r["minute"], r.get("period", 1), ht_minute),
        axis=1,
    )

    max_min = int(events_df["chrono_minute"].max()) + 1
    minutes = range(0, max_min + 1)

    weight_map = {"shot": w_shots, "corner": w_corners, "final_third": w_ft}
    total_w = w_shots + w_corners + w_ft
    if total_w == 0:
        return pd.Series(50.0, index=minutes)

    home_score = pd.Series(0.0, index=minutes)
    away_score = pd.Series(0.0, index=minutes)

    events_df["minute_bin"] = events_df["chrono_minute"].astype(int).clip(0, max_min)
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
# would otherwise collide horizontally. Placement itself is the xG race's
# _place_goal_labels - measured label widths, edge handling, retro-flip guard.
_Y_LEVELS = (1.04, 1.12, 1.20)
_RC_COLOR = "#E53935"


# ── Aspect layouts ────────────────────────────────────────────────────────────
#
# The wave is a TIME SERIES: minutes run horizontally and the encoding cannot
# be rotated without fighting every convention a reader has. So the portrait
# aspects do not stretch it - they give it a band at a workable aspect and
# spend the rest of the frame on what the 16:9 puts ON the plot.
#
# That split is the DP family's structural prior art, which this project
# already follows on the shot chart: 16:9 carries callouts on the plot, 9:16
# moves them to a list below, 9:8 keeps lines and markers only because the
# host is speaking the names aloud beside it.
#
# TYPE FLOOR: both portrait aspects are 9in wide delivered on a phone, so
# nothing readable sits below 16pt ([[project-cbs-rollingxg-vertical]]'s
# rule, applied across the shot chart's six variant layouts the same way).

_MOMENTUM_LAYOUT_DEFAULT = {
    "aspect":          "default",
    "subplots":        dict(top=0.72, bottom=0.10, left=0.06, right=0.98),
    "axes_rect":       None,
    "kicker_size":     11,   "title_size":   22,
    "y_kicker":        0.973, "y_title":     0.942, "y_bar": 0.912,
    "subtitle_y":      0.885, "subtitle_size": 11,
    "labels_on_plot":  True,
    "event_block":     False,
    "team_label_size": 12,
    "ht_size":         11,
    "event_label_size": 13,
    "marker_size":     8,
    "card_h":          0.045, "card_w": 0.9,
    "tick_size":       10,   "axis_label_size": 10,
    "axis_words":      True,
    "ht_vertical":     False,
    "key_y":           None,
}

# 9:8 tile - the chart shares the frame with the host, who names the scorers.
# Nine 16pt labels cannot fit 9 inches of width, and the host makes them
# redundant, so the plot keeps its markers and drops its text. A marker key
# replaces them: without labels the markers carry the whole vocabulary.
_MOMENTUM_LAYOUT_9X8 = {
    "aspect":          "9x8",
    "subplots":        None,
    "axes_rect":       [0.07, 0.13, 0.90, 0.70],
    "kicker_size":     16,   "title_size":   21,
    "y_kicker":        0.975, "y_title":     0.940, "y_bar": 0.905,
    "subtitle_y":      None, "subtitle_size": None,
    "labels_on_plot":  False,
    "event_block":     False,
    "team_label_size": 16,
    "ht_size":         16,
    "event_label_size": None,
    "marker_size":     10,
    # card_w is in MINUTES, so on a ~97-minute axis 1.4 was 1.4% of the
    # width - about 3 CSS px on a phone, invisible beside 8px goal dots and
    # ambiguous with them when the carded team also plays in red.
    "card_h":          0.060, "card_w": 3.0,
    "tick_size":       16,   "axis_label_size": None,
    "axis_words":      False,
    "ht_vertical":     True,
    "key_y":           0.045,
}

# 9:16 fullscreen - the wave takes a 2.3:1 band (its natural shape) and the
# callouts become a match timeline underneath, which is what the label band
# was always trying to be. Same move as the shot chart's second block.
_MOMENTUM_LAYOUT_9X16 = {
    "aspect":          "9x16",
    "subplots":        None,
    # 0.250, not the 0.215 first drawn: the lint's plot-floor guard fired,
    # and it was right - a wave band under a quarter of the frame is the
    # chart's subject getting squeezed by its own second block. At 0.250 the
    # band is 4.0in x 7.9in, a 2:1 time series, and the block still holds
    # twelve rows.
    "axes_rect":       [0.09, 0.600, 0.88, 0.250],
    "kicker_size":     16,   "title_size":   30,
    "y_kicker":        0.975, "y_title":     0.945, "y_bar": 0.918,
    "subtitle_y":      0.900, "subtitle_size": 16,
    "labels_on_plot":  False,
    "event_block":     True,
    "team_label_size": 16,
    "ht_size":         16,
    "event_label_size": None,
    "marker_size":     10,
    "card_h":          0.060, "card_w": 3.0,   # see the 9:8 note - minutes
    "tick_size":       16,   "axis_label_size": 16,
    "axis_words":      False,
    "ht_vertical":     True,
    "key_y":           None,
    # Event timeline block
    "block_head_y":    0.535, "block_top": 0.500, "block_bot": 0.075,
    "row_step_max":    0.048,
    "head_size":       16,   "row_size":  19,
    "min_x":           0.085, "name_x":   0.215, "score_x": 0.915,
    "rule_x0":         0.070,
}

_MOMENTUM_LAYOUTS = {
    "default": _MOMENTUM_LAYOUT_DEFAULT,
    "9x8":     _MOMENTUM_LAYOUT_9X8,
    "9x16":    _MOMENTUM_LAYOUT_9X16,
}


def _draw_event_block(fig, layout, events, minute_of):
    """The match timeline: one row per goal/card, in chronological order.

    Rows are DISTRIBUTED across the band rather than stepped from its top at
    a fixed pitch - a 1-1 and a 5-4 both have to fill the same space, and a
    fixed step leaves a hole under a short list. Same rule as the shot
    chart's stat block.

    Each row: accent bar in the event's team colour, the broadcast minute,
    the player, and the running score (or RED CARD). The accent bar is what
    says WHICH side, so it takes the chrome lift - a raw navy bar vanishes.
    """
    if not events:
        return
    x0 = layout["rule_x0"]
    head_y = layout["block_head_y"]
    fig.text(x0, head_y, "MATCH EVENTS", ha="left", va="center",
             fontsize=layout["head_size"], fontweight="bold", color=TEXT_MUTED)
    fig.text(layout["score_x"], head_y, "SCORE", ha="right", va="center",
             fontsize=layout["head_size"], fontweight="bold", color=TEXT_MUTED)
    fig.patches.append(mpatches.Rectangle(
        (x0, head_y - 0.011), layout["score_x"] - x0, 0.0008,
        transform=fig.transFigure, facecolor="#31435A", edgecolor="none",
        zorder=3))

    # Distribute, but CAP the pitch and top-align. Pure distribution is the
    # shot chart's rule and it works there because that block always holds
    # about five rows; a match has as few as two events, and a 1-1 spread
    # over the whole band put two lines of text in ~1000px of empty navy -
    # a legitimate scoreline reading as a failed render. Capped, the spare
    # space falls at the BOTTOM, where it is breathing room.
    band = layout["block_top"] - layout["block_bot"]
    step = min(band / max(len(events), 1), layout["row_step_max"])
    size = layout["row_size"]
    for i, ev in enumerate(events):
        y = layout["block_top"] - step * (i + 0.5)
        is_rc = ev["type"] == "rc"
        accent = ensure_line_contrast(
            _RC_COLOR if is_rc else ev["color"], BG_COLOR)
        fig.patches.append(mpatches.Rectangle(
            (x0, y - 0.010), 0.005, 0.020, transform=fig.transFigure,
            facecolor=accent, edgecolor="none", zorder=4))
        fig.text(layout["min_x"], y, f"{minute_of(ev)}'", ha="left",
                 va="center", fontsize=size, color=TEXT_SECONDARY, zorder=4)
        fig.text(layout["name_x"], y, (ev.get("label") or "").upper(),
                 ha="left", va="center", fontsize=size,
                 fontweight="bold" if not is_rc else "normal",
                 fontstyle="italic" if ev.get("og") else "normal",
                 color=TEXT_PRIMARY if not is_rc else TEXT_SECONDARY, zorder=4)
        right = "RED CARD" if is_rc else ev.get("score", "")
        fig.text(layout["score_x"], y, right, ha="right", va="center",
                 fontsize=size, fontweight="bold",
                 color=_RC_COLOR if is_rc else TEXT_PRIMARY, zorder=4)

    # Close the table. The header rule spans the full width and PROMISES a
    # table; with two events and a capped row pitch, the rows stopped and
    # nothing said so - two lines under an open-ended header is the visual
    # signature of rows that failed to load. A closing rule bounds the list,
    # so the space beneath it is plainly outside the table rather than
    # missing from it.
    last_y = layout["block_top"] - step * (len(events) - 0.5)
    fig.patches.append(mpatches.Rectangle(
        (x0, last_y - step * 0.5), layout["score_x"] - x0, 0.0008,
        transform=fig.transFigure, facecolor="#31435A", edgecolor="none",
        zorder=3))


def _draw_momentum_chart(momentum, match_info, goal_scorers,
                         own_goals=None, red_cards=None,
                         competition="", custom_title=None, custom_subtitle=None,
                         aspect="default"):
    layout = _MOMENTUM_LAYOUTS.get(aspect, _MOMENTUM_LAYOUT_DEFAULT)
    home_name = match_info["home_team"]
    away_name = match_info["away_team"]
    home_team_id = match_info.get("home_team_id")
    home_score = match_info["home_score"]
    away_score = match_info["away_score"]
    # Colour pipeline: a clash reaches for each club's OWN second colour first
    # (registry, distance-based on the lifted colours), falling back to the
    # old alternate dictionary; then each colour is lifted for WCAG contrast
    # against the dark background. Same order as the xG race and shot chart.
    #
    # NO luminance separation after the lift. That step exists for the
    # rolling-xG charts, where two thin lines interleave and isoluminant
    # pairs genuinely blur; here home is ALWAYS the fill above the 50-line
    # and away always below, so position disambiguates and the separation
    # only washed identity out - Strasbourg's #009FE3 rendered as lavender
    # against Monaco red, two colours nobody could confuse.
    swapped_home, swapped_away, resolved = _separate_using_secondary(
        match_info["home_color"], match_info["away_color"],
        match_info.get("home_secondary"), match_info.get("away_secondary"),
    )
    if not resolved:
        swapped_home, swapped_away, _ = check_color_similarity(
            swapped_home, swapped_away,
            home_name, away_name, threshold=150, interactive=False,
        )
    home_color = ensure_line_contrast(swapped_home, BG_COLOR)
    away_color = ensure_line_contrast(swapped_away, BG_COLOR)

    ht_minute = float(match_info.get("ht_minute", 45.0))
    date = match_info["date"]

    mins = np.array(momentum.index, dtype=float)
    vals = np.array(momentum.values, dtype=float)
    # +1.5 breathing room past the last event: a 90+1 winner IS often the last
    # event of the match, and chart_max == its chrono position put the marker
    # and label flush on the canvas edge, reading as cropped.
    chart_max = float(max((max(mins) + 1.5) if len(mins) else 90, 90))

    # ── Figure + axes ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=resolve_figsize(layout["aspect"]),
                           facecolor=BG_COLOR)
    if layout["subplots"] is not None:
        fig.subplots_adjust(**layout["subplots"])
    else:
        ax.set_position(layout["axes_rect"])
    ax.set_facecolor(BG_COLOR)

    # ── Header (xG race conventions) ──────────────────────────────────────────────
    render_two_team_score_header(
        fig,
        home_name=home_name, home_score=home_score, home_color=home_color,
        away_name=away_name, away_score=away_score, away_color=away_color,
        kicker="MATCH MOMENTUM",
        custom_title=custom_title,
        fontsize_kicker=layout["kicker_size"],
        fontsize_title=fit_fontsize(
            fig, custom_title or f"{home_name.upper()} {home_score}-{away_score} "
            f"{away_name.upper()}", layout["title_size"], floor=16),
        y_kicker=layout["y_kicker"], y_title=layout["y_title"],
        y_bar=layout["y_bar"],
    )

    subtitle_parts = [p for p in [competition.upper() if competition else "", date] if p]
    auto_subtitle = " | ".join(subtitle_parts)
    display_subtitle = custom_subtitle or auto_subtitle
    if display_subtitle and layout["subtitle_y"] is not None:
        fig.text(0.5, layout["subtitle_y"], display_subtitle, ha="center",
                 color=TEXT_SECONDARY, fontsize=layout["subtitle_size"])

    # Data-x, axes-y: everything anchored to a MINUTE but positioned relative
    # to the plot rather than to a momentum value - markers, event labels,
    # and the half-time label on the portrait aspects.
    label_transform = blended_transform_factory(ax.transData, ax.transAxes)

    # ── Filled momentum areas + white trajectory line ────────────────────────────────────────
    ax.fill_between(mins, vals, 50, where=(vals >= 50),
                    color=home_color, alpha=0.55, interpolate=True, linewidth=0)
    ax.fill_between(mins, vals, 50, where=(vals <= 50),
                    color=away_color, alpha=0.55, interpolate=True, linewidth=0)
    ax.plot(mins, vals, color="white", linewidth=2.0, alpha=0.9)

    # 50-line + HT marker (real half-time, not hardcoded 45)
    ax.axhline(50, color=SPINE_COLOR, linewidth=1.0, alpha=0.6)
    # Visible enough to be the thing its own label points at. At 0.8/0.45
    # this line measured 1.45:1 against the background while "HALF TIME"
    # measured 6.47:1 - a caption four times more visible than its referent,
    # so the eye attached it to the nearest line it COULD see. On Wolves v
    # Fulham that was Robinson's 45+3 goal line 24px away, and the label read
    # as naming the goal. Dashed and grey still separates it from the
    # team-coloured dotted goal lines.
    ax.axvline(ht_minute, color=SPINE_COLOR, linewidth=1.6,
               linestyle="--", alpha=0.85)
    # Inside the plot at the line's top, the xG race's exact treatment - the
    # backgrounded box keeps it legible over the wave. Floating it above the
    # plot put it inside the goal-label band, where a cold designer read it
    # as a stray third line of the nearest goal label.
    # On the narrow aspects it sits ABOVE the plot, over its own line. Two
    # earlier placements were both wrong and both measured:
    #   inside, at the top   - collided with any long club name, because a
    #                          16pt label and a 16pt team name both want the
    #                          top tenth of a 4in-tall wave
    #   inside, rotated      - the opaque halo box punched a rectangular hole
    #                          through the fill, and a chart that ALSO breaks
    #                          its fill at half time then had two identical
    #                          navy gaps meaning different things
    # Above the plot it needs no halo at all: nothing is behind it.
    if layout["ht_vertical"]:
        # The line CONTINUES up to the caption. Without the leader the
        # caption floated 50px of empty air above the plot, in the same grey
        # bold caps as the "MATCH MOMENTUM" kicker directly above it, and
        # read as a second kicker rather than as an annotation on a
        # position. Extending its own dashes to meet it settles what it
        # points at.
        ax.plot([ht_minute, ht_minute], [1.0, 1.043],
                transform=label_transform, color=SPINE_COLOR, linewidth=1.6,
                linestyle="--", alpha=0.85, clip_on=False, zorder=3)
        ax.text(ht_minute, 1.05, "HALF TIME", transform=label_transform,
                color=TEXT_SECONDARY, fontsize=layout["ht_size"],
                fontweight="bold", ha="center", va="bottom", alpha=0.85,
                clip_on=False)
    else:
        ax.text(ht_minute, 97, "HALF TIME", color=TEXT_SECONDARY,
                fontsize=layout["ht_size"], fontweight="bold", ha="center",
                va="top", alpha=0.85,
                bbox=dict(facecolor=BG_COLOR, edgecolor="none", pad=2))

    # ── Build all events (goals + own goals + red cards) ───────────────────────────────────
    import difflib as _dl

    def _is_home(team_name, team_id=None):
        if team_id and home_team_id:
            return str(team_id) == str(home_team_id)
        hs = _dl.SequenceMatcher(None, (team_name or "").lower(), home_name.lower()).ratio()
        as_ = _dl.SequenceMatcher(None, (team_name or "").lower(), away_name.lower()).ratio()
        return hs >= as_

    # Each event carries broadcast minute (for labels) + period (for sort
    # and chrono shift). chrono_x is the chronological match time since
    # kickoff - Period 1 events at their broadcast minute, Period 2+
    # appended after Period 1 ends so they plot sequentially after the
    # half-time line instead of overlapping with first-half stoppage.
    # See _chrono_minute(). Period inference for manual events splits at the
    # REAL whistle (xG race's _event_period), not a fixed 50.
    def _ev_period(ev):
        return _event_period(ev, ht_minute)

    all_events = []
    for g in (goal_scorers or []):
        all_events.append({
            "type": "goal", "minute": g["minute"], "team": g["team"],
            "period": g.get("period"),
            "team_id": g.get("team_id"),
            "label": g["player"] + (" (P)" if g.get("pen") else ""),
            "og": False,
        })
    for og in (own_goals or []):
        # Named like every other goal when the data knows the toucher -
        # "F. Lejeune (OG)" - the xG race convention. Bare "OG" only for
        # manually-added ones with no player.
        _p = og.get("player")
        all_events.append({
            "type": "goal", "minute": og["minute"], "team": og["team"],
            "period": og.get("period"),
            "team_id": None,
            "label": f"{_p} (OG)" if _p else "OG", "og": True,
        })
    for rc in (red_cards or []):
        if rc.get("card_type") and rc.get("card_type") not in ("red", "second_yellow"):
            continue
        all_events.append({
            "type": "rc", "minute": rc["minute"], "team": rc["team"],
            "period": rc.get("period"),
            "team_id": rc.get("team_id"),
            "label": rc.get("player", ""),
        })
    for ev in all_events:
        ev["chrono_x"] = _chrono_minute(ev["minute"], _ev_period(ev), ht_minute)
    all_events.sort(key=lambda x: (_ev_period(x), x["minute"]))

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

    if layout["labels_on_plot"]:
        _place_goal_labels(all_events, chart_max, ax=ax)

    # ── Render events (goals, OGs, red cards) ────────────────────────────────────────────
    for ev in all_events:
        flip_left = ev.get("x_side") == "left"
        label_y = _Y_LEVELS[ev.get("y_level", 0)]
        # chrono_x is the chart x-position (chronological match time since
        # kickoff). ev["minute"] is the broadcast minute kept only for the
        # label text, rendered the way broadcast writes it - floor+1, and
        # 45+2 rather than 47 (format_broadcast_minute; this page used to
        # print the raw floor, so every label sat one minute behind the
        # xG race's for the same goal).
        x_pos = ev.get("chrono_x", ev["minute"])
        label_x = x_pos - 0.6 if flip_left else x_pos + 0.6
        label_ha = "right" if flip_left else "left"
        side_color = home_color if ev["side"] == "home" else away_color
        minute_str = format_broadcast_minute(ev["minute"], _ev_period(ev))
        ev["color"] = side_color

        # Leader through the label band: the in-plot dotted line stops at the
        # plot top, but a label can sit three stacking rows above it - on a
        # nine-label chart nothing tied a label to its line and a cold
        # designer called dot-to-label matching "a colour-guessing game".
        # Only where labels ARE on the plot; the portrait aspects list their
        # events below instead, so a leader would point at nothing.
        if layout["labels_on_plot"]:
            ax.plot([x_pos, x_pos], [1.0, label_y], transform=label_transform,
                    linestyle=":",
                    color=side_color if ev["type"] == "goal" else _RC_COLOR,
                    linewidth=1.0, alpha=0.45, clip_on=False, zorder=4)

        if ev["type"] == "goal":
            ax.axvline(x_pos, color=side_color, linewidth=1.2,
                       linestyle=":", alpha=0.8)
            if ev["og"]:
                # A ring is identified by its HOLE, and the hole is what a
                # shrink takes first - worse, the OG marker kept landing in
                # the tightest clusters (55'/58'/61' sat 9px apart). Drawn
                # larger so the interior survives at phone size.
                ax.plot(x_pos, 1.005, "o", transform=label_transform,
                        markerfacecolor='none', markeredgecolor=side_color,
                        markersize=layout["marker_size"] * 1.3,
                        markeredgewidth=1.8,
                        clip_on=False, zorder=5)
            else:
                ax.plot(x_pos, 1.005, "o", transform=label_transform,
                        color=side_color, markersize=layout["marker_size"],
                        markeredgecolor="white", markeredgewidth=1.2,
                        clip_on=False, zorder=5)
            if layout["labels_on_plot"]:
                text = f"{ev['label']} ({minute_str}')\n{ev['score']}"
                ax.text(label_x, label_y, text, transform=label_transform,
                        color=side_color, fontsize=layout["event_label_size"],
                        fontweight="bold", va="bottom", ha=label_ha,
                        fontstyle="italic" if ev["og"] else "normal",
                        clip_on=False)

        else:  # rc
            # The STEM carries the team, the card carries the offence. The
            # glyph is red for every club because a red card is red, so with
            # a red stem too there was nothing on the variants saying WHOSE
            # card it was - on Strasbourg v Monaco it happened to be the red
            # team's, and the chart got the right answer by coincidence.
            ax.axvline(x_pos, color=side_color, linewidth=1.4,
                       linestyle="-.", alpha=0.85)
            # Card-shaped marker: vertical red rectangle at chart top edge,
            # tall enough to read as a CARD - at the old 0.028 height it was
            # a squashed rectangle in a row of circles, and when the carded
            # team's goals are also red it read as a tenth goal dot.
            # Card geometry is derived, not declared. Height is in AXES
            # fraction and width in MINUTES, so a fixed pair drew a 31x47
            # card on the tile and a 30x33 one on the phone - the second
            # stops reading as a card. Width is computed from the axes'
            # own pixel aspect to hold a constant 1:1.4 portrait shape.
            card_h_axes = layout["card_h"]
            _abox = ax.get_window_extent()
            _xspan = ax.get_xlim()[1] - ax.get_xlim()[0]
            card_w_min = ((card_h_axes * _abox.height / 1.4)
                          / max(_abox.width, 1) * _xspan)
            # Centred on the marker row, not sitting on top of it: anchored
            # at the plot edge the card's middle rode 21px above every goal
            # dot, breaking the skyline the row is supposed to make.
            card = mpatches.Rectangle(
                (x_pos - card_w_min / 2, 1.005 - card_h_axes / 2),
                card_w_min, card_h_axes,
                facecolor=_RC_COLOR, edgecolor='white', linewidth=1.5,
                transform=label_transform, clip_on=False, zorder=6,
            )
            ax.add_patch(card)
            if layout["labels_on_plot"]:
                player = ev.get("label", "")
                text = (f"{player} ({minute_str}')\nRED CARD"
                        if player else f"RED CARD ({minute_str}')")
                ax.text(label_x, label_y, text, transform=label_transform,
                        color=side_color, fontsize=layout["event_label_size"],
                        fontweight="bold", va="bottom", ha=label_ha,
                        clip_on=False)

    # ── Team labels at top-left and bottom-left of plot ─────────────────────────────────────
    # These are what say which half of the wave is whose, so on the portrait
    # aspects - where the event labels are gone - they are the only in-plot
    # attribution and carry the phone type floor.
    # A STROKE around the glyphs, not a filled box behind them. Both stop the
    # event lines striking the text through, but a box also erases whatever
    # else is behind it - and what is behind it is the wave. Measured on Real
    # Madrid v Athletic, the box under "ATHLETIC CLUB" cut the away side's
    # biggest surge of the match into three disconnected slices and left an
    # orphaned sliver of white stroke with no fill under it. A stroke hugs
    # the letterforms, so it costs a few pixels around glyphs instead of a
    # rectangle of data.
    #
    # They sit in the HEADROOM, outside the wave's reach entirely. Momentum
    # spans 0-100 and the axis runs -8..108, so axes fraction 0.94 is data
    # 101 and 0.06 is data -1: the wave cannot get there by construction.
    # At the old 0.96/0.04 the text extended back INTO the range - the
    # headroom fix above then pushed the deepest troughs into the away
    # label, and Athletic's biggest spike of the match ended inside the word
    # "CLUB" with no visible tip. Anchoring them away from the plot is what
    # the headroom is for; no dodging logic needed.
    _stroke = [mpe.withStroke(linewidth=3.0, foreground=BG_COLOR)]
    ax.text(0.005, 0.94, home_name.upper(), transform=ax.transAxes,
            color=home_color, fontsize=layout["team_label_size"],
            fontweight="bold", va="bottom", alpha=0.95,
            path_effects=_stroke, zorder=7)
    ax.text(0.005, 0.06, away_name.upper(), transform=ax.transAxes,
            color=away_color, fontsize=layout["team_label_size"],
            fontweight="bold", va="top", alpha=0.95,
            path_effects=_stroke, zorder=7)

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
    # 10pt, not 9: a 16in-wide figure delivers 9pt at 15px, under the 16px
    # laptop floor the lint derives - the race's ticks clear it.
    ax.tick_params(axis="x", colors=TEXT_SECONDARY,
                   labelsize=layout["tick_size"])
    # The axis WORDS drop on the portrait aspects: at the 16pt floor they
    # cost a band each, and both are already said by the chart - the ticks
    # are self-evidently minutes on a match chart, and the y-axis is named
    # by the two team labels inside the plot.
    if layout["axis_words"]:
        ax.set_xlabel("Minute", color=TEXT_SECONDARY,
                      fontsize=layout["axis_label_size"])
        ax.set_ylabel("Momentum", color=TEXT_SECONDARY,
                      fontsize=layout["axis_label_size"])
    # Headroom at both ends. Momentum spans 0-100 by construction, so at
    # (-2, 102) the most dominant spell in the match was drawn flat against
    # the axis floor - measured, 14 columns sitting on the spine with the
    # white stroke stopping short of its own fill. A clipped peak cannot say
    # how deep the pressure went, and reads as the frame slicing the shape.
    ax.set_ylim(-8, 108)
    ax.set_xlim(0, chart_max)
    # X-axis ticks show BROADCAST minute, positioned at the chrono_x where
    # that broadcast minute actually occurs. Same convention as xG race -
    # without this, a "60" tick lands at chrono_x = 60 while a 60' event
    # actually plots at chrono_x = 60 + (ht_minute - 45), and the label
    # disagrees with the event marker beside it.
    p2_offset = max(0.0, float(ht_minute) - 45.0)
    broadcast_ticks = [0, 15, 30, 45, 60, 75, 90]
    if chart_max > 95 + p2_offset:
        broadcast_ticks += [105, 120]
    tick_positions, tick_labels = [], []
    for b in broadcast_ticks:
        pos = b if b <= 45 else b + p2_offset
        if pos <= chart_max:
            tick_positions.append(pos)
            tick_labels.append(str(b))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)
    ax.yaxis.grid(True, color=SPINE_COLOR, alpha=0.18, linewidth=0.5)
    ax.set_axisbelow(True)

    # ── Portrait: the callouts become a match timeline below the wave ─────────
    if layout["event_block"]:
        _draw_event_block(
            fig, layout, all_events,
            lambda ev: format_broadcast_minute(ev["minute"], _ev_period(ev)))

    # ── Tile: a marker key, because the tile has no labels ────────────────────
    # Without it the markers are unexplained - a dot and a red rectangle
    # floating over a wave. Named only for what is actually ON this chart:
    # a key that lists an own goal on a match with none is furniture.
    if layout["key_y"] is not None and all_events:
        # Drawn item by item so the card's swatch can be RED. A card's whole
        # message is its colour, and a grey slab in the key threw that away
        # while the chart above showed a red rectangle - the key was
        # describing a different mark.
        bits = [("●  GOAL", TEXT_MUTED)]
        if any(e.get("og") for e in all_events):
            bits.append(("○  OWN GOAL", TEXT_MUTED))
        if any(e["type"] == "rc" for e in all_events):
            bits.append(("▮", _RC_COLOR))
            bits.append(("RED CARD", TEXT_MUTED))
        fig.canvas.draw()
        inv = fig.transFigure.inverted()
        widths = []
        for txt, _c in bits:
            probe = fig.text(0, -1, txt, fontsize=16)
            widths.append(probe.get_window_extent(
                renderer=fig.canvas.get_renderer()).transformed(inv).width)
            probe.remove()
        gap = 0.018
        total = sum(widths) + gap * (len(bits) - 1)
        x = 0.5 - total / 2
        for (txt, colour), w in zip(bits, widths):
            fig.text(x, layout["key_y"], txt, ha="left", va="center",
                     fontsize=16, color=colour)
            x += w + gap

    # ── Footer (standard convention) ──────────────────────────────────────────────
    add_cbs_footer(fig)

    return fig

# ── Page ───────────────────────────────────────────────────────────────────────

st.title("Match Momentum")
st.markdown("Rolling momentum balance using shots, corners, and final-third entries.")

aspect_choice = st.sidebar.radio(
    "Aspect ratio",
    options=["Standard (16:9)", "Tile (9:8)", "Vertical (9:16)"],
    index=0,
    help="In-video overlay aspects for PodcastShorts. "
         "9:8 = SBS tile (the chart shares the frame with the host, who "
         "names the scorers, so the wave keeps its markers and drops the "
         "text labels). 9:16 = fullscreen overlay; the callouts become a "
         "match timeline listed below the wave.",
)
aspect_param = ("9x8" if aspect_choice.startswith("Tile")
                else "9x16" if aspect_choice.startswith("Vertical")
                else "default")

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


def _own_goals_sidebar(home_team, away_team, auto_ogs, key_prefix,
                       game_id=None):
    """Render own goals sidebar and return list of {minute, team} dicts.

    `game_id` resolves an own goal's `teamId` to a side. The CSV path has no
    game id, but it also passes an empty `auto_ogs`, so nothing needs it.
    """
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
            # "Scored by" means the own-goal scorer, i.e. the CONCEDING side -
            # which is what both sources name.
            _side = own_goal_conceding_side(
                game_id, auto_ogs[i].get("teamId"),
                auto_ogs[i].get("credited_team"), home_team, away_team)
            default_scorer_idx = 1 if _side == "away" else 0
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
        # Carry the data's period and player through untouched edits. Losing
        # them here forced the chart back onto minute-based period inference
        # and a bare "OG" label - the exact pair of defects fixed on the
        # xG race page. An edited minute drops the period (it may no longer
        # be true) but keeps the player.
        og_period = og_player = None
        if i < len(auto_ogs):
            og_player = auto_ogs[i].get("player")
            if og_minute == default_minute:
                og_period = auto_ogs[i].get("period")
        own_goals.append({"minute": og_minute, "team": credited_team,
                          "period": og_period, "player": og_player})
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

    ht_minute = float(match_info.get("ht_minute", 45.0))

    # Operator guard: corrupt source data renders as confident nonsense here
    # (a half-match with one side's rows missing drew "Valencia 3-0, Barcelona
    # nonexistent" under a 3-1 title; a feed truncated at 21' drew a 0-0 that
    # read as abandoned). The chart cannot fix the data; the person about to
    # publish it can decide. Name the problem, still render.
    problems = []
    sides = events_df["team_side"].value_counts()
    for side, name_key in (("home", "home_team"), ("away", "away_team")):
        if sides.get(side, 0) == 0:
            problems.append(
                f"no events at all for {match_info[name_key]} — the source "
                f"data for this match looks like half a match")
    last_chrono = max(
        _chrono_minute(r["minute"], r.get("period", 1), ht_minute)
        for _, r in events_df.iterrows()
    ) if not events_df.empty else 0
    if last_chrono < 80:
        problems.append(
            f"event data ends at minute {last_chrono:.0f} — the match feed "
            f"looks truncated")
    total_goals = len(goal_scorers or []) + len(own_goals or [])
    title_goals = int(match_info.get("home_score", 0)) + int(match_info.get("away_score", 0))
    if total_goals != title_goals:
        problems.append(
            f"{total_goals} goal event(s) found but the final score says "
            f"{title_goals} — a goal will be missing from the chart")
    for p in problems:
        st.warning(f"Data integrity: {p}. The chart will render, but check "
                   f"the game's download status before publishing.")

    momentum = _compute_momentum(events_df, w_shots, w_corners, w_ft,
                                 ht_minute=ht_minute, window=window)
    fig = _draw_momentum_chart(
        momentum, match_info, goal_scorers,
        own_goals=own_goals,
        red_cards=red_cards,
        competition=competition,
        custom_title=custom_title,
        custom_subtitle=custom_subtitle,
        aspect=aspect_param,
    )

    home_slug = match_info["home_team"].replace(" ", "_")
    away_slug = match_info["away_team"].replace(" ", "_")
    suffix = "" if aspect_param == "default" else f"_{aspect_param}"
    fname = f"momentum_{home_slug}_vs_{away_slug}{suffix}.png"

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
                    if g.get("season_id"):
                        season_options[g["season_id"]] = season_label(
                            g["season_id"], g.get("season_name"))
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
            auto_ogs, key_prefix=selected_game["game_id"],
            game_id=selected_game["game_id"]
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
