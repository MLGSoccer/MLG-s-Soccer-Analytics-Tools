"""
Shot Chart Generator - CBS Sports Styling

Creates shot location charts showing where shots were taken on the pitch.
- Circles for non-goals, stars for goals
- Size scaled by xG value
- Separate chart per team
- Designed for easy expansion to player/team multi-game views

Data source: TruMedia CSV with EventX, EventY coordinates
Uses mplsoccer for pitch drawing.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
import os
from datetime import datetime
from mplsoccer import Pitch, VerticalPitch

# Import shared utilities
from shared.colors import (
    TEAM_COLORS, fuzzy_match_team
)
from shared.styles import BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer


# Shot-related playTypes in TruMedia data
SHOT_TYPES = {'Miss', 'Goal', 'PenaltyGoal', 'AttemptSaved', 'Post'}
GOAL_TYPES = {'Goal', 'PenaltyGoal'}

# Pitch color for contrast checking
PITCH_COLOR = '#1E5631'
FALLBACK_COLOR = '#FFFFFF'  # White as fallback for low-contrast colors


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def color_distance(color1, color2):
    """Calculate Euclidean distance between two hex colors."""
    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def ensure_pitch_contrast(color, min_distance=80):
    """
    Check if color has enough contrast with pitch.
    Returns fallback color (white) if too similar to pitch green.

    Specifically targets green-ish colors and very dark colors.
    """
    r, g, b = hex_to_rgb(color)

    # Check if color is green-dominant (likely to blend with pitch)
    is_greenish = g > r and g > b * 0.8

    # Check if very dark (hard to see on dark green)
    is_very_dark = r < 40 and g < 40 and b < 40

    # For green-ish colors, use stricter distance check
    if is_greenish and color_distance(color, PITCH_COLOR) < 120:
        return FALLBACK_COLOR

    # For very dark colors, swap to white
    if is_very_dark:
        return FALLBACK_COLOR

    return color


def check_bg_contrast(color, min_distance=100):
    """
    Check if color has enough contrast with the dark blue background (BG_COLOR).
    Returns True if color is visible, False if it would blend in.
    """
    return color_distance(color, BG_COLOR) >= min_distance


def detect_csv_mode(df):
    """
    Auto-detect single-match vs multi-match CSV.

    Checks unique game count (via gameId or Date+opponent combos) and team count.
    Returns 'single' or 'multi'.
    """
    # Check number of unique teams
    teams = df['Team'].nunique() if 'Team' in df.columns else 1

    # Check number of unique games
    if 'gameId' in df.columns:
        games = df['gameId'].nunique()
    elif 'Date' in df.columns:
        games = df['Date'].nunique()
    else:
        games = 1

    # Multi-match: more than 1 game OR only 1 team with many rows
    if games > 1:
        return 'multi'
    if teams == 1 and len(df) > 50:
        # Single team with lots of data likely means multi-match
        return 'multi'
    return 'single'


def load_shot_data(file_path):
    """
    Load and filter shot data from TruMedia CSV.

    Returns:
        DataFrame with shot data, match_info dict, team_colors dict
    """
    print(f"\nLoading TruMedia CSV: {file_path}")

    df = pd.read_csv(file_path)

    # Filter to shot events only
    shots_df = df[df['playType'].isin(SHOT_TYPES)].copy()

    print(f"Found {len(shots_df)} shots")

    # Extract match info
    first_row = df.iloc[0]
    match_info = {
        'home_team': first_row.get('homeTeam', 'Home'),
        'away_team': first_row.get('awayTeam', 'Away'),
        'date': first_row.get('Date', ''),
        'home_score': int(first_row.get('homeFinalScore', 0)),
        'away_score': int(first_row.get('awayFinalScore', 0)),
    }

    # Format date
    if match_info['date']:
        try:
            date_obj = datetime.strptime(match_info['date'], '%Y-%m-%d')
            match_info['date_formatted'] = date_obj.strftime('%b %d, %Y').upper()
        except:
            match_info['date_formatted'] = match_info['date']
    else:
        match_info['date_formatted'] = ''

    # Extract team colors from CSV
    team_colors = {}
    for _, row in shots_df.iterrows():
        team = row.get('Team', row.get('teamAbbrevName', 'Unknown'))
        color = row.get('newestTeamColor')
        if team and color and team not in team_colors:
            team_colors[team] = color

    # Get unique teams
    teams = shots_df['Team'].unique().tolist()
    print(f"Teams: {', '.join(teams)}")

    return shots_df, match_info, team_colors


def load_multi_match_shot_data(file_path):
    """
    Load multi-match shot data from TruMedia CSV (season-long, single team).

    Returns:
        (shots_df, multi_match_info, team_color)
        - shots_df: DataFrame with shots + _needs_flip column (per-row)
        - multi_match_info: dict with team_name, date_range, total_matches, player_list
        - team_color: hex color string for the team
    """
    print(f"\nLoading multi-match TruMedia CSV: {file_path}")

    df = pd.read_csv(file_path)

    # Filter to shot events only
    shots_df = df[df['playType'].isin(SHOT_TYPES)].copy()
    print(f"Found {len(shots_df)} shots across multiple matches")

    if shots_df.empty:
        return shots_df, {}, '#888888'

    # Identify the primary team (most frequent)
    team_counts = shots_df['Team'].value_counts()
    team_name = team_counts.index[0]

    # Filter to only the primary team's shots
    shots_df = shots_df[shots_df['Team'] == team_name].copy()
    print(f"Team: {team_name} ({len(shots_df)} shots)")

    # Get team color: prefer database (brand color) over CSV (match-specific kit)
    team_color = '#888888'
    db_color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if db_color:
        team_color = db_color
    elif 'newestTeamColor' in shots_df.columns:
        color_col = shots_df['newestTeamColor'].dropna()
        if not color_col.empty:
            team_color = color_col.iloc[0]

    # Build a match identifier for per-match grouping
    if 'gameId' in shots_df.columns:
        shots_df['_match_id'] = shots_df['gameId']
    elif 'Date' in shots_df.columns:
        # Use Date + opponent as match ID
        if 'homeTeam' in shots_df.columns and 'awayTeam' in shots_df.columns:
            shots_df['_match_id'] = shots_df['Date'] + '_' + shots_df['homeTeam'] + '_' + shots_df['awayTeam']
        else:
            shots_df['_match_id'] = shots_df['Date']
    else:
        shots_df['_match_id'] = 'unknown'

    # Per-match coordinate normalization: flip if avg EventX < 50
    shots_df['_needs_flip'] = False
    for match_id, group in shots_df.groupby('_match_id'):
        avg_x = group['EventX'].mean()
        if avg_x < 50:
            shots_df.loc[group.index, '_needs_flip'] = True

    # Extract player list
    if 'shooter' in shots_df.columns:
        player_list = sorted(shots_df['shooter'].dropna().unique().tolist())
    elif 'Player' in shots_df.columns:
        player_list = sorted(shots_df['Player'].dropna().unique().tolist())
    else:
        player_list = []

    # Date range
    total_matches = shots_df['_match_id'].nunique()
    if 'Date' in shots_df.columns:
        dates = pd.to_datetime(shots_df['Date'], errors='coerce').dropna()
        if not dates.empty:
            min_date = dates.min().strftime('%b %d, %Y').upper()
            max_date = dates.max().strftime('%b %d, %Y').upper()
            date_range = f"{min_date} - {max_date}"
        else:
            date_range = ''
    else:
        date_range = ''

    # Detect player vs team CSV: if only 1 unique shooter, it's a player CSV
    is_player_csv = len(player_list) == 1
    player_name = player_list[0] if is_player_csv else None

    multi_match_info = {
        'team_name': team_name,
        'date_range': date_range,
        'total_matches': total_matches,
        'player_list': player_list,
        'is_player_csv': is_player_csv,
        'player_name': player_name,
    }

    print(f"Matches: {total_matches}, Players: {len(player_list)}, Date range: {date_range}")

    return shots_df, multi_match_info, team_color


def plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=False,
                        marker_style='single'):
    """
    Plot shots on vertical half-pitch (goal at top).

    TruMedia coordinates: EventX = length (0-100), EventY = width (0-100)
    mplsoccer opta coordinates: x = length, y = width

    marker_style:
        'single' - circles for non-goals, stars for goals, all in team_color
        'multi'  - all circles; black fill for non-goals, team_color fill for goals
    """
    if shots_df.empty:
        return

    has_per_row_flip = '_needs_flip' in shots_df.columns

    for _, shot in shots_df.iterrows():
        x = shot['EventX']  # Length (towards goal)
        y = shot['EventY']  # Width (sideline to sideline)
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES

        # Per-row flip (multi-match) takes priority; otherwise use flip_coords param
        if has_per_row_flip:
            should_flip = shot['_needs_flip']
        else:
            should_flip = flip_coords

        if should_flip:
            x = 100 - x
            y = 100 - y

        # Scale marker size by xG
        base_size = 50
        size = base_size + (xg * 700)

        if marker_style == 'multi':
            # Multi-match style: all circles, black vs team_color fill
            marker = 'o'
            fill_color = team_color if is_goal else '#000000'
            edge_width = 1.5
        else:
            # Single-match style: circles for non-goals, stars for goals
            marker = '*' if is_goal else 'o'
            fill_color = team_color
            edge_width = 2 if is_goal else 1.5

        # Use pitch.scatter for proper coordinate handling
        pitch.scatter(
            x, y, s=size, c=fill_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=0.85, zorder=10, ax=ax
        )


def create_team_shot_chart(shots_df, team_name, team_color, match_info,
                           opponent_name, team_final_score=0, opponent_goals=0,
                           own_goals_for=0, own_goals_against=0,
                           flip_coords=False, competition=''):
    """
    Create a single team's shot chart using mplsoccer VerticalPitch.
    """
    # Create vertical half-pitch with opta coordinates (0-100)
    pitch = VerticalPitch(
        pitch_type='opta',
        half=True,
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=2,
        goal_type='box',
        pad_top=3,
        pad_bottom=0,
        pad_left=1,
        pad_right=1
    )

    # Create figure - wider than tall for half-pitch view
    fig, ax = plt.subplots(figsize=(12, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Add green rectangle for just the pitch area (inside the lines)
    # VerticalPitch half=True: y=0-100 (width), x=50-100 (attacking half)
    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    # Draw pitch lines
    pitch.draw(ax=ax)

    # Clip view to pitch bounds (hide lines extending into padding)
    ax.set_xlim(-1, 101)  # Minimal margin for sidelines
    ax.set_ylim(50, 103)  # Start at halfway line, room for goal at top

    # Plot shots
    plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=flip_coords)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])

    # Title with score
    fig.suptitle(f"{team_name.upper()} {team_final_score}-{opponent_goals} {opponent_name.upper()}",
                 fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Subtitle
    subtitle_parts = [f"{team_name.upper()} SHOT MAP"]
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    fig.text(0.5, 0.91, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Legend as third headline line with team color
    # Calculate positions based on text length for proper centering
    legend_ax = fig.add_axes([0, 0.85, 1, 0.04], facecolor='none')
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.axis('off')

    # Estimate character width (~0.007 per char at fontsize 10)
    char_width = 0.007
    marker_width = 0.025  # Space for marker + small gap
    gap = 0.03  # Gap between items

    # Calculate total legend width: "● Shot  ★ Goal"
    shot_text = "Shot"
    goal_text = "Goal"
    total_width = (marker_width + len(shot_text) * char_width +
                   gap + marker_width + len(goal_text) * char_width)

    # Start position to center the legend
    start_x = 0.5 - total_width / 2

    # Shot marker with team color
    legend_ax.scatter([start_x + 0.01], [0.5], s=80, c=team_color, marker='o',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(start_x + marker_width, 0.5, shot_text, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Goal marker with team color
    goal_start = start_x + marker_width + len(shot_text) * char_width + gap
    legend_ax.scatter([goal_start + 0.01], [0.5], s=120, c=team_color, marker='*',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(goal_start + marker_width, 0.5, goal_text, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Stats box at bottom - hovering over pitch bottom line
    goals_text = str(goals)
    if own_goals_for > 0:
        goals_text += f" + {own_goals_for} OG"

    stats_text = f"Shots: {total_shots}  |  xG: {total_xg:.2f}  |  Goals: {goals_text}"
    fig.text(0.5, 0.08, stats_text,
             ha='center', va='center', fontsize=16, fontweight='bold',
             color=TEXT_PRIMARY,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=2, alpha=0.95))

    plt.tight_layout(rect=[0.02, 0.04, 0.98, 0.84])

    # Add CBS Sports and TruMedia branding
    add_cbs_footer(fig, data_source='TruMedia')

    return fig


def create_multi_match_shot_chart(shots_df, team_name, team_color, multi_match_info,
                                   competition='', player_name=None):
    """
    Create a multi-match shot chart for one team on a vertical half-pitch.

    Marker style:
        - Non-goals: black fill, white edge, circle
        - Goals: team_color fill, white edge, circle
        - Size scaled by xG
    """
    # Create vertical half-pitch
    pitch = VerticalPitch(
        pitch_type='opta',
        half=True,
        pitch_color='none',
        line_color='white',
        linewidth=2,
        goal_type='box',
        pad_top=3,
        pad_bottom=0,
        pad_left=1,
        pad_right=1
    )

    fig, ax = plt.subplots(figsize=(12, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Green pitch rectangle
    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    ax.set_xlim(-1, 101)
    ax.set_ylim(50, 103)

    # Plot shots with multi-match marker style (per-row flipping via _needs_flip)
    plot_shots_vertical(ax, pitch, shots_df, team_color, marker_style='multi')

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
    total_matches = multi_match_info.get('total_matches', 0)
    shots_per_game = total_shots / total_matches if total_matches > 0 else 0

    # Derive season string from date range (e.g. "2025-26")
    season_str = ''
    date_range = multi_match_info.get('date_range', '')
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
    if player_name:
        title_text = f"{player_name.upper()} {season_str} SHOT MAP".strip()
        subtitle_parts = [team_name.upper()]
    else:
        title_text = f"{team_name.upper()} {season_str} SHOT MAP".strip()
        subtitle_parts = []

    title_obj = fig.suptitle(title_text, fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Team color accent bar matching title width
    fig.canvas.draw()
    title_bbox = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    title_bbox_fig = title_bbox.transformed(fig.transFigure.inverted())
    bar_x = title_bbox_fig.x0
    bar_width = title_bbox_fig.width
    # White border if team color blends into dark background
    bar_edge = 'white' if not check_bg_contrast(team_color) else 'none'
    bar_lw = 0.8 if bar_edge == 'white' else 0
    fig.patches.append(Rectangle(
        (bar_x, 0.935), bar_width, 0.003,
        transform=fig.transFigure, facecolor=team_color,
        edgecolor=bar_edge, linewidth=bar_lw, zorder=10
    ))

    # Add competition and date range to subtitle
    if competition:
        subtitle_parts.append(competition.upper())
    if date_range:
        subtitle_parts.append(date_range)

    if subtitle_parts:
        fig.text(0.5, 0.91, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Legend: ● Shot (black) and ● Goal (team color)
    legend_ax = fig.add_axes([0, 0.85, 1, 0.04], facecolor='none')
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.axis('off')

    char_width = 0.007
    marker_width = 0.025
    gap = 0.03

    shot_text = "Shot"
    goal_text = "Goal"
    total_width = (marker_width + len(shot_text) * char_width +
                   gap + marker_width + len(goal_text) * char_width)
    start_x = 0.5 - total_width / 2

    # Shot marker (black fill)
    legend_ax.scatter([start_x + 0.01], [0.5], s=80, c='#000000', marker='o',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(start_x + marker_width, 0.5, shot_text, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Goal marker (team color fill)
    goal_start = start_x + marker_width + len(shot_text) * char_width + gap
    legend_ax.scatter([goal_start + 0.01], [0.5], s=80, c=team_color, marker='o',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(goal_start + marker_width, 0.5, goal_text, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Stats box
    stats_text = (f"Shots: {total_shots}  |  xG: {total_xg:.1f}  |  Goals: {goals}"
                  f"  |  {total_matches} Matches  |  {shots_per_game:.1f} Shots/Game")
    fig.text(0.5, 0.08, stats_text,
             ha='center', va='center', fontsize=14, fontweight='bold',
             color=TEXT_PRIMARY,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=2, alpha=0.95))

    plt.tight_layout(rect=[0.02, 0.04, 0.98, 0.84])

    add_cbs_footer(fig, data_source='TruMedia')

    return fig


def plot_shots_horizontal(ax, pitch, shots_df, team_color, flip_x=False):
    """
    Plot shots on horizontal full pitch.
    """
    if shots_df.empty:
        return

    for _, shot in shots_df.iterrows():
        x = shot['EventX']
        y = shot['EventY']
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES

        if flip_x:
            x = 100 - x  # Mirror to opposite end

        base_size = 50
        size = base_size + (xg * 700)
        marker = '*' if is_goal else 'o'
        edge_width = 2 if is_goal else 1.5

        # Use pitch.scatter for proper coordinate handling
        pitch.scatter(
            x, y, s=size, c=team_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=0.85, zorder=10, ax=ax
        )


def create_combined_shot_chart(shots_df, team1_name, team1_color, team1_flip,
                                team2_name, team2_color, team2_flip,
                                match_info, competition=''):
    """
    Create a combined shot chart showing both teams on a full horizontal pitch.

    Home team (team1) attacks LEFT, Away team (team2) attacks RIGHT.
    """
    # Create horizontal full pitch with opta coordinates
    pitch = Pitch(
        pitch_type='opta',
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=2,
        goal_type='box',
        pad_top=1,
        pad_bottom=1,
        pad_left=3,  # Room for goal
        pad_right=3  # Room for goal
    )

    # Create figure - proportions match full pitch (~1.5:1 length:width)
    fig, ax = plt.subplots(figsize=(12, 8))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Add green rectangle for just the pitch area (inside the lines)
    # Horizontal pitch: x=0-100 (length), y=0-100 (width)
    pitch_rect = Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    # Draw pitch lines
    pitch.draw(ax=ax)

    # Filter shots by team
    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    # For combined chart: home attacks LEFT, away attacks RIGHT
    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50

    # Team1 (home) attacks left: flip only if their shots are on right (avg > 50)
    team1_combined_flip = team1_avg_x > 50
    # Team2 (away) attacks right: flip only if their shots are on left (avg < 50)
    team2_combined_flip = team2_avg_x < 50

    plot_shots_horizontal(ax, pitch, team1_shots, team1_color, flip_x=team1_combined_flip)
    plot_shots_horizontal(ax, pitch, team2_shots, team2_color, flip_x=team2_combined_flip)

    # Calculate stats
    team1_total_shots = len(team1_shots)
    team1_xg = team1_shots['xG'].sum()
    team1_goals = match_info.get('home_score', 0)
    team1_shot_goals = len(team1_shots[team1_shots['playType'].isin(GOAL_TYPES)])
    team1_own_goals = team1_goals - team1_shot_goals

    team2_total_shots = len(team2_shots)
    team2_xg = team2_shots['xG'].sum()
    team2_goals = match_info.get('away_score', 0)
    team2_shot_goals = len(team2_shots[team2_shots['playType'].isin(GOAL_TYPES)])
    team2_own_goals = team2_goals - team2_shot_goals

    # Title with score
    fig.suptitle(f"{team1_name.upper()} {team1_goals}-{team2_goals} {team2_name.upper()}",
                 fontsize=22, fontweight='bold', color=TEXT_PRIMARY, y=0.97)

    # Subtitle
    subtitle_parts = ['SHOT MAP']
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    fig.text(0.5, 0.91, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Legend as third headline line with team colors
    # Calculate positions based on text length for proper centering
    legend_ax = fig.add_axes([0, 0.85, 1, 0.04], facecolor='none')
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.axis('off')

    # Estimate character width (~0.006 per char at fontsize 10 for wider figure)
    char_width = 0.006
    marker_width = 0.02  # Space for marker + small gap
    gap = 0.025  # Gap between items

    # Calculate total legend width: "● Team1  ● Team2  ★ Goal"
    goal_text = "Goal"
    total_width = (marker_width + len(team1_name) * char_width +
                   gap + marker_width + len(team2_name) * char_width +
                   gap + marker_width + len(goal_text) * char_width)

    # Start position to center the legend
    start_x = 0.5 - total_width / 2

    # Team 1 marker and name
    legend_ax.scatter([start_x + 0.008], [0.5], s=80, c=team1_color, marker='o',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(start_x + marker_width, 0.5, team1_name, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Team 2 marker and name
    team2_start = start_x + marker_width + len(team1_name) * char_width + gap
    legend_ax.scatter([team2_start + 0.008], [0.5], s=80, c=team2_color, marker='o',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(team2_start + marker_width, 0.5, team2_name, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Goal marker
    goal_start = team2_start + marker_width + len(team2_name) * char_width + gap
    legend_ax.scatter([goal_start + 0.008], [0.5], s=120, c='gray', marker='*',
                      edgecolors='white', linewidths=1, zorder=10)
    legend_ax.text(goal_start + marker_width, 0.5, goal_text, ha='left', va='center',
                   fontsize=10, color=TEXT_SECONDARY)

    # Stats for both teams at bottom
    team1_goal_word = "goal" if team1_shot_goals == 1 else "goals"
    team1_goals_text = f"{team1_shot_goals} {team1_goal_word}"
    if team1_own_goals > 0:
        team1_goals_text += f" + {team1_own_goals} OG"

    team2_goal_word = "goal" if team2_shot_goals == 1 else "goals"
    team2_goals_text = f"{team2_shot_goals} {team2_goal_word}"
    if team2_own_goals > 0:
        team2_goals_text += f" + {team2_own_goals} OG"

    stats_text = (f"{team1_name}: {team1_total_shots} shots, {team1_xg:.2f} xG, {team1_goals_text}   |   "
                  f"{team2_name}: {team2_total_shots} shots, {team2_xg:.2f} xG, {team2_goals_text}")
    fig.text(0.5, 0.06, stats_text,
             ha='center', va='center', fontsize=14, fontweight='bold',
             color=TEXT_PRIMARY,
             bbox=dict(boxstyle='round,pad=0.5', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=2, alpha=0.95))

    plt.tight_layout(rect=[0.02, 0.03, 0.98, 0.84])

    # Add CBS Sports and TruMedia branding
    add_cbs_footer(fig, data_source='TruMedia')

    return fig


def create_shot_charts(file_path, output_folder=None, competition='', save=True):
    """
    Main function to create shot charts for both teams in a match.

    Args:
        file_path: path to TruMedia CSV
        output_folder: where to save charts (defaults to same as input)
        competition: competition name
        save: whether to save the charts

    Returns:
        list of (fig, filename) tuples
    """
    # Load data
    shots_df, match_info, team_colors = load_shot_data(file_path)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    # Get teams
    teams = shots_df['Team'].unique().tolist()
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    # Match teams to shot data team names
    def match_team_name(target, team_list):
        for t in team_list:
            if target.lower() in t.lower() or t.lower() in target.lower():
                return t
        return team_list[0] if team_list else target

    team1_name = match_team_name(home_team, teams)
    team2_name = match_team_name(away_team, [t for t in teams if t != team1_name])

    # Get colors - prefer CSV colors, fall back to database lookup
    def resolve_color(team_name, team_colors_dict):
        """Resolve team color with fallback chain."""
        if team_name in team_colors_dict:
            return team_colors_dict[team_name]
        for csv_team, color in team_colors_dict.items():
            if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                return color
        color, matched, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            return color
        return '#888888'

    team1_color_raw = resolve_color(team1_name, team_colors)
    team2_color_raw = resolve_color(team2_name, team_colors)

    # Ensure colors have enough contrast with pitch
    team1_color = ensure_pitch_contrast(team1_color_raw)
    team2_color = ensure_pitch_contrast(team2_color_raw)

    print(f"\nHome: {team1_name} ({team1_color_raw}" + (f" -> {team1_color})" if team1_color != team1_color_raw else ")"))
    print(f"Away: {team2_name} ({team2_color_raw}" + (f" -> {team2_color})" if team2_color != team2_color_raw else ")"))

    # Determine coordinate flipping
    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50

    # If team's average shot X is < 50, they're attacking left, need to flip
    team1_flip = team1_avg_x < 50
    team2_flip = team2_avg_x < 50

    print(f"\n{team1_name} avg shot X: {team1_avg_x:.1f} (flip: {team1_flip})")
    print(f"{team2_name} avg shot X: {team2_avg_x:.1f} (flip: {team2_flip})")

    # Get final scores from CSV
    team1_final_score = match_info.get('home_score', 0)
    team2_final_score = match_info.get('away_score', 0)

    # Count goals from shots to detect own goals
    team1_shot_goals = len(team1_shots[team1_shots['playType'].isin(GOAL_TYPES)])
    team2_shot_goals = len(team2_shots[team2_shots['playType'].isin(GOAL_TYPES)])

    team1_own_goals = team1_final_score - team1_shot_goals
    team2_own_goals = team2_final_score - team2_shot_goals

    results = []

    # Create chart for team 1 (home)
    print(f"\nCreating shot chart for {team1_name}...")
    fig1 = create_team_shot_chart(
        team1_shots, team1_name, team1_color, match_info,
        team2_name, team_final_score=team1_final_score, opponent_goals=team2_final_score,
        own_goals_for=team1_own_goals, own_goals_against=team2_own_goals,
        flip_coords=team1_flip, competition=competition
    )
    filename1 = f"shot_chart_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig1, filename1))

    # Create chart for team 2 (away)
    print(f"Creating shot chart for {team2_name}...")
    fig2 = create_team_shot_chart(
        team2_shots, team2_name, team2_color, match_info,
        team1_name, team_final_score=team2_final_score, opponent_goals=team1_final_score,
        own_goals_for=team2_own_goals, own_goals_against=team1_own_goals,
        flip_coords=team2_flip, competition=competition
    )
    filename2 = f"shot_chart_{team2_name.replace(' ', '_')}_vs_{team1_name.replace(' ', '_')}.png"
    results.append((fig2, filename2))

    # Create combined chart
    print(f"Creating combined shot chart...")
    fig_combined = create_combined_shot_chart(
        shots_df, team1_name, team1_color, team1_flip,
        team2_name, team2_color, team2_flip,
        match_info, competition=competition
    )
    filename_combined = f"shot_chart_combined_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig_combined, filename_combined))

    # Save if requested
    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, filename in results:
            filepath = os.path.join(output_folder, filename)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                       facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def create_multi_match_charts(file_path, output_folder=None, competition='',
                               player_name=None, save=True):
    """
    Main function to create multi-match shot charts for a single team.

    Args:
        file_path: path to TruMedia CSV (season-long, single team)
        output_folder: where to save charts (defaults to same as input)
        competition: competition name
        player_name: optional player filter (None = all players)
        save: whether to save the charts

    Returns:
        list of (fig, filename) tuples
    """
    # Load data
    shots_df, multi_match_info, team_color_raw = load_multi_match_shot_data(file_path)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    team_name = multi_match_info['team_name']

    # Resolve color with pitch contrast check
    color, matched, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if team_color_raw and team_color_raw != '#888888':
        team_color = ensure_pitch_contrast(team_color_raw)
    elif color:
        team_color = ensure_pitch_contrast(color)
    else:
        team_color = '#888888'

    print(f"Team: {team_name} (color: {team_color})")

    # Filter to player if specified
    if player_name:
        shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
        shots_df = shots_df[shots_df[shooter_col] == player_name].copy()
        # Recalculate match count for filtered data
        multi_match_info['total_matches'] = shots_df['_match_id'].nunique()
        print(f"Filtered to {player_name}: {len(shots_df)} shots")

    if shots_df.empty:
        print(f"No shots found for player: {player_name}")
        return []

    results = []

    # Create multi-match chart
    fig = create_multi_match_shot_chart(
        shots_df, team_name, team_color, multi_match_info,
        competition=competition, player_name=player_name
    )

    # Build filename
    name_part = team_name.replace(' ', '_')
    if player_name:
        name_part = f"{player_name.replace(' ', '_')}_{name_part}"
    filename = f"shot_map_{name_part}_season.png"
    results.append((fig, filename))

    # Save if requested
    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, fn in results:
            filepath = os.path.join(output_folder, fn)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                       facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def run(config):
    """
    Entry point for launcher integration.
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder', os.path.dirname(file_path))
    competition = config.get('competition', '')
    save = config.get('save', True)

    results = create_shot_charts(file_path, output_folder, competition, save)

    if not save:
        for fig, _ in results:
            plt.show()

    for fig, _ in results:
        plt.close(fig)

    print("\nDone!")


def main():
    """Standalone entry point - prompts user for inputs."""
    print("\n" + "="*60)
    print("CBS SPORTS SHOT CHART BUILDER")
    print("="*60)

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
