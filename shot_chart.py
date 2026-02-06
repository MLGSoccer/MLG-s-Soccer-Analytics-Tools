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


def plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=False):
    """
    Plot shots on vertical half-pitch (goal at top).

    TruMedia coordinates: EventX = length (0-100), EventY = width (0-100)
    mplsoccer opta coordinates: x = length, y = width
    """
    if shots_df.empty:
        return

    for _, shot in shots_df.iterrows():
        x = shot['EventX']  # Length (towards goal)
        y = shot['EventY']  # Width (sideline to sideline)
        xg = shot['xG']
        is_goal = shot['playType'] in GOAL_TYPES

        # Flip coordinates if needed (so all teams attack towards top)
        if flip_coords:
            x = 100 - x
            y = 100 - y

        # Scale marker size by xG
        base_size = 50
        size = base_size + (xg * 700)

        # Choose marker: star for goal, circle for non-goal
        marker = '*' if is_goal else 'o'
        edge_width = 2 if is_goal else 1.5

        # Plot the shot (mplsoccer handles coordinate transformation)
        ax.scatter(
            x, y, s=size, c=team_color, marker=marker,
            edgecolors='white', linewidths=edge_width,
            alpha=0.85, zorder=10
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
        pitch_color=PITCH_COLOR,
        line_color='white',
        linewidth=2,
        goal_type='box'
    )

    # Create figure with pitch
    fig, ax = pitch.draw(figsize=(10, 10))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Plot shots
    plot_shots_vertical(ax, pitch, shots_df, team_color, flip_coords=flip_coords)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])

    # Title with score
    fig.suptitle(f"{team_name.upper()} {team_final_score}-{opponent_goals} {opponent_name.upper()}",
                 fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.98)

    # Subtitle
    subtitle_parts = [f"{team_name.upper()} SHOT MAP"]
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    fig.text(0.5, 0.93, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Stats box at bottom
    goals_text = str(goals)
    if own_goals_for > 0:
        goals_text += f" + {own_goals_for} OG"

    stats_text = f"Shots: {total_shots}  |  xG: {total_xg:.2f}  |  Goals: {goals_text}"
    fig.text(0.5, 0.02, stats_text,
             ha='center', va='center', fontsize=13, fontweight='bold',
             color=TEXT_PRIMARY,
             bbox=dict(boxstyle='round,pad=0.4', facecolor=CBS_BLUE,
                      edgecolor='white', linewidth=1, alpha=0.9))

    # Legend
    legend_elements = [
        plt.scatter([], [], s=150, c=team_color, marker='o',
                   edgecolors='white', linewidths=1.5, label='Shot'),
        plt.scatter([], [], s=250, c=team_color, marker='*',
                   edgecolors='white', linewidths=2, label='Goal'),
    ]
    ax.legend(handles=legend_elements, loc='lower left',
              fontsize=9, framealpha=0.85,
              facecolor=BG_COLOR, edgecolor='white',
              labelcolor=TEXT_PRIMARY, ncol=2)

    plt.tight_layout(rect=[0, 0.05, 1, 0.92])

    return fig


def plot_shots_horizontal(ax, shots_df, team_color, flip_x=False):
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
    # Create horizontal full pitch with opta coordinates
    pitch = Pitch(
        pitch_type='opta',
        pitch_color=PITCH_COLOR,
        line_color='white',
        linewidth=2,
        goal_type='box'
    )

    # Create figure with pitch
    fig, ax = pitch.draw(figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

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
    fig.suptitle(f"{team1_name.upper()} {team1_goals}-{team2_goals} {team2_name.upper()}",
                 fontsize=22, fontweight='bold', color=TEXT_PRIMARY, y=0.98)

    # Subtitle
    subtitle_parts = ['SHOT MAP']
    if competition:
        subtitle_parts.append(competition.upper())
    if match_info.get('date_formatted'):
        subtitle_parts.append(match_info['date_formatted'])

    fig.text(0.5, 0.93, ' | '.join(subtitle_parts),
             ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

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
    fig.text(0.5, 0.02, stats_text,
             ha='center', va='center', fontsize=11, fontweight='bold',
             color=TEXT_PRIMARY,
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
    ax.legend(handles=legend_elements, loc='upper left',
              fontsize=9, framealpha=0.85,
              facecolor=BG_COLOR, edgecolor='white',
              labelcolor=TEXT_PRIMARY, ncol=3)

    plt.tight_layout(rect=[0, 0.05, 1, 0.92])

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
