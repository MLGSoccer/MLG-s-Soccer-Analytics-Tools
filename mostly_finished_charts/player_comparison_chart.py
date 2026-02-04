"""
Player Comparison Chart Builder
Compares a selected player against peers at the same position using percentile rankings.
Uses CBS Sports styling.
"""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os
import sys
import unicodedata

# Add parent directory for shared imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.styles import BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer
from shared.file_utils import get_file_path, get_output_folder
from shared.colors import TEAM_COLORS, fuzzy_match_team


# =============================================================================
# TEXT UTILITIES
# =============================================================================
def strip_accents(text):
    """Remove accents/diacritics from text for flexible matching.

    Converts 'Gyökeres' to 'Gyokeres' so searches work without special characters.
    """
    if not isinstance(text, str):
        return text
    # Normalize to decomposed form (separates base char from diacritics)
    normalized = unicodedata.normalize('NFD', text)
    # Filter out combining characters (the diacritics)
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')


def accent_insensitive_contains(series, search_term):
    """Check if series contains search_term, ignoring accents."""
    search_normalized = strip_accents(search_term).lower()
    return series.apply(lambda x: search_normalized in strip_accents(str(x)).lower() if pd.notna(x) else False)


# =============================================================================
# COLOR UTILITIES
# =============================================================================
def get_validated_team_color(team_name, csv_color=None):
    """Get team color, preferring shared color library over CSV.

    Falls back to CSV color if team not found in library,
    then to default blue if no CSV color.
    """
    # Check shared color library first (using fuzzy matching)
    color, matched_name, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if color:
        return color

    # Fall back to CSV color if available
    if csv_color:
        return csv_color

    # Default fallback
    return '#6CABDD'


def get_text_color_for_background(bg_color):
    """Return white or dark text color based on background luminance.

    Uses relative luminance formula to determine contrast.
    """
    # Convert hex to RGB
    hex_color = bg_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    # Calculate relative luminance (sRGB)
    # Using simplified formula: 0.299*R + 0.587*G + 0.114*B
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

    # Return white for dark backgrounds, dark for light backgrounds
    return '#FFFFFF' if luminance < 0.5 else '#1A1A1A'


# =============================================================================
# POSITION MAPPING
# =============================================================================
POSITION_MAPPING = {
    # Center Back
    'Left Centre Back': 'Center Back',
    'Right Centre Back': 'Center Back',
    'Central Defender': 'Center Back',

    # Fullback/Wingback
    'Left Back': 'Fullback/Wingback',
    'Right Back': 'Fullback/Wingback',
    'Left Wing Back': 'Fullback/Wingback',
    'Right Wing Back': 'Fullback/Wingback',

    # Defensive Midfielder
    'Defensive Midfielder': 'Defensive Midfielder',

    # Central Midfielder
    'Central Midfielder': 'Central Midfielder',

    # Attacking Midfielder/Winger
    'Centre Attacking Midfielder': 'Attacking Mid/Winger',
    'Left Attacking Midfielder': 'Attacking Mid/Winger',
    'Right Attacking Midfielder': 'Attacking Mid/Winger',
    'Left Midfielder': 'Attacking Mid/Winger',
    'Right Midfielder': 'Attacking Mid/Winger',
    'Left Winger': 'Attacking Mid/Winger',
    'Right Winger': 'Attacking Mid/Winger',

    # Striker
    'Centre Forward': 'Striker',
    'Striker': 'Striker',
    'Second Striker': 'Striker',
}

POSITION_CATEGORIES = [
    'Center Back',
    'Fullback/Wingback',
    'Defensive Midfielder',
    'Central Midfielder',
    'Attacking Mid/Winger',
    'Striker',
]


# =============================================================================
# METRIC DEFINITIONS
# =============================================================================
# Maps our metric names to CSV column names
# Format: (display_name, csv_column, is_percentage, higher_is_better)
METRICS = {
    'SCORING': [
        ('Goals (non-pen)', 'GoalExPn', False, True),
        ('xG (non-pen)', 'NPxG', False, True),
        ('Shots', 'Shot', False, True),
        ('xG/Shot', None, False, True),  # Calculated
    ],
    'CHANCE CREATION': [
        ('Assists', 'Ast', False, True),
        ('xA', 'xA', False, True),
        ('Key Passes', 'Chance', False, True),
        ('npxG+xA', None, False, True),  # Calculated
    ],
    'PASSING': [
        ('Passes', 'PsAtt', False, True),
        ('Pass %', 'Pass%', True, True),
        ('Prog. Passes', 'ProgPass', False, True),
        ('Final 3rd Passes', 'PsIntoA3rd', False, True),
    ],
    'DRIBBLING': [
        ('Prog. Carries', 'ProgCarry', False, True),
        ('Take-Ons', 'TakeOn', False, True),
        ('Take-On %', 'TakeOn%', True, True),
    ],
    'DEFENSIVE': [
        ('Tackles', 'TcklAtt', False, True),
        ('Interceptions', 'Int', False, True),
        ('Aerial Duels', 'Aerials', False, True),
        ('Blocks', 'ShtBlk', False, True),
    ],
}


# =============================================================================
# DATA LOADING AND PROCESSING
# =============================================================================
def load_player_data(csv_path):
    """Load and process player data from CSV"""
    df = pd.read_csv(csv_path, encoding='utf-8')

    # Map positions to our categories
    df['PositionCategory'] = df['Position'].map(POSITION_MAPPING)

    # Filter out players without a mapped position (e.g., goalkeepers)
    df = df[df['PositionCategory'].notna()].copy()

    # Calculate derived metrics
    # xG/Shot
    df['xG/Shot'] = df.apply(
        lambda row: row['ExpG'] / row['Shot'] if row['Shot'] > 0 else 0,
        axis=1
    )

    # npxG+xA (non-penalty xG + xA)
    df['npxG+xA'] = df['NPxG'] + df['xA']

    # Convert percentage strings to floats if needed
    for col in ['Pass%', 'TakeOn%', 'Tackle%', 'Duel%', 'Aerial%']:
        if col in df.columns:
            if df[col].dtype == object:
                # Replace '-' and empty strings with NaN, then convert
                df[col] = df[col].replace(['-', ''], np.nan)
                # Only strip % if the values contain it
                df[col] = pd.to_numeric(df[col].astype(str).str.rstrip('%'), errors='coerce')

    return df


def get_player_value(player_row, metric_name, csv_column):
    """Get a player's value for a specific metric"""
    if csv_column is None:
        # Calculated metric
        if metric_name == 'xG/Shot':
            return player_row['xG/Shot']
        elif metric_name == 'npxG+xA':
            return player_row['npxG+xA']
    else:
        return player_row[csv_column]
    return 0


def calculate_percentile(value, peer_values):
    """Calculate percentile rank of a value within peer values"""
    if len(peer_values) == 0:
        return 50

    # Count how many peers have lower values
    below = sum(1 for v in peer_values if v < value)
    equal = sum(1 for v in peer_values if v == value)

    # Percentile formula: (below + 0.5 * equal) / total * 100
    percentile = (below + 0.5 * equal) / len(peer_values) * 100
    return percentile


def get_player_percentiles(df, player_name, min_minutes=900, compare_position=None):
    """Calculate percentile rankings for a player vs position peers

    Args:
        df: Player dataframe
        player_name: Name of player to analyze
        min_minutes: Minimum minutes for peer comparison
        compare_position: Optional position override for comparison (from POSITION_CATEGORIES)
    """
    # Find the player - check multiple name columns
    player_mask = df['Player'] == player_name

    # Try full name column (exact match)
    if not player_mask.any() and 'playerFullName' in df.columns:
        player_mask = df['playerFullName'] == player_name

    # Try partial match on abbreviated name (accent-insensitive)
    if not player_mask.any():
        player_mask = accent_insensitive_contains(df['Player'], player_name)

    # Try partial match on full name (accent-insensitive)
    if not player_mask.any() and 'playerFullName' in df.columns:
        player_mask = accent_insensitive_contains(df['playerFullName'], player_name)

    if not player_mask.any():
        return None, None, None, None

    player_row = df[player_mask].iloc[0]
    player_position = player_row['PositionCategory']

    # Use override position if provided, otherwise use player's natural position
    comparison_position = compare_position if compare_position else player_position

    # Get peers (comparison position, minimum minutes)
    peers = df[
        (df['PositionCategory'] == comparison_position) &
        (df['Min'] >= min_minutes)
    ]

    # Calculate percentiles for each metric
    results = {}
    for category, metrics in METRICS.items():
        results[category] = []
        for display_name, csv_column, is_pct, higher_is_better in metrics:
            # Get player value
            player_value = get_player_value(player_row, display_name, csv_column)

            # Get peer values
            if csv_column is None:
                if display_name == 'xG/Shot':
                    peer_values = peers['xG/Shot'].tolist()
                elif display_name == 'npxG+xA':
                    peer_values = peers['npxG+xA'].tolist()
            else:
                peer_values = peers[csv_column].tolist()

            # Calculate percentile
            percentile = calculate_percentile(player_value, peer_values)

            # Format value for display
            if is_pct:
                value_str = f"{player_value:.1f}%"
            elif player_value < 1 and player_value > 0:
                value_str = f"{player_value:.2f}"
            else:
                value_str = f"{player_value:.1f}"

            results[category].append({
                'name': display_name,
                'value': player_value,
                'value_str': value_str,
                'percentile': percentile,
            })

    return results, player_row, len(peers), comparison_position


# =============================================================================
# VISUALIZATION
# =============================================================================
def get_color_from_percentile(pct):
    """Get color from RdYlGn colormap"""
    return plt.cm.RdYlGn(pct / 100)


def create_category_chart(category_name, metrics, player_row, peer_count, output_path, comparison_position=None):
    """Create an individual category chart with percentile bars."""

    # Player info
    player_name = player_row.get('playerFullName', player_row['Player'])
    natural_position = player_row['PositionCategory']
    position = comparison_position if comparison_position else natural_position
    team = player_row.get('newestTeam', player_row.get('teamName', ''))
    csv_color = player_row.get('newestTeamColor', None)
    team_color = get_validated_team_color(team, csv_color)

    # Player details
    player_age = player_row.get('Age', player_row.get('age', ''))
    player_nationality = player_row.get('Nationality', player_row.get('nationality', player_row.get('Nation', player_row.get('nation', ''))))
    player_height = player_row.get('Height', player_row.get('height', ''))
    player_weight = player_row.get('Weight', player_row.get('weight', ''))
    player_minutes = player_row.get('Min', player_row.get('minutes', 0))
    nineties_played = player_minutes / 90 if player_minutes else 0

    # Helper to check if value is valid (not empty, not NaN)
    def is_valid_info(val):
        if val is None or val == '':
            return False
        if isinstance(val, float) and pd.isna(val):
            return False
        return True

    has_player_info = any([is_valid_info(v) for v in [player_age, player_nationality, player_height, player_weight]])

    # Calculate figure height based on number of metrics
    num_metrics = len(metrics)
    fig_height = 3.5 + (num_metrics * 0.6)
    if has_player_info:
        fig_height += 0.4

    # Create figure
    fig = plt.figure(figsize=(10, fig_height))
    fig.patch.set_facecolor(BG_COLOR)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')

    # Title - player name and team
    fig.text(0.5, 0.94, f'{player_name.upper()}  •  {team.upper()}', ha='center', fontsize=18,
             fontweight='bold', color='white')

    # Player info strip
    if has_player_info:
        strip_y = 0.87
        strip_height = 0.045

        strip_rect = mpatches.FancyBboxPatch(
            (0.05, strip_y), 0.90, strip_height,
            boxstyle="round,pad=0.003",
            facecolor=team_color, edgecolor='none',
            transform=ax.transAxes
        )
        ax.add_patch(strip_rect)

        info_y = strip_y + strip_height / 2
        positions = [0.18, 0.38, 0.62, 0.82]
        labels = ['AGE', 'NATIONALITY', 'HEIGHT', 'WEIGHT']
        # Convert height to feet/inches
        if is_valid_info(player_height):
            total_inches = float(player_height) / 2.54
            feet = int(total_inches // 12)
            inches = int(round(total_inches % 12))
            if inches == 12:
                feet += 1
                inches = 0
            height_str = f"{feet}'{inches}\""
        else:
            height_str = '-'

        # Convert weight to lbs
        if is_valid_info(player_weight):
            lbs = float(player_weight) * 2.20462
            weight_str = f"{int(round(lbs))} lbs"
        else:
            weight_str = '-'

        values = [str(int(player_age)) if is_valid_info(player_age) else '-',
                  str(player_nationality) if is_valid_info(player_nationality) else '-',
                  height_str,
                  weight_str]

        # Determine text color based on team color luminance
        text_color = get_text_color_for_background(team_color)

        for pos, label, value in zip(positions, labels, values):
            ax.text(pos, info_y + 0.006, label, fontsize=7, color=text_color,
                    transform=ax.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.8)
            ax.text(pos, info_y - 0.006, value, fontsize=10, color=text_color,
                    transform=ax.transAxes, ha='center', va='top', fontweight='bold')

        subtitle_y = 0.82
    else:
        subtitle_y = 0.87

    # Subtitle with position
    fig.text(0.5, subtitle_y, f'{position}  •  vs Position Peers  •  Last 365 Days  •  {nineties_played:.1f} 90s',
             ha='center', fontsize=10, color='#8BA3B8')

    # Category header
    header_y = subtitle_y - 0.08
    ax.text(0.08, header_y, category_name, fontsize=14, fontweight='bold', color='#6CABDD',
            transform=ax.transAxes)

    # Draw metrics
    row_height = 0.12
    bar_width = 0.45
    y_pos = header_y - 0.08

    # Column headers
    bar_start_x = 0.25
    val_x = bar_start_x + bar_width + 0.02
    pct_x = val_x + 0.08
    ax.text(val_x, y_pos + 0.05, 'PER 90', fontsize=9, color='#556B7F',
            transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(pct_x, y_pos + 0.05, 'PCTL', fontsize=9, color='#556B7F',
            transform=ax.transAxes, ha='left', fontweight='bold')

    for metric in metrics:
        metric_name = metric['name']
        value_str = metric['value_str']
        percentile = metric['percentile']

        # Metric name
        ax.text(0.08, y_pos, metric_name, fontsize=11, color='white',
                transform=ax.transAxes, va='center')

        # Bar background
        bar_start_x = 0.25
        bg_rect = mpatches.FancyBboxPatch((bar_start_x, y_pos - 0.025), bar_width, 0.05,
                                           boxstyle="round,pad=0.005",
                                           facecolor='#2a3a4a', edgecolor='none',
                                           transform=ax.transAxes)
        ax.add_patch(bg_rect)

        # Bar fill
        color = get_color_from_percentile(percentile)
        fill_width = bar_width * (percentile / 100)
        fill_rect = mpatches.FancyBboxPatch((bar_start_x, y_pos - 0.025), fill_width, 0.05,
                                             boxstyle="round,pad=0.005",
                                             facecolor=color, edgecolor='none',
                                             transform=ax.transAxes)
        ax.add_patch(fill_rect)

        # Value text
        val_x = bar_start_x + bar_width + 0.02
        ax.text(val_x, y_pos, value_str, fontsize=11, color='white',
                transform=ax.transAxes, va='center', ha='left')

        # Percentile text
        pct_x = val_x + 0.08
        ax.text(pct_x, y_pos, f'{percentile:.0f}%', fontsize=11, fontweight='bold',
                color=color, transform=ax.transAxes, va='center', ha='left')

        y_pos -= row_height

    # Percentile scale at bottom
    gradient_width = 0.5
    legend_x = 0.25
    legend_y = 0.08

    ax.text(legend_x + gradient_width/2, legend_y + 0.04, 'PERCENTILE SCALE', ha='center',
            fontsize=9, fontweight='bold', color='#556B7F', transform=ax.transAxes)

    # Draw gradient bar
    for i in range(100):
        color = get_color_from_percentile(i)
        rect = mpatches.Rectangle((legend_x + i * gradient_width/100, legend_y),
                                    gradient_width/100 + 0.001, 0.025,
                                    facecolor=color, edgecolor='none',
                                    transform=ax.transAxes)
        ax.add_patch(rect)

    ax.text(legend_x, legend_y - 0.02, '0%', fontsize=8, color='#888888',
            ha='left', transform=ax.transAxes)
    ax.text(legend_x + gradient_width/2, legend_y - 0.02, '50%', fontsize=8, color='#888888',
            ha='center', transform=ax.transAxes)
    ax.text(legend_x + gradient_width, legend_y - 0.02, '100%', fontsize=8, color='#888888',
            ha='right', transform=ax.transAxes)

    # Footer
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.01, f'Data: TruMedia  •  n={peer_count} {position}s',
             fontsize=8, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


def create_comparison_chart(results, player_row, peer_count, output_path, comparison_position=None):
    """Create the player comparison chart"""

    # Use full name if available, otherwise abbreviated
    player_name = player_row.get('playerFullName', player_row['Player'])
    natural_position = player_row['PositionCategory']
    position = comparison_position if comparison_position else natural_position
    team = player_row.get('newestTeam', player_row.get('teamName', ''))
    csv_color = player_row.get('newestTeamColor', None)
    team_color = get_validated_team_color(team, csv_color)

    # Player info (with fallbacks if not in CSV)
    player_age = player_row.get('Age', player_row.get('age', ''))
    player_nationality = player_row.get('Nationality', player_row.get('nationality', player_row.get('Nation', player_row.get('nation', ''))))
    player_height = player_row.get('Height', player_row.get('height', ''))
    player_weight = player_row.get('Weight', player_row.get('weight', ''))
    player_minutes = player_row.get('Min', player_row.get('minutes', 0))
    nineties_played = player_minutes / 90 if player_minutes else 0

    # Create figure
    fig = plt.figure(figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')

    # Title - player name and team on main line
    fig.text(0.5, 0.95, f'{player_name.upper()}  •  {team.upper()}', ha='center', fontsize=24,
             fontweight='bold', color='white')

    # ============ PLAYER INFO STRIP WITH TEAM COLOR ============
    strip_y = 0.895
    strip_height = 0.035

    # Helper to check if value is valid (not empty, not NaN)
    def is_valid_info(val):
        if val is None or val == '':
            return False
        if isinstance(val, float) and pd.isna(val):
            return False
        return True

    # Check if we have player info to display
    has_player_info = any([is_valid_info(v) for v in [player_age, player_nationality, player_height, player_weight]])

    if has_player_info:
        # Full team color strip
        strip_rect = mpatches.FancyBboxPatch(
            (0.05, strip_y), 0.90, strip_height,
            boxstyle="round,pad=0.003",
            facecolor=team_color, edgecolor='none',
            transform=ax.transAxes
        )
        ax.add_patch(strip_rect)

        # Info items centered in strip
        info_y = strip_y + strip_height / 2
        positions = [0.18, 0.38, 0.62, 0.82]
        labels = ['AGE', 'NATIONALITY', 'HEIGHT', 'WEIGHT']

        # Convert height to feet/inches
        if is_valid_info(player_height):
            total_inches = float(player_height) / 2.54
            feet = int(total_inches // 12)
            inches = int(round(total_inches % 12))
            if inches == 12:
                feet += 1
                inches = 0
            height_str = f"{feet}'{inches}\""
        else:
            height_str = '-'

        # Convert weight to lbs
        if is_valid_info(player_weight):
            lbs = float(player_weight) * 2.20462
            weight_str = f"{int(round(lbs))} lbs"
        else:
            weight_str = '-'

        values = [str(int(player_age)) if is_valid_info(player_age) else '-',
                  str(player_nationality) if is_valid_info(player_nationality) else '-',
                  height_str,
                  weight_str]

        # Determine text color based on team color luminance
        text_color = get_text_color_for_background(team_color)

        for pos, label, value in zip(positions, labels, values):
            ax.text(pos, info_y + 0.005, label, fontsize=8, color=text_color,
                    transform=ax.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.8)
            ax.text(pos, info_y - 0.005, value, fontsize=11, color=text_color,
                    transform=ax.transAxes, ha='center', va='top', fontweight='bold')

        # Subtitle below strip
        fig.text(0.5, 0.86, f'{position}  •  vs Position Peers  •  Last 365 Days  •  {nineties_played:.1f} 90s',
                 ha='center', fontsize=12, color='#8BA3B8')
    else:
        # No player info - use original layout
        fig.text(0.5, 0.89, f'{position}  •  vs Position Peers  •  Last 365 Days  •  {nineties_played:.1f} 90s',
                 ha='center', fontsize=12, color='#8BA3B8')

    # Layout parameters
    row_height = 0.05
    category_gap = 0.03
    bar_width = 0.22

    # Column positions
    left_col_x = 0.05
    right_col_x = 0.54

    def draw_category(cat_name, metrics, start_x, start_y):
        """Draw a category section"""
        y_pos = start_y

        # Category header
        ax.text(start_x, y_pos, cat_name, fontsize=11, fontweight='bold', color='#6CABDD',
                transform=ax.transAxes)
        y_pos -= row_height * 0.8

        for metric in metrics:
            metric_name = metric['name']
            value_str = metric['value_str']
            percentile = metric['percentile']

            # Metric name
            ax.text(start_x, y_pos, metric_name, fontsize=10, color='white',
                    transform=ax.transAxes, va='center')

            # Bar background
            bar_start_x = start_x + 0.15
            bg_rect = mpatches.FancyBboxPatch((bar_start_x, y_pos - 0.014), bar_width, 0.028,
                                               boxstyle="round,pad=0.005",
                                               facecolor='#2a3a4a', edgecolor='none',
                                               transform=ax.transAxes)
            ax.add_patch(bg_rect)

            # Bar fill
            color = get_color_from_percentile(percentile)
            fill_width = bar_width * (percentile / 100)
            fill_rect = mpatches.FancyBboxPatch((bar_start_x, y_pos - 0.014), fill_width, 0.028,
                                                 boxstyle="round,pad=0.005",
                                                 facecolor=color, edgecolor='none',
                                                 transform=ax.transAxes)
            ax.add_patch(fill_rect)

            # Value text
            val_x = bar_start_x + bar_width + 0.015
            ax.text(val_x, y_pos, value_str, fontsize=10, color='white',
                    transform=ax.transAxes, va='center', ha='left')

            # Percentile text
            pct_x = val_x + 0.05
            ax.text(pct_x, y_pos, f'{percentile:.0f}%', fontsize=10, fontweight='bold',
                    color=color, transform=ax.transAxes, va='center', ha='left')

            y_pos -= row_height

        return y_pos - category_gap

    # Draw left column (Scoring, Chance Creation, Passing)
    # Adjust starting position based on whether info strip is shown
    y_start = 0.78 if has_player_info else 0.82

    # Column headers
    header_y = y_start + 0.025
    # Left column headers
    ax.text(left_col_x + 0.15 + bar_width + 0.015, header_y, 'PER 90', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(left_col_x + 0.15 + bar_width + 0.065, header_y, 'PCTL', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')
    # Right column headers
    ax.text(right_col_x + 0.15 + bar_width + 0.015, header_y, 'PER 90', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(right_col_x + 0.15 + bar_width + 0.065, header_y, 'PCTL', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')

    y_left = y_start
    for cat in ['SCORING', 'CHANCE CREATION', 'PASSING']:
        y_left = draw_category(cat, results[cat], left_col_x, y_left)

    # Draw right column (Dribbling, Defensive)
    y_right = y_start
    for cat in ['DRIBBLING', 'DEFENSIVE']:
        y_right = draw_category(cat, results[cat], right_col_x, y_right)

    # Abbreviation legend (lower right, above percentile scale)
    gradient_width = 0.32
    legend_x = 0.75 - (gradient_width / 2)

    abbrev_y = 0.20
    ax.text(legend_x, abbrev_y + 0.02, 'ABBREVIATIONS', fontsize=8, fontweight='bold',
            color='#556B7F', transform=ax.transAxes)

    abbreviations = [
        ('xG/Shot', 'Expected Goals per Shot'),
        ('xA', 'Expected Assists'),
        ('npxG+xA', 'Non-Penalty xG + xA'),
    ]

    line_height = 0.016
    for i, (abbrev, full) in enumerate(abbreviations):
        y = abbrev_y - (i * line_height)
        ax.text(legend_x, y, f'{abbrev}', fontsize=7, color='white', fontweight='bold',
                transform=ax.transAxes, va='center')
        ax.text(legend_x + 0.065, y, f'= {full}', fontsize=7, color='#888888',
                transform=ax.transAxes, va='center')

    # Percentile scale (below abbreviations)
    legend_y = 0.06

    ax.text(legend_x + gradient_width/2, legend_y + 0.03, 'PERCENTILE SCALE', ha='center',
            fontsize=9, fontweight='bold', color='#556B7F', transform=ax.transAxes)

    # Draw gradient bar
    for i in range(100):
        color = get_color_from_percentile(i)
        rect = mpatches.Rectangle((legend_x + i * gradient_width/100, legend_y),
                                    gradient_width/100 + 0.001, 0.02,
                                    facecolor=color, edgecolor='none',
                                    transform=ax.transAxes)
        ax.add_patch(rect)

    ax.text(legend_x, legend_y - 0.015, '0%', fontsize=8, color='#888888',
            ha='left', transform=ax.transAxes)
    ax.text(legend_x + gradient_width/2, legend_y - 0.015, '50%', fontsize=8, color='#888888',
            ha='center', transform=ax.transAxes)
    ax.text(legend_x + gradient_width, legend_y - 0.015, '100%', fontsize=8, color='#888888',
            ha='right', transform=ax.transAxes)

    # Footer
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=11, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.015, f'Data: TruMedia  •  Percentile rank among {position}s (n={peer_count}, min. 900 min)',
             fontsize=9, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


# =============================================================================
# RUN FUNCTION (for launcher integration)
# =============================================================================
def run(config):
    """Run player comparison chart from launcher config."""
    csv_path = config.get('file_path')
    output_folder = config.get('output_folder')
    player_name = config.get('player_name')
    min_minutes = config.get('min_minutes', 900)
    compare_position = config.get('compare_position', None)

    print("\nLoading player data...")
    df = load_player_data(csv_path)
    print(f"  Loaded {len(df)} players")

    # Calculate percentiles
    print(f"\nAnalyzing {player_name}...")
    results, player_row, peer_count, comparison_position = get_player_percentiles(
        df, player_name, min_minutes, compare_position
    )

    if results is None:
        print(f"  Player '{player_name}' not found.")
        # Show similar names (accent-insensitive search)
        matches = []
        if 'playerFullName' in df.columns:
            full_matches = df[accent_insensitive_contains(df['playerFullName'], player_name)]
            matches = full_matches[['Player', 'playerFullName', 'teamName']].values.tolist()
        if not matches:
            abbrev_matches = df[accent_insensitive_contains(df['Player'], player_name)]
            matches = abbrev_matches[['Player', 'playerFullName', 'teamName']].values.tolist()
        if matches:
            print("  Did you mean:")
            for abbrev, full, team in matches[:10]:
                print(f"    - {full} ({abbrev}) - {team}")
        return None

    print(f"  Position: {player_row['PositionCategory']}")
    if compare_position and compare_position != player_row['PositionCategory']:
        print(f"  Comparing as: {comparison_position}")
    print(f"  Team: {player_row.get('newestTeam', player_row.get('teamName', 'Unknown'))}")
    print(f"  Comparing against {peer_count} peers")

    # Generate charts
    full_name = player_row.get('playerFullName', player_row['Player'])
    safe_name = full_name.replace(' ', '_').replace('.', '').replace("'", '')

    saved_files = []

    # Main combined chart
    main_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}.png")
    print("\nGenerating main chart...")
    create_comparison_chart(results, player_row, peer_count, main_output_path, comparison_position)
    saved_files.append(main_output_path)

    # Individual category charts
    print("\nGenerating individual category charts...")
    categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'DRIBBLING', 'DEFENSIVE']
    for category in categories:
        cat_slug = category.lower().replace(' ', '_')
        cat_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}_{cat_slug}.png")
        create_category_chart(category, results[category], player_row, peer_count, cat_output_path, comparison_position)
        saved_files.append(cat_output_path)

    print(f"\n[OK] Generated {len(saved_files)} charts")
    return saved_files


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("\n" + "="*60)
    print("PLAYER COMPARISON CHART BUILDER")
    print("="*60)
    print("Compares a player against position peers using percentile rankings.")
    print("Data source: TruMedia CSV export (last 365 days)")

    # Get CSV file
    csv_path = get_file_path("Player stats CSV file")
    if not csv_path:
        return

    print("\nLoading player data...")
    df = load_player_data(csv_path)
    print(f"  Loaded {len(df)} players")

    # Show available positions
    print("\n" + "-"*40)
    print("POSITION CATEGORIES:")
    for i, pos in enumerate(POSITION_CATEGORIES, 1):
        count = len(df[df['PositionCategory'] == pos])
        print(f"  {i}. {pos} ({count} players)")

    # Get player name
    print("\n" + "-"*40)
    player_name = input("Enter player name to analyze: ").strip()

    if not player_name:
        print("No player name entered.")
        return

    # First lookup to find the player and their natural position
    results, player_row, peer_count, _ = get_player_percentiles(df, player_name)

    if results is None:
        print(f"  Player '{player_name}' not found.")
        # Show similar names - search both columns (accent-insensitive)
        matches = []
        if 'playerFullName' in df.columns:
            full_matches = df[accent_insensitive_contains(df['playerFullName'], player_name)]
            matches = full_matches[['Player', 'playerFullName', 'teamName']].values.tolist()
        if not matches:
            abbrev_matches = df[accent_insensitive_contains(df['Player'], player_name)]
            matches = abbrev_matches[['Player', 'playerFullName', 'teamName']].values.tolist()
        if matches:
            print("  Did you mean:")
            for abbrev, full, team in matches[:10]:
                print(f"    - {full} ({abbrev}) - {team}")
        return

    natural_position = player_row['PositionCategory']
    print(f"\n  Player's position: {natural_position}")
    print(f"  Team: {player_row.get('newestTeam', player_row.get('teamName', 'Unknown'))}")

    # Option to compare against a different position
    print("\n" + "-"*40)
    print("COMPARE AGAINST POSITION:")
    print("  0. Use player's natural position (default)")
    for i, pos in enumerate(POSITION_CATEGORIES, 1):
        count = len(df[df['PositionCategory'] == pos])
        marker = " <--" if pos == natural_position else ""
        print(f"  {i}. {pos} ({count} players){marker}")

    pos_choice = input("\nSelect position (0-6, or Enter for default): ").strip()

    compare_position = None
    if pos_choice and pos_choice != '0':
        try:
            pos_idx = int(pos_choice) - 1
            if 0 <= pos_idx < len(POSITION_CATEGORIES):
                compare_position = POSITION_CATEGORIES[pos_idx]
        except ValueError:
            pass

    # Calculate percentiles with chosen comparison position
    print(f"\nAnalyzing {player_name}...")
    results, player_row, peer_count, comparison_position = get_player_percentiles(
        df, player_name, compare_position=compare_position
    )

    print(f"  Comparing as: {comparison_position}")
    print(f"  Comparing against {peer_count} peers")

    # Get output folder
    output_folder = get_output_folder()

    # Generate charts - use full name for filename
    full_name = player_row.get('playerFullName', player_row['Player'])
    safe_name = full_name.replace(' ', '_').replace('.', '').replace("'", '')

    # Main combined chart
    main_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}.png")
    print("\nGenerating main chart...")
    create_comparison_chart(results, player_row, peer_count, main_output_path, comparison_position)

    # Individual category charts
    print("\nGenerating individual category charts...")
    categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'DRIBBLING', 'DEFENSIVE']
    for category in categories:
        cat_slug = category.lower().replace(' ', '_')
        cat_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}_{cat_slug}.png")
        create_category_chart(category, results[category], player_row, peer_count, cat_output_path, comparison_position)

    print("\n" + "="*60)
    print(f"COMPLETE - Generated 6 charts")
    print("="*60)

    # Open the main chart
    try:
        os.startfile(main_output_path)
    except Exception as e:
        print(f"Could not open chart: {e}")


if __name__ == "__main__":
    main()
