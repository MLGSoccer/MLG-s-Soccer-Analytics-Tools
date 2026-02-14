"""
Player Bar Chart Builder
Creates horizontal bar charts comparing multiple players on a single stat.
Supports individual player selection, team roster, or league leaderboard modes.
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.styles import BG_COLOR, SPINE_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer
from shared.file_utils import get_file_path, get_output_folder
from shared.colors import (
    TEAM_COLORS, TEAM_ABBREV, fuzzy_match_team, get_team_color,
    ensure_contrast_with_background, expand_team_name
)
from shared.stat_mappings import (
    STAT_DISPLAY_NAMES, ALREADY_PER_90, LOWER_IS_BETTER,
    get_stat_display_name, is_per_90_stat, is_lower_better
)

# Import position mapping from player_comparison_chart
from mostly_finished_charts.player_comparison_chart import POSITION_MAPPING, POSITION_CATEGORIES


# =============================================================================
# DATA LOADING AND PROCESSING
# =============================================================================
def load_player_data(csv_path):
    """Load and process player data from CSV.

    Args:
        csv_path: Path to CSV file

    Returns:
        DataFrame with player data and position categories added
    """
    df = pd.read_csv(csv_path, encoding='utf-8')

    # Map positions to categories
    if 'Position' in df.columns:
        df['PositionCategory'] = df['Position'].map(POSITION_MAPPING)
    else:
        df['PositionCategory'] = None

    # Ensure Minutes column exists
    if 'Min' not in df.columns and 'minutes' in df.columns:
        df['Min'] = df['minutes']

    return df


def get_available_stats(df):
    """Get list of numeric columns that can be used as stats.

    Args:
        df: Player DataFrame

    Returns:
        List of column names that are numeric
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    # Filter out ID columns and other non-stat columns
    exclude = {'playerId', 'teamId', 'seasonId', 'competitionId', 'index', 'Unnamed: 0'}
    return [col for col in numeric_cols if col not in exclude]


def calculate_per_90(value, minutes):
    """Calculate per-90 value from raw total.

    Args:
        value: Raw stat value (total)
        minutes: Minutes played

    Returns:
        Per-90 normalized value
    """
    if pd.isna(value) or pd.isna(minutes) or minutes == 0:
        return 0
    return (value / minutes) * 90


def calculate_raw_total(per_90_value, minutes):
    """Calculate raw total from per-90 value.

    Args:
        per_90_value: Per-90 stat value
        minutes: Minutes played

    Returns:
        Raw total value
    """
    if pd.isna(per_90_value) or pd.isna(minutes) or minutes == 0:
        return 0
    return (per_90_value * minutes) / 90


def filter_players(df, min_minutes=0, position=None, leagues=None):
    """Apply filters to player DataFrame.

    Args:
        df: Player DataFrame
        min_minutes: Minimum minutes filter (default 0)
        position: Position category filter (optional)
        leagues: List of league names to include (optional)

    Returns:
        Filtered DataFrame
    """
    filtered = df.copy()

    # Minutes filter
    if min_minutes > 0 and 'Min' in filtered.columns:
        filtered = filtered[filtered['Min'] >= min_minutes]

    # Position filter
    if position and 'PositionCategory' in filtered.columns:
        filtered = filtered[filtered['PositionCategory'] == position]

    # League filter
    if leagues:
        league_col = None
        for col in ['newestLeague', 'leagueName', 'League', 'competition', 'competitionName']:
            if col in filtered.columns:
                league_col = col
                break

        if league_col:
            filtered = filtered[filtered[league_col].isin(leagues)]
        else:
            print(f"  [!] Warning: No league column found, cannot filter by league")

    return filtered


def normalize_name(name):
    """Normalize a name by removing accents and converting to lowercase.

    Args:
        name: Name string to normalize

    Returns:
        Normalized string without accents
    """
    if pd.isna(name):
        return ''
    # Normalize unicode characters (NFD decomposes accents)
    # Then encode to ASCII, ignoring non-ASCII chars
    normalized = unicodedata.normalize('NFD', str(name))
    ascii_name = normalized.encode('ascii', 'ignore').decode('ascii')
    return ascii_name.lower().strip()


def fuzzy_find_player(df, player_name):
    """Find a player by name using fuzzy matching.

    Args:
        df: Player DataFrame
        player_name: Name to search for

    Returns:
        Matching row or None
    """
    name_lower = player_name.lower().strip()
    name_normalized = normalize_name(player_name)

    # Try exact match on Player column
    if 'Player' in df.columns:
        exact = df[df['Player'].str.lower() == name_lower]
        if len(exact) > 0:
            return exact.iloc[0]

    # Try exact match on full name
    if 'playerFullName' in df.columns:
        exact = df[df['playerFullName'].str.lower() == name_lower]
        if len(exact) > 0:
            return exact.iloc[0]

    # Try normalized match (handles accents)
    if 'playerFullName' in df.columns:
        df_temp = df.copy()
        df_temp['_normalized'] = df_temp['playerFullName'].apply(normalize_name)
        exact_norm = df_temp[df_temp['_normalized'] == name_normalized]
        if len(exact_norm) > 0:
            return exact_norm.iloc[0]

    # Try partial match
    if 'playerFullName' in df.columns:
        partial = df[df['playerFullName'].str.lower().str.contains(name_lower, na=False, regex=False)]
        if len(partial) > 0:
            return partial.iloc[0]

    # Try partial match on normalized names
    if 'playerFullName' in df.columns:
        df_temp = df.copy()
        df_temp['_normalized'] = df_temp['playerFullName'].apply(normalize_name)
        partial_norm = df_temp[df_temp['_normalized'].str.contains(name_normalized, na=False, regex=False)]
        if len(partial_norm) > 0:
            return partial_norm.iloc[0]

    if 'Player' in df.columns:
        partial = df[df['Player'].str.lower().str.contains(name_lower, na=False, regex=False)]
        if len(partial) > 0:
            return partial.iloc[0]

    return None


def select_players_individual(df, player_names):
    """Get specific players by name.

    Args:
        df: Player DataFrame
        player_names: List of player names to find

    Returns:
        DataFrame with matched players
    """
    rows = []
    not_found = []

    for name in player_names:
        player = fuzzy_find_player(df, name)
        if player is not None:
            rows.append(player)
        else:
            not_found.append(name)

    if not_found:
        print(f"  [!] Players not found: {', '.join(not_found)}")

    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame()


def select_players_team(df, team_name):
    """Get all players from a team.

    Args:
        df: Player DataFrame
        team_name: Team name to filter by

    Returns:
        DataFrame with players from that team
    """
    team_lower = team_name.lower().strip()

    # Try different team columns
    for col in ['newestTeam', 'teamName', 'Team', 'team']:
        if col in df.columns:
            matches = df[df[col].str.lower().str.contains(team_lower, na=False)]
            if len(matches) > 0:
                return matches

    print(f"  [!] Team '{team_name}' not found")
    return pd.DataFrame()


def select_players_league(df, stat, limit=10, ascending=False):
    """Get top N players from the data for a given stat.

    Args:
        df: Player DataFrame (should be pre-filtered by league if needed)
        stat: Stat column to rank by
        limit: Number of players to return (default 10)
        ascending: If True, get lowest values instead of highest

    Returns:
        DataFrame with top N players
    """
    if stat not in df.columns:
        print(f"  [!] Stat '{stat}' not found in data")
        return pd.DataFrame()

    # Sort by stat
    sorted_df = df.sort_values(by=stat, ascending=ascending, na_position='last')

    return sorted_df.head(limit)


def get_player_team_color(player_row, csv_color_col='newestTeamColor'):
    """Get team color for a player.

    Args:
        player_row: Player data row
        csv_color_col: Column name for CSV color

    Returns:
        Hex color string
    """
    # Try CSV color first
    csv_color = player_row.get(csv_color_col, None)
    if csv_color and pd.notna(csv_color):
        color = csv_color
    else:
        # Get team name
        team = None
        for col in ['newestTeam', 'teamName', 'Team', 'team']:
            if col in player_row and pd.notna(player_row.get(col)):
                team = player_row[col]
                break

        if team:
            # Try fuzzy match in color database
            color, _, _ = fuzzy_match_team(team, TEAM_COLORS)
            if not color:
                color = '#888888'  # Default gray
        else:
            color = '#888888'

    # Ensure contrast with background (use higher threshold for better visibility)
    return ensure_contrast_with_background(color, BG_COLOR, min_distance=120)


def prepare_chart_data(df, stat, data_format='per90', display_as='per90'):
    """Prepare data for the horizontal bar chart.

    Args:
        df: Player DataFrame
        stat: Stat column to use
        data_format: Format of data in CSV - 'raw' or 'per90'
        display_as: How to display on chart - 'raw' or 'per90'

    Returns:
        List of tuples: (player_name, team_abbrev, value, color)
    """
    data = []

    for _, row in df.iterrows():
        # Get player name
        name = row.get('playerFullName', row.get('Player', 'Unknown'))

        # Get team abbreviation
        team = row.get('teamAbbrevName', row.get('newestTeam', row.get('teamName', '')))
        # If full name, try to abbreviate
        if len(team) > 4:
            # Check if we can find an abbreviation
            for abbrev, full in TEAM_ABBREV.items():
                if full.lower() == team.lower() or team.lower() in full.lower():
                    team = abbrev
                    break
            else:
                # Just use first 3 chars
                team = team[:3].upper()

        # Get stat value from CSV
        csv_value = row.get(stat, 0)
        if pd.isna(csv_value):
            csv_value = 0

        minutes = row.get('Min', row.get('minutes', 0))

        # Convert based on data_format and display_as
        # Skip conversion for percentage stats (they're always rates)
        if stat in ALREADY_PER_90:
            value = csv_value
        elif data_format == 'raw' and display_as == 'per90':
            # CSV has raw totals, want per-90
            value = calculate_per_90(csv_value, minutes)
        elif data_format == 'per90' and display_as == 'raw':
            # CSV has per-90, want raw totals
            value = calculate_raw_total(csv_value, minutes)
        else:
            # Same format, no conversion needed
            value = csv_value

        # Get color
        color = get_player_team_color(row)

        data.append((name, team, value, color))

    return data


def get_team_abbrev(team_name):
    """Get abbreviation for a team name.

    Args:
        team_name: Full team name

    Returns:
        3-4 letter abbreviation
    """
    if not team_name:
        return ''

    # Check abbreviation mapping
    for abbrev, full in TEAM_ABBREV.items():
        if full.lower() == team_name.lower():
            return abbrev

    # Partial match
    for abbrev, full in TEAM_ABBREV.items():
        if team_name.lower() in full.lower() or full.lower() in team_name.lower():
            return abbrev

    # Just use first 3 chars
    return team_name[:3].upper()


# =============================================================================
# CHART CREATION
# =============================================================================
def create_horizontal_bar_chart(data, stat_display, title, subtitle, output_path, sort_ascending=False):
    """Create horizontal bar chart with CBS Sports styling.

    Args:
        data: List of tuples (player_name, team_abbrev, value, color)
        stat_display: Display name for the stat
        title: Chart title
        subtitle: Chart subtitle
        output_path: Where to save the chart
        sort_ascending: If True, lowest values at top

    Returns:
        Path to saved chart
    """
    # Sort data - highest values first by default, then reverse for plotting
    # (matplotlib barh plots from bottom to top, so we reverse to get highest at top)
    sorted_data = sorted(data, key=lambda x: x[2], reverse=not sort_ascending)
    sorted_data = list(reversed(sorted_data))  # Reverse so highest is at top visually

    num_players = len(sorted_data)
    if num_players == 0:
        print("  [!] No data to chart")
        return None

    # Figure sizing
    fig_height = max(4, 1.5 + num_players * 0.5)
    fig = plt.figure(figsize=(12, fig_height))
    fig.patch.set_facecolor(BG_COLOR)

    # Create axis with proper margins
    ax = fig.add_axes([0.25, 0.12, 0.65, 0.75])
    ax.set_facecolor(BG_COLOR)

    # Y positions (bottom to top for proper ordering)
    y_positions = np.arange(num_players)

    # Extract data
    names = [f"{d[0]} ({d[1]})" for d in sorted_data]
    values = [d[2] for d in sorted_data]
    colors = [d[3] for d in sorted_data]

    # Create horizontal bars
    bars = ax.barh(y_positions, values, color=colors, height=0.7, edgecolor='none')

    # Add value labels at end of bars
    max_val = max(values) if values else 1
    for i, (bar, val) in enumerate(zip(bars, values)):
        # Format value
        if val < 0.1 and val > 0:
            val_str = f'{val:.3f}'
        elif val < 10:
            val_str = f'{val:.2f}'
        else:
            val_str = f'{val:.1f}'

        # Position label
        x_pos = bar.get_width() + max_val * 0.02
        ax.text(x_pos, bar.get_y() + bar.get_height()/2, val_str,
                va='center', ha='left', color='white', fontsize=10, fontweight='bold')

    # Style axis
    ax.set_yticks(y_positions)
    ax.set_yticklabels(names, fontsize=11, fontweight='bold', color='white')

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(SPINE_COLOR)
    ax.spines['bottom'].set_color(SPINE_COLOR)

    ax.tick_params(axis='x', colors=SPINE_COLOR, labelcolor=TEXT_PRIMARY)
    ax.tick_params(axis='y', colors=SPINE_COLOR, left=False, labelcolor='white', labelsize=11)

    ax.xaxis.grid(True, linestyle='--', alpha=0.3, color=SPINE_COLOR)
    ax.set_axisbelow(True)

    # Set x-axis limit to accommodate labels
    ax.set_xlim(0, max_val * 1.15)

    # X-axis label
    ax.set_xlabel(stat_display, fontsize=11, color=TEXT_SECONDARY, labelpad=10)

    # Title
    fig.text(0.5, 0.95, title, ha='center', fontsize=16, fontweight='bold', color='white')

    # Subtitle
    fig.text(0.5, 0.90, subtitle, ha='center', fontsize=11, color=TEXT_SECONDARY)

    # Footer
    add_cbs_footer(fig)

    # Save
    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


# =============================================================================
# MAIN FUNCTIONS
# =============================================================================
def run(config):
    """Run player bar chart from config dict.

    Config keys:
        file_path: Path to CSV file
        output_folder: Where to save output
        mode: 'individual', 'team', or 'league'
        players: List of player names (for individual mode)
        team: Team name (for team mode)
        leagues: List of league names (for league mode, optional)
        stat: CSV column name for stat
        data_format: Format of CSV data - 'raw' or 'per90' (default 'per90')
        display_as: How to display values - 'raw' or 'per90' (default 'per90')
        per_90: DEPRECATED - use data_format/display_as instead
        min_minutes: Minimum minutes filter
        position: Position category filter
        sort_ascending: Whether to sort lowest first
        max_players: Maximum players to show
        title: Optional custom title
        gui_mode: If True, skip interactive prompts
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder')
    mode = config.get('mode', 'league')
    stat = config.get('stat')

    # Handle data format settings (with backwards compatibility for per_90)
    if 'data_format' in config:
        data_format = config.get('data_format', 'per90')
        display_as = config.get('display_as', 'per90')
    else:
        # Backwards compatibility: per_90=True means raw->per90, per_90=False means no change
        per_90 = config.get('per_90', True)
        data_format = 'raw' if per_90 else 'per90'
        display_as = 'per90' if per_90 else 'per90'

    min_minutes = config.get('min_minutes', 0)
    position = config.get('position')
    sort_ascending = config.get('sort_ascending', False)
    max_players = config.get('max_players', 10)
    custom_title = config.get('title')

    # Load data
    print("\nLoading player data...")
    df = load_player_data(file_path)
    print(f"  Loaded {len(df)} players")

    # Validate stat
    if stat not in df.columns:
        print(f"  [!] Stat '{stat}' not found in data")
        available = get_available_stats(df)
        print(f"  Available stats: {', '.join(available[:20])}...")
        return None

    # Apply base filters
    filtered = filter_players(df, min_minutes=min_minutes, position=position)
    print(f"  After filters: {len(filtered)} players")

    # Select players based on mode
    if mode == 'individual':
        players = config.get('players', [])
        if not players:
            print("  [!] No players specified for individual mode")
            return None
        selected = select_players_individual(filtered, players)

    elif mode == 'team':
        team = config.get('team')
        if not team:
            print("  [!] No team specified for team mode")
            return None
        selected = select_players_team(filtered, team)
        if max_players:
            # Sort by stat and limit
            selected = selected.sort_values(by=stat, ascending=sort_ascending, na_position='last')
            selected = selected.head(max_players)

    elif mode == 'league':
        leagues = config.get('leagues')
        if leagues:
            # Filter by leagues
            league_col = None
            for col in ['newestLeague', 'leagueName', 'League', 'competition', 'competitionName']:
                if col in filtered.columns:
                    league_col = col
                    break
            if league_col:
                filtered = filtered[filtered[league_col].isin(leagues)]
                print(f"  Filtered to {leagues}: {len(filtered)} players")
            else:
                print(f"  [!] Warning: No league column found, cannot filter by league")

        selected = select_players_league(filtered, stat, limit=max_players, ascending=sort_ascending)
    else:
        print(f"  [!] Unknown mode: {mode}")
        return None

    if len(selected) == 0:
        print("  [!] No players selected")
        return None

    print(f"  Selected {len(selected)} players")

    # Prepare chart data
    chart_data = prepare_chart_data(selected, stat, data_format, display_as)

    # Build title and subtitle
    stat_display = get_stat_display_name(stat)

    if custom_title:
        title = custom_title
    else:
        if mode == 'individual':
            title = f"{stat_display} Comparison"
        elif mode == 'team':
            team = config.get('team', '')
            title = f"{team.upper()} - {stat_display}"
        else:
            leagues = config.get('leagues')
            if leagues:
                league_str = ', '.join(leagues)
            else:
                league_str = 'All Leagues'
            title = f"{stat_display} - {league_str} Top {len(selected)}"

    # Build subtitle parts
    subtitle_parts = []
    if display_as == 'per90' and stat not in ALREADY_PER_90:
        subtitle_parts.append('Per 90')
    elif display_as == 'raw' and stat not in ALREADY_PER_90:
        subtitle_parts.append('Total')
    if min_minutes > 0:
        subtitle_parts.append(f'Min. {min_minutes} minutes')
    if position:
        subtitle_parts.append(position)

    subtitle = '  |  '.join(subtitle_parts) if subtitle_parts else ''

    # Generate output filename
    safe_stat = stat.replace('/', '_').replace('%', 'pct')
    if mode == 'team':
        team = config.get('team', 'team')
        safe_team = team.replace(' ', '_')[:20]
        filename = f"player_bar_{safe_team}_{safe_stat}.png"
    elif mode == 'individual':
        filename = f"player_bar_comparison_{safe_stat}.png"
    else:
        filename = f"player_bar_leaderboard_{safe_stat}.png"

    output_path = os.path.join(output_folder, filename)

    # Create chart
    print("\nGenerating chart...")
    result = create_horizontal_bar_chart(
        chart_data, stat_display, title, subtitle, output_path, sort_ascending
    )

    return result


def main():
    """Interactive CLI entry point."""
    print("\n" + "=" * 60)
    print("PLAYER BAR CHART BUILDER")
    print("=" * 60)
    print("Compare multiple players on a single stat.")

    # Get file
    file_path = get_file_path("TruMedia Player Stats CSV file")
    if not file_path:
        return

    # Load data
    print("\nLoading player data...")
    df = load_player_data(file_path)
    print(f"  Loaded {len(df)} players")

    # Show available stats
    available_stats = get_available_stats(df)
    print("\n" + "-" * 40)
    print("AVAILABLE STATS (showing first 25):")
    for i, stat in enumerate(available_stats[:25], 1):
        display = get_stat_display_name(stat)
        print(f"  {stat:15} -> {display}")

    # Get stat selection
    print("\n" + "-" * 40)
    stat = input("Enter stat column name: ").strip()
    if not stat:
        print("No stat entered.")
        return

    if stat not in df.columns:
        print(f"  [!] Stat '{stat}' not found")
        return

    # Per-90 toggle
    if stat not in ALREADY_PER_90:
        per_90_choice = input("Show as per-90? (y/n, default=y): ").strip().lower()
        per_90 = per_90_choice != 'n'
    else:
        per_90 = False
        print(f"  (Stat is already a rate, not normalizing)")

    # Selection mode
    print("\n" + "-" * 40)
    print("SELECTION MODE:")
    print("  1. Individual players - pick specific players")
    print("  2. Team roster - all players from a team")
    print("  3. League leaderboard - top 10 from league(s)")

    while True:
        mode_choice = input("Select mode (1-3): ").strip()
        if mode_choice in ['1', '2', '3']:
            break
        print("Invalid choice.")

    config = {
        'file_path': file_path,
        'stat': stat,
        'per_90': per_90,
    }

    if mode_choice == '1':
        config['mode'] = 'individual'
        print("\nEnter player names (one per line, empty line to finish):")
        players = []
        while True:
            name = input("  > ").strip()
            if not name:
                break
            players.append(name)
        if not players:
            print("No players entered.")
            return
        config['players'] = players

    elif mode_choice == '2':
        config['mode'] = 'team'
        team = input("\nEnter team name: ").strip()
        if not team:
            print("No team entered.")
            return
        config['team'] = team

    else:  # mode_choice == '3'
        config['mode'] = 'league'

        # Check for league column
        league_col = None
        for col in ['newestLeague', 'leagueName', 'League', 'competition', 'competitionName']:
            if col in df.columns:
                league_col = col
                break

        if league_col:
            leagues = df[league_col].dropna().unique().tolist()
            print(f"\nAvailable leagues: {', '.join(leagues)}")
            league_input = input("Filter by league(s)? (comma-separated, or Enter for all): ").strip()
            if league_input:
                config['leagues'] = [l.strip() for l in league_input.split(',')]

    # Filters
    print("\n" + "-" * 40)
    print("FILTERS:")

    min_min_input = input("Minimum minutes (default=0): ").strip()
    config['min_minutes'] = int(min_min_input) if min_min_input.isdigit() else 0

    # Position filter
    print("\nPosition categories:")
    for i, pos in enumerate(POSITION_CATEGORIES, 1):
        print(f"  {i}. {pos}")
    print("  0. All positions")

    pos_choice = input("Filter by position (0-6, default=0): ").strip()
    if pos_choice and pos_choice != '0':
        try:
            idx = int(pos_choice) - 1
            if 0 <= idx < len(POSITION_CATEGORIES):
                config['position'] = POSITION_CATEGORIES[idx]
        except ValueError:
            pass

    # Sort order
    sort_choice = input("\nSort order - highest first? (y/n, default=y): ").strip().lower()
    config['sort_ascending'] = sort_choice == 'n'

    # Max players
    if config['mode'] != 'individual':
        max_input = input("Maximum players to show (default=10): ").strip()
        config['max_players'] = int(max_input) if max_input.isdigit() else 10

    # Output folder
    config['output_folder'] = get_output_folder()

    # Run
    result = run(config)

    if result:
        print("\n" + "=" * 60)
        print("COMPLETE")
        print("=" * 60)

        # Try to open the file
        try:
            os.startfile(result)
        except Exception as e:
            print(f"Could not open chart: {e}")


if __name__ == "__main__":
    main()
