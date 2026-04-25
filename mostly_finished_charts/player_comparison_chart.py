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

from shared.styles import BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer, BROADCAST_FIGSIZE
from shared.file_utils import get_file_path, get_output_folder
from shared.colors import (
    TEAM_COLORS, fuzzy_match_team, check_colors_need_fix,
    color_distance, get_team_abbrev,
    normalize_team_name
)


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
_HEX_COLOR_RE = __import__('re').compile(r'^#[0-9A-Fa-f]{6}$')


def get_validated_team_color(team_name, csv_color=None):
    """Get team color, preferring shared color library over CSV.

    Falls back to CSV color if team not found in library,
    then to default blue if no CSV color or color is invalid.
    """
    # Check shared color library first (using fuzzy matching)
    color, matched_name, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if color:
        return color

    # Fall back to CSV color only if it's a valid hex color
    if csv_color and isinstance(csv_color, str) and _HEX_COLOR_RE.match(csv_color):
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
# =============================================================================
# LEAGUE CATEGORY MAPPING
# =============================================================================
def get_league_category(league_name):
    """Map a TruMedia league name to a display category.

    Returns one of: "Big 5 European Leagues", "Americas Big 4", "Women's Soccer",
    or the raw league name if no category matches.
    """
    if not league_name or (isinstance(league_name, float) and pd.isna(league_name)):
        return None

    league_lower = str(league_name).lower()

    # Women's leagues (check first - most specific)
    if any(w in league_lower for w in ['nwsl', 'wsl', 'women']):
        return "Women's Soccer"

    # Big 5 European Leagues
    if any(w in league_lower for w in ['premier league', 'la liga', 'bundesliga', 'ligue 1',
                                        'champions league', 'europa league', 'conference league']):
        return "Big 5 European Leagues"
    if 'serie a' in league_lower and not any(w in league_lower for w in ['brazil', 'brasileir']):
        return "Big 5 European Leagues"

    # Americas Big 4
    if any(w in league_lower for w in ['mls', 'major league soccer', 'liga mx',
                                        'brasileir', 'brazil serie', 'primera division',
                                        'argentine', 'argentina']):
        return "Americas Big 4"

    # Fallback to raw league name
    return league_name


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
        ('Final 3rd Passes', 'PsIntoA3rd', False, True),
    ],
    'PROGRESSION': [
        ('Prog. Passes', 'ProgPass', False, True),
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

    # Normalize team names (strip "Women" where safe)
    for col in ['newestTeam', 'teamName']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: normalize_team_name(x) if pd.notna(x) else x)

    # Map positions to our categories
    df['PositionCategory'] = df['Position'].map(POSITION_MAPPING)

    # Filter out players without a mapped position (e.g., goalkeepers)
    df = df[df['PositionCategory'].notna()].copy()

    # Convert all stat columns to numeric (TruMedia CSVs use "-" for missing values)
    stat_cols = [
        'Min', 'Shot', 'NPxG', 'GoalExPn', 'xA', 'Goal', 'ExpG',
        'Chance', 'Ast', 'ProgPass', 'ProgCarry', 'TakeOn', 'PsAtt',
        'PsIntoA3rd', 'ShtBlk', 'Int', 'TcklAtt', 'Duels', 'Aerials',
        'GM', 'Age', 'Weight', 'Height',
    ]
    for col in stat_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Calculate derived metrics
    # xG/Shot (non-penalty xG per shot)
    df['xG/Shot'] = df.apply(
        lambda row: row['NPxG'] / row['Shot'] if row['Shot'] > 0 else 0,
        axis=1
    )

    # npxG+xA (non-penalty xG + xA)
    df['npxG+xA'] = df['NPxG'] + df['xA']

    # Convert percentage strings to floats if needed
    # Check for non-numeric dtype (covers both legacy 'object' and newer pandas StringDtype)
    for col in ['Pass%', 'TakeOn%', 'Tackle%', 'Duel%', 'Aerial%']:
        if col in df.columns and not pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].replace(['-', ''], np.nan)
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
    peer_values = [v for v in peer_values if v is not None and not (isinstance(v, float) and np.isnan(v))]
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
    name_col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
    player_mask = df[name_col] == player_name

    # Try the other name column (exact match)
    if not player_mask.any():
        alt_col = 'Player' if name_col == 'playerFullName' else 'playerFullName'
        if alt_col in df.columns:
            player_mask = df[alt_col] == player_name

    # Try partial match on full name (accent-insensitive)
    if not player_mask.any() and 'playerFullName' in df.columns:
        player_mask = accent_insensitive_contains(df['playerFullName'], player_name)

    # Try partial match on abbreviated name (accent-insensitive)
    if not player_mask.any() and 'Player' in df.columns:
        player_mask = accent_insensitive_contains(df['Player'], player_name)

    if not player_mask.any():
        return None, None, None, None

    # If multiple rows match (mid-season transfer or combined pool),
    # take the row with the most recent game date so team info reflects current club
    matched = df[player_mask]
    if len(matched) > 1 and 'lastGameDate' in matched.columns:
        matched = matched.sort_values('lastGameDate', ascending=False)
    player_row = matched.iloc[0]
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
from matplotlib.colors import LinearSegmentedColormap

# Saturated traffic-light colormap tuned for the CBS dark navy background.
# Preserves the red=bad / yellow=mid / green=good semantic that sports
# viewers expect, but with anchors crisp enough to stay legible as thin
# slivers at the low end (matplotlib's default RdYlGn interpolates through
# muddy maroon that disappears on dark navy).
_PERCENTILE_CMAP = LinearSegmentedColormap.from_list(
    'cbs_traffic',
    [
        (0.00, '#E63946'),   # bright red
        (0.50, '#F4D03F'),   # warm yellow
        (1.00, '#2ECC71'),   # bright green
    ],
)


def get_color_from_percentile(pct):
    """Get color from the CBS traffic-light colormap."""
    return _PERCENTILE_CMAP(pct / 100)


def create_category_chart(category_name, metrics, player_row, peer_count, output_path, comparison_position=None):
    """Create an individual category chart with percentile bars."""

    # Player info
    player_name = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
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
                                           facecolor='#3A4A5C', edgecolor='none',
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
                color='white', transform=ax.transAxes, va='center', ha='left')

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
    league_category = get_league_category(player_row.get('newestLeague', ''))
    footer_right = f'Data: Opta/STATS Perform  •  {league_category}' if league_category else 'Data: Opta/STATS Perform'
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.01, footer_right,
             fontsize=8, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


def create_comparison_chart(results, player_row, peer_count, output_path, comparison_position=None,
                            custom_title=None, custom_subtitle=None):
    """Create the player comparison chart"""

    # Use full name if available, otherwise abbreviated
    player_name = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
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

    # Create figure (single-match / single-feature analytical chart → 16:9)
    fig = plt.figure(figsize=BROADCAST_FIGSIZE)
    fig.patch.set_facecolor(BG_COLOR)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')

    # Title - player name and team on main line
    fig.text(0.5, 0.95, custom_title or f'{player_name.upper()}  •  {team.upper()}',
             ha='center', fontsize=24, fontweight='bold', color='white')

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
        auto_subtitle = f'{position}  •  vs Position Peers  •  Last 365 Days  •  {nineties_played:.1f} 90s'
        fig.text(0.5, 0.86, custom_subtitle or auto_subtitle,
                 ha='center', fontsize=12, color='#8BA3B8')
    else:
        # No player info - use original layout
        auto_subtitle = f'{position}  •  vs Position Peers  •  Last 365 Days  •  {nineties_played:.1f} 90s'
        fig.text(0.5, 0.89, custom_subtitle or auto_subtitle,
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
                                               facecolor='#3A4A5C', edgecolor='none',
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
                    color='white', transform=ax.transAxes, va='center', ha='left')

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
    for cat in ['PROGRESSION', 'DEFENSIVE']:
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
    league_category = get_league_category(player_row.get('newestLeague', ''))
    footer_right = f'Data: Opta/STATS Perform  •  Percentile rank among {position}s  •  {league_category}' if league_category else f'Data: Opta/STATS Perform  •  Percentile rank among {position}s'
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=11, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.015, footer_right,
             fontsize=9, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


# =============================================================================
# MULTI-PLAYER COMPARISON FUNCTIONS
# =============================================================================
def resolve_player_colors(player_rows, threshold=60):
    """Resolve colors for multiple players, ensuring all are visually distinct.

    Args:
        player_rows: List of player row dicts/Series
        threshold: Minimum color distance required between any two players

    Returns:
        Tuple of (colors_list, has_conflicts) where has_conflicts is True if
        colors couldn't be fully resolved
    """
    from shared.colors import get_alternate_color

    teams = []
    colors = []

    for player_row in player_rows:
        team = player_row.get('newestTeam', player_row.get('teamName', ''))
        csv_color = player_row.get('newestTeamColor', None)
        color = get_validated_team_color(team, csv_color)
        teams.append(team)
        colors.append(color)

    # Multiple passes to resolve conflicts
    max_iterations = 3
    has_conflicts = False

    for iteration in range(max_iterations):
        conflicts_found = False

        for i in range(len(colors)):
            for j in range(i + 1, len(colors)):
                dist = color_distance(colors[i], colors[j])

                if dist < threshold:
                    conflicts_found = True

                    # Try to fix by using alternate color for one of them
                    # Prefer changing the player whose color conflicts with fewer others
                    alt_i = get_alternate_color(teams[i])
                    alt_j = get_alternate_color(teams[j])

                    best_fix = None
                    best_improvement = 0

                    # Check if alternate for player i helps
                    if alt_i:
                        new_dist = color_distance(alt_i, colors[j])
                        # Also check against other players
                        other_ok = all(
                            color_distance(alt_i, colors[k]) >= threshold
                            for k in range(len(colors)) if k != i and k != j
                        )
                        if new_dist >= threshold and other_ok:
                            improvement = new_dist - dist
                            if improvement > best_improvement:
                                best_improvement = improvement
                                best_fix = ('i', alt_i)

                    # Check if alternate for player j helps
                    if alt_j:
                        new_dist = color_distance(colors[i], alt_j)
                        other_ok = all(
                            color_distance(alt_j, colors[k]) >= threshold
                            for k in range(len(colors)) if k != i and k != j
                        )
                        if new_dist >= threshold and other_ok:
                            improvement = new_dist - dist
                            if improvement > best_improvement:
                                best_improvement = improvement
                                best_fix = ('j', alt_j)

                    # Apply best fix if found; otherwise use fallback palette
                    if best_fix:
                        if best_fix[0] == 'i':
                            colors[i] = best_fix[1]
                        else:
                            colors[j] = best_fix[1]
                    else:
                        # No alternate available — pick from fallback palette
                        _FALLBACKS = ['#0057A8', '#F5A623', '#00A070', '#9B59B6', '#1ABC9C', '#FFFFFF']
                        for fb in _FALLBACKS:
                            if all(color_distance(fb, colors[k]) >= threshold
                                   for k in range(len(colors)) if k != j):
                                colors[j] = fb
                                break

        if not conflicts_found:
            break

    # Final check for remaining conflicts
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            if color_distance(colors[i], colors[j]) < threshold:
                has_conflicts = True
                break

    return colors, has_conflicts


def get_multiple_player_percentiles(df, player_names, min_minutes=900, compare_position=None):
    """Calculate percentile rankings for multiple players.

    Args:
        df: Player dataframe
        player_names: List of player names (2-3 players)
        min_minutes: Minimum minutes for peer comparison
        compare_position: Optional position override for comparison

    Returns:
        Tuple of (results_by_player, player_rows, peer_count, comparison_position)
        results_by_player is dict: {player_name: {category: [metrics]}}
    """
    results_by_player = {}
    player_rows = []

    # Find position from first player if not specified
    first_results, first_row, first_peers, first_position = get_player_percentiles(
        df, player_names[0], min_minutes, compare_position
    )

    if first_results is None:
        return None, None, None, None

    results_by_player[player_names[0]] = first_results
    player_rows.append(first_row)

    # Use first player's position as the comparison position
    comparison_position = first_position

    # Get percentiles for remaining players
    for player_name in player_names[1:]:
        results, player_row, peer_count, _ = get_player_percentiles(
            df, player_name, min_minutes, comparison_position
        )

        if results is None:
            return None, None, None, None

        results_by_player[player_name] = results
        player_rows.append(player_row)

    # Use peer count from first player (same position comparison)
    peers = df[
        (df['PositionCategory'] == comparison_position) &
        (df['Min'] >= min_minutes)
    ]
    peer_count = len(peers)

    return results_by_player, player_rows, peer_count, comparison_position


def draw_player_header_cards(ax, player_rows, player_colors, y_position, card_height=0.055):
    """Draw player identification cards in the header.

    Cards automatically size to fill ~90% of width based on player count.

    Args:
        ax: Matplotlib axis
        player_rows: List of player row dicts
        player_colors: List of colors for each player
        y_position: Y position for cards (0-1 in axes coords)
        card_height: Height of each card
    """
    num_players = len(player_rows)

    # Cards fill ~90% of width, with small gaps between
    total_width = 0.88
    gap = 0.02
    card_width = (total_width - (num_players - 1) * gap) / num_players
    start_x = 0.5 - total_width / 2

    # Font sizes scale with card width
    name_fontsize = 14 if num_players == 2 else 11
    info_fontsize = 9 if num_players == 2 else 8
    max_name_len = 22 if num_players == 2 else 18

    for i, (player_row, color) in enumerate(zip(player_rows, player_colors)):
        x = start_x + i * (card_width + gap)

        # Color card
        swatch = mpatches.FancyBboxPatch(
            (x, y_position), card_width, card_height,
            boxstyle="round,pad=0.005",
            facecolor=color, edgecolor='#556B7F', linewidth=1,
            transform=ax.transAxes
        )
        ax.add_patch(swatch)

        # Player name (centered in card)
        player_name = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
        if len(player_name) > max_name_len:
            player_name = player_name[:max_name_len - 2] + '..'

        text_color = get_text_color_for_background(color)
        ax.text(x + card_width / 2, y_position + card_height * 0.65,
                player_name.upper(), fontsize=name_fontsize, fontweight='bold',
                color=text_color, transform=ax.transAxes, ha='center', va='center')

        # Team and 90s (smaller text below name)
        team = player_row.get('newestTeam', player_row.get('teamName', ''))
        max_team_len = 18 if num_players == 2 else 14
        if len(team) > max_team_len:
            team = team[:max_team_len - 2] + '..'
        minutes = player_row.get('Min', 0)
        nineties = minutes / 90 if minutes else 0

        ax.text(x + card_width / 2, y_position + card_height * 0.25,
                f"{team}  |  {nineties:.0f} 90s", fontsize=info_fontsize,
                color=text_color, transform=ax.transAxes, ha='center', va='center',
                alpha=0.85)


def draw_player_info_strips(ax, fig, player_rows, player_colors, start_y, strip_height=0.045):
    """Draw individual info strips for each player side-by-side in one row.

    Args:
        ax: Matplotlib axis
        fig: Figure object for text
        player_rows: List of player row dicts
        player_colors: List of colors for each player
        start_y: Y position for the strips
        strip_height: Height of each strip

    Returns:
        Y position below the strips
    """
    num_players = len(player_rows)
    total_width = 0.90
    gap = 0.015
    strip_width = (total_width - (num_players - 1) * gap) / num_players
    start_x = 0.05

    def is_valid_info(val):
        if val is None or val == '':
            return False
        if isinstance(val, float) and pd.isna(val):
            return False
        return True

    for i, (player_row, color) in enumerate(zip(player_rows, player_colors)):
        strip_x = start_x + i * (strip_width + gap)

        # Draw colored strip
        strip_rect = mpatches.FancyBboxPatch(
            (strip_x, start_y), strip_width, strip_height,
            boxstyle="round,pad=0.003",
            facecolor=color, edgecolor='none',
            transform=ax.transAxes
        )
        ax.add_patch(strip_rect)

        # Get player info
        player_height = player_row.get('Height', player_row.get('height', ''))
        player_weight = player_row.get('Weight', player_row.get('weight', ''))
        player_minutes = player_row.get('Min', player_row.get('minutes', 0))
        nineties = player_minutes / 90 if player_minutes else 0

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

        text_color = get_text_color_for_background(color)
        info_y = start_y + strip_height / 2

        # Layout: HEIGHT | WEIGHT | 90s - spread across this player's strip
        strip_center = strip_x + strip_width / 2
        if num_players == 2:
            # More space - spread out
            positions = [strip_x + strip_width * 0.2, strip_x + strip_width * 0.5, strip_x + strip_width * 0.8]
            labels = ['HEIGHT', 'WEIGHT', '90s']
            label_size = 7
            value_size = 10
        else:
            # 3 players - more compact
            positions = [strip_x + strip_width * 0.22, strip_x + strip_width * 0.5, strip_x + strip_width * 0.78]
            labels = ['HT', 'WT', '90s']
            label_size = 6
            value_size = 9

        values = [height_str, weight_str, f"{nineties:.1f}"]

        for pos, label, value in zip(positions, labels, values):
            ax.text(pos, info_y + 0.008, label, fontsize=label_size, color=text_color,
                    transform=ax.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.7)
            ax.text(pos, info_y - 0.005, value, fontsize=value_size, color=text_color,
                    transform=ax.transAxes, ha='center', va='top', fontweight='bold')

    return start_y - 0.01  # Return position below strips


def draw_grouped_bars(ax, metrics_data, player_colors, player_names, label_x, bar_x, start_y,
                      bar_width=0.25, bar_height=0.018, row_spacing=0.07,
                      label_fontsize=9):
    """Draw grouped horizontal bars for multiple players per metric.

    Bar fill is the player's team color at full saturation. Player identity is
    carried by color; rank vs peers is carried by bar length + the white
    percentile number. No intensity modulation — that channel was redundant
    with bar length and broken for light-colored teams.
    """
    y_pos = start_y
    num_players = len(player_names)
    bar_gap = 0.003
    group_height = num_players * (bar_height + bar_gap)

    for metric in metrics_data:
        metric_name = metric['name']
        group_center_y = y_pos - group_height / 2

        ax.text(label_x, group_center_y, metric_name, fontsize=label_fontsize, color='white',
                transform=ax.transAxes, va='center', ha='left')

        for i, player_data in enumerate(metric['players']):
            bar_y = y_pos - (i * (bar_height + bar_gap)) - bar_height / 2
            percentile = player_data['percentile']
            value_str = player_data['value_str']

            bg_rect = mpatches.FancyBboxPatch(
                (bar_x, bar_y), bar_width, bar_height,
                boxstyle="round,pad=0.002",
                facecolor='#3A4A5C', edgecolor='none',
                transform=ax.transAxes
            )
            ax.add_patch(bg_rect)

            fill_color = player_colors[i]
            fill_width = bar_width * (percentile / 100)
            fill_rect = mpatches.FancyBboxPatch(
                (bar_x, bar_y), max(fill_width, 0.005), bar_height,
                boxstyle="round,pad=0.002",
                facecolor=fill_color, edgecolor='none',
                transform=ax.transAxes
            )
            ax.add_patch(fill_rect)

            val_x = bar_x + bar_width + 0.01
            ax.text(val_x, bar_y + bar_height / 2, value_str, fontsize=8, color='white',
                    transform=ax.transAxes, va='center', ha='left')

            pct_x = val_x + 0.04
            ax.text(pct_x, bar_y + bar_height / 2, f'{percentile:.0f}%', fontsize=8, fontweight='bold',
                    color='white', transform=ax.transAxes, va='center', ha='left')

        y_pos -= row_spacing

    return y_pos


def create_multi_player_comparison_chart(results_by_player, player_rows, peer_count,
                                          comparison_position, output_path,
                                          custom_title=None, custom_subtitle=None):
    """Create the multi-player comparison chart (combined view).

    Args:
        results_by_player: Dict mapping player names to their results
        player_rows: List of player row dicts
        peer_count: Number of peers in comparison
        comparison_position: Position being compared
        output_path: Path to save the chart
    """
    player_names = list(results_by_player.keys())
    num_players = len(player_names)

    # Resolve colors with conflict handling
    player_colors, has_color_conflicts = resolve_player_colors(player_rows)
    if has_color_conflicts:
        print("[!] Warning: Some player colors are similar and couldn't be fully resolved")

    # Create figure (wider for multi-player)
    fig = plt.figure(figsize=(16, 12))
    fig.patch.set_facecolor(BG_COLOR)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')

    # Draw player names centered above their color bars
    # Calculate strip positions first (same logic as draw_player_info_strips)
    total_width = 0.90
    gap = 0.015
    strip_width = (total_width - (num_players - 1) * gap) / num_players
    start_x = 0.05
    strips_y = 0.90
    names_y = 0.95

    # Optional custom title above player names
    if custom_title:
        fig.text(0.5, 0.985, custom_title, ha='center', fontsize=14,
                 fontweight='bold', color='white')

    # Font size based on number of players
    name_fontsize = 18 if num_players == 2 else 15

    for i, player_row in enumerate(player_rows):
        strip_x = start_x + i * (strip_width + gap)
        strip_center = strip_x + strip_width / 2

        pname = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
        team = player_row.get('newestTeam', player_row.get('teamName', ''))
        team_abbrev = get_team_abbrev(team)

        # Shorten name if needed
        max_len = 20 if num_players == 2 else 16
        if len(pname) > max_len:
            pname = pname[:max_len - 2] + '..'

        # Draw name centered above this player's strip
        # No separator between names — players are peer-equivalent on the same axis,
        # not head-to-head; "vs" framing was misleading.
        fig.text(strip_center, names_y, f"{pname} ({team_abbrev})".upper(),
                 ha='center', fontsize=name_fontsize, fontweight='bold', color='white')

    # Player info strips (height/weight/90s in team colors)
    strips_bottom = draw_player_info_strips(ax, fig, player_rows, player_colors, start_y=strips_y)

    # Subtitle
    subtitle_y = strips_bottom - 0.015
    auto_subtitle = f'{comparison_position} vs Position Peers  |  Last 365 Days'
    fig.text(0.5, subtitle_y, custom_subtitle or auto_subtitle,
             ha='center', fontsize=11, color='#8BA3B8')

    # Prepare metrics data for grouped bars
    def prepare_category_metrics(category):
        """Prepare metrics data for a category in grouped bar format."""
        metrics = []
        category_metrics = METRICS[category]

        for display_name, csv_column, is_pct, higher_is_better in category_metrics:
            player_data = []
            for pname in player_names:
                # Find the metric in this player's results
                for m in results_by_player[pname][category]:
                    if m['name'] == display_name:
                        player_data.append(m)
                        break

            metrics.append({
                'name': display_name,
                'players': player_data
            })

        return metrics

    # Layout - two columns
    # Left column: label at 0.03, bars at 0.17
    # Right column: label at 0.52, bars at 0.66
    left_label_x = 0.03
    left_bar_x = 0.17
    right_label_x = 0.52
    right_bar_x = 0.66
    bar_width = 0.23

    # Row spacing and bar height depend on number of players
    if num_players == 2:
        row_spacing = 0.055
        bar_height = 0.016
        category_gap = 0.025
    else:
        # 3 players - more compact to fit everything
        row_spacing = 0.048
        bar_height = 0.014
        category_gap = 0.018

    def draw_category_section(category, label_x, bar_x, y_pos):
        """Draw a category section with grouped bars."""
        # Category header
        ax.text(label_x, y_pos, category, fontsize=11, fontweight='bold', color='#6CABDD',
                transform=ax.transAxes)
        y_pos -= 0.022

        # Column headers (above bars)
        ax.text(bar_x + bar_width + 0.01, y_pos + 0.01, 'PER 90', fontsize=7,
                color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')
        ax.text(bar_x + bar_width + 0.05, y_pos + 0.01, 'PCTL', fontsize=7,
                color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')

        y_pos -= 0.012

        metrics = prepare_category_metrics(category)
        final_y = draw_grouped_bars(ax, metrics, player_colors, player_names,
                                     label_x, bar_x, y_pos, bar_width=bar_width,
                                     bar_height=bar_height, row_spacing=row_spacing)
        return final_y - category_gap  # Gap before next category

    # Draw left column (Scoring, Chance Creation, Passing)
    # Start below subtitle with some padding
    y_start = subtitle_y - 0.035
    y_left = y_start
    for cat in ['SCORING', 'CHANCE CREATION', 'PASSING']:
        y_left = draw_category_section(cat, left_label_x, left_bar_x, y_left)

    # Draw right column (Dribbling, Defensive)
    y_right = y_start
    for cat in ['PROGRESSION', 'DEFENSIVE']:
        y_right = draw_category_section(cat, right_label_x, right_bar_x, y_right)

    # ── Abbreviations box (mid-right, consistency with single-player) ────
    abbrev_x = 0.68
    abbrev_y = 0.13
    ax.text(abbrev_x, abbrev_y + 0.02, 'ABBREVIATIONS', fontsize=8,
            fontweight='bold', color='#556B7F', transform=ax.transAxes)
    for i, (ab, full) in enumerate([
        ('xG/Shot', 'Expected Goals per Shot'),
        ('xA',      'Expected Assists'),
        ('npxG+xA', 'Non-Penalty xG + xA'),
    ]):
        y = abbrev_y - (i * 0.018)
        ax.text(abbrev_x, y, ab, fontsize=7, color='white', fontweight='bold',
                transform=ax.transAxes, va='center')
        ax.text(abbrev_x + 0.06, y, f'= {full}', fontsize=7, color='#888888',
                transform=ax.transAxes, va='center')

    # ── Compact player legend (bottom-left) ──────────────────────────────
    # Reinforces "color = player" as a backup to the team-colored info strips
    legend_y = 0.04
    legend_start_x = 0.04
    for i, (pname, color) in enumerate(zip(player_names, player_colors)):
        item_x = legend_start_x + i * 0.13
        display_name = (player_rows[i]['playerFullName']
                        if 'playerFullName' in player_rows[i].index
                        else player_rows[i].get('Player', ''))
        if len(display_name) > 18:
            display_name = display_name[:16] + '..'
        swatch = mpatches.Rectangle((item_x, legend_y), 0.012, 0.012,
                                     facecolor=color, edgecolor='none',
                                     transform=ax.transAxes)
        ax.add_patch(swatch)
        ax.text(item_x + 0.015, legend_y + 0.006, display_name,
                fontsize=8, color='white', transform=ax.transAxes, va='center')

    # ── Footer ───────────────────────────────────────────────────────────
    info_x = 0.98
    league_category = get_league_category(player_rows[0].get('newestLeague', ''))
    footer_right = f'Data: Opta/STATS Perform  •  Percentile rank among {comparison_position}s  •  {league_category}' if league_category else f'Data: Opta/STATS Perform  •  Percentile rank among {comparison_position}s'
    fig.text(info_x, 0.038, footer_right,
             fontsize=8, color='#666666', ha='right')
    fig.text(info_x, 0.015, 'CBS SPORTS', fontsize=10, fontweight='bold',
             color=CBS_BLUE, ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_multi_player_category_chart(category, results_by_player, player_rows,
                                        peer_count, comparison_position, output_path):
    """Create an individual category chart for multi-player comparison.

    Args:
        category: Category name (e.g., 'SCORING')
        results_by_player: Dict mapping player names to their results
        player_rows: List of player row dicts
        peer_count: Number of peers in comparison
        comparison_position: Position being compared
        output_path: Path to save the chart
    """
    player_names = list(results_by_player.keys())
    num_players = len(player_names)
    player_colors, _ = resolve_player_colors(player_rows)

    # Get metrics for this category
    num_metrics = len(METRICS[category])

    # Calculate figure height based on metrics and players
    # More height = more space for content + legend
    fig_height = 4.5 + (num_metrics * 0.5 * num_players)

    # Create figure
    fig = plt.figure(figsize=(11, fig_height))
    fig.patch.set_facecolor(BG_COLOR)

    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')

    # Calculate layout proportions based on content
    # Reserve bottom 12% for legend and footer
    content_bottom = 0.14
    content_top = 0.94

    # Title
    fig.text(0.5, content_top, f'{category} COMPARISON', ha='center', fontsize=18,
             fontweight='bold', color='white')

    # Player header cards
    draw_player_header_cards(ax, player_rows, player_colors, y_position=content_top - 0.08)

    # Subtitle
    subtitle_y = content_top - 0.14
    fig.text(0.5, subtitle_y, f'{comparison_position} vs Position Peers  |  Last 365 Days',
             ha='center', fontsize=10, color='#8BA3B8')

    # Prepare metrics data
    metrics = []
    for display_name, csv_column, is_pct, higher_is_better in METRICS[category]:
        player_data = []
        for pname in player_names:
            for m in results_by_player[pname][category]:
                if m['name'] == display_name:
                    player_data.append(m)
                    break
        metrics.append({
            'name': display_name,
            'players': player_data
        })

    # Layout - bars start below subtitle
    label_x = 0.08
    bar_x = 0.28
    bar_width = 0.42
    bars_start_y = subtitle_y - 0.06

    # Column headers
    ax.text(bar_x + bar_width + 0.02, bars_start_y + 0.02, 'PER 90', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(bar_x + bar_width + 0.07, bars_start_y + 0.02, 'PCTL', fontsize=8,
            color='#556B7F', transform=ax.transAxes, ha='left', fontweight='bold')

    # Calculate row spacing to fit content in available space
    available_height = bars_start_y - content_bottom - 0.02
    row_spacing = available_height / num_metrics

    # Draw grouped bars
    draw_grouped_bars(ax, metrics, player_colors, player_names,
                      label_x, bar_x, bars_start_y, bar_width=bar_width, bar_height=0.028,
                      row_spacing=row_spacing, label_fontsize=15)

    # Footer
    league_category = get_league_category(player_rows[0].get('newestLeague', ''))
    footer_right = f'Data: Opta/STATS Perform  •  {league_category}' if league_category else 'Data: Opta/STATS Perform'
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE)
    fig.text(0.98, 0.015, footer_right,
             fontsize=8, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


# =============================================================================
# RUN FUNCTION (for launcher integration)
# =============================================================================
def run(config):
    """Run player comparison chart from launcher config.

    Supports both single-player mode (player_name) and multi-player mode (player_names list).
    """
    csv_path = config.get('file_path')
    output_folder = config.get('output_folder')
    min_minutes = config.get('min_minutes', 900)
    compare_position = config.get('compare_position', None)

    # Check for multi-player mode
    player_names = config.get('player_names', None)
    player_name = config.get('player_name', None)

    print("\nLoading player data...")
    df = load_player_data(csv_path)
    print(f"  Loaded {len(df)} players")

    # Multi-player mode
    if player_names and len(player_names) >= 2:
        print(f"\nAnalyzing {len(player_names)} players: {', '.join(player_names)}")

        results_by_player, player_rows, peer_count, comparison_position = get_multiple_player_percentiles(
            df, player_names, min_minutes, compare_position
        )

        if results_by_player is None:
            print(f"  One or more players not found.")
            return None

        print(f"  Position: {comparison_position}")
        print(f"  Comparing against {peer_count} peers")

        # Generate safe filename from all player names
        safe_names = '_vs_'.join([
            p.replace(' ', '_').replace('.', '').replace("'", '')[:15]
            for p in player_names
        ])

        saved_files = []

        # Main combined chart
        main_output_path = os.path.join(output_folder, f"player_comparison_multi_{safe_names}.png")
        print("\nGenerating multi-player comparison chart...")
        create_multi_player_comparison_chart(
            results_by_player, player_rows, peer_count,
            comparison_position, main_output_path
        )
        saved_files.append(main_output_path)

        # Individual category charts
        print("\nGenerating individual category charts...")
        categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'PROGRESSION', 'DEFENSIVE']
        for category in categories:
            cat_slug = category.lower().replace(' ', '_')
            cat_output_path = os.path.join(output_folder, f"player_comparison_multi_{safe_names}_{cat_slug}.png")
            create_multi_player_category_chart(
                category, results_by_player, player_rows,
                peer_count, comparison_position, cat_output_path
            )
            saved_files.append(cat_output_path)

        print(f"\n[OK] Generated {len(saved_files)} multi-player charts")
        return saved_files

    # Single-player mode (original behavior)
    if not player_name:
        print("Error: No player_name or player_names provided in config")
        return None

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
    full_name = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
    safe_name = full_name.replace(' ', '_').replace('.', '').replace("'", '')

    saved_files = []

    # Main combined chart
    main_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}.png")
    print("\nGenerating main chart...")
    create_comparison_chart(results, player_row, peer_count, main_output_path, comparison_position)
    saved_files.append(main_output_path)

    # Individual category charts
    print("\nGenerating individual category charts...")
    categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'PROGRESSION', 'DEFENSIVE']
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
    print("Data source: Opta/STATS Perform CSV export (last 365 days)")

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
    full_name = player_row['playerFullName'] if 'playerFullName' in player_row.index else player_row.get('Player', '')
    safe_name = full_name.replace(' ', '_').replace('.', '').replace("'", '')

    # Main combined chart
    main_output_path = os.path.join(output_folder, f"player_comparison_{safe_name}.png")
    print("\nGenerating main chart...")
    create_comparison_chart(results, player_row, peer_count, main_output_path, comparison_position)

    # Individual category charts
    print("\nGenerating individual category charts...")
    categories = ['SCORING', 'CHANCE CREATION', 'PASSING', 'PROGRESSION', 'DEFENSIVE']
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
