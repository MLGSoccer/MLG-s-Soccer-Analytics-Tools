"""
xG Rolling Average Chart Builder
Creates rolling average xG charts for team performance analysis.
"""
import csv
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
import os

# Import shared utilities
from shared.colors import (
    TEAM_ABBREV, TEAM_COLORS,
    load_custom_colors, load_custom_abbrevs,
    expand_team_name_with_prompt, get_team_color,
    get_contrast_color, fuzzy_match_team,
    ensure_contrast_with_background
)
from shared.styles import BG_COLOR, style_axis
from shared.file_utils import get_file_path, get_output_folder


def expand_team_name(abbrev):
    """Convert abbreviation to full team name if known (no prompt version for opponents)."""
    if abbrev in TEAM_ABBREV:
        return TEAM_ABBREV[abbrev]
    custom_abbrevs = load_custom_abbrevs()
    return custom_abbrevs.get(abbrev, abbrev)


def parse_trumedia_csv(filepath, target_team=None, gui_mode=False):
    """Parse TruMedia CSV to extract match-by-match xG data.
    Auto-detects format: match summary vs event log.

    Returns list of match dicts with:
    - date, opponent, is_home
    - xg_for, xg_against
    - goals_for, goals_against
    - team_color

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

    # Detect format: match summary has 'xGA' column, event log has 'shooter'
    if get_idx('xGA') is not None:
        f.close()
        return parse_match_summary_csv(filepath, target_team, gui_mode=gui_mode)
    else:
        f.close()
        return parse_event_log_csv(filepath, target_team, gui_mode=gui_mode)


def parse_match_summary_csv(filepath, target_team=None, season_filter=None, gui_mode=False):
    """Parse TruMedia match summary CSV (one row per match).

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

    date_idx = get_idx('Date')
    team_idx = get_idx('Team')
    opponent_idx = get_idx('opponent')
    xg_idx = get_idx('xG')
    xga_idx = get_idx('xGA')
    gf_idx = get_idx('GF')
    ga_idx = get_idx('GA')
    home_idx = get_idx('Home')
    season_idx = get_idx('seasonName')

    # First pass: collect available seasons if we need to filter
    if season_filter is None and season_idx is not None:
        f.seek(0)
        next(reader)  # skip header
        seasons = set()
        for row in reader:
            if len(row) > season_idx:
                seasons.add(row[season_idx])

        if len(seasons) > 1:
            season_list = sorted(seasons, reverse=True)
            if gui_mode:
                # Auto-select most recent season in GUI mode
                season_filter = season_list[0]
                print(f"[OK] Auto-selected season: {season_filter}")
            else:
                print("\nSeasons available:")
                for i, s in enumerate(season_list, 1):
                    print(f"  {i}. {s}")
                print(f"  {len(season_list) + 1}. All seasons")

                while True:
                    choice = input("\nSelect season (default=1 for most recent): ").strip()
                    if choice == '':
                        season_filter = season_list[0]
                        break
                    try:
                        idx = int(choice)
                        if 1 <= idx <= len(season_list):
                            season_filter = season_list[idx - 1]
                            break
                        elif idx == len(season_list) + 1:
                            season_filter = None  # All seasons
                            break
                    except ValueError:
                        pass
                    print("Invalid choice, try again.")

        # Reset file for second pass
        f.seek(0)
        next(reader)

    matches = []
    team_abbrev = None
    team_name = None

    for row in reader:
        if len(row) < len(header):
            continue

        # Apply season filter
        if season_filter and season_idx is not None:
            if len(row) > season_idx and row[season_idx] != season_filter:
                continue

        date = row[date_idx] if date_idx else ''
        team = row[team_idx] if team_idx else ''
        opponent = row[opponent_idx] if opponent_idx else ''
        season = row[season_idx] if season_idx and len(row) > season_idx else ''

        if team_abbrev is None:
            team_abbrev = team
            if gui_mode:
                # Use non-prompting expansion in GUI mode
                team_name = expand_team_name(team)
            else:
                team_name = expand_team_name_with_prompt(team)

        try:
            xg_for = float(row[xg_idx]) if xg_idx and row[xg_idx] else 0
            xg_against = float(row[xga_idx]) if xga_idx and row[xga_idx] else 0
            goals_for = int(row[gf_idx]) if gf_idx and row[gf_idx] else 0
            goals_against = int(row[ga_idx]) if ga_idx and row[ga_idx] else 0
            is_home = row[home_idx] == '1' if home_idx else True
        except (ValueError, IndexError):
            continue

        matches.append({
            'date': date,
            'opponent': expand_team_name(opponent),
            'is_home': is_home,
            'xg_for': xg_for,
            'xg_against': xg_against,
            'goals_for': goals_for,
            'goals_against': goals_against,
            'season': season
        })

    f.close()

    # Sort by date (oldest first)
    matches.sort(key=lambda x: x['date'])

    season_label = f" ({season_filter})" if season_filter else ""
    print(f"[OK] Found {len(matches)} matches for {team_name}{season_label}")

    # Get team color (no prompt in GUI mode)
    team_color = get_team_color(team_name, prompt_if_missing=not gui_mode)

    return matches, team_name, team_color


def parse_event_log_csv(filepath, target_team=None, gui_mode=False):
    """Parse TruMedia event log CSV (one row per event).

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

    # Column indices
    date_idx = get_idx('Date')
    home_idx = get_idx('homeTeam')
    away_idx = get_idx('awayTeam')
    team_idx = get_idx('Team')
    xg_idx = get_idx('xG')
    playtype_idx = get_idx('playType')
    shooter_idx = get_idx('shooter')
    color_idx = get_idx('newestTeamColor')
    period_idx = get_idx('Period')
    season_idx = get_idx('seasonName')

    # Group events by match (date + teams)
    matches = defaultdict(lambda: {
        'home_team': None, 'away_team': None, 'date': None,
        'home_xg': 0, 'away_xg': 0,
        'home_goals': 0, 'away_goals': 0,
        'home_color': None, 'away_color': None,
        'season': ''
    })

    all_teams = set()

    for row in reader:
        if len(row) < len(header):
            continue

        # Skip penalty shootout
        if period_idx is not None and row[period_idx]:
            try:
                if int(row[period_idx]) > 4:
                    continue
            except ValueError:
                pass

        date = row[date_idx] if date_idx else ''
        home = row[home_idx] if home_idx else ''
        away = row[away_idx] if away_idx else ''
        team = row[team_idx] if team_idx else ''

        if not date or not home or not away:
            continue

        match_key = f"{date}_{home}_{away}"
        match = matches[match_key]
        match['date'] = date
        match['home_team'] = home
        match['away_team'] = away

        # Capture season
        if season_idx is not None and len(row) > season_idx:
            match['season'] = row[season_idx]

        all_teams.add(home)
        all_teams.add(away)

        # Capture team colors
        if color_idx and row[color_idx]:
            if team == home:
                match['home_color'] = row[color_idx]
            elif team == away:
                match['away_color'] = row[color_idx]

        # Only count shots (rows with shooter)
        if shooter_idx is None or not row[shooter_idx]:
            continue

        xg = float(row[xg_idx]) if xg_idx and row[xg_idx] else 0
        playtype = row[playtype_idx] if playtype_idx else ''

        is_goal = playtype in ('Goal', 'PenaltyGoal')

        if team == home:
            match['home_xg'] += xg
            if is_goal:
                match['home_goals'] += 1
        elif team == away:
            match['away_xg'] += xg
            if is_goal:
                match['away_goals'] += 1

    f.close()

    # If no target team specified, ask user (or auto-select in GUI mode)
    if target_team is None:
        team_list = sorted(all_teams)
        if gui_mode:
            # Auto-select first team in GUI mode
            target_team = team_list[0] if team_list else None
            print(f"[OK] Auto-selected team: {target_team}")
        else:
            print("\nTeams found in data:")
            for i, t in enumerate(team_list, 1):
                print(f"  {i}. {t}")

            while True:
                choice = input("\nSelect team number: ").strip()
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(team_list):
                        target_team = team_list[idx]
                        break
                except ValueError:
                    pass
                print("Invalid choice, try again.")

    print(f"\n[OK] Analyzing: {target_team}")

    # Convert to team-centric match list
    team_matches = []
    team_color = None

    for match_key, match in sorted(matches.items(), key=lambda x: x[1]['date']):
        if match['home_team'] == target_team:
            team_matches.append({
                'date': match['date'],
                'opponent': match['away_team'],
                'is_home': True,
                'xg_for': match['home_xg'],
                'xg_against': match['away_xg'],
                'goals_for': match['home_goals'],
                'goals_against': match['away_goals'],
                'season': match['season']
            })
            if match['home_color']:
                team_color = match['home_color']
        elif match['away_team'] == target_team:
            team_matches.append({
                'date': match['date'],
                'opponent': match['home_team'],
                'is_home': False,
                'xg_for': match['away_xg'],
                'xg_against': match['home_xg'],
                'goals_for': match['away_goals'],
                'goals_against': match['home_goals'],
                'season': match['season']
            })
            if match['away_color']:
                team_color = match['away_color']

    print(f"[OK] Found {len(team_matches)} matches")

    return team_matches, target_team, team_color


def calculate_rolling_average(values, window=10):
    """Calculate rolling average with specified window."""
    rolling = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        rolling.append(sum(window_vals) / len(window_vals))
    return rolling


def find_season_boundaries(matches):
    """Find match indices where season changes.

    Returns list of (match_number, season_name) tuples.
    First entry is always (1, first_season).
    """
    boundaries = []
    current_season = None
    for i, match in enumerate(matches):
        season = match.get('season', '')
        if season != current_season:
            boundaries.append((i + 1, season))  # match_number is 1-indexed
            current_season = season
    return boundaries


def draw_season_boundaries(ax, boundaries, y_pos='top'):
    """Draw vertical lines and labels at season boundaries.

    Only draws boundaries if there are multiple seasons.
    """
    if len(boundaries) <= 1:
        return  # Only one or zero seasons, no boundaries to draw

    for match_num, season_name in boundaries[1:]:  # Skip first (no line before first season)
        ax.axvline(x=match_num - 0.5, color='white', linestyle='--', linewidth=1, alpha=0.5)

        if y_pos == 'top':
            y = ax.get_ylim()[1]
            va = 'bottom'
        else:
            y = ax.get_ylim()[0]
            va = 'top'

        ax.text(match_num - 0.5, y, f' {season_name}', color='white', fontsize=8,
                alpha=0.7, ha='left', va=va, rotation=0)


def create_rolling_charts(matches, team_name, team_color, output_path, window=10):
    """Create the 4-panel rolling xG chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    # Find season boundaries for multi-season data
    season_boundaries = find_season_boundaries(matches)

    # Extract data series
    xg_for = [m['xg_for'] for m in matches]
    xg_against = [m['xg_against'] for m in matches]
    goals_for = [m['goals_for'] for m in matches]
    goals_against = [m['goals_against'] for m in matches]
    xg_diff = [m['xg_for'] - m['xg_against'] for m in matches]

    # Calculate rolling averages
    xg_for_rolling = calculate_rolling_average(xg_for, window)
    xg_against_rolling = calculate_rolling_average(xg_against, window)
    xg_diff_rolling = calculate_rolling_average(xg_diff, window)

    # Calculate cumulative values
    xg_for_cumul = np.cumsum(xg_for)
    xg_against_cumul = np.cumsum(xg_against)
    goals_for_cumul = np.cumsum(goals_for)
    goals_against_cumul = np.cumsum(goals_against)

    match_nums = list(range(1, len(matches) + 1))

    # Colors - smart contrast for xG Against
    color_for = ensure_contrast_with_background(team_color)
    color_against = get_contrast_color(team_color)
    color_diff = '#2ECC71'  # Green for positive diff

    fig = plt.figure(figsize=(16, 10))
    fig.patch.set_facecolor(BG_COLOR)

    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.3, top=0.82)

    # ============ Panel 1: xG Difference (top left) ============
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_COLOR)

    ax1.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax1.fill_between(match_nums, xg_diff_rolling, 0, color=color_for, alpha=0.2)
    ax1.plot(match_nums, xg_diff_rolling, color=color_for, linewidth=3)

    ax1.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax1.set_ylabel('xG DIFFERENCE', fontsize=12, fontweight='bold', color='white')
    ax1.set_title(f'xG DIFFERENCE ({window}-GAME ROLLING)', fontsize=14, fontweight='bold', color='white', pad=10)

    style_axis(ax1)
    draw_season_boundaries(ax1, season_boundaries, y_pos='top')

    # ============ Panel 2: xG For and Against (top right) ============
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(BG_COLOR)

    ax2.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax2.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')

    ax2.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax2.set_ylabel('xG', fontsize=12, fontweight='bold', color='white')
    ax2.set_title(f'xG FOR & AGAINST ({window}-GAME ROLLING)', fontsize=14, fontweight='bold', color='white', pad=10)
    ax2.legend(loc='upper left', fontsize=9, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))

    style_axis(ax2)
    draw_season_boundaries(ax2, season_boundaries, y_pos='top')

    # ============ Panel 3: All Three Combined (bottom left) ============
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(BG_COLOR)

    ax3.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax3.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax3.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')
    ax3.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=2, label='xG Diff', linestyle=':')

    ax3.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=12, fontweight='bold', color='white')
    ax3.set_title(f'COMBINED VIEW ({window}-GAME ROLLING)', fontsize=14, fontweight='bold', color='white', pad=10)
    ax3.legend(loc='upper left', fontsize=9, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))

    style_axis(ax3)
    draw_season_boundaries(ax3, season_boundaries, y_pos='top')

    # ============ Panel 4: Cumulative xG vs Goals (bottom right) ============
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(BG_COLOR)

    ax4.plot(match_nums, xg_for_cumul, color=color_for, linewidth=3, label='xG For')
    ax4.plot(match_nums, goals_for_cumul, color=color_for, linewidth=2, linestyle='--', alpha=0.7, label='Goals For')
    ax4.plot(match_nums, xg_against_cumul, color=color_against, linewidth=3, label='xG Against')
    ax4.plot(match_nums, goals_against_cumul, color=color_against, linewidth=2, linestyle='--', alpha=0.7, label='Goals Against')

    ax4.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax4.set_ylabel('CUMULATIVE', fontsize=12, fontweight='bold', color='white')
    ax4.set_title('CUMULATIVE xG vs ACTUAL GOALS', fontsize=14, fontweight='bold', color='white', pad=10)
    ax4.legend(loc='upper left', fontsize=8, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))

    style_axis(ax4)
    draw_season_boundaries(ax4, season_boundaries, y_pos='top')

    # Main title
    fig.text(0.5, 0.97, f'{team_name.upper()}', ha='center', fontsize=22, fontweight='bold', color='white')

    # Build subtitle with season range if multiple seasons
    if len(season_boundaries) > 1:
        first_season = season_boundaries[0][1]
        last_season = season_boundaries[-1][1]
        season_text = f'{first_season} - {last_season} | '
    else:
        season_text = f'{season_boundaries[0][1]} | ' if season_boundaries and season_boundaries[0][1] else ''

    fig.text(0.5, 0.93, f'{season_text}{window}-GAME ROLLING xG ANALYSIS | {len(matches)} MATCHES',
             ha='center', fontsize=13, color='#8BA3B8', style='italic')

    # Footer
    fig.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color='#00325B')

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_individual_charts(matches, team_name, team_color, output_folder, window=10):
    """Create each panel as a standalone chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    # Find season boundaries for multi-season data
    season_boundaries = find_season_boundaries(matches)

    # Build subtitle prefix with season range if multiple seasons
    if len(season_boundaries) > 1:
        first_season = season_boundaries[0][1]
        last_season = season_boundaries[-1][1]
        season_text = f'{first_season} - {last_season} | '
    else:
        season_text = f'{season_boundaries[0][1]} | ' if season_boundaries and season_boundaries[0][1] else ''

    # Extract data series
    xg_for = [m['xg_for'] for m in matches]
    xg_against = [m['xg_against'] for m in matches]
    goals_for = [m['goals_for'] for m in matches]
    goals_against = [m['goals_against'] for m in matches]
    xg_diff = [m['xg_for'] - m['xg_against'] for m in matches]

    # Calculate rolling averages
    xg_for_rolling = calculate_rolling_average(xg_for, window)
    xg_against_rolling = calculate_rolling_average(xg_against, window)
    xg_diff_rolling = calculate_rolling_average(xg_diff, window)

    # Calculate cumulative values
    xg_for_cumul = np.cumsum(xg_for)
    xg_against_cumul = np.cumsum(xg_against)
    goals_for_cumul = np.cumsum(goals_for)
    goals_against_cumul = np.cumsum(goals_against)

    match_nums = list(range(1, len(matches) + 1))

    # Colors
    color_for = ensure_contrast_with_background(team_color)
    color_against = get_contrast_color(team_color)
    color_diff = '#2ECC71'

    title_base = f'{team_name.upper()}'

    # ============ Chart 1: xG Difference ============
    fig1, ax1 = plt.subplots(figsize=(12, 7))
    fig1.patch.set_facecolor(BG_COLOR)
    ax1.set_facecolor(BG_COLOR)

    ax1.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax1.fill_between(match_nums, xg_diff_rolling, 0, color=color_for, alpha=0.2)
    ax1.plot(match_nums, xg_diff_rolling, color=color_for, linewidth=3)

    ax1.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax1.set_ylabel('xG DIFFERENCE', fontsize=14, fontweight='bold', color='white')
    style_axis(ax1)
    draw_season_boundaries(ax1, season_boundaries, y_pos='top')

    fig1.text(0.5, 0.95, title_base, ha='center', fontsize=20, fontweight='bold', color='white')
    fig1.text(0.5, 0.90, f'{season_text}xG DIFFERENCE ({window}-GAME ROLLING)', ha='center', fontsize=12, color='#8BA3B8', style='italic')
    fig1.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color='#00325B')

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path1 = os.path.join(output_folder, "rolling_xg_difference.png")
    plt.savefig(path1, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path1}")
    plt.close()

    # ============ Chart 2: xG For and Against ============
    fig2, ax2 = plt.subplots(figsize=(12, 7))
    fig2.patch.set_facecolor(BG_COLOR)
    ax2.set_facecolor(BG_COLOR)

    ax2.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax2.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')

    ax2.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax2.set_ylabel('xG', fontsize=14, fontweight='bold', color='white')
    ax2.legend(loc='upper left', fontsize=11, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))
    style_axis(ax2)
    draw_season_boundaries(ax2, season_boundaries, y_pos='top')

    fig2.text(0.5, 0.95, title_base, ha='center', fontsize=20, fontweight='bold', color='white')
    fig2.text(0.5, 0.90, f'{season_text}xG FOR & AGAINST ({window}-GAME ROLLING)', ha='center', fontsize=12, color='#8BA3B8', style='italic')
    fig2.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color='#00325B')

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path2 = os.path.join(output_folder, "rolling_xg_for_against.png")
    plt.savefig(path2, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path2}")
    plt.close()

    # ============ Chart 3: All Three Combined ============
    fig3, ax3 = plt.subplots(figsize=(12, 7))
    fig3.patch.set_facecolor(BG_COLOR)
    ax3.set_facecolor(BG_COLOR)

    ax3.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax3.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax3.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')
    ax3.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=2, label='xG Diff', linestyle=':')

    ax3.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=14, fontweight='bold', color='white')
    ax3.legend(loc='upper left', fontsize=11, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))
    style_axis(ax3)
    draw_season_boundaries(ax3, season_boundaries, y_pos='top')

    fig3.text(0.5, 0.95, title_base, ha='center', fontsize=20, fontweight='bold', color='white')
    fig3.text(0.5, 0.90, f'{season_text}COMBINED VIEW ({window}-GAME ROLLING)', ha='center', fontsize=12, color='#8BA3B8', style='italic')
    fig3.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color='#00325B')

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path3 = os.path.join(output_folder, "rolling_xg_combined.png")
    plt.savefig(path3, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path3}")
    plt.close()

    # ============ Chart 4: Cumulative xG vs Goals ============
    fig4, ax4 = plt.subplots(figsize=(12, 7))
    fig4.patch.set_facecolor(BG_COLOR)
    ax4.set_facecolor(BG_COLOR)

    ax4.plot(match_nums, xg_for_cumul, color=color_for, linewidth=3, label='xG For')
    ax4.plot(match_nums, goals_for_cumul, color=color_for, linewidth=2, linestyle='--', alpha=0.7, label='Goals For')
    ax4.plot(match_nums, xg_against_cumul, color=color_against, linewidth=3, label='xG Against')
    ax4.plot(match_nums, goals_against_cumul, color=color_against, linewidth=2, linestyle='--', alpha=0.7, label='Goals Against')

    ax4.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax4.set_ylabel('CUMULATIVE', fontsize=14, fontweight='bold', color='white')
    ax4.legend(loc='upper left', fontsize=10, facecolor=BG_COLOR, edgecolor='#556B7F', labelcolor='white',
               bbox_to_anchor=(1.02, 1))
    style_axis(ax4)
    draw_season_boundaries(ax4, season_boundaries, y_pos='top')

    fig4.text(0.5, 0.95, title_base, ha='center', fontsize=20, fontweight='bold', color='white')
    fig4.text(0.5, 0.90, f'{season_text}CUMULATIVE xG vs ACTUAL GOALS', ha='center', fontsize=12, color='#8BA3B8', style='italic')
    fig4.text(0.02, 0.01, 'CBS SPORTS', fontsize=10, fontweight='bold', color='#00325B')

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path4 = os.path.join(output_folder, "rolling_xg_cumulative.png")
    plt.savefig(path4, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path4}")
    plt.close()


def run(config):
    """Entry point for launcher - config contains all needed params.

    Config keys:
        file_path: str - Path to TruMedia CSV file
        output_folder: str - Where to save charts
        window: int - Rolling window size (default 10)
        gui_mode: bool - If True, skip all interactive prompts (default True)
    """
    file_path = config['file_path']
    output_folder = config['output_folder']
    window = config.get('window', 10)
    gui_mode = config.get('gui_mode', True)

    print("\nParsing match data...")
    matches, team_name, team_color = parse_trumedia_csv(file_path, gui_mode=gui_mode)

    if len(matches) < 10:
        print(f"\n[!] Warning: Only {len(matches)} matches found.")
        print("    Rolling average may be less meaningful with fewer matches.")

    output_path = os.path.join(output_folder, "xg_rolling_analysis.png")

    print("\nGenerating charts...")
    create_rolling_charts(matches, team_name, team_color, output_path, window)

    print("\nGenerating individual charts...")
    create_individual_charts(matches, team_name, team_color, output_folder, window)

    print("\nDone!")


def main():
    """Standalone entry point - prompts user for inputs."""
    print("\n" + "="*60)
    print("xG ROLLING AVERAGE CHART BUILDER")
    print("="*60)
    print("Analyzes team xG performance over a season.")
    print("Requires TruMedia Event Log CSV with multiple matches.")

    event_path = get_file_path("TruMedia Event Log CSV file")
    if not event_path:
        return

    # Get rolling window
    window_input = input("\nRolling window size (default=10): ").strip()
    window = int(window_input) if window_input.isdigit() else 10

    output_folder = get_output_folder()

    config = {
        'file_path': event_path,
        'output_folder': output_folder,
        'window': window
    }
    run(config)


if __name__ == "__main__":
    main()
