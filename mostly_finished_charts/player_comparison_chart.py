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

# NOTE: deliberately NOT using the shared physical footer margin here. This
# chart draws its own credit line, and its frames are full: every category
# card carries a centred note immediately above the credit, so lifting the
# credit to the 0.19in floor closed that gap to 5-9px with 41-45px of
# horizontal overlap. The note has to move first, and that is a re-review of
# the whole card rather than a margin change.
from shared.styles import (BG_COLOR, SPINE_COLOR, CBS_BLUE_LIGHT,
                           TEXT_PRIMARY, TEXT_SECONDARY, add_cbs_footer,
                           BROADCAST_FIGSIZE)
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

    # Default fallback. NOT #6CABDD - that is this chart family's own
    # category-heading blue, so "no colour found" was being drawn as a
    # confident-looking brand accent byte-identical to the furniture around it.
    # 22 of 265 clubs in the live pools land here (8.3%), almost all with a
    # missing feed colour; Werder Bremen Women rendered its club rule in the
    # same blue as the words SCORING and PASSING beside it.
    #
    # A neutral grey is the honest answer: it reads as "unknown", not as a
    # brand, and it cannot be mistaken for chart chrome.
    return TEAM_COLOR_UNKNOWN


# Drawn when a club has neither a registry entry nor a usable feed colour.
# Deliberately a desaturated grey: it must not look like a team's colour and
# must not collide with any UI colour on the chart.
TEAM_COLOR_UNKNOWN = '#8A94A6'


def has_bio_value(val):
    """True if a bio field carries a real measurement.

    ZERO counts as missing, and that is the whole point of this function.
    `load_player_data` runs `pd.to_numeric(...).fillna(0)` over a stat list
    that includes Height, Weight and Age, so a player the feed knows nothing
    about arrives as 0.0 rather than NaN and sails through an
    "is it None/blank/NaN" test. The chart then printed 0'0" and 0 LBS as
    though they were measurements.

    Not rare: height is 0-or-missing for 12.8% of players across the three
    live pools and weight for 25.5%. It reached the shipped 16:9 chart as
    well - every fixture in that review happened to have a height, which is
    exactly the kind of gap a fixture set cannot show you. Three builders had
    their own private copy of the old test; they now share this one.
    """
    if val is None or val == '':
        return False
    if isinstance(val, float) and pd.isna(val):
        return False
    try:
        return float(val) != 0
    except (TypeError, ValueError):
        return True          # a non-numeric field (nationality) is fine as-is


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
# Terms a reader cannot be expected to decode. One definition, three consumers:
# the single-player chart, the multi-player chart, and - new - the standalone
# category panels, which carried these terms with no glossary at all.
GLOSSARY = {
    'xG/Shot': 'Expected Goals per Shot',
    'xA': 'Expected Assists',
    'npxG+xA': 'Non-Penalty xG + xA',
}

# Furniture grey. The old #556B7F sat at 2.85:1 against the page - below the
# 3:1 floor for non-text, on the labels that are the ONLY thing naming the
# PER 90 and PCTL columns. This is the grey the subtitle already uses, so it
# raises contrast to 6.03:1 without introducing another value to the palette.
LABEL_GREY = '#8BA3B8'


def draw_glossary(ax, x, y, metric_names, fontsize=10, line_height=0.024,
                  term_gap=0.075, heading=True):
    """Define whichever GLOSSARY terms actually appear in `metric_names`.

    Returns the y below the block, or `y` unchanged when nothing needed
    defining - a heading over an empty list is worse than no heading, and only
    two of the five categories contain a term at all.
    """
    needed = [(t, d) for t, d in GLOSSARY.items() if t in set(metric_names)]
    if not needed:
        return y
    if heading:
        ax.text(x, y + 0.022, 'ABBREVIATIONS', fontsize=fontsize,
                fontweight='bold', color=LABEL_GREY, transform=ax.transAxes)
    for i, (term, definition) in enumerate(needed):
        row = y - i * line_height
        ax.text(x, row, term, fontsize=fontsize, color='white',
                fontweight='bold', transform=ax.transAxes, va='center')
        ax.text(x + term_gap, row, f'= {definition}', fontsize=fontsize,
                color=LABEL_GREY, transform=ax.transAxes, va='center')
    return y - len(needed) * line_height


def build_footer_text(position=None, pool_label=None):
    """Right-hand footer: what the percentiles were actually computed against.

    `pool_label` names the PEER POOL. It is passed in by the caller, which is
    the only layer that knows which pool it loaded - the chart sees one player
    row and cannot infer it.

    This replaces a `get_league_category(player_row['newestLeague'])` that
    derived the label from the SUBJECT'S OWN LEAGUE while the sentence it built
    described the peer set. Measured over the three live pools, that was wrong
    or inconsistent for 2,017 of 7,242 players (27.9%):

      - 786 players whose club plays in Brazilian Serie A were labelled
        "Big 5 European Leagues". The feed writes that competition as a bare
        "Serie A" and the Italy/Brazil guard looked for the words "brazil" or
        "brasileir", which never appear in it.
      - 341 Frauen Bundesliga players got the same label, because "bundesliga"
        is a substring of the men's Big-5 test.
      - 613 Primera Division and 277 Premiere Ligue players fell through to the
        raw competition name and so disagreed with peers in their own pool,
        which the footer was describing.

    It was also a near miss on something worse: 'primera division' sits in the
    Americas list, and only the accent in "Division" stopped Spain's top flight
    being labelled "Americas Big 4".

    Where no pool label is supplied the segment is OMITTED rather than guessed.
    An uploaded CSV has no known pool, and naming a population we cannot
    identify is what caused this in the first place.
    """
    parts = ['Data: Opta/STATS Perform']
    if position:
        parts.append(f'Percentile rank among {position}s')
    if pool_label:
        parts.append(pool_label)
    return '  •  '.join(parts)


def footer_segments(position=None, pool_label=None):
    """The footer split for narrow frames: (what it ranks against, source).

    One line of this is ~6.5in of type at the phone floor, which fits a 16in
    canvas and does not fit a 9in one - on the variants it ran off the left
    edge to x=-0.32 and straight through CBS SPORTS. Splitting keeps every
    segment the 16:9 review established has to be there rather than dropping
    the peer set or the pool to make it fit.
    """
    scope = []
    if position:
        scope.append(f'Percentile rank among {position}s')
    if pool_label:
        scope.append(pool_label)
    return '  •  '.join(scope), 'Data: Opta/STATS Perform'


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


def get_player_percentiles(df, player_name, min_minutes=900, compare_position=None,
                           player_id=None):
    """Calculate percentile rankings for a player vs position peers

    Args:
        df: Player dataframe
        player_name: Name of player to analyze
        min_minutes: Minimum minutes for peer comparison
        compare_position: Optional position override for comparison (from POSITION_CATEGORIES)
        player_id: TruMedia playerId. Preferred over the name where the caller
            has one, because a name is not an identifier: 88 full names in the
            North American pool, 7 in Europe and 1 in the women's pool belong
            to two DIFFERENT players. Selecting one of those used to return
            whichever had played most recently, with the other unreachable -
            the numbers were right, they were just somebody else's.
    """
    # Find the player - by id when we have one, otherwise by name.
    name_col = 'playerFullName' if 'playerFullName' in df.columns else 'Player'
    if player_id and 'playerId' in df.columns:
        player_mask = df['playerId'] == player_id
        if player_mask.any():
            matched = df[player_mask]
            player_row = matched.iloc[0]
            return _percentiles_for_row(df, player_row, min_minutes, compare_position)
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
    return _percentiles_for_row(df, player_row, min_minutes,
                                compare_position)


def _percentiles_for_row(df, player_row, min_minutes=900, compare_position=None):
    """Percentiles for an already-resolved player ROW.

    Split out so that resolving a player by id and resolving one by name run
    the identical computation afterwards. With this logic inline, the only
    way to add an id lookup was to duplicate it.
    """
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


def create_category_chart(category_name, metrics, player_row, peer_count, output_path, comparison_position=None,
                          pool_label=None):
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
    is_valid_info = has_bio_value

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
        # Derived in INCHES, not as a flat fraction. This figure's height
        # varies with the metric count (3.5 + n*0.6), so a fixed 0.045 was
        # 20pt of strip here against 29pt on the 9in combined chart - not
        # enough to hold two rows once they sat on the type floor.
        strip_height = 0.40 / fig_height
        strip_y = 0.915 - strip_height

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
            ax.text(pos, strip_y + strip_height * 0.70, label, fontsize=10,
                    color=text_color, transform=ax.transAxes, ha='center',
                    va='center', fontweight='bold', alpha=0.85)
            ax.text(pos, strip_y + strip_height * 0.30, value, fontsize=10,
                    color=text_color, transform=ax.transAxes, ha='center',
                    va='center', fontweight='bold')

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
    ax.text(val_x, y_pos + 0.05, 'PER 90', fontsize=9, color=LABEL_GREY,
            transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(pct_x, y_pos + 0.05, 'PCTL', fontsize=9, color=LABEL_GREY,
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
        ax.text(pct_x, y_pos, f'{percentile:.0f}', fontsize=11, fontweight='bold',
                color='white', transform=ax.transAxes, va='center', ha='left')

        y_pos -= row_height

    # Glossary. This panel gets shared on its own, and two of the five
    # categories carry a term a reader cannot decode - SCORING has xG/Shot,
    # CHANCE CREATION has xA and npxG+xA - while the block that defined them
    # existed only on the combined charts. draw_glossary emits nothing for the
    # three categories that need nothing.
    draw_glossary(ax, 0.08, 0.20, [m['name'] for m in metrics],
                  fontsize=9, line_height=0.05, term_gap=0.13)

    # Percentile scale at bottom
    gradient_width = 0.5
    legend_x = 0.25
    legend_y = 0.08

    ax.text(legend_x + gradient_width/2, legend_y + 0.04, 'PERCENTILE SCALE', ha='center',
            fontsize=9, fontweight='bold', color=LABEL_GREY, transform=ax.transAxes)

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
    # This standalone panel used to drop the "Percentile rank among Xs"
    # segment, so shared on its own it never said what it was ranked against.
    footer_right = build_footer_text(comparison_position, pool_label)
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE_LIGHT)
    fig.text(0.98, 0.01, footer_right,
             fontsize=8, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"  Saved: {output_path}")
    plt.close()

    return output_path


def create_comparison_chart(results, player_row, peer_count, output_path, comparison_position=None,
                            custom_title=None, custom_subtitle=None, pool_label=None):
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

    # ============ PLAYER BIO LINE ============
    # This was a solid 0.90-wide band in the club colour. Measured by ink area
    # x CIELab distance from the page, it outweighed the TITLE 10 to 1 and was
    # the single loudest thing on the chart - carrying age, nationality, height
    # and weight, which are the four least interesting facts on it. The five
    # highest-energy scanlines in the whole image were all inside it.
    #
    # It also collided semantically: a club-colour field sat 5.0 dE from the
    # ramp's own ~48th-percentile yellow, so the biggest block on a page whose
    # bars mean "good to bad" was, to within a just-noticeable difference, the
    # colour that page uses for "average". On a red-kit club it landed 10.6 dE
    # from the 0th-percentile red, and a cold reader took the header as part of
    # the verdict before reading a word.
    #
    # Club identity is now a thin rule under the title - an accent, not a
    # ground - and the bio is a quiet line in the subtitle's own grey. That
    # also fixes Juventus and Parma, whose real colour is black and which
    # rendered as an empty band on a dark page.
    rule_y = 0.917
    rule_h = 0.006
    bio_y = 0.893

    # Helper to check if value is valid (not empty, not NaN)
    is_valid_info = has_bio_value

    # Check if we have player info to display
    has_player_info = any([is_valid_info(v) for v in [player_age, player_nationality, player_height, player_weight]])

    if has_player_info:
        # Club colour as a rule under the title, centred on the body width.
        ax.add_patch(mpatches.Rectangle(
            (0.30, rule_y), 0.40, rule_h,
            facecolor=team_color, edgecolor='none', transform=ax.transAxes))

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

        # Labelled, because "27 · FRANCE · 5'10\" · 165 LBS" reads as a list of
        # unrelated numbers without them.
        bio = []
        if is_valid_info(player_age):
            bio.append(f'AGE {int(player_age)}')
        if is_valid_info(player_nationality):
            bio.append(str(player_nationality).upper())
        if height_str != '-':
            bio.append(height_str)
        if weight_str != '-':
            bio.append(weight_str.upper())
        fig.text(0.5, bio_y, '   •   '.join(bio), ha='center', fontsize=11,
                 color=TEXT_SECONDARY)

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
    # 0.22 put the right column's bars at x=0.91, leaving 0.09 for two number
    # columns AND a margin - which is why the PCTL column ran to the frame edge
    # with 18px of right margin against 99px on the left. 0.19 buys the margin
    # back without touching the encoding, since bar length stays proportional.
    bar_width = 0.19

    # Column positions. 0.04/0.53 rather than 0.05/0.54 so that the right
    # column's PCTL edge lands at 0.96 and both margins are 0.04 - the body ink
    # used to run x99..1762 of 1782, a 99px left margin against 18px on the
    # right, with the percentile column overhanging the strip above it by 74px.
    left_col_x = 0.04
    right_col_x = 0.53

    # Numbers are RIGHT-aligned to these edges. Left-aligned, the per-90 column
    # ran 26px ragged and put "44.0%" where a bare decimal was expected, and the
    # percent signs staggered down the PCTL column. Columns of figures are read
    # by scanning down, which needs a common right edge.
    NUM_W = 0.035          # room for the widest string ("86.2%")
    val_right = {left_col_x: left_col_x + 0.15 + bar_width + 0.05,
                 right_col_x: right_col_x + 0.15 + bar_width + 0.05}
    pct_right = {left_col_x: val_right[left_col_x] + NUM_W + 0.005,
                 right_col_x: val_right[right_col_x] + NUM_W + 0.005}

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
            ax.text(val_right[start_x], y_pos, value_str, fontsize=10, color='white',
                    transform=ax.transAxes, va='center', ha='right')

            # Percentile text
            ax.text(pct_right[start_x], y_pos, f'{percentile:.0f}', fontsize=10,
                    fontweight='bold', color='white', transform=ax.transAxes,
                    va='center', ha='right')

            y_pos -= row_height

        return y_pos - category_gap

    # Draw left column (Scoring, Chance Creation, Passing)
    # Adjust starting position based on whether info strip is shown
    y_start = 0.78 if has_player_info else 0.82

    # Column headers
    header_y = y_start + 0.025
    # Left column headers
    ax.text(val_right[left_col_x], header_y, 'PER 90', fontsize=10,
            color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')
    ax.text(pct_right[left_col_x], header_y, 'PCTL', fontsize=10,
            color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')
    # Right column headers
    ax.text(val_right[right_col_x], header_y, 'PER 90', fontsize=10,
            color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')
    ax.text(pct_right[right_col_x], header_y, 'PCTL', fontsize=10,
            color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')

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

    # Left where it is. It defines LEFT-column terms and sits on the right,
    # which is a fair criticism - but the left column runs all three of its
    # categories down to y=0.02 and has no room to receive it. Moving it needs
    # a column rebalance, not a nudge, so that stays a Tier 3 question.
    all_metric_names = [m['name'] for ms in results.values() for m in ms]
    draw_glossary(ax, legend_x, 0.20, all_metric_names)

    # Percentile scale (below abbreviations)
    legend_y = 0.06

    ax.text(legend_x + gradient_width/2, legend_y + 0.03, 'PERCENTILE SCALE', ha='center',
            fontsize=10, fontweight='bold', color=LABEL_GREY, transform=ax.transAxes)

    # Draw gradient bar
    for i in range(100):
        color = get_color_from_percentile(i)
        rect = mpatches.Rectangle((legend_x + i * gradient_width/100, legend_y),
                                    gradient_width/100 + 0.001, 0.02,
                                    facecolor=color, edgecolor='none',
                                    transform=ax.transAxes)
        ax.add_patch(rect)

    ax.text(legend_x, legend_y - 0.018, '0%', fontsize=10, color=LABEL_GREY,
            ha='left', transform=ax.transAxes)
    ax.text(legend_x + gradient_width/2, legend_y - 0.018, '50%', fontsize=10, color=LABEL_GREY,
            ha='center', transform=ax.transAxes)
    ax.text(legend_x + gradient_width, legend_y - 0.018, '100%', fontsize=10, color=LABEL_GREY,
            ha='right', transform=ax.transAxes)

    # Footer
    footer_right = build_footer_text(position, pool_label)
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=11, fontweight='bold', color=CBS_BLUE_LIGHT)
    fig.text(0.98, 0.015, footer_right,
             fontsize=9, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"\nSaved: {output_path}")
    plt.close()


# =============================================================================
# ASPECT VARIANTS - phone frames, single player only
# =============================================================================
# The user's call (2026-09-07): variants come off the SINGLE-PLAYER chart only.
# The other three builders size their canvas to their content and are left
# alone.
#
# Both frames are linted at delivery="phone", which is a 26-delivered-px floor
# = 15.6pt on a 9in-wide canvas. That floor is the whole design constraint
# here: 19 metrics plus 5 category headers is 24 rows, and at floor size one
# row of type is 1.35% of a 9:16 frame's height but 2.7% of a 9:8's.
#
# So the two frames carry the same 19 metrics but not the same annotation,
# which is the rule the DP family already follows - detail scales down with the
# frame rather than being crushed into it:
#
#   9:16  one column, 24 rows, bar + PER 90 + PCTL. The natural port.
#   9:8   two columns, and the PER 90 column is DROPPED.
#
# Dropping PER 90 on the tile is not an editorial cut - no metric is lost, and
# it buys the horizontal room two columns need at floor type. It also removes
# this chart's single worst misread: a cold viewer read the bar as the per-90
# number sitting next to it, twice, having already caught the mistake once. On
# the tile the bar and the number now mean the same thing.
# ── frame geometry ───────────────────────────────────────────────────────────
# Two rules, both learned the hard way over four passes:
#
# 1. Anything right-aligned is given as an explicit RIGHT EDGE. Expressed as
#    "bar end plus a gap", a right-aligned string's LEFT edge lands back inside
#    the bar it is meant to sit beside - and the lint cannot see it, because
#    text-on-patch is not text-on-text.
#
# 2. A column is as wide as the WIDER of its value and its HEADER, measured at
#    the size it is actually drawn. "PCTL" is wider than "100", "PER 90" beats
#    most rates, and "Final 3rd Pass" at 16.5pt is wider than the same string
#    at the 15.6pt I kept measuring it at. Every collision in this builder came
#    from sizing a column to one of the strings that goes in it rather than to
#    the widest.
#
# Type: separation is bought ABOVE the floor, not at it. Letting every role
# settle onto 15.6pt is how the DP type pass went lint-clean with no hierarchy
# left - the category heading and the payload number are what earn the room.
_COMPARISON_LAYOUT_9X16 = {
    'figsize': (9, 16), 'dpi': 120,          # -> 1080 x 1920
    'columns': 1, 'show_per90': True,
    'title_size': 34, 'bio_size': 16, 'subtitle_size': 16,
    'cat_size': 23, 'metric_size': 18, 'num_size': 19, 'colhdr_size': 16,
    'footer_size': 16, 'brand_size': 17,
    'body_top': 0.845, 'body_bottom': 0.052, 'cat_extra': 0.011,
    'bar_h': 0.0132, 'rule_y': 0.947, 'rule_h': 0.0034,
    'title_y': 0.962, 'bio_y': 0.930, 'subtitle_y': 0.908,
    'col_x': [0.055],
    # PCTL sits BESIDE the bar, PER 90 outboard of it. The bar encodes the
    # percentile, and with PER 90 in between it was 49px from the bar and the
    # percentile 51px further out - so proximity paired the bar with the number
    # it does NOT encode. Both a cold analyst and a cold viewer independently
    # assumed bar length was the per-90 rate.
    'bar_dx': 0.300, 'bar_right_dx': 0.678,
    'pct_right_dx': 0.774, 'num_right_dx': 0.890,
    'footer_scope_y': 0.038,
}
_COMPARISON_LAYOUT_9X8 = {
    'figsize': (9, 8), 'dpi': 120,           # -> 1080 x 960
    # PER 90 is present. Dropping it was my call and it was wrong: it left the
    # percentile as the only number on a row, and a lone number beside a metric
    # named "Pass %" cannot say which quantity it is. Both cold analysts argued
    # to restore it. The tile pays for it in bar length - ~105px against the
    # tall frame's ~420 - which is the right trade for a number nobody misreads.
    'columns': 2, 'show_per90': True,
    'title_size': 27, 'bio_size': 15.6, 'subtitle_size': 15.6,
    'cat_size': 19, 'metric_size': 16, 'num_size': 17.5, 'colhdr_size': 15.6,
    'footer_size': 15.6, 'brand_size': 16,
    'body_top': 0.735, 'body_bottom': 0.092, 'cat_extra': 0.018,
    'bar_h': 0.0210, 'rule_y': 0.905, 'rule_h': 0.0060,
    'title_y': 0.930, 'bio_y': 0.867, 'subtitle_y': 0.822,
    'col_x': [0.024, 0.509],
    # Solved as a whole budget, not nudged: the 0.467 column has to hold the
    # longest label (0.168), the widest per-90 or its header (0.094) and the
    # PCTL header (0.067) = 0.329, leaving 0.138 for the bar and three gaps.
    # ~20px gaps buy an 89px bar, and that is the real ceiling for two columns
    # of nineteen metrics at the phone type floor.
    'bar_dx': 0.190, 'bar_right_dx': 0.272,
    'pct_right_dx': 0.358, 'num_right_dx': 0.467,
    'short_labels': True,
    'footer_scope_y': 0.068,
}

# Only for the 9:8 tile, where the full name does not fit the label column.
# Shorten the WRAPPER, never the statistic: dropping "(non-pen)" from Goals and
# xG was flagged independently by two cold analysts, because without it the
# tile states a penalty taker's total goal rate when it is showing his
# non-penalty rate, and it silently breaks the goals-minus-xG comparison since
# the reader cannot know the two rows share a basis. "Final 3rd" alone lost its
# noun and drew "Passes? Touches? Entries?" from a cold reader.
SHORT_METRIC_LABELS = {
    'Goals (non-pen)': 'Goals (np)',
    'xG (non-pen)': 'xG (np)',
    'Final 3rd Passes': 'Final 3rd Pass',
    'Prog. Passes': 'Prog. Pass',
    'Prog. Carries': 'Prog. Carry',
}

_COMPARISON_LAYOUTS = {'9x16': _COMPARISON_LAYOUT_9X16,
                       '9x8': _COMPARISON_LAYOUT_9X8}

# How the five categories split across columns. One column takes them in order;
# two columns split 3/2, which is 14 rows against 10 - the same imbalance the
# 16:9 has, and it is the metric counts (4,4,3,4,4) that cause it, not the
# split. 2/3 is just the mirror.
_ASPECT_COLUMN_SPLIT = {
    1: [['SCORING', 'CHANCE CREATION', 'PASSING', 'PROGRESSION', 'DEFENSIVE']],
    2: [['SCORING', 'CHANCE CREATION', 'PASSING'],
        ['PROGRESSION', 'DEFENSIVE']],
}


def accent_on_page(hex_colour, floor_de=18.0, page=BG_COLOR):
    """A club colour bright enough to read as a mark on the dark page.

    The accent rule is the one brand-carrying element left on the chart, and a
    club whose colour is near-black vanishes into the ground. Measured against
    the live pools: 4 of 246 clubs sit under dE 10 and 43 under dE 15, and
    Juventus at dE 12.2 is BELOW the bar track (12.6) - the most deliberately
    recessive thing on the card.

    Lightness is raised in HSL, which preserves hue and saturation, so the club
    still reads as black-ish or navy rather than being swapped for another
    colour. This is the same tradeoff the xG Race family settled: where the
    furniture cannot move, the mark yields just enough to be seen.
    """
    from shared.colors import ciede2000, lighten_hsl
    colour = hex_colour
    for _ in range(12):
        if ciede2000(colour, page) >= floor_de:
            return colour
        colour = lighten_hsl(colour, 0.06)
    return colour


# Percentiles print as a BARE INTEGER under a PCTL header, and the reason is
# worth keeping: both of the obvious alternatives were tried on real readers
# and both produced a confidently wrong number.
#
#   "77%"  beside a metric named "Take-On %" reads as a 77% take-on rate.
#          The rate is 44.0%. Two of the nineteen metrics name a unit in their
#          own label, and beside those a percent sign is simply misread.
#
#   "77th" under a header saying RANK reads as a league position - and that
#          INVERTS the meaning at the bottom of the scale, which is far worse.
#          A cold reader took "xG 1st / Shots 4th / xG/Shot 1st" as the best
#          centre back in the Big 5 at shooting. He is the worst: 1st, 4th and
#          1st percentile. They resolved the contradiction by concluding the
#          BARS were broken. A second reviewer reached the same reading
#          independently, and noted the mirror case - "Shots 100th" beside a
#          full bar reads as a bad rank next to the best score on the card.
#
# A bare integer is neither a rate (no unit) nor a position (no ordinal), and
# the bar and colour agree with it in both directions.


def _player_facts(player_row, comparison_position=None):
    """Name, club, colour and bio line - shared by every single-player frame."""
    name = (player_row['playerFullName']
            if 'playerFullName' in player_row.index
            else player_row.get('Player', ''))
    position = comparison_position or player_row['PositionCategory']
    team = player_row.get('newestTeam', player_row.get('teamName', ''))
    colour = get_validated_team_color(team, player_row.get('newestTeamColor'))

    ok = has_bio_value
    bio = []
    age = player_row.get('Age', '')
    if ok(age):
        bio.append(f'AGE {int(age)}')
    nation = player_row.get('Nation', player_row.get('Nationality', ''))
    if ok(nation):
        bio.append(str(nation).upper())
    height = player_row.get('Height', '')
    if ok(height):
        total_in = float(height) / 2.54
        feet, inches = int(total_in // 12), int(round(total_in % 12))
        if inches == 12:
            feet, inches = feet + 1, 0
        bio.append(f"{feet}'{inches}\"")
    weight = player_row.get('Weight', '')
    if ok(weight):
        bio.append(f'{int(round(float(weight) * 2.20462))} LBS')

    minutes = player_row.get('Min', 0) or 0
    return name, team, position, colour, bio, minutes / 90


def create_comparison_aspect_chart(results, player_row, peer_count, output_path,
                                   comparison_position=None, aspect='9x16',
                                   custom_title=None, custom_subtitle=None,
                                   pool_label=None):
    """The percentile profile, rendered for a phone-shaped frame."""
    if aspect not in _COMPARISON_LAYOUTS:
        raise ValueError(f'aspect must be one of {sorted(_COMPARISON_LAYOUTS)}')
    L = _COMPARISON_LAYOUTS[aspect]

    name, team, position, colour, bio, nineties = _player_facts(
        player_row, comparison_position)

    fig = plt.figure(figsize=L['figsize'], dpi=L['dpi'])
    fig.patch.set_facecolor(BG_COLOR)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG_COLOR)
    ax.axis('off')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # ── header ────────────────────────────────────────────────────────────
    title_text = custom_title or name.upper()
    # Shrink to fit. The bio line already had a budget; the title did not, and
    # the longest name in the pool ran 990px inside a 1080px canvas - past the
    # body grid on both sides and one character from clipping.
    t_size = L['title_size']
    for _ in range(8):
        probe = fig.text(0.5, L['title_y'], title_text, ha='center',
                         fontsize=t_size, fontweight='bold')
        fig.canvas.draw()
        tw = probe.get_window_extent().transformed(
            fig.transFigure.inverted()).width
        probe.remove()
        if tw <= 0.88:
            break
        t_size *= 0.94
    title = fig.text(0.5, L['title_y'], title_text, ha='center',
                     fontsize=t_size, fontweight='bold', color='white')

    # The rule is drawn to the TITLE's measured width, not a hard-coded 0.40.
    # Fixed, it overhung short names by 62-67px a side (a black bar 43% wider
    # than "JHON LUCUMI") and ran half the length of the longest one. Measured
    # after a draw, because the width is not knowable before the text exists.
    fig.canvas.draw()
    tb = title.get_window_extent().transformed(fig.transFigure.inverted())
    rule_w = min(max(tb.width * 0.92, 0.16), 0.90)
    ax.add_patch(mpatches.Rectangle(
        (0.5 - rule_w / 2, L['rule_y']), rule_w, L['rule_h'],
        facecolor=accent_on_page(colour), edgecolor='none',
        transform=ax.transAxes))

    # The bio has to fit the frame, and it is the only line whose length is
    # driven by the DATA - club name plus nationality. With no budget it just
    # ran until it neared the edge: the left margin fell from 53px to 20px on
    # "WERDER BREMEN WOMEN", one character from clipping, while every other
    # element held a 59px grid. Shed the least important item first (weight,
    # then height, then nationality) rather than shrinking the type or letting
    # it run.
    parts = [team.upper()] + list(bio)
    while len(parts) > 1:
        probe = fig.text(0.5, L['bio_y'], '   •   '.join(parts), ha='center',
                         fontsize=L['bio_size'], color=TEXT_SECONDARY)
        fig.canvas.draw()
        w = probe.get_window_extent().transformed(
            fig.transFigure.inverted()).width
        probe.remove()
        if w <= 0.90:
            break
        parts.pop()          # drop from the tail: weight, height, nationality
    fig.text(0.5, L['bio_y'], '   •   '.join(parts), ha='center',
             fontsize=L['bio_size'], color=TEXT_SECONDARY)

    # "vs Position Peers" never said how many, and with N unknown a reader
    # cannot tell 100 from 99.6 rounded up. Naming the count costs no more
    # room than the phrase it replaces, and both reviews asked for it.
    peers_txt = (f'vs {peer_count} {position}s' if peer_count
                 else f'vs {position} Peers')
    sub = custom_subtitle or (f'{peers_txt}  •  Last 365 Days  •  '
                              f'{nineties:.1f} 90s')
    fig.text(0.5, L['subtitle_y'], sub, ha='center',
             fontsize=L['subtitle_size'], color=LABEL_GREY)

    # ── body ──────────────────────────────────────────────────────────────
    # Row pitch is DERIVED from the row count so the body always fits its
    # budget by construction, rather than being a tuned constant that silently
    # overflows when the metric set changes.
    split = _ASPECT_COLUMN_SPLIT[L['columns']]
    rows_per_col = [sum(1 + len(results[c]) for c in cats) for cats in split]
    extra = max(len(c) - 1 for c in split) * L['cat_extra']
    pitch = (L['body_top'] - L['body_bottom'] - extra) / max(rows_per_col)

    for col_i, cats in enumerate(split):
        x = L['col_x'][col_i]
        bar_x = x + L['bar_dx']
        bar_w = L['bar_right_dx'] - L['bar_dx']
        num_right = (x + L['num_right_dx']) if L['show_per90'] else None
        pct_right = x + L['pct_right_dx']
        y = L['body_top']

        # 0.42 of a pitch put the header 6px under the first SCORING cap and
        # 53px above the number it labels - nine times closer to the wrong
        # thing, so it read as part of the section title. Sit it just above its
        # own column instead.
        # 0.78 of a pitch put this 28px above SCORING and 93px above the first
        # number it labels, with a larger-type heading in between - so it read
        # as SCORING's top line. It also inverted that heading's spacing: 28px
        # above against 35px below, where every other heading gets 49/35.
        hdr_y = y + pitch * 1.15
        ax.text(pct_right, hdr_y, 'PCTL', fontsize=L['colhdr_size'],
                color=LABEL_GREY, fontweight='bold', ha='right',
                transform=ax.transAxes)
        if L['show_per90']:
            ax.text(num_right, hdr_y, 'PER 90',
                    fontsize=L['colhdr_size'], color=LABEL_GREY,
                    fontweight='bold', ha='right', transform=ax.transAxes)

        for ci, cat in enumerate(cats):
            # A heading was allotted exactly one data row, so it floated
            # midway and sat ~30% CLOSER to the section above it than to the
            # one it labels (26px vs 34px, every heading, both shapes).
            # Gestalt proximity was grouping it with the wrong block.
            if ci:
                y -= L['cat_extra']
            ax.text(x, y, cat, fontsize=L['cat_size'], fontweight='bold',
                    color='#6CABDD', transform=ax.transAxes, va='center')
            y -= pitch
            for metric in results[cat]:
                pctl = metric['percentile']
                label = metric['name']
                if L.get('short_labels'):
                    label = SHORT_METRIC_LABELS.get(label, label)
                ax.text(x, y, label, fontsize=L['metric_size'],
                        color='white', transform=ax.transAxes, va='center')
                # The pad rounds the ends, and it is charged to BOTH axes.
                # Fixed at 0.004 it was 2.3% of the tall frame's track but 7%
                # of the tile's, so the same percentile drew at nearly three
                # times the length fraction depending on the shape - and it
                # inflated a 25px-designed bar to 42px with elliptical
                # corners. Scale it with the bar and subtract it back out.
                pad = 0.0115 * bar_w
                h = max(L['bar_h'] - 2 * pad, L['bar_h'] * 0.35)
                box = f'round,pad={pad}'
                ax.add_patch(mpatches.FancyBboxPatch(
                    (bar_x, y - h / 2), bar_w, h, boxstyle=box,
                    facecolor='#3A4A5C', edgecolor='none',
                    transform=ax.transAxes))
                ax.add_patch(mpatches.FancyBboxPatch(
                    (bar_x, y - h / 2), bar_w * (pctl / 100), h, boxstyle=box,
                    facecolor=get_color_from_percentile(pctl), edgecolor='none',
                    transform=ax.transAxes))
                if L['show_per90']:
                    ax.text(num_right, y, metric['value_str'],
                            fontsize=L['num_size'], color='white',
                            transform=ax.transAxes, va='center', ha='right')
                ax.text(pct_right, y, f'{pctl:.0f}', fontsize=L['num_size'],
                        fontweight='bold', color='white',
                        transform=ax.transAxes, va='center', ha='right')
                y -= pitch

    # ── footer ────────────────────────────────────────────────────────────
    scope, source = footer_segments(position, pool_label)
    fig.text(0.5, L['footer_scope_y'], scope, ha='center',
             fontsize=L['footer_size'], color=LABEL_GREY)
    fig.text(0.048, 0.018, 'CBS SPORTS',
             fontsize=L['brand_size'],
             fontweight='bold', color=CBS_BLUE_LIGHT)
    fig.text(1 - 0.048, 0.018, source,
             fontsize=L['footer_size'],
             color='#666666', ha='right')

    plt.savefig(output_path, dpi=L['dpi'], facecolor=BG_COLOR,
                edgecolor='none')
    print(f"  Saved: {output_path}")
    plt.close()
    return output_path


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

                    # Check if alternate for player j helps.
                    #
                    # NOTE the >= here against the > above: on a tie the LATER
                    # player is the one displaced. Two players from the same
                    # club have identical alternates, so the tie is the common
                    # case, and a strict > meant index 0 always lost its club
                    # colour. That made a player's colour depend on who else
                    # was in the frame: Mbappe rendered black beside a Real
                    # Madrid team-mate and gold beside two other clubs, so the
                    # same gold meant two different people across one family.
                    # Displacing the later player keeps the first subject on
                    # their own colour in every comparison they appear in.
                    if alt_j:
                        new_dist = color_distance(colors[i], alt_j)
                        other_ok = all(
                            color_distance(alt_j, colors[k]) >= threshold
                            for k in range(len(colors)) if k != i and k != j
                        )
                        if new_dist >= threshold and other_ok:
                            improvement = new_dist - dist
                            if improvement >= best_improvement:
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


def get_multiple_player_percentiles(df, player_names, min_minutes=900,
                                    compare_position=None, player_ids=None):
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
    # Keyed by id where available. Two selected players CAN share a name -
    # that is the whole reason ids are threaded through here - and a
    # name-keyed dict would silently drop one of them. The chart reads its
    # display names from player_rows, not from these keys, so they only have
    # to be unique.
    ids = list(player_ids) if player_ids else [None] * len(player_names)
    ids += [None] * (len(player_names) - len(ids))

    # Find position from first player if not specified
    first_results, first_row, first_peers, first_position = get_player_percentiles(
        df, player_names[0], min_minutes, compare_position, player_id=ids[0]
    )

    if first_results is None:
        return None, None, None, None

    results_by_player[ids[0] or player_names[0]] = first_results
    player_rows.append(first_row)

    # Use first player's position as the comparison position
    comparison_position = first_position

    # Get percentiles for remaining players
    for player_name, pid in zip(player_names[1:], ids[1:]):
        results, player_row, peer_count, _ = get_player_percentiles(
            df, player_name, min_minutes, comparison_position, player_id=pid
        )

        if results is None:
            return None, None, None, None

        results_by_player[pid or player_name] = results
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

    is_valid_info = has_bio_value

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
        # Both branches now sit on the 9.6pt floor. The 3-player strip was at
        # 6pt/9pt - 10.0px and 15.0px delivered - which is the reference data
        # nobody could read on the chart carrying the MOST of it. The narrower
        # card is paid for with the shorter labels, not with smaller type.
        if num_players == 2:
            # More space - spread out
            positions = [strip_x + strip_width * 0.2, strip_x + strip_width * 0.5, strip_x + strip_width * 0.8]
            labels = ['HEIGHT', 'WEIGHT', '90s']
        else:
            # 3 players - more compact
            positions = [strip_x + strip_width * 0.22, strip_x + strip_width * 0.5, strip_x + strip_width * 0.78]
            labels = ['HT', 'WT', '90s']
        label_size = 10
        value_size = 11

        values = [height_str, weight_str, f"{nineties:.1f}"]

        for pos, label, value in zip(positions, labels, values):
            ax.text(pos, info_y + 0.008, label, fontsize=label_size, color=text_color,
                    transform=ax.transAxes, ha='center', va='bottom', fontweight='bold', alpha=0.7)
            ax.text(pos, info_y - 0.005, value, fontsize=value_size, color=text_color,
                    transform=ax.transAxes, ha='center', va='top', fontweight='bold')

    return start_y - 0.01  # Return position below strips


def draw_grouped_bars(ax, metrics_data, player_colors, player_names, label_x, bar_x, start_y,
                      bar_width=0.25, bar_height=0.018, row_spacing=0.07,
                      label_fontsize=10):
    """Draw grouped horizontal bars for multiple players per metric.

    Bar fill is the player's team color at full saturation. Player identity is
    carried by color; rank vs peers is carried by bar length + the white
    percentile number. No intensity modulation — that channel was redundant
    with bar length and broken for light-colored teams.
    """
    y_pos = start_y
    num_players = len(player_names)
    # Proportional to the bar, not a constant: when the 3-player layout thins
    # its bars a fixed 0.003 became a proportionally WIDER split inside the
    # group, working against the grouping it sits in.
    bar_gap = 0.19 * bar_height
    pitch = bar_height + bar_gap

    # Bar i is centred at y_pos - i*pitch, so the group spans (n-1) pitches
    # between the FIRST and LAST centres - not n. Using n put every metric
    # label exactly half a pitch below its group: on a 2-player chart that
    # landed the label on player 2's bar, so the label read as belonging to
    # that player's row rather than to the pair.
    group_span = (num_players - 1) * pitch

    # The gap BETWEEN groups has to be held constant, not left as whatever
    # row_spacing has after the group has eaten its share. Pinned at 0.07 it
    # gave 2 players a 0.031 edge-to-edge gap against 0.003 within (10x, and
    # legible) but 3 players only 0.010 (3x) - twelve bars fused into a single
    # slab with the metric boundaries invisible.
    #
    # Solve for the gap instead of the pitch. GROUP_GAP is exactly what the
    # 2-player layout already had, so that chart is unchanged to the pixel and
    # only the 3-player case moves.
    GROUP_GAP = row_spacing - pitch - bar_height
    row_pitch = group_span + bar_height + GROUP_GAP

    for metric in metrics_data:
        metric_name = metric['name']
        group_center_y = y_pos - group_span / 2

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

            # Right-aligned, same reason as the single-player chart: three
            # figures stacked per metric are read by scanning DOWN, and
            # left-aligned they stagger - "44.0%" sat where a bare decimal was
            # expected and the percent signs never lined up.
            ax.text(bar_x + bar_width + 0.045, bar_y + bar_height / 2, value_str,
                    fontsize=10, color='white', transform=ax.transAxes,
                    va='center', ha='right')

            ax.text(bar_x + bar_width + 0.087, bar_y + bar_height / 2,
                    f'{percentile:.0f}', fontsize=10, fontweight='bold',
                    color='white', transform=ax.transAxes, va='center',
                    ha='right')

        y_pos -= row_pitch

    return y_pos


def create_multi_player_comparison_chart(results_by_player, player_rows, peer_count,
                                          comparison_position, output_path,
                                          custom_title=None, custom_subtitle=None,
                                          pool_label=None):
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

    # Row spacing and bar height depend on number of players.
    #
    # The vertical budget is y_start (0.840) down to the legend (0.040) = 0.800,
    # and the left column has to fit 11 metrics plus three category headers.
    # The 3-player numbers below are SOLVED against that budget rather than
    # guessed: the old 0.048/0.014 spent everything it had on the third bar and
    # left the between-group gap at exactly 0.000, fusing twelve bars into one
    # slab. Buying the gap back out of bar height and category chrome fits in
    # 0.788 and separates better than the 2-player chart does.
    #
    #        row_pitch  content  between-group : within-group
    #  n=2     0.0550    0.782         6.6x
    #  n=3     0.0586    0.788         7.9x   (was 0.0480 / 0.684 / 0.0x)
    if num_players == 2:
        row_spacing = 0.055
        bar_height = 0.016
        category_gap = 0.025
    else:
        row_spacing = 0.0443
        bar_height = 0.012
        category_gap = 0.014

    def draw_category_section(category, label_x, bar_x, y_pos):
        """Draw a category section with grouped bars."""
        # Category header
        ax.text(label_x, y_pos, category, fontsize=11, fontweight='bold', color='#6CABDD',
                transform=ax.transAxes)
        y_pos -= 0.022

        # Column headers (above bars)
        ax.text(bar_x + bar_width + 0.045, y_pos + 0.01, 'PER 90', fontsize=10,
                color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')
        ax.text(bar_x + bar_width + 0.087, y_pos + 0.01, 'PCTL', fontsize=10,
                color=LABEL_GREY, transform=ax.transAxes, ha='right', fontweight='bold')

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
    _all_names = [m[0] for ms in METRICS.values() for m in ms]
    draw_glossary(ax, 0.68, 0.13, _all_names, line_height=0.020, term_gap=0.07)

    # The compact player legend that used to sit here is GONE. It restated
    # colour -> player about 1200px below the header cards, which already carry
    # each player's name directly above their own coloured strip and their own
    # bars. Both a cold viewer and a cold designer reported it independently:
    # the viewer never used it and decoded the chart from the header chips, the
    # designer called it redundant. Removing it also stops the "Final 3rd
    # Passes" label colliding with it on the taller 3-player layout.

    # ── Footer ───────────────────────────────────────────────────────────
    info_x = 0.98
    # Was player_rows[0] - so on a multi-player chart whichever player the user
    # happened to pick FIRST labelled the whole frame, and no single player's
    # league can describe a pool at all.
    footer_right = build_footer_text(comparison_position, pool_label)
    # CBS SPORTS bottom-LEFT with the source line bottom-right, matching the
    # single-player chart and the rest of the CBS family. This builder had them
    # stacked on the right, so two charts from one family branded on opposite
    # sides of the frame.
    fig.text(info_x, 0.015, footer_right, fontsize=8, color='#666666',
             ha='right')
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=10, fontweight='bold',
             color=CBS_BLUE_LIGHT)

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_multi_player_category_chart(category, results_by_player, player_rows,
                                        peer_count, comparison_position, output_path,
                                        pool_label=None):
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
            color=LABEL_GREY, transform=ax.transAxes, ha='left', fontweight='bold')
    ax.text(bar_x + bar_width + 0.07, bars_start_y + 0.02, 'PCTL', fontsize=8,
            color=LABEL_GREY, transform=ax.transAxes, ha='left', fontweight='bold')

    # Calculate row spacing to fit content in available space
    available_height = bars_start_y - content_bottom - 0.02
    row_spacing = available_height / num_metrics

    # Draw grouped bars
    draw_grouped_bars(ax, metrics, player_colors, player_names,
                      label_x, bar_x, bars_start_y, bar_width=bar_width, bar_height=0.028,
                      row_spacing=row_spacing, label_fontsize=15)

    # Footer
    footer_right = build_footer_text(comparison_position, pool_label)
    fig.text(0.02, 0.015, 'CBS SPORTS', fontsize=10, fontweight='bold', color=CBS_BLUE_LIGHT)
    fig.text(0.98, 0.015, footer_right,
             fontsize=8, color='#666666', ha='right')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
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
