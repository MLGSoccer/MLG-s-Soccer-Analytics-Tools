"""High-level chart assembly: title, subtitles, legend, stats row, pitch,
stats card, footer. Produces the final figure objects.

Depends on:
    .colors   — color math for contrast + brand preservation
    .data     — loading, reconciliation, highlight classification, types
    .drawing  — low-level marker drawing
"""
import os

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from mplsoccer import Pitch, VerticalPitch

from shared.colors import TEAM_COLORS, fuzzy_match_team
from shared.styles import (
    BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, add_cbs_footer,
    render_two_team_score_header,
)

from .colors import (
    PITCH_COLOR, check_bg_contrast, ensure_bg_readable, ensure_pitch_contrast,
)
from .data import (
    GOAL_TYPES, classify_highlight, compute_highlight_stats, compute_pen_stats,
    detect_csv_mode, load_multi_match_shot_data, load_shot_data, reconcile_team_goals,
)
from .drawing import (
    compute_ylim_floor, plot_shots_horizontal, plot_shots_vertical,
)


def create_team_shot_chart(shots_df, team_name, team_color, match_info,
                           opponent_name, team_final_score=0, opponent_goals=0,
                           own_goals_for=0, own_goals_against=0,
                           flip_coords=False, competition='',
                           exclude_penalties=False, highlight_mode='All',
                           player_name=None, is_home=True,
                           custom_title=None, custom_subtitle=None):
    """Create a single team's shot chart using mplsoccer VerticalPitch."""
    pitch = VerticalPitch(
        pitch_type='opta',
        half=True,
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=1.3,
        goal_type='box',
        pad_top=3,
        pad_bottom=0,
        pad_left=1,
        pad_right=1
    )

    fig, ax = plt.subplots(figsize=(12, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Green rectangle for the pitch area (inside the lines)
    # VerticalPitch half=True: y=0-100 (width), x=50-100 (attacking half)
    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Penalty stats from shots_df BEFORE the filter. Use pre-computed stats
    # from match_info if available (CSV path), else derive from unfiltered
    # shots_df (DB path). Ensures labeling is correct regardless of data path.
    pen_map_cached = match_info.get('pen_stats_by_team') or {}
    pen_stats = pen_map_cached.get(team_name) or compute_pen_stats(shots_df)

    # Filter penalties before classifying or plotting
    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    # Classify shots for highlighting
    shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Clip view: dynamic floor crops dead space, expands if a shot lives there
    ax.set_xlim(-1, 101)
    ax.set_ylim(compute_ylim_floor(shots_df, flip_coords=flip_coords), 103)

    # Plot shots
    plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=flip_coords,
                        highlight_mode=highlight_mode)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
    highlight_stats = compute_highlight_stats(shots_df, highlight_mode)

    # Primary title: identifies whose chart this is (team or player)
    shot_map_label = "NON-PENALTY SHOT MAP" if exclude_penalties else "SHOT MAP"
    if custom_title:
        title_text = custom_title
    elif player_name:
        title_text = f"{player_name.upper()} {shot_map_label}"
    else:
        title_text = f"{team_name.upper()} {shot_map_label}"

    title_obj = fig.suptitle(title_text, fontsize=20, fontweight='bold',
                              color=TEXT_PRIMARY, y=0.97)

    # Team color accent bar matching title width
    fig.canvas.draw()
    title_bbox = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    title_bbox_fig = title_bbox.transformed(fig.transFigure.inverted())
    bar_edge = 'white' if not check_bg_contrast(team_color) else 'none'
    bar_lw = 0.8 if bar_edge == 'white' else 0
    fig.patches.append(Rectangle(
        (title_bbox_fig.x0, 0.933), title_bbox_fig.width, 0.005,
        transform=fig.transFigure, facecolor=team_color,
        edgecolor=bar_edge, linewidth=bar_lw, zorder=10
    ))

    # Subtitle: match context (score, opponent, competition, date)
    if custom_subtitle:
        fig.text(0.5, 0.91, custom_subtitle, ha='center', va='center',
                 fontsize=11, color=TEXT_SECONDARY)
    else:
        # Match line — score from the focal team's perspective
        if player_name:
            # Player chart: show full match matchup so team context is clear
            if is_home:
                match_str = f"{team_name.upper()} {team_final_score}-{opponent_goals} {opponent_name.upper()}"
            else:
                match_str = f"{opponent_name.upper()} {opponent_goals}-{team_final_score} {team_name.upper()}"
        else:
            match_str = f"{team_final_score}-{opponent_goals} vs {opponent_name.upper()}"

        subtitle_parts = [match_str]
        if highlight_mode != 'All':
            subtitle_parts.append(f"{highlight_mode.upper()} SHOTS HIGHLIGHTED")
        if competition:
            subtitle_parts.append(competition.upper())
        if match_info.get('date_formatted'):
            subtitle_parts.append(match_info['date_formatted'])

        fig.text(0.5, 0.91, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Legend: shape-only (Goal = star, Shot = circle; size encodes xG qualitatively)
    legend_handles = [
        Line2D([0], [0], marker='*', color='none', markerfacecolor=team_color,
               markeredgecolor='white', markeredgewidth=1, markersize=14, label='Goal'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor=team_color,
               markeredgecolor='white', markeredgewidth=1, markersize=10,
               label='Shot'),
    ]
    fig.legend(handles=legend_handles, loc='upper center',
                bbox_to_anchor=(0.5, 0.89), ncol=2, frameon=False,
                fontsize=10, labelcolor=TEXT_SECONDARY,
                handletextpad=0.5, columnspacing=3.0)

    # Stats row: large numbers with small uppercase labels beneath.
    # Label xG explicitly as "Non-Pen xG" when penalties are filtered, so the
    # number's meaning is unambiguous without needing inline reconciliation.
    xg_label = "Non-Pen xG" if exclude_penalties else "xG"
    stat_cols = [
        (0.30, str(total_shots), "SHOTS"),
        (0.50, f"{total_xg:.2f}", xg_label),
        (0.70, str(goals), "GOALS"),
    ]
    for x, val, lbl in stat_cols:
        fig.text(x, 0.095, val, ha='center', va='center',
                 fontsize=26, fontweight='bold', color=TEXT_PRIMARY)
        fig.text(x, 0.055, lbl, ha='center', va='center',
                 fontsize=10, color=TEXT_SECONDARY)

    # Inline modifiers next to the primary numbers at smaller font:
    #   GOALS: "(+N OG)" and/or "(+N pen)" when filter hides pen goals
    #   xG:    "(+X.XX pen)" when filter hides penalty xG (paired with goal pen)
    pen_goals = pen_stats.get('goals', 0)
    pen_xg = pen_stats.get('xg', 0.0)
    show_pen_annotation = exclude_penalties and pen_goals > 0

    goals_extras = []
    if own_goals_for > 0:
        goals_extras.append(f"+{own_goals_for} OG")
    if show_pen_annotation:
        goals_extras.append(f"+{pen_goals} pen")
    if goals_extras:
        fig.text(0.715, 0.095, f"({', '.join(goals_extras)})",
                 ha='left', va='center',
                 fontsize=14, color=TEXT_SECONDARY)

    if show_pen_annotation:
        fig.text(0.545, 0.095, f"(+{pen_xg:.2f} pen)",
                 ha='left', va='center',
                 fontsize=14, color=TEXT_SECONDARY)

    if highlight_stats:
        hl_text = (f"{highlight_mode}:  {highlight_stats['shots']} shots  ·  "
                   f"{highlight_stats['xg']:.2f} xG  ·  {highlight_stats['goals']} goals")
        fig.text(0.5, 0.025, hl_text, ha='center', va='center',
                 fontsize=10, color=TEXT_SECONDARY, style='italic')

    plt.tight_layout(rect=[0.02, 0.14, 0.98, 0.84])

    add_cbs_footer(fig)
    fig.text(0.5, 0.01, 'Circle size = xG', ha='center', va='bottom',
             fontsize=8, color=TEXT_MUTED, style='italic')

    return fig


def create_multi_match_shot_chart(shots_df, team_name, team_color, multi_match_info,
                                   competition='', player_name=None,
                                   exclude_penalties=False, highlight_mode='All',
                                   shots_against=False,
                                   custom_title=None, custom_subtitle=None,
                                   minutes=None):
    """Create a multi-match shot chart for one team on a vertical half-pitch.

    Marker style:
        - Non-goals: black fill, white edge, circle
        - Goals: team_color fill, white edge, circle
        - Size scaled by xG
    """
    pitch = VerticalPitch(
        pitch_type='opta',
        half=True,
        pitch_color='none',
        line_color='white',
        linewidth=1.3,
        goal_type='box',
        pad_top=3,
        pad_bottom=0,
        pad_left=1,
        pad_right=1
    )

    fig, ax = plt.subplots(figsize=(12, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Filter penalties before classifying or plotting
    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    # Classify shots for highlighting
    shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Clip view: dynamic floor crops dead space, expands if a shot lives there
    ax.set_xlim(-1, 101)
    ax.set_ylim(compute_ylim_floor(shots_df), 103)

    # Plot shots with multi-match marker style (per-row flipping via _needs_flip)
    plot_shots_vertical(ax, pitch, shots_df, team_color, marker_style='multi',
                        highlight_mode=highlight_mode)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
    total_matches = multi_match_info.get('total_matches', 0)
    shots_per_game = total_shots / total_matches if total_matches > 0 else 0
    highlight_stats = compute_highlight_stats(shots_df, highlight_mode)

    # Derive season string from date range (e.g. "2025-26")
    season_str = ''
    if 'Date' in shots_df.columns:
        dates = pd.to_datetime(shots_df['Date'], errors='coerce').dropna()
        if not dates.empty:
            min_year = dates.min().year
            max_year = dates.max().year
            if min_year == max_year:
                season_str = str(min_year)
            else:
                season_str = f"{min_year}-{str(max_year)[-2:]}"

    # Title and subtitle
    map_label = "SHOTS AGAINST MAP" if shots_against else "SHOT MAP"
    if player_name:
        if shots_against:
            auto_title = f"{player_name.upper()} SHOTS AGAINST {team_name.upper()} {season_str}".strip()
            subtitle_parts = []
        else:
            auto_title = f"{player_name.upper()} {season_str} SHOT MAP".strip()
            subtitle_parts = [team_name.upper()]
    else:
        auto_title = f"{team_name.upper()} {season_str} {map_label}".strip()
        subtitle_parts = []

    title_obj = fig.suptitle(custom_title or auto_title,
                              fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Team color accent bar matching title width
    fig.canvas.draw()
    title_bbox = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    title_bbox_fig = title_bbox.transformed(fig.transFigure.inverted())
    bar_x = title_bbox_fig.x0
    bar_width = title_bbox_fig.width
    bar_edge = 'white' if not check_bg_contrast(team_color) else 'none'
    bar_lw = 0.8 if bar_edge == 'white' else 0
    fig.patches.append(Rectangle(
        (bar_x, 0.933), bar_width, 0.005,
        transform=fig.transFigure, facecolor=team_color,
        edgecolor=bar_edge, linewidth=bar_lw, zorder=10
    ))

    if custom_subtitle:
        fig.text(0.5, 0.91, custom_subtitle, ha='center', va='center',
                 fontsize=11, color=TEXT_SECONDARY)
    else:
        if competition:
            subtitle_parts.append(competition.upper())
        # Match count gives the data scope without duplicating the season label
        # (already in title) or the date range (was redundant for single-season
        # charts). Falls back to the only context line for CSVs with no league.
        if total_matches:
            match_word = 'MATCH' if total_matches == 1 else 'MATCHES'
            subtitle_parts.append(f"{total_matches} {match_word}")
        # highlight stats go in the bottom stats row, not here -- keeps all
        # summary numbers in one place.
        if exclude_penalties:
            subtitle_parts.append('Non-Penalty Shots')

        if subtitle_parts:
            fig.text(0.5, 0.91, ' | '.join(subtitle_parts),
                     ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Legend: color-only (Goal = team color, Shot = black)
    legend_handles = [
        Line2D([0], [0], marker='o', color='none', markerfacecolor=team_color,
               markeredgecolor='white', markeredgewidth=1, markersize=11, label='Goal'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#000000',
               markeredgecolor='white', markeredgewidth=1, markersize=11,
               label='Shot'),
    ]
    fig.legend(handles=legend_handles, loc='upper center',
                bbox_to_anchor=(0.5, 0.89), ncol=2, frameon=False,
                fontsize=10, labelcolor=TEXT_SECONDARY,
                handletextpad=0.5, columnspacing=3.0)

    # Big-number row: per-90 rates for player charts, raw totals for team charts.
    # Smaller context line below gives the sample-size details without competing.
    if minutes and player_name:
        shots_90 = total_shots / minutes * 90
        xg_90 = total_xg / minutes * 90
        goals_90 = goals / minutes * 90
        stat_cols = [
            (0.25, f"{goals_90:.2f}", "GOALS/90"),
            (0.50, f"{shots_90:.2f}", "SHOTS/90"),
            (0.75, f"{xg_90:.2f}", "xG/90"),
        ]
        context_text = (
            f"{goals} goals  ·  {total_shots} shots  ·  {total_xg:.1f} xG  "
            f"·  {total_matches} matches  ·  {minutes} minutes"
        )
    else:
        stat_cols = [
            (0.25, str(goals), "GOALS"),
            (0.50, str(total_shots), "SHOTS"),
            (0.75, f"{total_xg:.1f}", "xG"),
        ]
        goals_per_game = goals / total_matches if total_matches else 0
        xg_per_game = total_xg / total_matches if total_matches else 0
        context_text = (
            f"{total_matches} matches  ·  {goals_per_game:.1f} goals/game  "
            f"·  {shots_per_game:.1f} shots/game  ·  {xg_per_game:.2f} xG/game"
        )

    for x, val, lbl in stat_cols:
        fig.text(x, 0.105, val, ha='center', va='center',
                 fontsize=26, fontweight='bold', color=TEXT_PRIMARY)
        fig.text(x, 0.07, lbl, ha='center', va='center',
                 fontsize=10, color=TEXT_SECONDARY)

    fig.text(0.5, 0.035, context_text, ha='center', va='center',
             fontsize=10, color=TEXT_SECONDARY)

    # Highlight breakdown sits where "Circle size = xG" would -- one or the other,
    # not both, so they don't collide.
    if highlight_stats:
        g = highlight_stats['goals']
        goal_word = 'goal' if g == 1 else 'goals'
        shot_word = 'shot' if highlight_stats['shots'] == 1 else 'shots'
        hl_text = (f"{highlight_mode.lower()}:  {g} {goal_word}  ·  "
                   f"{highlight_stats['shots']} {shot_word}  ·  "
                   f"{highlight_stats['xg']:.1f} xG")
        fig.text(0.5, 0.012, hl_text, ha='center', va='center',
                 fontsize=9, color=TEXT_SECONDARY, style='italic')
    else:
        fig.text(0.5, 0.012, 'Circle size = xG', ha='center', va='center',
                 fontsize=8, color=TEXT_MUTED, style='italic')

    plt.tight_layout(rect=[0.02, 0.14, 0.98, 0.84])

    add_cbs_footer(fig)

    return fig


def create_combined_shot_chart(shots_df, team1_name, team1_color, team1_flip,
                                team2_name, team2_color, team2_flip,
                                match_info, competition='',
                                exclude_penalties=False, highlight_mode='All',
                                custom_title=None, custom_subtitle=None):
    """Create a combined shot chart showing both teams on a full horizontal pitch.

    Home team (team1) attacks LEFT, Away team (team2) attacks RIGHT.
    """
    pitch = Pitch(
        pitch_type='opta',
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=1.3,
        goal_type='box',
        pad_top=1,
        pad_bottom=1,
        pad_left=3,
        pad_right=3
    )

    fig, ax = plt.subplots(figsize=(12, 8))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    pitch_rect = Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Per-team penalty stats computed BEFORE any filter runs. Prefer the pre-
    # computed match_info['pen_stats_by_team'] (CSV path) if present, else
    # derive from the unfiltered shots_df (DB path).
    pen_map_cached = match_info.get('pen_stats_by_team') or {}
    team1_pen_stats = pen_map_cached.get(team1_name) or compute_pen_stats(
        shots_df[shots_df['Team'] == team1_name])
    team2_pen_stats = pen_map_cached.get(team2_name) or compute_pen_stats(
        shots_df[shots_df['Team'] == team2_name])

    # Filter penalties before classifying or plotting
    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    # Filter shots by team and classify for highlighting
    team1_shots = classify_highlight(shots_df[shots_df['Team'] == team1_name].copy(), highlight_mode)
    team2_shots = classify_highlight(shots_df[shots_df['Team'] == team2_name].copy(), highlight_mode)

    # For combined chart: home attacks LEFT, away attacks RIGHT
    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50

    team1_combined_flip = team1_avg_x > 50
    team2_combined_flip = team2_avg_x < 50

    plot_shots_horizontal(ax, pitch, team1_shots, team1_color, flip_x=team1_combined_flip,
                          flip_y=True, highlight_mode=highlight_mode)
    plot_shots_horizontal(ax, pitch, team2_shots, team2_color, flip_x=team2_combined_flip,
                          flip_y=False, highlight_mode=highlight_mode)

    # Reconcile stats using pre-filter pen stats
    team1_total_shots = len(team1_shots)
    team1_xg = team1_shots['xG'].sum()
    team1_goals = match_info.get('home_score', 0)

    team2_total_shots = len(team2_shots)
    team2_xg = team2_shots['xG'].sum()
    team2_goals = match_info.get('away_score', 0)

    t1_breakdown = reconcile_team_goals(
        team1_shots, team1_goals, team1_pen_stats, exclude_penalties)
    t2_breakdown = reconcile_team_goals(
        team2_shots, team2_goals, team2_pen_stats, exclude_penalties)
    team1_shot_goals = t1_breakdown.shot_goals
    team2_shot_goals = t2_breakdown.shot_goals
    team1_own_goals = t1_breakdown.own_goals
    team2_own_goals = t2_breakdown.own_goals

    # Title + split accent bar via shared helper. No kicker on shot chart;
    # title sits higher (y=0.97) and bar at y=0.933.
    render_two_team_score_header(
        fig,
        home_name=team1_name, home_score=team1_goals, home_color=team1_color,
        away_name=team2_name, away_score=team2_goals, away_color=team2_color,
        custom_title=custom_title,
        y_title=0.97,
        y_bar=0.933,
        bar_contrast_edge=True,
    )

    # xG sub-line directly under the score. Non-Pen label when penalties filtered.
    xg_subline_label = "Non-Pen xG" if exclude_penalties else "xG"
    fig.text(0.5, 0.92, f"{xg_subline_label}  {team1_xg:.2f} — {team2_xg:.2f}",
             ha='center', va='center',
             fontsize=12, fontweight='bold', color=TEXT_SECONDARY)

    # Per-team highlight stats
    team1_hl_stats = compute_highlight_stats(team1_shots, highlight_mode)
    team2_hl_stats = compute_highlight_stats(team2_shots, highlight_mode)

    # Subtitle
    if custom_subtitle:
        fig.text(0.5, 0.895, custom_subtitle, ha='center', va='center',
                 fontsize=11, color=TEXT_SECONDARY)
    else:
        shot_map_label = "NON-PENALTY SHOT MAP" if exclude_penalties else "SHOT MAP"
        subtitle_parts = [shot_map_label]
        if highlight_mode != 'All':
            subtitle_parts.append(f"{highlight_mode.upper()} SHOTS HIGHLIGHTED")
        if competition:
            subtitle_parts.append(competition.upper())
        if match_info.get('date_formatted'):
            subtitle_parts.append(match_info['date_formatted'])

        fig.text(0.5, 0.895, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Shape legend only (team colors are communicated by the colored team
    # names in the stats row at the bottom of the chart).
    shape_handles = [
        Line2D([0], [0], marker='*', color='none', markerfacecolor='#888888',
               markeredgecolor='white', markeredgewidth=1, markersize=14, label='Goal'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#888888',
               markeredgecolor='white', markeredgewidth=1, markersize=10,
               label='Shot'),
    ]
    fig.legend(handles=shape_handles, loc='upper center',
                bbox_to_anchor=(0.5, 0.87), ncol=2, frameon=False,
                fontsize=10, labelcolor=TEXT_SECONDARY,
                handletextpad=0.5, columnspacing=3.0)

    # Per-team stat groups: team name header (in team color), then number/label columns.
    # Combined chart's tight columns can't fit inline pen annotations on xG, so
    # the filter status is communicated via the "Non-Pen xG" label.
    xg_label_combined = "Non-Pen xG" if exclude_penalties else "xG"

    def _draw_team_stats(cx, name, color, shots_n, xg_val, goals_n, og, pen_stats):
        fig.text(cx, 0.135, name.upper(), ha='center', va='center',
                 fontsize=11, fontweight='bold', color=ensure_bg_readable(color))
        cols = [
            (cx - 0.07, str(shots_n), "SHOTS"),
            (cx,        f"{xg_val:.2f}", xg_label_combined),
            (cx + 0.07, str(goals_n), "GOALS"),
        ]
        for x, val, lbl in cols:
            fig.text(x, 0.095, val, ha='center', va='center',
                     fontsize=22, fontweight='bold', color=TEXT_PRIMARY)
            fig.text(x, 0.06, lbl, ha='center', va='center',
                     fontsize=9, color=TEXT_SECONDARY)

        pen_goals = pen_stats.get('goals', 0)
        show_pen_annotation = exclude_penalties and pen_goals > 0

        goals_extras = []
        if og > 0:
            goals_extras.append(f"+{og} OG")
        if show_pen_annotation:
            goals_extras.append(f"+{pen_goals} pen")
        if goals_extras:
            fig.text(cx + 0.082, 0.095, f"({', '.join(goals_extras)})",
                     ha='left', va='center',
                     fontsize=12, color=TEXT_SECONDARY)

    _draw_team_stats(0.24, team1_name, team1_color,
                     team1_total_shots, team1_xg, team1_shot_goals, team1_own_goals,
                     team1_pen_stats)
    _draw_team_stats(0.76, team2_name, team2_color,
                     team2_total_shots, team2_xg, team2_shot_goals, team2_own_goals,
                     team2_pen_stats)

    if team1_hl_stats and team2_hl_stats:
        hl_text = (f"{highlight_mode}:  {team1_name} {team1_hl_stats['shots']}sh · "
                   f"{team1_hl_stats['xg']:.2f}xG · {team1_hl_stats['goals']}g    "
                   f"{team2_name} {team2_hl_stats['shots']}sh · "
                   f"{team2_hl_stats['xg']:.2f}xG · {team2_hl_stats['goals']}g")
        fig.text(0.5, 0.025, hl_text, ha='center', va='center',
                 fontsize=9, color=TEXT_SECONDARY, style='italic')

    plt.tight_layout(rect=[0.02, 0.16, 0.98, 0.84])

    add_cbs_footer(fig)
    fig.text(0.5, 0.01, 'Circle size = xG', ha='center', va='bottom',
             fontsize=8, color=TEXT_MUTED, style='italic')

    return fig


# ---------------------------------------------------------------------------
# Top-level orchestrators — load data, assemble all charts for a match/season.

def create_shot_charts(file_path, output_folder=None, competition='', save=True,
                       exclude_penalties=False, highlight_mode='All'):
    """Main function to create shot charts for both teams in a match.

    Returns:
        list of (fig, filename) tuples
    """
    shots_df, match_info, team_colors = load_shot_data(
        file_path, exclude_penalties=exclude_penalties)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    teams = shots_df['Team'].unique().tolist()
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    def match_team_name(target, team_list):
        for t in team_list:
            if target.lower() in t.lower() or t.lower() in target.lower():
                return t
        return team_list[0] if team_list else target

    team1_name = match_team_name(home_team, teams)
    team2_name = match_team_name(away_team, [t for t in teams if t != team1_name])

    # Resolve team colors with CSV → fuzzy-match → gray fallback chain
    def resolve_color(team_name, team_colors_dict):
        if team_name in team_colors_dict:
            return team_colors_dict[team_name]
        for csv_team, color in team_colors_dict.items():
            if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                return color
        color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            return color
        return '#888888'

    team1_color_raw = resolve_color(team1_name, team_colors)
    team2_color_raw = resolve_color(team2_name, team_colors)

    team1_color = ensure_pitch_contrast(team1_color_raw)
    team2_color = ensure_pitch_contrast(team2_color_raw)

    print(f"\nHome: {team1_name} ({team1_color_raw}"
          + (f" -> {team1_color})" if team1_color != team1_color_raw else ")"))
    print(f"Away: {team2_name} ({team2_color_raw}"
          + (f" -> {team2_color})" if team2_color != team2_color_raw else ")"))

    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50
    team1_flip = team1_avg_x < 50
    team2_flip = team2_avg_x < 50

    print(f"\n{team1_name} avg shot X: {team1_avg_x:.1f} (flip: {team1_flip})")
    print(f"{team2_name} avg shot X: {team2_avg_x:.1f} (flip: {team2_flip})")

    team1_final_score = match_info.get('home_score', 0)
    team2_final_score = match_info.get('away_score', 0)

    # Reconcile each team's shot goals with the scoreline (handles OG + pen)
    pen_map = match_info.get('pen_stats_by_team') or {}
    t1_breakdown = reconcile_team_goals(
        team1_shots, team1_final_score,
        pen_map.get(team1_name) or compute_pen_stats(team1_shots),
        exclude_penalties,
    )
    t2_breakdown = reconcile_team_goals(
        team2_shots, team2_final_score,
        pen_map.get(team2_name) or compute_pen_stats(team2_shots),
        exclude_penalties,
    )
    team1_own_goals = t1_breakdown.own_goals
    team2_own_goals = t2_breakdown.own_goals

    results = []

    print(f"\nCreating shot chart for {team1_name}...")
    fig1 = create_team_shot_chart(
        team1_shots, team1_name, team1_color, match_info,
        team2_name, team_final_score=team1_final_score, opponent_goals=team2_final_score,
        own_goals_for=team1_own_goals, own_goals_against=team2_own_goals,
        flip_coords=team1_flip, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename1 = f"shot_chart_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig1, filename1))

    print(f"Creating shot chart for {team2_name}...")
    fig2 = create_team_shot_chart(
        team2_shots, team2_name, team2_color, match_info,
        team1_name, team_final_score=team2_final_score, opponent_goals=team1_final_score,
        own_goals_for=team2_own_goals, own_goals_against=team1_own_goals,
        flip_coords=team2_flip, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename2 = f"shot_chart_{team2_name.replace(' ', '_')}_vs_{team1_name.replace(' ', '_')}.png"
    results.append((fig2, filename2))

    print("Creating combined shot chart...")
    fig_combined = create_combined_shot_chart(
        shots_df, team1_name, team1_color, team1_flip,
        team2_name, team2_color, team2_flip,
        match_info, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename_combined = f"shot_chart_combined_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig_combined, filename_combined))

    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, filename in results:
            filepath = os.path.join(output_folder, filename)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                        facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def create_multi_match_charts(file_path, output_folder=None, competition='',
                               player_name=None, save=True, exclude_penalties=False,
                               highlight_mode='All'):
    """Main function to create multi-match shot charts for a single team.

    Returns:
        list of (fig, filename) tuples
    """
    shots_df, multi_match_info, team_color_raw = load_multi_match_shot_data(
        file_path, exclude_penalties=exclude_penalties)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    team_name = multi_match_info['team_name']

    color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if team_color_raw and team_color_raw != '#888888':
        team_color = ensure_pitch_contrast(team_color_raw)
    elif color:
        team_color = ensure_pitch_contrast(color)
    else:
        team_color = '#888888'

    print(f"Team: {team_name} (color: {team_color})")

    if player_name:
        shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
        shots_df = shots_df[shots_df[shooter_col] == player_name].copy()
        multi_match_info['total_matches'] = shots_df['_match_id'].nunique()
        print(f"Filtered to {player_name}: {len(shots_df)} shots")

    if shots_df.empty:
        print(f"No shots found for player: {player_name}")
        return []

    results = []

    fig = create_multi_match_shot_chart(
        shots_df, team_name, team_color, multi_match_info,
        competition=competition, player_name=player_name,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )

    name_part = team_name.replace(' ', '_')
    if player_name:
        name_part = f"{player_name.replace(' ', '_')}_{name_part}"
    filename = f"shot_map_{name_part}_season.png"
    results.append((fig, filename))

    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, fn in results:
            filepath = os.path.join(output_folder, fn)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                        facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def run(config):
    """Entry point for launcher/GUI integration.

    Auto-detects single vs multi-match mode using detect_csv_mode().
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder', os.path.dirname(file_path))
    competition = config.get('competition', '')
    exclude_penalties = config.get('exclude_penalties', False)
    highlight_mode = config.get('highlight_mode', 'All')
    save = config.get('save', True)

    df = pd.read_csv(file_path)
    mode = detect_csv_mode(df)
    print(f"Detected CSV mode: {mode}")

    if mode == 'multi':
        results = create_multi_match_charts(
            file_path, output_folder, competition,
            save=save, exclude_penalties=exclude_penalties,
            highlight_mode=highlight_mode
        )
    else:
        results = create_shot_charts(
            file_path, output_folder, competition,
            save=save, exclude_penalties=exclude_penalties,
            highlight_mode=highlight_mode
        )

    if not save:
        for fig, _ in results:
            plt.show()

    saved_paths = []
    for fig, fn in results:
        if save and output_folder:
            saved_paths.append(os.path.join(output_folder, fn))
        plt.close(fig)

    print("\nDone!")
    return saved_paths


def main():
    """Standalone entry point — prompts user for inputs."""
    print("\n" + "=" * 60)
    print("CBS SPORTS SHOT CHART BUILDER")
    print("=" * 60)

    file_path = input("\nPath to TruMedia CSV: ").strip().strip('"').strip("'")

    if not file_path or not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return

    competition = input("Competition (e.g., SERIE A, PREMIER LEAGUE): ").strip().upper()

    default_output = os.path.dirname(file_path) or os.path.expanduser("~/Downloads")
    output_folder = input(f"Output folder (default: {default_output}): ").strip() or default_output

    results = create_shot_charts(file_path, output_folder, competition, save=True)

    for fig, _ in results:
        plt.show()
        plt.close(fig)

    print("\nDone!")


if __name__ == "__main__":
    main()
