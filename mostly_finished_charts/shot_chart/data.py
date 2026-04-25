"""Data loading and reconciliation for shot charts.

Types that describe the dicts flowing between functions, TruMedia CSV loaders,
and the pure functions that derive analytical values (penalty stats, goal
reconciliation, highlight classification) from raw shot data.
"""
from datetime import datetime
from typing import NamedTuple, TypedDict
try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

import pandas as pd

from shared.colors import TEAM_COLORS, fuzzy_match_team, normalize_team_name


# ---------------------------------------------------------------------------
# Schema types — documentation for dicts that flow between functions.

class PenStats(TypedDict):
    """Penalty stats for a single team, counted from unfiltered shot data."""
    shots: int   # number of penalty shots taken
    goals: int   # number of penalty shots scored
    xg: float    # total xG of penalty shots


class MatchInfo(TypedDict):
    """Metadata for a single-match shot chart. Optional fields are populated
    by some data paths (CSV via load_shot_data) but not others (DB); chart
    functions should degrade gracefully when optional keys are missing."""
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    date: str
    date_formatted: NotRequired[str]
    pen_stats_by_team: NotRequired[dict[str, PenStats]]
    has_extra_time: NotRequired[bool]
    first_half_end_minute: NotRequired[int]


class TeamGoalBreakdown(NamedTuple):
    """Reconciled goal totals for a team, derived from shot data + scoreline."""
    shot_goals: int   # count of shot-goals in the shots_df the chart displays
    own_goals: int    # OGs benefiting this team (score minus real shot goals)
    pen_shots: int    # penalties taken (pre-filter)
    pen_goals: int    # penalty goals scored (pre-filter)
    pen_xg: float     # total xG of penalty shots (pre-filter)


# ---------------------------------------------------------------------------
# Constants

# Shot-related playTypes in TruMedia data
SHOT_TYPES = {'Miss', 'Goal', 'PenaltyGoal', 'AttemptSaved', 'Post'}
GOAL_TYPES = {'Goal', 'PenaltyGoal'}

# Highlight categories for shot type filtering
HIGHLIGHT_CATEGORIES = {
    'Open Play': {'Open play', 'Fastbreak/Counter'},
    'Set Piece': {'Corner', 'Throw-in', 'Direct Free Kick', 'Free Kick Set Piece'},
}


# ---------------------------------------------------------------------------
# Reconciliation helpers — pure functions, no I/O.

def compute_pen_stats(team_shots_df) -> PenStats:
    """Compute penalty stats (shots/goals/xG) for a team from its shot data.

    Caller should pass the team's UNFILTERED shots. If the data lacks the
    ShotPlayStyle column (some data sources), returns zeros.
    """
    if 'ShotPlayStyle' not in team_shots_df.columns or team_shots_df.empty:
        return PenStats(shots=0, goals=0, xg=0.0)
    pens = team_shots_df[team_shots_df['ShotPlayStyle'] == 'Penalty']
    if pens.empty:
        return PenStats(shots=0, goals=0, xg=0.0)
    return PenStats(
        shots=len(pens),
        goals=len(pens[pens['playType'].isin(GOAL_TYPES)]),
        xg=float(pens['xG'].sum()),
    )


def reconcile_team_goals(
    team_shots_displayed,
    final_score: int,
    pen_stats: PenStats,
    exclude_penalties: bool,
) -> TeamGoalBreakdown:
    """Reconcile a team's shot data with the scoreline.

    Args:
        team_shots_displayed: the team's shots_df in its *display* state — may
            have been filtered by exclude_penalties or may be unfiltered.
        final_score: the team's match final score.
        pen_stats: penalty stats computed from UNFILTERED data (use
            compute_pen_stats() before any filter runs, or read from
            match_info['pen_stats_by_team']).
        exclude_penalties: whether the caller filtered penalty shots out of
            team_shots_displayed. Used to decide OG correction.

    Returns a TeamGoalBreakdown with reconciled counts and pen fields.
    """
    shot_goals = len(team_shots_displayed[team_shots_displayed['playType'].isin(GOAL_TYPES)])
    # When exclude_penalties filtered pen goals out of shot_goals, subtract
    # pen_goals from the OG calc so we don't double-count them as OGs.
    pen_adjust = pen_stats['goals'] if exclude_penalties else 0
    own_goals = max(0, final_score - shot_goals - pen_adjust)
    return TeamGoalBreakdown(
        shot_goals=shot_goals,
        own_goals=own_goals,
        pen_shots=pen_stats['shots'],
        pen_goals=pen_stats['goals'],
        pen_xg=pen_stats['xg'],
    )


# ---------------------------------------------------------------------------
# Highlight mode helpers — open-play vs. set-piece filtering for emphasis.

def classify_highlight(shots_df, highlight_mode):
    """Add '_highlighted' bool column to shots_df based on highlight mode.

    If highlight_mode is 'All' or ShotPlayStyle column is missing, all shots
    are marked as highlighted (preserving current behavior).
    """
    if highlight_mode == 'All' or 'ShotPlayStyle' not in shots_df.columns:
        shots_df['_highlighted'] = True
    else:
        target_styles = HIGHLIGHT_CATEGORIES.get(highlight_mode, set())
        shots_df['_highlighted'] = shots_df['ShotPlayStyle'].isin(target_styles)
    return shots_df


def compute_highlight_stats(shots_df, highlight_mode):
    """Return stats dict {shots, xg, goals} for highlighted shots only.

    Returns None when highlight_mode is 'All' (no extra stats needed).
    """
    if highlight_mode == 'All':
        return None
    highlighted = shots_df[shots_df['_highlighted']]
    return {
        'shots': len(highlighted),
        'xg': highlighted['xG'].sum(),
        'goals': len(highlighted[highlighted['playType'].isin(GOAL_TYPES)]),
    }


# ---------------------------------------------------------------------------
# CSV loading

def detect_csv_mode(df):
    """Auto-detect single-match vs multi-match CSV.

    Checks unique game count (via gameId or Date+opponent combos) and team
    count. Returns 'single' or 'multi'.
    """
    teams = df['Team'].nunique() if 'Team' in df.columns else 1

    if 'gameId' in df.columns:
        games = df['gameId'].nunique()
    elif 'Date' in df.columns:
        games = df['Date'].nunique()
    else:
        games = 1

    if games > 1:
        return 'multi'
    if teams == 1 and len(df) > 50:
        # Single team with lots of data likely means multi-match
        return 'multi'
    return 'single'


def _use_decimal_coords(df):
    """Use decimal coordinate columns when available, overwriting integer ones."""
    if 'EventXDecimal' in df.columns:
        df['EventX'] = df['EventXDecimal']
    if 'EventYDecimal' in df.columns:
        df['EventY'] = df['EventYDecimal']
    elif 'EventYDecimal1' in df.columns:
        df['EventY'] = df['EventYDecimal1']
    return df


def load_shot_data(file_path, exclude_penalties=False):
    """Load and filter shot data from a TruMedia CSV for a single match.

    Returns:
        (shots_df, match_info, team_colors)
    """
    print(f"\nLoading TruMedia CSV: {file_path}")

    df = pd.read_csv(file_path)

    # Filter to shot events only
    shots_df = df[df['playType'].isin(SHOT_TYPES)].copy()
    shots_df = _use_decimal_coords(shots_df)

    # Normalize team names (strip "Women" where safe)
    if 'Team' in shots_df.columns:
        shots_df['Team'] = shots_df['Team'].apply(normalize_team_name)

    # Per-team penalty stats captured BEFORE any filter so chart labeling can
    # reconcile the filtered-out penalty goals/xG with the scoreline.
    pen_stats_by_team = {}
    if 'ShotPlayStyle' in shots_df.columns and 'Team' in shots_df.columns:
        for team in shots_df['Team'].unique():
            team_shots = shots_df[shots_df['Team'] == team]
            pen_stats_by_team[team] = compute_pen_stats(team_shots)

    # Exclude penalties if requested
    if exclude_penalties and 'ShotPlayStyle' in shots_df.columns:
        before = len(shots_df)
        shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        print(f"Excluded {before - len(shots_df)} penalty shots")

    print(f"Found {len(shots_df)} shots")

    # Extract match info
    first_row = df.iloc[0]
    match_info = {
        'home_team': normalize_team_name(first_row.get('homeTeam') if pd.notna(first_row.get('homeTeam')) else 'Home'),
        'away_team': normalize_team_name(first_row.get('awayTeam') if pd.notna(first_row.get('awayTeam')) else 'Away'),
        'date': first_row.get('Date', ''),
        'home_score': int(first_row.get('homeFinalScore')) if pd.notna(first_row.get('homeFinalScore')) else int(df['homeCurrentScore'].max()) if 'homeCurrentScore' in df.columns else 0,
        'away_score': int(first_row.get('awayFinalScore')) if pd.notna(first_row.get('awayFinalScore')) else int(df['awayCurrentScore'].max()) if 'awayCurrentScore' in df.columns else 0,
        'pen_stats_by_team': pen_stats_by_team,
    }

    # Format date
    if match_info['date']:
        try:
            date_obj = datetime.strptime(match_info['date'], '%Y-%m-%d')
            match_info['date_formatted'] = date_obj.strftime('%b %d, %Y').upper()
        except Exception:
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

    teams = shots_df['Team'].unique().tolist()
    print(f"Teams: {', '.join(teams)}")

    return shots_df, match_info, team_colors


def load_multi_match_shot_data(file_path, exclude_penalties=False):
    """Load multi-match shot data from a TruMedia CSV (season-long, single team).

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
    shots_df = _use_decimal_coords(shots_df)

    # Exclude penalties if requested
    if exclude_penalties and 'ShotPlayStyle' in shots_df.columns:
        before = len(shots_df)
        shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        print(f"Excluded {before - len(shots_df)} penalty shots")

    # Normalize team names (strip "Women" where safe)
    if 'Team' in shots_df.columns:
        shots_df['Team'] = shots_df['Team'].apply(normalize_team_name)

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
