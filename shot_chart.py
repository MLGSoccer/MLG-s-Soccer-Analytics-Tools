"""
Shot Chart Generator - CBS Sports Styling

Creates shot location charts showing where shots were taken on the pitch.
- Circles for non-goals, stars for goals
- Size scaled by xG value
- Separate chart per team
- Designed for easy expansion to player/team multi-game views

Data source: TruMedia CSV with EventX, EventY coordinates
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Arc, Rectangle, Circle, FancyBboxPatch
import numpy as np
import pandas as pd
import os
from datetime import datetime

# Import shared utilities
from shared.colors import (
    TEAM_COLORS, fuzzy_match_team
)
from shared.styles import BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer


# Shot-related playTypes in TruMedia data
SHOT_TYPES = {'Miss', 'Goal', 'PenaltyGoal', 'AttemptSaved'}
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


def draw_pitch(ax, orientation='horizontal', pitch_color='#2E7D32', line_color='white', alpha=0.9):
    """
    Draw a soccer pitch on the given axes.

    Args:
        ax: matplotlib axes
        orientation: 'horizontal' (width along x-axis) or 'vertical'
        pitch_color: background color of the pitch
        line_color: color of pitch markings
        alpha: transparency of pitch elements

    Pitch dimensions based on standard 105m x 68m scaled to 100 x 100 coordinate system.
    TruMedia uses 0-100 for both X and Y.
    """
    # Pitch dimensions (normalized to 0-100)
    pitch_length = 100  # X-axis (towards goal)
    pitch_width = 100   # Y-axis (sideline to sideline)

    # Penalty area dimensions (scaled from real pitch)
    # Real: 16.5m from goal line, 40.3m wide (16.5m from each post + 7.32m goal)
    # Scaled: ~16.5% of length, ~60% of width
    penalty_area_length = 17
    penalty_area_width = 44
    penalty_area_left = (pitch_width - penalty_area_width) / 2

    # 6-yard box
    six_yard_length = 6
    six_yard_width = 20
    six_yard_left = (pitch_width - six_yard_width) / 2

    # Goal dimensions
    goal_width = 12  # Scaled representation
    goal_left = (pitch_width - goal_width) / 2

    # Penalty spot
    penalty_spot_dist = 12

    # Draw pitch background
    pitch_bg = FancyBboxPatch(
        (0, 0), pitch_length, pitch_width,
        boxstyle="round,pad=0,rounding_size=2",
        facecolor=pitch_color, edgecolor=line_color, linewidth=2,
        alpha=alpha, zorder=0
    )
    ax.add_patch(pitch_bg)

    # Center line
    ax.axvline(x=pitch_length/2, color=line_color, linewidth=1.5, alpha=0.7, zorder=1)

    # Center circle
    center_circle = Circle(
        (pitch_length/2, pitch_width/2), 10,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(center_circle)

    # Center spot
    ax.plot(pitch_length/2, pitch_width/2, 'o', color=line_color, markersize=4, zorder=2)

    # RIGHT penalty area (attacking end - where shots are taken)
    right_penalty = Rectangle(
        (pitch_length - penalty_area_length, penalty_area_left),
        penalty_area_length, penalty_area_width,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(right_penalty)

    # RIGHT 6-yard box
    right_six_yard = Rectangle(
        (pitch_length - six_yard_length, six_yard_left),
        six_yard_length, six_yard_width,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(right_six_yard)

    # RIGHT penalty spot
    ax.plot(pitch_length - penalty_spot_dist, pitch_width/2, 'o',
            color=line_color, markersize=4, zorder=2)

    # RIGHT penalty arc
    right_arc = Arc(
        (pitch_length - penalty_spot_dist, pitch_width/2), 20, 20,
        angle=0, theta1=125, theta2=235,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(right_arc)

    # RIGHT goal
    right_goal = Rectangle(
        (pitch_length, goal_left), 2, goal_width,
        facecolor='white', edgecolor=line_color, linewidth=2, alpha=0.9, zorder=1
    )
    ax.add_patch(right_goal)

    # LEFT penalty area (defensive end)
    left_penalty = Rectangle(
        (0, penalty_area_left),
        penalty_area_length, penalty_area_width,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(left_penalty)

    # LEFT 6-yard box
    left_six_yard = Rectangle(
        (0, six_yard_left),
        six_yard_length, six_yard_width,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(left_six_yard)

    # LEFT penalty spot
    ax.plot(penalty_spot_dist, pitch_width/2, 'o',
            color=line_color, markersize=4, zorder=2)

    # LEFT penalty arc
    left_arc = Arc(
        (penalty_spot_dist, pitch_width/2), 20, 20,
        angle=0, theta1=-55, theta2=55,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(left_arc)

    # LEFT goal
    left_goal = Rectangle(
        (-2, goal_left), 2, goal_width,
        facecolor='white', edgecolor=line_color, linewidth=2, alpha=0.9, zorder=1
    )
    ax.add_patch(left_goal)

    # Set axis limits with small padding for goals
    ax.set_xlim(-4, pitch_length + 4)
    ax.set_ylim(-2, pitch_width + 2)
    ax.set_aspect('equal')
    ax.axis('off')


def draw_half_pitch(ax, pitch_color='#2E7D32', line_color='white', alpha=0.9):
    """
    Draw only the attacking half of the pitch with goal at TOP.

    Real pitch: 105m x 68m (length x width)
    Data coordinates: 0-100 for both X (length) and Y (width)

    Orientation: Goal at top, halfway line at bottom
    - Display X-axis = data Y (width, 0-100 left to right)
    - Display Y-axis = data X (length, 50-100 bottom to top)
    """
    # Real pitch ratio: length/width = 105/68 ≈ 1.544
    # For vertical orientation, we want width (68m) on X-axis, half-length (52.5m) on Y-axis
    # Aspect ratio = (52.5/68) ≈ 0.772 for half pitch

    # Pitch markings (all in percentage, 0-100 scale)
    # Penalty area: 16.5m deep from goal line
    penalty_area_depth = 16.5 / 105 * 100  # ~15.7%

    # Penalty area width: 40.3m
    penalty_area_width_pct = 40.3 / 68 * 100  # ~59.3%
    penalty_area_left = (100 - penalty_area_width_pct) / 2

    # 6-yard box: 5.5m deep
    six_yard_depth = 5.5 / 105 * 100  # ~5.2%

    # 6-yard box width: 18.3m
    six_yard_width_pct = 18.3 / 68 * 100  # ~26.9%
    six_yard_left = (100 - six_yard_width_pct) / 2

    # Goal width: 7.32m
    goal_width_pct = 7.32 / 68 * 100  # ~10.8%
    goal_left = (100 - goal_width_pct) / 2

    # Penalty spot: 11m from goal
    penalty_spot_dist = 11 / 105 * 100  # ~10.5%

    # Center circle radius: 9.15m
    center_circle_radius_x = 9.15 / 68 * 100  # as % of width
    center_circle_radius_y = 9.15 / 105 * 100  # as % of length

    # Draw pitch background (half pitch)
    # X: 0-100 (width), Y: 50-100 (half length, goal at top)
    pitch_bg = FancyBboxPatch(
        (0, 50), 100, 50,
        boxstyle="round,pad=0,rounding_size=1",
        facecolor=pitch_color, edgecolor=line_color, linewidth=2,
        alpha=alpha, zorder=0
    )
    ax.add_patch(pitch_bg)

    # Halfway line (horizontal at bottom)
    ax.axhline(y=50, color=line_color, linewidth=2, alpha=0.8, zorder=1)

    # Center circle arc (visible portion)
    center_arc = Arc(
        (50, 50), center_circle_radius_x * 2, center_circle_radius_y * 2,
        angle=0, theta1=0, theta2=180,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(center_arc)

    # Penalty area (at top)
    penalty_area = Rectangle(
        (penalty_area_left, 100 - penalty_area_depth),
        penalty_area_width_pct, penalty_area_depth,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(penalty_area)

    # 6-yard box
    six_yard = Rectangle(
        (six_yard_left, 100 - six_yard_depth),
        six_yard_width_pct, six_yard_depth,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(six_yard)

    # Penalty spot
    ax.plot(50, 100 - penalty_spot_dist, 'o',
            color=line_color, markersize=4, zorder=2)

    # Penalty arc
    penalty_arc = Arc(
        (50, 100 - penalty_spot_dist), center_circle_radius_x * 2, center_circle_radius_y * 2,
        angle=0, theta1=215, theta2=325,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(penalty_arc)

    # Goal (at top, outside pitch boundary)
    goal_depth = 2.5 / 105 * 100
    goal = Rectangle(
        (goal_left, 100), goal_width_pct, goal_depth,
        facecolor='white', edgecolor=line_color, linewidth=2, alpha=0.9, zorder=1
    )
    ax.add_patch(goal)

    # Set tight axis limits (minimal padding)
    ax.set_xlim(-1, 101)
    ax.set_ylim(49, 102.5)

    # Aspect ratio: real pitch is 105m x 68m
    # We want 1 unit of Y (length) to display same as 68/105 units of X (width)
    ax.set_aspect(105 / 68)
    ax.axis('off')


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


def plot_shots(ax, shots_df, team_color, flip_coords=False):
    """
    Plot shots on the pitch (vertical orientation, goal at top).

    Args:
        ax: matplotlib axes with pitch drawn
        shots_df: DataFrame filtered to one team's shots
        team_color: hex color for the team
        flip_coords: if True, flip coordinates (for teams attacking opposite direction in CSV)

    Coordinate mapping (vertical orientation):
        - Display X = data EventY (width, sideline to sideline)
        - Display Y = data EventX (length, towards goal at top)
    """
    if shots_df.empty:
        return

    for _, shot in shots_df.iterrows():
        data_x = shot['EventX']  # Length (towards goal)
        data_y = shot['EventY']  # Width (sideline to sideline)
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES

        # Flip coordinates if needed (so all teams attack towards top)
        if flip_coords:
            data_x = 100 - data_x
            data_y = 100 - data_y

        # Map to display coordinates (vertical orientation)
        display_x = data_y  # Width on horizontal axis
        display_y = data_x  # Length on vertical axis (goal at top = 100)

        # Scale marker size by xG (minimum size for visibility)
        # xG ranges from ~0.01 to ~0.8 typically
        # Map to marker sizes ~100 to ~800
        base_size = 50
        size = base_size + (xg * 700)

        # Choose marker: star for goal, circle for non-goal
        marker = '*' if is_goal else 'o'
        edge_width = 2 if is_goal else 1.5

        # Plot the shot
        ax.scatter(
            display_x, display_y,
            s=size,
            c=team_color,
            marker=marker,
            edgecolors='white',
            linewidths=edge_width,
            alpha=0.85,
            zorder=10
        )


def create_team_shot_chart(shots_df, team_name, team_color, match_info,
                           opponent_name, team_final_score=0, opponent_goals=0,
                           own_goals_for=0, own_goals_against=0,
                           flip_coords=False, competition=''):
    """
    Create a single team's shot chart.

    Args:
        shots_df: DataFrame with shot data for this team
        team_name: name of the team
        team_color: hex color
        match_info: dict with date, etc.
        opponent_name: opponent team name
        flip_coords: whether to flip coordinates
        competition: competition name for subtitle

    Returns:
        matplotlib figure
    """
    # Calculate figure size based on pitch dimensions
    # X range: -1 to 101 = 102 units (width)
    # Y range: 49 to 102.5 = 53.5 units (half-pitch length)
    # Aspect ratio 105/68 means Y is stretched relative to X
    # Visual ratio = (Y_range * aspect) / X_range = (53.5 * 105/68) / 102 = 0.81
    # So pitch is ~1.23x wider than tall visually

    fig_width = 12
    pitch_visual_ratio = (53.5 * (105/68)) / 102  # ~0.81
    pitch_height = fig_width * pitch_visual_ratio

    # Add minimal space for title and stats
    title_space = 0.7  # inches
    stats_space = 0.5  # inches
    fig_height = pitch_height + title_space + stats_space

    fig = plt.figure(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(BG_COLOR)

    # Position axes to fill available space
    title_frac = title_space / fig_height
    stats_frac = stats_space / fig_height
    pitch_frac = pitch_height / fig_height

    # [left, bottom, width, height]
    ax = fig.add_axes([0.0, stats_frac, 1.0, pitch_frac])
    ax.set_facecolor(BG_COLOR)

    # Draw the half pitch
    draw_half_pitch(ax, pitch_color='#1E5631', line_color='white', alpha=0.85)

    # Plot shots
    plot_shots(ax, shots_df, team_color, flip_coords=flip_coords)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])

    # Title with score - at top of figure (use actual final score, not shot-based)
    title_y = 1 - (title_frac * 0.3)
    fig.text(0.5, title_y, f"{team_name.upper()} {team_final_score}-{opponent_goals} {opponent_name.upper()}",
             ha='center', va='center', fontsize=20, fontweight='bold',
             color=TEXT_PRIMARY, fontfamily='sans-serif')

    # Subtitle
    subtitle_parts = [f"{team_name.upper()} SHOT MAP"]
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    subtitle_y = 1 - (title_frac * 0.7)
    fig.text(0.5, subtitle_y, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY,
             fontfamily='sans-serif')

    # Stats box - at bottom (include own goals in the goals text if applicable)
    goals_text = str(goals)
    if own_goals_for > 0:
        goals_text += f" + {own_goals_for} OG"

    stats_text = f"Shots: {total_shots}  |  xG: {total_xg:.2f}  |  Goals: {goals_text}"
    fig.text(0.5, stats_frac * 0.5, stats_text,
             ha='center', va='center', fontsize=13, fontweight='bold',
             color=TEXT_PRIMARY, fontfamily='sans-serif',
             bbox=dict(boxstyle='round,pad=0.4', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=1, alpha=0.9))

    # Legend - inside pitch, upper left
    legend_elements = [
        plt.scatter([], [], s=150, c=team_color, marker='o',
                   edgecolors='white', linewidths=1.5, label='Shot'),
        plt.scatter([], [], s=250, c=team_color, marker='*',
                   edgecolors='white', linewidths=2, label='Goal'),
    ]
    legend = ax.legend(handles=legend_elements, loc='upper left',
                       fontsize=9, framealpha=0.85,
                       facecolor=BG_COLOR, edgecolor='white',
                       labelcolor=TEXT_PRIMARY, ncol=3,
                       bbox_to_anchor=(0, 100), bbox_transform=ax.transData)

    return fig


def draw_full_pitch_horizontal(ax, pitch_color='#2E7D32', line_color='white', alpha=0.9):
    """
    Draw a full pitch in horizontal orientation (goals on left and right).

    X-axis: 0-100 (length, goal to goal)
    Y-axis: 0-100 (width, sideline to sideline)
    """
    # Pitch markings (percentage-based)
    penalty_area_depth = 16.5 / 105 * 100
    penalty_area_width_pct = 40.3 / 68 * 100
    penalty_area_bottom = (100 - penalty_area_width_pct) / 2

    six_yard_depth = 5.5 / 105 * 100
    six_yard_width_pct = 18.3 / 68 * 100
    six_yard_bottom = (100 - six_yard_width_pct) / 2

    goal_width_pct = 7.32 / 68 * 100
    goal_bottom = (100 - goal_width_pct) / 2

    penalty_spot_dist = 11 / 105 * 100
    center_circle_radius = 9.15 / 105 * 100

    # Draw pitch background
    pitch_bg = FancyBboxPatch(
        (0, 0), 100, 100,
        boxstyle="round,pad=0,rounding_size=1",
        facecolor=pitch_color, edgecolor=line_color, linewidth=2,
        alpha=alpha, zorder=0
    )
    ax.add_patch(pitch_bg)

    # Center line
    ax.axvline(x=50, color=line_color, linewidth=2, alpha=0.8, zorder=1)

    # Center circle
    center_circle = Circle(
        (50, 50), center_circle_radius * 100 / 100,
        fill=False, edgecolor=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(center_circle)

    # Center spot
    ax.plot(50, 50, 'o', color=line_color, markersize=4, zorder=2)

    # LEFT penalty area
    left_penalty = Rectangle(
        (0, penalty_area_bottom), penalty_area_depth, penalty_area_width_pct,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(left_penalty)

    # LEFT 6-yard box
    left_six_yard = Rectangle(
        (0, six_yard_bottom), six_yard_depth, six_yard_width_pct,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(left_six_yard)

    # LEFT penalty spot
    ax.plot(penalty_spot_dist, 50, 'o', color=line_color, markersize=4, zorder=2)

    # LEFT penalty arc
    left_arc = Arc(
        (penalty_spot_dist, 50), center_circle_radius * 2, center_circle_radius * 2 * (105/68),
        angle=0, theta1=-55, theta2=55,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(left_arc)

    # LEFT goal
    goal_depth = 2
    left_goal = Rectangle(
        (-goal_depth, goal_bottom), goal_depth, goal_width_pct,
        facecolor='white', edgecolor=line_color, linewidth=2, alpha=0.9, zorder=1
    )
    ax.add_patch(left_goal)

    # RIGHT penalty area
    right_penalty = Rectangle(
        (100 - penalty_area_depth, penalty_area_bottom), penalty_area_depth, penalty_area_width_pct,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(right_penalty)

    # RIGHT 6-yard box
    right_six_yard = Rectangle(
        (100 - six_yard_depth, six_yard_bottom), six_yard_depth, six_yard_width_pct,
        fill=False, edgecolor=line_color, linewidth=2, alpha=0.8, zorder=1
    )
    ax.add_patch(right_six_yard)

    # RIGHT penalty spot
    ax.plot(100 - penalty_spot_dist, 50, 'o', color=line_color, markersize=4, zorder=2)

    # RIGHT penalty arc
    right_arc = Arc(
        (100 - penalty_spot_dist, 50), center_circle_radius * 2, center_circle_radius * 2 * (105/68),
        angle=0, theta1=125, theta2=235,
        color=line_color, linewidth=1.5, alpha=0.7, zorder=1
    )
    ax.add_patch(right_arc)

    # RIGHT goal
    right_goal = Rectangle(
        (100, goal_bottom), goal_depth, goal_width_pct,
        facecolor='white', edgecolor=line_color, linewidth=2, alpha=0.9, zorder=1
    )
    ax.add_patch(right_goal)

    # Set axis limits
    ax.set_xlim(-3, 103)
    ax.set_ylim(-1, 101)
    ax.set_aspect(68 / 105)  # Real pitch proportions
    ax.axis('off')


def plot_shots_horizontal(ax, shots_df, team_color, flip_x=False):
    """
    Plot shots on horizontal pitch (X = length toward goal, Y = width).

    Args:
        flip_x: If True, mirror shots to opposite end (X -> 100-X). Y stays same.
    """
    if shots_df.empty:
        return

    for _, shot in shots_df.iterrows():
        x = shot['EventX']
        y = shot['EventY']
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES

        if flip_x:
            x = 100 - x  # Mirror to opposite end, keep Y same

        base_size = 50
        size = base_size + (xg * 700)
        marker = '*' if is_goal else 'o'
        edge_width = 2 if is_goal else 1.5

        ax.scatter(
            x, y, s=size, c=team_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=0.85, zorder=10
        )


def create_combined_shot_chart(shots_df, team1_name, team1_color, team1_flip,
                                team2_name, team2_color, team2_flip,
                                match_info, competition=''):
    """
    Create a combined shot chart showing both teams on a full horizontal pitch.

    Home team (team1) attacks LEFT, Away team (team2) attacks RIGHT.
    """
    # Full pitch: 105m x 68m, aspect = 68/105 = 0.648
    fig_width = 14
    pitch_height = fig_width * (68 / 105)
    title_space = 0.8
    stats_space = 0.5
    fig_height = pitch_height + title_space + stats_space

    fig = plt.figure(figsize=(fig_width, fig_height))
    fig.patch.set_facecolor(BG_COLOR)

    title_frac = title_space / fig_height
    stats_frac = stats_space / fig_height
    pitch_frac = pitch_height / fig_height

    ax = fig.add_axes([0.0, stats_frac, 1.0, pitch_frac])
    ax.set_facecolor(BG_COLOR)

    # Draw full pitch
    draw_full_pitch_horizontal(ax, pitch_color='#1E5631', line_color='white', alpha=0.85)

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

    plot_shots_horizontal(ax, team1_shots, team1_color, flip_x=team1_combined_flip)
    plot_shots_horizontal(ax, team2_shots, team2_color, flip_x=team2_combined_flip)

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
    title_y = 1 - (title_frac * 0.35)
    fig.text(0.5, title_y, f"{team1_name.upper()} {team1_goals}-{team2_goals} {team2_name.upper()}",
             ha='center', va='center', fontsize=22, fontweight='bold',
             color=TEXT_PRIMARY, fontfamily='sans-serif')

    # Subtitle
    subtitle_parts = ['SHOT MAP']
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    subtitle_y = 1 - (title_frac * 0.75)
    fig.text(0.5, subtitle_y, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY,
             fontfamily='sans-serif')

    # Stats for both teams at bottom (include own goals if applicable)
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
    fig.text(0.5, stats_frac * 0.5, stats_text,
             ha='center', va='center', fontsize=11, fontweight='bold',
             color=TEXT_PRIMARY, fontfamily='sans-serif',
             bbox=dict(boxstyle='round,pad=0.4', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=1, alpha=0.9))

    # Legend with both team colors
    legend_elements = [
        plt.scatter([], [], s=150, c=team1_color, marker='o',
                   edgecolors='white', linewidths=1.5, label=team1_name),
        plt.scatter([], [], s=150, c=team2_color, marker='o',
                   edgecolors='white', linewidths=1.5, label=team2_name),
        plt.scatter([], [], s=200, c='gray', marker='*',
                   edgecolors='white', linewidths=2, label='Goal'),
    ]
    legend = ax.legend(handles=legend_elements, loc='upper left',
                       fontsize=9, framealpha=0.85,
                       facecolor=BG_COLOR, edgecolor='white',
                       labelcolor=TEXT_PRIMARY, ncol=3,
                       bbox_to_anchor=(0, 100), bbox_transform=ax.transData)

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
        # First try exact match from CSV
        if team_name in team_colors_dict:
            return team_colors_dict[team_name]
        # Try fuzzy match in CSV colors
        for csv_team, color in team_colors_dict.items():
            if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                return color
        # Try built-in database (non-interactive)
        color, matched, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            return color
        # Default fallback
        return '#888888'

    team1_color_raw = resolve_color(team1_name, team_colors)
    team2_color_raw = resolve_color(team2_name, team_colors)

    # Ensure colors have enough contrast with pitch
    team1_color = ensure_pitch_contrast(team1_color_raw)
    team2_color = ensure_pitch_contrast(team2_color_raw)

    print(f"\nHome: {team1_name} ({team1_color_raw}" + (f" -> {team1_color})" if team1_color != team1_color_raw else ")"))
    print(f"Away: {team2_name} ({team2_color_raw}" + (f" -> {team2_color})" if team2_color != team2_color_raw else ")"))

    # Determine coordinate flipping
    # Home team typically attacks right in first half, so their shots should be on right side
    # Check average X coordinate for each team to determine direction
    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50

    # If team's average shot X is < 50, they're attacking left, need to flip
    team1_flip = team1_avg_x < 50
    team2_flip = team2_avg_x < 50

    print(f"\n{team1_name} avg shot X: {team1_avg_x:.1f} (flip: {team1_flip})")
    print(f"{team2_name} avg shot X: {team2_avg_x:.1f} (flip: {team2_flip})")

    # Get final scores from CSV (more reliable than aggregating shots - includes own goals, etc.)
    team1_final_score = match_info.get('home_score', 0)
    team2_final_score = match_info.get('away_score', 0)

    # Count goals from shots to detect own goals
    team1_shot_goals = len(team1_shots[team1_shots['playType'].isin(GOAL_TYPES)])
    team2_shot_goals = len(team2_shots[team2_shots['playType'].isin(GOAL_TYPES)])

    # Own goals = final score - shot goals (own goals benefit a team but aren't their shots)
    team1_own_goals = team1_final_score - team1_shot_goals  # OGs benefiting team1
    team2_own_goals = team2_final_score - team2_shot_goals  # OGs benefiting team2

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

    # Create combined chart (horizontal, both teams)
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


def run(config):
    """
    Entry point for launcher integration.

    Config keys:
        file_path: str - Path to TruMedia CSV
        output_folder: str - Where to save charts
        competition: str - Competition name (optional)
        save: bool - Whether to save (default True)
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

    # Get file path
    file_path = input("\nPath to TruMedia CSV: ").strip().strip('"').strip("'")

    if not file_path or not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return

    # Get competition
    competition = input("Competition (e.g., SERIE A, PREMIER LEAGUE): ").strip().upper()

    # Get output folder
    default_output = os.path.dirname(file_path) or os.path.expanduser("~/Downloads")
    output_folder = input(f"Output folder (default: {default_output}): ").strip() or default_output

    # Create charts
    results = create_shot_charts(file_path, output_folder, competition, save=True)

    # Show charts
    for fig, _ in results:
        plt.show()
        plt.close(fig)

    print("\nDone!")


if __name__ == "__main__":
    main()
