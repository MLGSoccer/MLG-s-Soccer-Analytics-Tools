"""
Player Rolling Average Chart Builder
Creates rolling average charts for individual player performance analysis.
Tracks shots, goals, and xG on a per-90-minutes basis.
"""
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

# Import shared utilities
from shared.colors import get_team_color, get_contrast_color, ensure_contrast_with_background
from shared.styles import BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_SUBTLE, style_axis
from shared.file_utils import get_file_path, get_output_folder


def format_height_imperial(height_cm):
    """Convert height from cm to feet/inches format (e.g., 6'2")."""
    if not height_cm:
        return '-'
    try:
        total_inches = float(height_cm) / 2.54
        feet = int(total_inches // 12)
        inches = int(round(total_inches % 12))
        if inches == 12:
            feet += 1
            inches = 0
        return f"{feet}'{inches}\""
    except (ValueError, TypeError):
        return '-'


def format_weight_imperial(weight_kg):
    """Convert weight from kg to lbs format (e.g., 185 lbs)."""
    if not weight_kg:
        return '-'
    try:
        lbs = float(weight_kg) * 2.20462
        return f"{int(round(lbs))} lbs"
    except (ValueError, TypeError):
        return '-'


def parse_player_summary_csv(filepath, gui_mode=False):
    """Parse TruMedia player summary CSV (one row per match).

    Returns:
        matches: list of match dicts with per-match stats
        player_name: full player name
        team_name: team name
        team_color: team color from CSV or fallback
        season: season name
        player_info: dict with Age, Nationality, Height, Weight

    Args:
        gui_mode: If True, skip all interactive prompts and use defaults
    """
    f = open(filepath, encoding='utf-8')
    reader = csv.reader(f)
    header = next(reader)

    def get_idx(col_name):
        try:
            return header.index(col_name)
        except ValueError:
            return None

    # Column indices (use explicit None check, not 'or', since index 0 is falsy)
    date_idx = get_idx('Date')
    player_idx = get_idx('playerFullName')
    if player_idx is None:
        player_idx = get_idx('Player')
    team_idx = get_idx('newestTeam')
    if team_idx is None:
        team_idx = get_idx('teamName')
    color_idx = get_idx('newestTeamColor')
    opponent_idx = get_idx('opponent')
    result_idx = get_idx('Result')
    min_idx = get_idx('Min')
    goals_idx = get_idx('Goal')
    xg_idx = get_idx('ExpG')
    shots_idx = get_idx('Shot')
    season_idx = get_idx('seasonName')

    # Player info column indices (use explicit None check, not 'or', since index 0 is falsy)
    age_idx = get_idx('Age')
    if age_idx is None:
        age_idx = get_idx('age')
    nationality_idx = get_idx('Nationality')
    if nationality_idx is None:
        nationality_idx = get_idx('nationality')
    if nationality_idx is None:
        nationality_idx = get_idx('Nation')
    if nationality_idx is None:
        nationality_idx = get_idx('nation')
    height_idx = get_idx('Height')
    if height_idx is None:
        height_idx = get_idx('height')
    weight_idx = get_idx('Weight')
    if weight_idx is None:
        weight_idx = get_idx('weight')

    matches = []
    player_name = None
    team_name = None
    team_color = None
    season = None
    player_info = {'age': '', 'nationality': '', 'height': '', 'weight': ''}

    for row in reader:
        if len(row) < len(header):
            continue

        # Capture player/team info from first row
        if player_name is None:
            player_name = row[player_idx] if player_idx is not None else 'Unknown Player'
            team_name = row[team_idx] if team_idx is not None else 'Unknown Team'
            team_color = row[color_idx] if color_idx is not None and row[color_idx] else None
            season = row[season_idx] if season_idx is not None else ''

            # Capture player bio info
            player_info['age'] = row[age_idx] if age_idx is not None and row[age_idx] else ''
            player_info['nationality'] = row[nationality_idx] if nationality_idx is not None and row[nationality_idx] else ''
            player_info['height'] = row[height_idx] if height_idx is not None and row[height_idx] else ''
            player_info['weight'] = row[weight_idx] if weight_idx is not None and row[weight_idx] else ''

        try:
            minutes = int(row[min_idx]) if min_idx is not None and row[min_idx] else 0
            goals = int(row[goals_idx]) if goals_idx is not None and row[goals_idx] else 0
            xg = float(row[xg_idx]) if xg_idx is not None and row[xg_idx] else 0
            shots = int(row[shots_idx]) if shots_idx is not None and row[shots_idx] else 0
        except (ValueError, IndexError):
            continue

        # Skip matches with 0 minutes
        if minutes == 0:
            continue

        matches.append({
            'date': row[date_idx] if date_idx is not None else '',
            'opponent': row[opponent_idx] if opponent_idx is not None else '',
            'result': row[result_idx] if result_idx is not None else '',
            'minutes': minutes,
            'goals': goals,
            'xg': xg,
            'shots': shots,
            'season': row[season_idx] if season_idx is not None else '',
            # Store team/player info with each match for multi-team scenarios
            'team_name': row[team_idx] if team_idx is not None else '',
            'team_color': row[color_idx] if color_idx is not None and row[color_idx] else None,
            'age': row[age_idx] if age_idx is not None and row[age_idx] else '',
            'nationality': row[nationality_idx] if nationality_idx is not None and row[nationality_idx] else '',
            'height': row[height_idx] if height_idx is not None and row[height_idx] else '',
            'weight': row[weight_idx] if weight_idx is not None and row[weight_idx] else '',
        })

    f.close()

    # Sort by date (oldest first for chronological rolling)
    matches.sort(key=lambda x: x['date'])

    # Use team info from most recent match (last after sorting)
    if matches:
        most_recent = matches[-1]
        team_name = most_recent['team_name'] or team_name
        team_color = most_recent['team_color'] or team_color
        player_info['age'] = most_recent['age'] or player_info['age']
        player_info['nationality'] = most_recent['nationality'] or player_info['nationality']
        player_info['height'] = most_recent['height'] or player_info['height']
        player_info['weight'] = most_recent['weight'] or player_info['weight']

    print(f"[OK] Found {len(matches)} matches for {player_name}")
    print(f"     Team: {team_name} (most recent) | Season: {season}")

    # Fallback for team color (no prompt in GUI mode)
    if not team_color:
        team_color = get_team_color(team_name, prompt_if_missing=not gui_mode)

    return matches, player_name, team_name, team_color, season, player_info


def calculate_per_90(value, minutes):
    """Calculate per-90-minute rate."""
    if minutes == 0:
        return 0
    return (value / minutes) * 90


def find_season_boundaries(matches):
    """Find match indices where season changes.

    Returns list of (match_index, season_name) tuples for each new season start.
    """
    boundaries = []
    current_season = None

    for i, match in enumerate(matches):
        if match['season'] != current_season:
            boundaries.append((i + 1, match['season']))  # +1 for 1-indexed match numbers
            current_season = match['season']

    return boundaries


def draw_season_boundaries(ax, boundaries, y_pos='top'):
    """Draw vertical lines and labels at season boundaries.

    Args:
        ax: matplotlib axis
        boundaries: list of (match_index, season_name) tuples
        y_pos: 'top' or 'bottom' for label position
    """
    if len(boundaries) <= 1:
        return  # Only one season, no boundaries to draw

    # Skip the first boundary (start of first season) - only draw where seasons change
    for match_num, season_name in boundaries[1:]:
        # Draw vertical line at the boundary (between previous and current match)
        ax.axvline(x=match_num - 0.5, color='white', linestyle='--', linewidth=1, alpha=0.5)

        # Add season label
        if y_pos == 'top':
            y = ax.get_ylim()[1]
            va = 'bottom'
        else:
            y = ax.get_ylim()[0]
            va = 'top'

        ax.text(match_num - 0.5, y, f' {season_name}', color='white', fontsize=8,
                alpha=0.7, ha='left', va=va, rotation=0)


def calculate_rolling_average(values, window=10):
    """Calculate rolling average with specified window."""
    rolling = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        rolling.append(sum(window_vals) / len(window_vals))
    return rolling


def calculate_rolling_per90_weighted(values, minutes, window=10):
    """Calculate minutes-weighted rolling per-90 rate.

    Instead of averaging per-90 rates, this sums raw values and minutes
    over the window, then calculates the true per-90 rate.

    Formula: (sum of values in window / sum of minutes in window) * 90
    """
    rolling = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        window_mins = minutes[start:i + 1]

        total_value = sum(window_vals)
        total_minutes = sum(window_mins)

        if total_minutes > 0:
            rolling.append((total_value / total_minutes) * 90)
        else:
            rolling.append(0)
    return rolling


def create_rolling_charts(matches, player_name, team_name, team_color, season, output_path, window=10, player_info=None):
    """Create the 4-panel player rolling chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    if player_info is None:
        player_info = {'age': '', 'nationality': '', 'height': '', 'weight': ''}

    # Find season boundaries for multi-season data
    season_boundaries = find_season_boundaries(matches)

    # Extract raw values and minutes
    xg_values = [m['xg'] for m in matches]
    goals_values = [m['goals'] for m in matches]
    shots_values = [m['shots'] for m in matches]
    minutes = [m['minutes'] for m in matches]

    # Calculate per-90 values for each match (for scatter plot display)
    xg_per90 = [calculate_per_90(m['xg'], m['minutes']) for m in matches]
    goals_per90 = [calculate_per_90(m['goals'], m['minutes']) for m in matches]
    shots_per90 = [calculate_per_90(m['shots'], m['minutes']) for m in matches]

    # Calculate minutes-weighted rolling per-90 rates
    xg_rolling = calculate_rolling_per90_weighted(xg_values, minutes, window)
    goals_rolling = calculate_rolling_per90_weighted(goals_values, minutes, window)
    shots_rolling = calculate_rolling_per90_weighted(shots_values, minutes, window)

    # Calculate xG per shot (shot quality)
    xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in matches]
    # Rolling xG per shot (weighted by shots taken)
    xg_per_shot_rolling = []
    for i in range(len(matches)):
        start = max(0, i - window + 1)
        window_xg = sum(xg_values[start:i + 1])
        window_shots = sum(shots_values[start:i + 1])
        if window_shots > 0:
            xg_per_shot_rolling.append(window_xg / window_shots)
        else:
            xg_per_shot_rolling.append(0)

    match_nums = list(range(1, len(matches) + 1))

    # Colors - team-specific scheme
    color_xg = ensure_contrast_with_background(team_color)  # Team color for xG (primary)
    color_goals = get_contrast_color(team_color)  # Contrast color for goals
    color_shots = '#FFFFFF'                    # White for shots (with dashed line)

    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor(BG_COLOR)

    # Check if we have player info to display
    has_player_info = any([player_info.get('age'), player_info.get('nationality'),
                           player_info.get('height'), player_info.get('weight')])

    # Adjust grid top based on whether info strip is shown
    grid_top = 0.78 if has_player_info else 0.82
    gs = fig.add_gridspec(2, 2, hspace=0.55, wspace=0.25, top=grid_top)

    # ============ Panel 1: Rolling xG/90 vs Goals/90 with shading (top left) ============
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_COLOR)

    # Shading for over/underperformance (no legend labels - visual is self-explanatory)
    ax1.fill_between(match_nums, goals_rolling, xg_rolling,
                     where=[g > x for g, x in zip(goals_rolling, xg_rolling)],
                     interpolate=True,
                     color=color_goals, alpha=0.3)
    ax1.fill_between(match_nums, goals_rolling, xg_rolling,
                     where=[g <= x for g, x in zip(goals_rolling, xg_rolling)],
                     interpolate=True,
                     color=color_xg, alpha=0.3)

    ax1.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label='xG/90')
    ax1.plot(match_nums, goals_rolling, color=color_goals, linewidth=3, label='Goals/90')

    ax1.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax1.set_ylabel('PER 90 MINUTES', fontsize=12, fontweight='bold', color='white')
    ax1.set_title(f'GOALS/90 vs xG/90 ({window}-GAME ROLLING)', fontsize=14, fontweight='bold', color='white', pad=10)
    ax1.legend(loc='upper center', fontsize=9, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.15), ncol=2)

    style_axis(ax1)
    draw_season_boundaries(ax1, season_boundaries)

    # ============ Panel 2: xG Per 90 Trend (top right) ============
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(BG_COLOR)

    # Show individual match xG/90 as scatter points
    ax2.scatter(match_nums, xg_per90, color=color_xg, alpha=0.4, s=50, zorder=2)
    # Rolling line on top
    ax2.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label=f'Rolling Avg', zorder=3)
    # Season average line (minutes-weighted)
    total_minutes = sum(minutes)
    season_avg_xg = (sum(xg_values) / total_minutes * 90) if total_minutes > 0 else 0
    ax2.axhline(y=season_avg_xg, color='white', linestyle='--', linewidth=1.5, alpha=0.7, label=f'Season Avg: {season_avg_xg:.2f}')

    ax2.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax2.set_ylabel('xG PER 90', fontsize=12, fontweight='bold', color='white')
    ax2.set_title('xG PER 90 TREND', fontsize=14, fontweight='bold', color='white', pad=10)
    ax2.legend(loc='upper center', fontsize=9, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.15), ncol=2)

    style_axis(ax2)
    draw_season_boundaries(ax2, season_boundaries)

    # ============ Panel 3: Shot Volume & Quality (bottom left) ============
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(BG_COLOR)

    # Bars for per-match shots
    ax3.bar(match_nums, shots_values, color=color_xg, alpha=0.7,
            edgecolor='white', linewidth=0.5, label='Shots (per match)', zorder=2)

    ax3.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax3.set_ylabel('SHOTS', fontsize=12, fontweight='bold', color=color_xg)
    ax3.tick_params(axis='y', labelcolor=color_xg)
    ax3.set_title('SHOT VOLUME & QUALITY', fontsize=14, fontweight='bold', color='white', pad=10)

    # Style primary axis
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_color(color_goals)
    ax3.spines['left'].set_color(SPINE_COLOR)
    ax3.spines['bottom'].set_color(SPINE_COLOR)
    ax3.tick_params(colors=SPINE_COLOR)
    ax3.tick_params(axis='y', colors=color_xg)
    ax3.set_axisbelow(True)
    ax3.grid(axis='y', color=SPINE_COLOR, linestyle='-', linewidth=0.5, alpha=0.3)

    # Secondary axis for rolling xG per shot
    ax3b = ax3.twinx()
    ax3b.plot(match_nums, xg_per_shot_rolling, color=color_goals, linewidth=3,
              marker='o', markersize=4, label=f'xG/Shot ({window}-game rolling)', zorder=3)
    ax3b.set_ylabel('xG PER SHOT', fontsize=12, fontweight='bold', color=color_goals)
    ax3b.tick_params(axis='y', labelcolor=color_goals)
    ax3b.spines['right'].set_color(color_goals)

    # Combined legend below x-axis
    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3b.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper center', fontsize=9,
               facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.15), ncol=2)
    draw_season_boundaries(ax3, season_boundaries)

    # ============ Panel 4: Last 10 Matches vs Season Average (bottom right) ============
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(BG_COLOR)

    # Use last 10 matches to align with rolling window
    max_bars = 10
    if len(matches) > max_bars:
        display_matches = matches[-max_bars:]
    else:
        display_matches = matches

    display_shots = [m['shots'] for m in display_matches]
    display_xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in display_matches]
    display_labels = [f"{m['opponent']} ({m['minutes']}')" for m in display_matches]
    x_pos = np.arange(len(display_matches))

    # Calculate season averages (from all matches)
    avg_shots = sum(shots_values) / len(shots_values) if shots_values else 1
    all_xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in matches]
    avg_xg_per_shot = sum(all_xg_per_shot) / len(all_xg_per_shot) if all_xg_per_shot else 1

    # Normalize to season average (1.0 = average)
    shots_norm = [v / avg_shots if avg_shots > 0 else 0 for v in display_shots]
    xg_per_shot_norm = [v / avg_xg_per_shot if avg_xg_per_shot > 0 else 0 for v in display_xg_per_shot]

    bar_width = 0.35
    bars1 = ax4.bar(x_pos - bar_width/2, shots_norm, bar_width, label='Shots', color=color_xg, edgecolor='white', linewidth=0.5)
    bars2 = ax4.bar(x_pos + bar_width/2, xg_per_shot_norm, bar_width, label='xG/Shot', color=color_goals, edgecolor='white', linewidth=0.5)

    # Add value labels on top of each bar
    def add_bar_labels(bars, values, ax):
        for bar, val in zip(bars, values):
            height = bar.get_height()
            label = f'{val:.2f}' if isinstance(val, float) and val < 10 else f'{int(val)}'
            ax.annotate(label, xy=(bar.get_x() + bar.get_width() / 2, height),
                       xytext=(0, 2), textcoords='offset points',
                       ha='center', va='bottom', fontsize=6, color='white', fontweight='bold')

    add_bar_labels(bars1, display_shots, ax4)
    add_bar_labels(bars2, display_xg_per_shot, ax4)

    # Season average line at 1.0
    ax4.axhline(y=1.0, color='white', linestyle='--', linewidth=1.5, alpha=0.7)

    ax4.set_xticks(x_pos)
    ax4.set_xticklabels(display_labels, fontsize=7, color='white')
    ax4.set_title(f'LAST {len(display_matches)} MATCHES (vs SEASON AVG)', fontsize=14, fontweight='bold', color='white', pad=10)

    # Hide y-axis
    ax4.set_ylabel('')
    ax4.set_yticklabels([])
    ax4.tick_params(axis='y', length=0)

    # Set y limits with padding for labels
    max_val = max(max(shots_norm), max(xg_per_shot_norm)) if display_matches else 1
    ax4.set_ylim(0, max_val * 1.15)

    # Style
    ax4.spines['top'].set_visible(False)
    ax4.spines['right'].set_visible(False)
    ax4.spines['left'].set_color(SPINE_COLOR)
    ax4.spines['bottom'].set_color(SPINE_COLOR)
    ax4.tick_params(axis='x', colors='white')

    # Legend below x-axis
    ax4.legend(loc='upper center', fontsize=9, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=2)

    # Build subtitle with season info
    unique_seasons = list(dict.fromkeys([m['season'] for m in matches]))  # Preserve order
    if len(unique_seasons) > 1:
        season_text = f"{unique_seasons[0]} - {unique_seasons[-1]}"
    else:
        season_text = season

    # Calculate totals
    total_goals = sum(goals_values)
    total_shots = sum(shots_values)
    total_xg = sum(xg_values)

    # Main title - player name and team
    fig.text(0.5, 0.97, f'{player_name.upper()}  •  {team_name.upper()}', ha='center', fontsize=22, fontweight='bold', color='white')

    # ============ PLAYER INFO STRIP WITH TEAM COLOR ============
    if has_player_info:
        # Create an axes for drawing the strip
        ax_header = fig.add_axes([0, 0, 1, 1])
        ax_header.set_facecolor('none')
        ax_header.axis('off')

        strip_y = 0.905
        strip_height = 0.035

        # Full team color strip
        strip_rect = mpatches.FancyBboxPatch(
            (0.05, strip_y), 0.90, strip_height,
            boxstyle="round,pad=0.003",
            facecolor=team_color, edgecolor='none',
            transform=ax_header.transAxes
        )
        ax_header.add_patch(strip_rect)

        # Info items centered in strip
        info_y = strip_y + strip_height / 2
        positions = [0.18, 0.38, 0.62, 0.82]
        labels = ['AGE', 'NATIONALITY', 'HEIGHT', 'WEIGHT']
        values = [str(player_info.get('age', '')) or '-',
                  str(player_info.get('nationality', '')) or '-',
                  format_height_imperial(player_info.get('height')),
                  format_weight_imperial(player_info.get('weight'))]

        for pos, label, value in zip(positions, labels, values):
            ax_header.text(pos, info_y + 0.005, label, fontsize=8, color='#FFFFFF',
                    transform=ax_header.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.8)
            ax_header.text(pos, info_y - 0.005, value, fontsize=11, color='white',
                    transform=ax_header.transAxes, ha='center', va='top', fontweight='bold')

        # Subtitle and stats below strip
        fig.text(0.5, 0.87, f'{season_text} | {window}-GAME ROLLING | {len(matches)} MATCHES',
                 ha='center', fontsize=13, color='#8BA3B8', style='italic')
        fig.text(0.5, 0.84, f'{total_goals} Goals | {total_shots} Shots | {total_xg:.2f} xG',
                 ha='center', fontsize=11, color='white', fontweight='bold')
    else:
        # Original layout without info strip
        fig.text(0.5, 0.93, f'{season_text} | {window}-GAME ROLLING | {len(matches)} MATCHES',
                 ha='center', fontsize=13, color='#8BA3B8', style='italic')
        fig.text(0.5, 0.895, f'{total_goals} Goals | {total_shots} Shots | {total_xg:.2f} xG',
                 ha='center', fontsize=11, color='white', fontweight='bold')

    # Footer
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.01, 'DATA: TRUMEDIA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_individual_charts(matches, player_name, team_name, team_color, season, output_folder, window=10, player_info=None):
    """Create each panel as a standalone chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    if player_info is None:
        player_info = {'age': '', 'nationality': '', 'height': '', 'weight': ''}

    # Find season boundaries for multi-season data
    season_boundaries = find_season_boundaries(matches)

    # Build subtitle with season info
    unique_seasons = list(dict.fromkeys([m['season'] for m in matches]))  # Preserve order
    if len(unique_seasons) > 1:
        season_text = f"{unique_seasons[0]} - {unique_seasons[-1]}"
    else:
        season_text = season

    # Extract raw values and minutes
    xg_values = [m['xg'] for m in matches]
    goals_values = [m['goals'] for m in matches]
    shots_values = [m['shots'] for m in matches]
    minutes = [m['minutes'] for m in matches]

    # Calculate per-90 values for each match (for scatter plot display)
    xg_per90 = [calculate_per_90(m['xg'], m['minutes']) for m in matches]
    goals_per90 = [calculate_per_90(m['goals'], m['minutes']) for m in matches]
    shots_per90 = [calculate_per_90(m['shots'], m['minutes']) for m in matches]

    # Calculate minutes-weighted rolling per-90 rates
    xg_rolling = calculate_rolling_per90_weighted(xg_values, minutes, window)
    goals_rolling = calculate_rolling_per90_weighted(goals_values, minutes, window)
    shots_rolling = calculate_rolling_per90_weighted(shots_values, minutes, window)

    # Calculate xG per shot (shot quality)
    xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in matches]
    # Rolling xG per shot (weighted by shots taken)
    xg_per_shot_rolling = []
    for i in range(len(matches)):
        start = max(0, i - window + 1)
        window_xg = sum(xg_values[start:i + 1])
        window_shots = sum(shots_values[start:i + 1])
        if window_shots > 0:
            xg_per_shot_rolling.append(window_xg / window_shots)
        else:
            xg_per_shot_rolling.append(0)

    match_nums = list(range(1, len(matches) + 1))

    # Colors - team-specific scheme
    color_xg = ensure_contrast_with_background(team_color)  # Team color for xG (primary)
    color_goals = get_contrast_color(team_color)  # Contrast color for goals

    # Calculate totals
    total_goals = sum(goals_values)
    total_shots = sum(shots_values)
    total_xg = sum(xg_values)

    title_base = f'{player_name.upper()}  •  {team_name.upper()}'
    subtitle = f'{season_text}'
    stats_line = f'{total_goals} Goals | {total_shots} Shots | {total_xg:.2f} xG'

    # Check if we have player info to display
    has_player_info = any([player_info.get('age'), player_info.get('nationality'),
                           player_info.get('height'), player_info.get('weight')])

    def add_info_strip_to_figure(fig, title, chart_subtitle):
        """Add player info strip to an individual chart figure."""
        if has_player_info:
            # Title
            fig.text(0.5, 0.97, title, ha='center', fontsize=20, fontweight='bold', color='white')

            # Create axes for drawing the strip
            ax_header = fig.add_axes([0, 0, 1, 1])
            ax_header.set_facecolor('none')
            ax_header.axis('off')

            strip_y = 0.91
            strip_height = 0.035

            # Full team color strip
            strip_rect = mpatches.FancyBboxPatch(
                (0.05, strip_y), 0.90, strip_height,
                boxstyle="round,pad=0.003",
                facecolor=team_color, edgecolor='none',
                transform=ax_header.transAxes
            )
            ax_header.add_patch(strip_rect)

            # Info items
            info_y = strip_y + strip_height / 2
            positions = [0.18, 0.38, 0.62, 0.82]
            labels = ['AGE', 'NATIONALITY', 'HEIGHT', 'WEIGHT']
            values = [str(player_info.get('age', '')) or '-',
                      str(player_info.get('nationality', '')) or '-',
                      format_height_imperial(player_info.get('height')),
                      format_weight_imperial(player_info.get('weight'))]

            for pos, label, value in zip(positions, labels, values):
                ax_header.text(pos, info_y + 0.005, label, fontsize=8, color='#FFFFFF',
                        transform=ax_header.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.8)
                ax_header.text(pos, info_y - 0.005, value, fontsize=11, color='white',
                        transform=ax_header.transAxes, ha='center', va='top', fontweight='bold')

            # Subtitle and stats
            fig.text(0.5, 0.87, chart_subtitle, ha='center', fontsize=11, color='#8BA3B8', style='italic')
            fig.text(0.5, 0.84, stats_line, ha='center', fontsize=10, color='white', fontweight='bold')
            return [0, 0.08, 1, 0.82]  # tight_layout rect with strip
        else:
            # Original layout without strip
            fig.text(0.5, 0.96, title, ha='center', fontsize=20, fontweight='bold', color='white')
            fig.text(0.5, 0.92, chart_subtitle, ha='center', fontsize=11, color='#8BA3B8', style='italic')
            fig.text(0.5, 0.885, stats_line, ha='center', fontsize=10, color='white', fontweight='bold')
            return [0, 0.08, 1, 0.88]  # tight_layout rect without strip

    # ============ Chart 1: Rolling xG/90 vs Goals/90 with shading ============
    fig1, ax1 = plt.subplots(figsize=(12, 7))
    fig1.patch.set_facecolor(BG_COLOR)
    ax1.set_facecolor(BG_COLOR)

    # Shading for over/underperformance (no legend labels - visual is self-explanatory)
    ax1.fill_between(match_nums, goals_rolling, xg_rolling,
                     where=[g > x for g, x in zip(goals_rolling, xg_rolling)],
                     interpolate=True,
                     color=color_goals, alpha=0.3)
    ax1.fill_between(match_nums, goals_rolling, xg_rolling,
                     where=[g <= x for g, x in zip(goals_rolling, xg_rolling)],
                     interpolate=True,
                     color=color_xg, alpha=0.3)

    ax1.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label='xG/90')
    ax1.plot(match_nums, goals_rolling, color=color_goals, linewidth=3, label='Goals/90')

    ax1.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax1.set_ylabel('PER 90 MINUTES', fontsize=14, fontweight='bold', color='white')
    ax1.legend(loc='upper center', fontsize=10, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=2)
    style_axis(ax1)
    draw_season_boundaries(ax1, season_boundaries)

    layout_rect = add_info_strip_to_figure(fig1, title_base, f'{subtitle} | GOALS/90 vs xG/90 ({window}-GAME ROLLING)')
    fig1.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig1.text(0.98, 0.01, 'DATA: TRUMEDIA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=layout_rect)
    path1 = os.path.join(output_folder, "player_goals_vs_xg_rolling.png")
    plt.savefig(path1, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path1}")
    plt.close()

    # ============ Chart 2: xG Per 90 Trend ============
    fig2, ax2 = plt.subplots(figsize=(12, 7))
    fig2.patch.set_facecolor(BG_COLOR)
    ax2.set_facecolor(BG_COLOR)

    ax2.scatter(match_nums, xg_per90, color=color_xg, alpha=0.4, s=60, zorder=2)
    ax2.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label=f'{window}-Game Rolling', zorder=3)
    # Season average (minutes-weighted)
    total_minutes = sum(minutes)
    season_avg_xg = (sum(xg_values) / total_minutes * 90) if total_minutes > 0 else 0
    ax2.axhline(y=season_avg_xg, color='white', linestyle='--', linewidth=1.5, alpha=0.7,
                label=f'Season Avg: {season_avg_xg:.2f}')

    ax2.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax2.set_ylabel('xG PER 90', fontsize=14, fontweight='bold', color='white')
    ax2.legend(loc='upper center', fontsize=10, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=2)
    style_axis(ax2)
    draw_season_boundaries(ax2, season_boundaries)

    layout_rect = add_info_strip_to_figure(fig2, title_base, f'{subtitle} | xG PER 90 TREND')
    fig2.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig2.text(0.98, 0.01, 'DATA: TRUMEDIA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=layout_rect)
    path2 = os.path.join(output_folder, "player_xg_per90_trend.png")
    plt.savefig(path2, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path2}")
    plt.close()

    # ============ Chart 3: Shot Volume & Quality ============
    fig3, ax3 = plt.subplots(figsize=(12, 7))
    fig3.patch.set_facecolor(BG_COLOR)
    ax3.set_facecolor(BG_COLOR)

    # Bars for per-match shots
    ax3.bar(match_nums, shots_values, color=color_xg, alpha=0.7,
            edgecolor='white', linewidth=0.5, label='Shots (per match)', zorder=2)

    ax3.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax3.set_ylabel('SHOTS', fontsize=14, fontweight='bold', color=color_xg)
    ax3.tick_params(axis='y', labelcolor=color_xg)

    # Style primary axis
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_color(color_goals)
    ax3.spines['left'].set_color(SPINE_COLOR)
    ax3.spines['bottom'].set_color(SPINE_COLOR)
    ax3.tick_params(colors=SPINE_COLOR)
    ax3.tick_params(axis='y', colors=color_xg)
    ax3.set_axisbelow(True)
    ax3.grid(axis='y', color=SPINE_COLOR, linestyle='-', linewidth=0.5, alpha=0.3)

    # Secondary axis for rolling xG per shot
    ax3b = ax3.twinx()
    ax3b.plot(match_nums, xg_per_shot_rolling, color=color_goals, linewidth=3,
              marker='o', markersize=5, label=f'xG/Shot ({window}-game rolling)', zorder=3)
    ax3b.set_ylabel('xG PER SHOT', fontsize=14, fontweight='bold', color=color_goals)
    ax3b.tick_params(axis='y', labelcolor=color_goals)
    ax3b.spines['right'].set_color(color_goals)

    # Combined legend below x-axis
    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3b.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper center', fontsize=11,
               facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=2)
    draw_season_boundaries(ax3, season_boundaries)

    layout_rect = add_info_strip_to_figure(fig3, title_base, f'{subtitle} | SHOT VOLUME & QUALITY')
    fig3.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig3.text(0.98, 0.01, 'DATA: TRUMEDIA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=layout_rect)
    path3 = os.path.join(output_folder, "player_shot_volume_quality.png")
    plt.savefig(path3, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path3}")
    plt.close()

    # ============ Chart 4: Last 10 Matches vs Season Average ============
    fig4, ax4 = plt.subplots(figsize=(12, 7))
    fig4.patch.set_facecolor(BG_COLOR)
    ax4.set_facecolor(BG_COLOR)

    # Use last 10 matches to align with rolling window
    max_bars = 10
    if len(matches) > max_bars:
        display_matches = matches[-max_bars:]
    else:
        display_matches = matches

    display_shots = [m['shots'] for m in display_matches]
    display_xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in display_matches]
    display_labels = [f"{m['opponent']} ({m['minutes']}')" for m in display_matches]
    x_pos = np.arange(len(display_matches))

    # Calculate season averages (from all matches)
    avg_shots = sum(shots_values) / len(shots_values) if shots_values else 1
    all_xg_per_shot = [m['xg'] / m['shots'] if m['shots'] > 0 else 0 for m in matches]
    avg_xg_per_shot = sum(all_xg_per_shot) / len(all_xg_per_shot) if all_xg_per_shot else 1

    # Normalize to season average (1.0 = average)
    shots_norm = [v / avg_shots if avg_shots > 0 else 0 for v in display_shots]
    xg_per_shot_norm = [v / avg_xg_per_shot if avg_xg_per_shot > 0 else 0 for v in display_xg_per_shot]

    bar_width = 0.35
    bars1 = ax4.bar(x_pos - bar_width/2, shots_norm, bar_width, label='Shots', color=color_xg, edgecolor='white', linewidth=0.5)
    bars2 = ax4.bar(x_pos + bar_width/2, xg_per_shot_norm, bar_width, label='xG/Shot', color=color_goals, edgecolor='white', linewidth=0.5)

    # Add value labels on top of each bar
    def add_bar_labels(bars, values, ax):
        for bar, val in zip(bars, values):
            height = bar.get_height()
            label = f'{val:.2f}' if isinstance(val, float) and val < 10 else f'{int(val)}'
            ax.annotate(label, xy=(bar.get_x() + bar.get_width() / 2, height),
                       xytext=(0, 2), textcoords='offset points',
                       ha='center', va='bottom', fontsize=7, color='white', fontweight='bold')

    add_bar_labels(bars1, display_shots, ax4)
    add_bar_labels(bars2, display_xg_per_shot, ax4)

    # Season average line at 1.0
    ax4.axhline(y=1.0, color='white', linestyle='--', linewidth=1.5, alpha=0.7)

    ax4.set_xlabel('OPPONENT (MINUTES)', fontsize=14, fontweight='bold', color='white')
    ax4.set_xticks(x_pos)
    ax4.set_xticklabels(display_labels, fontsize=9, color='white')

    # Hide y-axis
    ax4.set_ylabel('')
    ax4.set_yticklabels([])
    ax4.tick_params(axis='y', length=0)

    # Set y limits with padding for labels
    max_val = max(max(shots_norm), max(xg_per_shot_norm)) if display_matches else 1
    ax4.set_ylim(0, max_val * 1.15)

    # Style
    ax4.spines['top'].set_visible(False)
    ax4.spines['right'].set_visible(False)
    ax4.spines['left'].set_color(SPINE_COLOR)
    ax4.spines['bottom'].set_color(SPINE_COLOR)
    ax4.tick_params(axis='x', colors='white')

    # Legend below x-axis
    ax4.legend(loc='upper center', fontsize=10, facecolor=BG_COLOR, edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.1), ncol=2)

    layout_rect = add_info_strip_to_figure(fig4, title_base, f'{subtitle} | LAST {len(display_matches)} MATCHES (vs SEASON AVG)')
    fig4.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig4.text(0.98, 0.01, 'DATA: TRUMEDIA', fontsize=8, color=TEXT_SUBTLE, ha='right')

    plt.tight_layout(rect=layout_rect)
    path4 = os.path.join(output_folder, "player_last10_vs_avg.png")
    plt.savefig(path4, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path4}")
    plt.close()


def run(config):
    """Entry point for launcher - config contains all needed params.

    Config keys:
        file_path: str - Path to TruMedia Player Summary CSV file
        output_folder: str - Where to save charts
        window: int - Rolling window size (default 10)
        gui_mode: bool - If True, skip all interactive prompts (default True)
    """
    file_path = config['file_path']
    output_folder = config['output_folder']
    window = config.get('window', 10)
    gui_mode = config.get('gui_mode', True)

    print("\nParsing player data...")
    matches, player_name, team_name, team_color, season, player_info = parse_player_summary_csv(file_path, gui_mode=gui_mode)

    # Debug: Print player info
    print(f"  Player info from CSV: {player_info}")

    if len(matches) < 5:
        print(f"\n[!] Warning: Only {len(matches)} matches found.")
        print("    Rolling average may be less meaningful with fewer matches.")

    # Create safe filename from player name
    safe_name = player_name.replace(' ', '_').replace('.', '')
    output_path = os.path.join(output_folder, f"{safe_name}_rolling_analysis.png")

    print("\nGenerating combined chart...")
    create_rolling_charts(matches, player_name, team_name, team_color, season, output_path, window, player_info)

    print("\nGenerating individual charts...")
    create_individual_charts(matches, player_name, team_name, team_color, season, output_folder, window, player_info)

    print("\nDone!")


def main():
    """Standalone entry point - prompts user for inputs."""
    print("\n" + "="*60)
    print("PLAYER ROLLING AVERAGE CHART BUILDER")
    print("="*60)
    print("Analyzes individual player performance over a season.")
    print("Tracks shots, goals, and xG on a per-90-minutes basis.")
    print("Requires TruMedia Player Summary CSV.")

    csv_path = get_file_path("TruMedia Player Summary CSV file")
    if not csv_path:
        return

    # Get rolling window
    window_input = input("\nRolling window size (default=10): ").strip()
    window = int(window_input) if window_input.isdigit() else 10

    output_folder = get_output_folder()

    config = {
        'file_path': csv_path,
        'output_folder': output_folder,
        'window': window
    }
    run(config)


if __name__ == "__main__":
    main()
