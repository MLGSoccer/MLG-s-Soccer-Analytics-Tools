"""
xG Rolling Average Chart Builder
Creates rolling average xG charts for team performance analysis.
"""
import csv
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
import matplotlib.patheffects as mpe
import numpy as np
from collections import defaultdict
import os

# Import shared utilities
from shared.colors import (
    TEAM_ABBREV, TEAM_COLORS,
    load_custom_colors, load_custom_abbrevs,
    expand_team_name_with_prompt, get_team_color,
    get_contrast_color, fuzzy_match_team, color_distance,
    ensure_contrast_with_background, derive_companion_line
)
from shared.styles import (
    BG_COLOR, style_axis, add_cbs_footer, TEXT_PRIMARY, TEXT_MUTED,
    GRID_COLOR,
    BROADCAST_FIGSIZE, DASHBOARD_FIGSIZE, TEXT_SECONDARY, SPINE_COLOR,
)
from shared.file_utils import get_file_path, get_output_folder
# One home for the rolling window and the season split - this file used to
# carry its own copies, as does player_rollingxg_chart.py, which imports
# format_season_text from HERE. Re-exported below so that import keeps working
# and the player chart inherits the corrected caption untouched.
from shared.rolling import (
    find_season_segments, segment_starts, rolling_average, longest_segment,
    partial_rolling_average, format_season_text, draw_season_boundaries,
)

__all__ = ['parse_trumedia_csv', 'create_rolling_charts',
           'create_individual_charts', 'create_aspect_chart',
           'InsufficientMatches', 'format_season_text', 'run', 'main']


def _add_team_color_bar(fig, title_obj, color, bar_y, height=0.005):
    """Draw a thin team-color accent bar matching the rendered title width.

    Mirrors the xG race / momentum convention: anchor the bar under the title
    text regardless of how long the team name is.
    """
    x0, width = _accent_bar_min_width(fig, title_obj)
    fig.patches.append(Rectangle(
        (x0, bar_y), width, height,
        transform=fig.transFigure, facecolor=color,
        edgecolor='none', zorder=10,
    ))


def expand_team_name(abbrev):
    """Convert abbreviation to full team name if known (no prompt version for opponents)."""
    if abbrev in TEAM_ABBREV:
        return TEAM_ABBREV[abbrev]
    custom_abbrevs = load_custom_abbrevs()
    return custom_abbrevs.get(abbrev, abbrev)


# Keywords in seasonName that indicate a women's competition
_WOMENS_SEASON_KEYWORDS = ['wsl', 'nwsl', "women", 'w-league', 'liga f', 'frauen', 'd1 feminine', 'division 1 feminine']


def _is_womens_competition(season_name):
    """Check if a season name indicates a women's competition."""
    s = season_name.lower()
    return any(kw in s for kw in _WOMENS_SEASON_KEYWORDS)


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
    # Check league columns for women's competition detection
    league_idx = next((idx for idx in [get_idx('leagueName'), get_idx('newestLeague'), get_idx('competitionName'), get_idx('League')] if idx is not None), None)

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
                # Include all seasons by default in GUI mode
                season_filter = None
                print(f"[OK] Including all seasons ({len(season_list)} found)")
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

            # Detect women's competition from season or league name
            # and append " Women" for teams that share a name with a men's team
            # (e.g., "ARS" -> "Arsenal" -> "Arsenal Women" in WSL)
            league = row[league_idx] if league_idx and len(row) > league_idx else ''
            competition_hint = league or season
            if competition_hint and _is_womens_competition(competition_hint):
                from shared.colors import WOMENS_ONLY_CLUBS
                if team_name not in WOMENS_ONLY_CLUBS and not team_name.endswith(' Women'):
                    team_name = team_name + ' Women'

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

    if not matches:
        raise ValueError("No match data found in CSV. Make sure the file is a match summary with Team, xG, and xGA columns.")

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
            if not team_list:
                raise ValueError("No teams found in CSV. Make sure the file has homeTeam/awayTeam columns with valid data.")
            target_team = team_list[0]
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


class InsufficientMatches(ValueError):
    """Raised when no rolling window in the selection is ever full.

    Not a warning: a 'W-game rolling average' over fewer than W matches in any
    one season has nothing to draw, and the old code drew the raw per-match
    values instead - a 2-match selection rendered as a '10-GAME ROLLING
    AVERAGE'. Callers should catch this and offer the largest usable window.
    """

    def __init__(self, window, usable):
        self.window = window
        self.usable = usable
        super().__init__(
            f"A {window}-game rolling average needs {window} matches in one "
            f"season; the longest run here is {usable}."
        )


def _series(matches, window):
    """Every series both chart builders need, computed once.

    These two builders each carried their own copy of this block and had
    already drifted apart in their subtitles. One source now.
    """
    segments = find_season_segments(matches)
    starts = segment_starts(segments)

    xg_for = [m['xg_for'] for m in matches]
    xg_against = [m['xg_against'] for m in matches]
    goals_for = [m['goals_for'] for m in matches]
    goals_against = [m['goals_against'] for m in matches]
    xg_diff = [f - a for f, a in zip(xg_for, xg_against)]

    usable = longest_segment(matches)
    if usable < window:
        raise InsufficientMatches(window, usable)

    return {
        'segments': segments,
        'match_nums': list(range(1, len(matches) + 1)),
        'xg_for': xg_for, 'xg_against': xg_against,
        'goals_for': goals_for, 'goals_against': goals_against,
        'xg_for_rolling': rolling_average(xg_for, window, starts),
        'xg_against_rolling': rolling_average(xg_against, window, starts),
        'xg_diff_rolling': rolling_average(xg_diff, window, starts),
        # the provisional lead-in, drawn dotted where the window is not full
        'xg_for_partial': partial_rolling_average(xg_for, window, starts),
        'xg_against_partial': partial_rolling_average(xg_against, window, starts),
        'xg_diff_partial': partial_rolling_average(xg_diff, window, starts),
        'xg_for_cumul': np.cumsum(xg_for),
        'xg_against_cumul': np.cumsum(xg_against),
        'goals_for_cumul': np.cumsum(goals_for),
        'goals_against_cumul': np.cumsum(goals_against),
        # Caption naming the actual competitions, not a count of years.
        'season_text': format_season_text(segments),
    }


MIN_LEAD_IN_SAMPLES = 3


def _first_undrawn_match(segments, minimum):
    """Start of the earliest season too short to carry ANY line.

    Callers pass `MIN_LEAD_IN_SAMPLES`, not the window: since the provisional
    lead-in covers a season the full window cannot fill, the only stretch left
    with nothing drawn in it is one shorter than the lead-in's own minimum.
    Shading anything longer would contradict the line running through it.

    Returned to `draw_season_boundaries` so the rolling panels can shade that
    stretch. The cumulative panel passes nothing, because it genuinely does
    accumulate across the boundary - the same divider glyph means different
    things on the two kinds of panel, and only the shading distinguishes them.
    """
    for seg in segments[1:]:
        if (seg["end"] - seg["start"] + 1) < minimum:
            return seg["start"]
    return None


def _accent_bar_min_width(fig, title_obj, minimum=0.12):
    """Floor on the title accent bar's width, in figure fractions.

    The bar tracks the rendered title width, so it swung 6x across the
    library - 294px under SOUTHAMPTON, 50px under AZ, where it reads as a
    stray dash rather than a brand device.
    """
    fig.canvas.draw()
    bb = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    bb = bb.transformed(fig.transFigure.inverted())
    if bb.width >= minimum:
        return bb.x0, bb.width
    return 0.5 - minimum / 2, minimum


def _mark_window_start(ax, window, n, fontsize=11, avoid=()):
    """Show where the rolling window first becomes full.

    Without it the empty left margin - 17% of the plot on a 55-match chart,
    53% on an 18-match one - reads as missing data rather than as the window
    filling. Recorded as an open finding against the vertical mockup ("the
    match-10 dashed rule is unlabelled in all four renders").

    The label sits OVER THE GAP, to the left of the rule and at mid-height,
    not on the axis floor to its right. Parked at the floor a cold viewer read
    the dotted rule as a manager change or a winter break and never noticed
    the caption at all on any of six charts - the caption was explaining a gap
    from the wrong side of it.
    """
    if window <= 1 or window > n:
        return
    ax.axvline(x=window, color=SPINE_COLOR, linestyle=':', linewidth=2.2,
               alpha=1.0)
    y0, y1 = ax.get_ylim()
    # Sit in whichever half of the empty margin the lines do NOT start in.
    # Pinned to mid-height it landed level with the first drawn value on the
    # short frame, where a cold viewer read it as an annotation pointing at
    # the opening descent rather than as an explanation of the gap.
    vals = [v for v in avoid if v is not None and not np.isnan(v)]
    if vals:
        room_above = y1 - max(vals)
        room_below = min(vals) - y0
        frac = (0.90 if room_above >= room_below else 0.10)
    else:
        frac = 0.78
    ax.text(window - (n * 0.015), y0 + (y1 - y0) * frac,
            # Three short lines, not two long ones: "UNDER 10 GAMES" on one
            # line reached far enough left to sit on the y-tick labels.
            f'DOTTED:\nUNDER {window}\nGAMES', color=TEXT_MUTED,
            fontsize=fontsize, alpha=0.95, ha='right', va='center',
            linespacing=1.25, zorder=7,
            # A lead-in spanning the full range leaves no clear band, so the
            # label is made readable where it lands rather than moved
            # somewhere worse. A stroke hugs the glyphs; a bbox would erase
            # the line behind them.
            path_effects=[mpe.withStroke(linewidth=3.0, foreground=BG_COLOR)])


PARTIAL_STYLE = dict(linestyle=(0, (1.4, 1.9)), alpha=0.55)


def _limits_with_partial(full, partial, floor_zero=False):
    """y-limits containing EVERYTHING drawn, lead-in included.

    An earlier version capped how far the axis could stretch for the lead-in
    and let the rest clip. That was wrong, and the user caught it: clipping is
    absence. Southampton's opening 4.53 simply was not on the chart, which is
    the very thing drawing the lead-in exists to avoid.

    The reason a cap looked defensible is that the ORIGINAL defect was an
    inflated ceiling. But that defect was never the axis on its own - it was a
    SOLID line, labelled "10-game rolling average", making one match look like
    settled form; three cold reviews read it as a team collapsing. The lead-in
    is now dotted, dimmed and captioned "UNDER 10 GAMES", so the misreading it
    caused is gone and the axis can honestly hold it.

    Cost, measured over 298 production team-seasons: the true series keeps a
    median 89.5% of the frame, 62% at the 10th percentile, 40% at worst. The
    median chart is untouched - on most, the lead-in peak sits below the true
    peak and nothing changes at all.
    """
    vals = [v for ser in list(full) + list(partial) for v in ser
            if not np.isnan(v)]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.06, 0.02)
    return (0.0 if floor_zero else lo - pad), hi + pad


def _integer_match_axis(ax, n):
    """Force whole-number ticks on the MATCH axis.

    Matplotlib fits a continuous locator to what is an ordinal count, so at
    2-4 matches it ticks 0.75, 1.25, 1.75 and at 19-21 it ticks 2.5, 7.5,
    12.5. There is no match 7.5.
    """
    from matplotlib.ticker import MaxNLocator
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins='auto'))
    ax.set_xlim(0.5, n + 0.5)


def create_rolling_charts(matches, team_name, team_color, output_path, window=10,
                          custom_title=None, custom_subtitle=None):
    """Create the 4-panel rolling xG chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    s = _series(matches, window)
    season_segments = s['segments']
    match_nums = s['match_nums']
    xg_for_rolling = s['xg_for_rolling']
    xg_against_rolling = s['xg_against_rolling']
    xg_diff_rolling = s['xg_diff_rolling']
    p_for, p_ag = s['xg_for_partial'], s['xg_against_partial']
    p_diff = s['xg_diff_partial']
    xg_for_cumul, xg_against_cumul = s['xg_for_cumul'], s['xg_against_cumul']
    goals_for_cumul, goals_against_cumul = s['goals_for_cumul'], s['goals_against_cumul']

    # Colors - smart contrast for xG Against
    color_for = ensure_contrast_with_background(team_color)
    color_against = get_contrast_color(team_color)
    # Cumulative panel: same colour carries the side, LUMINANCE carries
    # expected-vs-actual. Dashed used to do that job, but dashed already means
    # "against" in panels 2 and 3, so panel 4 was reusing the token for a
    # different distinction inside the same figure.
    color_goals_for = derive_companion_line(color_for)
    color_goals_against = derive_companion_line(
        color_against, avoid=[color_goals_for, color_for])
    # xG Difference is the TEAM COLOUR, by explicit user decision 2026-09-05:
    # "that line should be team color, it's ok if it breaks consistency across
    # charts". It had been a hue-picked neutral (orchid for 149 of 223 clubs)
    # so that one quantity carried one colour and the team colour meant xG For
    # alone - defensible, and rejected: on the flagship difference panel the
    # club's own colour matters more than the internal scheme.
    #
    # The cost lands in panel 3, which now carries two team-coloured lines
    # (xG For solid, xG Diff dotted). They are separated by style, weight and
    # by sitting in different bands of the axis, and the legend names both.
    color_diff = color_for
    # When the two companions cannot be pulled apart - a white kit has no hue
    # to keep, so its companion is grey while teal's is pale cyan, 115 apart -
    # separate them by PATTERN instead. A cold viewer reported the two "Goals"
    # legend swatches as indistinguishable on exactly that chart. The second
    # cue appears only when the colour cue fails: 45 of 223 clubs.
    _goals_against_style = (
        ':' if color_distance(color_goals_for, color_goals_against) >= 150
        else '-.')

    fig = plt.figure(figsize=DASHBOARD_FIGSIZE)
    fig.patch.set_facecolor(BG_COLOR)

    gs = fig.add_gridspec(2, 2, hspace=0.52, wspace=0.28, top=0.82)

    # ============ Panel 1: xG Difference (top left) ============
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_COLOR)

    ax1.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax1.plot(match_nums, p_diff, color=color_diff, linewidth=2.2,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=3)

    ax1.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax1.set_ylabel('xG DIFFERENCE', fontsize=12, fontweight='bold', color='white')
    ax1.set_title('xG DIFFERENCE', fontsize=14, fontweight='bold', color='white', pad=22)

    style_axis(ax1)
    _integer_match_axis(ax1, len(match_nums))
    _lim = _limits_with_partial([xg_diff_rolling], [p_diff], floor_zero=False)
    if _lim:
        ax1.set_ylim(*_lim)
    _mark_window_start(ax1, window, len(match_nums),
                       avoid=[v for ser in [p_diff]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax1, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    # ============ Panel 2: xG For and Against (top right) ============
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(BG_COLOR)

    ax2.plot(match_nums, p_for, color=color_for, linewidth=2.2, **PARTIAL_STYLE)
    ax2.plot(match_nums, p_ag, color=color_against, linewidth=2.2, **PARTIAL_STYLE)
    ax2.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax2.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')

    ax2.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax2.set_ylabel('xG', fontsize=12, fontweight='bold', color='white')
    ax2.set_title('xG FOR & AGAINST', fontsize=14, fontweight='bold', color='white', pad=10)
    ax2.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=11, frameon=False, labelcolor='white')

    style_axis(ax2)
    _integer_match_axis(ax2, len(match_nums))
    _lim = _limits_with_partial([xg_for_rolling, xg_against_rolling], [p_for, p_ag], floor_zero=False)
    if _lim:
        ax2.set_ylim(*_lim)
    _mark_window_start(ax2, window, len(match_nums),
                       avoid=[v for ser in [p_for, p_ag]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax2, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    # ============ Panel 3: All Three Combined (bottom left) ============
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(BG_COLOR)

    ax3.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    for _p, _c in ((p_for, color_for), (p_ag, color_against), (p_diff, color_diff)):
        ax3.plot(match_nums, _p, color=_c, linewidth=1.8, **PARTIAL_STYLE)
    ax3.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax3.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')
    ax3.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=2, label='xG Diff', linestyle=':')

    ax3.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=12, fontweight='bold', color='white')
    ax3.set_title('COMBINED VIEW', fontsize=14, fontweight='bold', color='white', pad=10)
    ax3.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=11, frameon=False, labelcolor='white')

    style_axis(ax3)
    _integer_match_axis(ax3, len(match_nums))
    _lim = _limits_with_partial([xg_for_rolling, xg_against_rolling, xg_diff_rolling], [p_for, p_ag, p_diff], floor_zero=False)
    if _lim:
        ax3.set_ylim(*_lim)
    _mark_window_start(ax3, window, len(match_nums),
                       avoid=[v for ser in [p_for, p_ag, p_diff]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax3, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    # ============ Panel 4: Cumulative xG vs Goals (bottom right) ============
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(BG_COLOR)

    ax4.plot(match_nums, xg_for_cumul, color=color_for, linewidth=3, label='xG For')
    ax4.plot(match_nums, goals_for_cumul, color=color_goals_for, linewidth=2.4, linestyle=':', label='Goals For')
    ax4.plot(match_nums, xg_against_cumul, color=color_against, linewidth=3, label='xG Against')
    ax4.plot(match_nums, goals_against_cumul, color=color_goals_against, linewidth=2.4,
             linestyle=_goals_against_style, label='Goals Against')

    ax4.set_xlabel('MATCH', fontsize=12, fontweight='bold', color='white')
    ax4.set_ylabel('CUMULATIVE xG / GOALS', fontsize=12, fontweight='bold', color='white')
    ax4.set_title('CUMULATIVE xG vs ACTUAL GOALS', fontsize=14, fontweight='bold', color='white', pad=10)
    ax4.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=11, frameon=False, labelcolor='white')

    style_axis(ax4)
    _integer_match_axis(ax4, len(match_nums))
    draw_season_boundaries(ax4, season_segments, y_pos='top')

    # Header: kicker, title, accent bar, subtitle (xG race / momentum convention)
    fig.text(0.5, 0.985, 'TEAM ROLLING xG', fontsize=11, fontweight='bold',
             color=TEXT_SECONDARY, ha='center', va='center')
    title_obj = fig.text(0.5, 0.942, custom_title or f'{team_name.upper()}',
                         ha='center', va='center', fontsize=22, fontweight='bold',
                         color='white')
    _add_team_color_bar(fig, title_obj, color_for, bar_y=0.912)

    # Caption names the competitions rather than counting season-years
    season_text = f"{s['season_text']} | " if s['season_text'] else ''

    auto_subtitle = f'{season_text}{window}-GAME ROLLING AVERAGE | {len(matches)} MATCHES'
    fig.text(0.5, 0.885, custom_subtitle or auto_subtitle,
             ha='center', fontsize=13, color=TEXT_SECONDARY)

    # Footer
    add_cbs_footer(fig)

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_individual_charts(matches, team_name, team_color, output_folder, window=10):
    """Create each panel as a standalone chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    s = _series(matches, window)
    season_segments = s['segments']
    match_nums = s['match_nums']
    xg_for_rolling = s['xg_for_rolling']
    xg_against_rolling = s['xg_against_rolling']
    xg_diff_rolling = s['xg_diff_rolling']
    p_for, p_ag = s['xg_for_partial'], s['xg_against_partial']
    p_diff = s['xg_diff_partial']
    xg_for_cumul, xg_against_cumul = s['xg_for_cumul'], s['xg_against_cumul']
    goals_for_cumul, goals_against_cumul = s['goals_for_cumul'], s['goals_against_cumul']
    season_text = f"{s['season_text']} | " if s['season_text'] else ''

    # Colors - identical derivation to the combined figure, which is the point
    # of _series: these two builders had drifted apart once already.
    color_for = ensure_contrast_with_background(team_color)
    color_against = get_contrast_color(team_color)
    color_goals_for = derive_companion_line(color_for)
    color_goals_against = derive_companion_line(
        color_against, avoid=[color_goals_for, color_for])
    # team colour, as above
    color_diff = color_for
    # When the two companions cannot be pulled apart - a white kit has no hue
    # to keep, so its companion is grey while teal's is pale cyan, 115 apart -
    # separate them by PATTERN instead. A cold viewer reported the two "Goals"
    # legend swatches as indistinguishable on exactly that chart. The second
    # cue appears only when the colour cue fails: 45 of 223 clubs.
    _goals_against_style = (
        ':' if color_distance(color_goals_for, color_goals_against) >= 150
        else '-.')

    title_base = f'{team_name.upper()}'

    # ============ Chart 1: xG Difference ============
    fig1, ax1 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig1.patch.set_facecolor(BG_COLOR)
    ax1.set_facecolor(BG_COLOR)

    ax1.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    ax1.plot(match_nums, p_diff, color=color_diff, linewidth=2.2,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=3)

    ax1.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax1.set_ylabel('xG DIFFERENCE', fontsize=14, fontweight='bold', color='white')
    style_axis(ax1)
    _integer_match_axis(ax1, len(match_nums))
    _lim = _limits_with_partial([xg_diff_rolling], [p_diff], floor_zero=False)
    if _lim:
        ax1.set_ylim(*_lim)
    _mark_window_start(ax1, window, len(match_nums),
                       avoid=[v for ser in [p_diff]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax1, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    fig1.text(0.5, 0.985, 'xG DIFFERENCE', fontsize=11, fontweight='bold',
              color=TEXT_SECONDARY, ha='center', va='center')
    title1 = fig1.text(0.5, 0.92, title_base, ha='center', va='center',
                       fontsize=32, fontweight='bold', color='white')
    _add_team_color_bar(fig1, title1, color_for, bar_y=0.895)
    fig1.text(0.5, 0.86, f'{season_text}{window}-GAME ROLLING AVERAGE | {len(matches)} MATCHES',
              ha='center', fontsize=12, color=TEXT_SECONDARY)
    add_cbs_footer(fig1)

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path1 = os.path.join(output_folder, "rolling_xg_difference.png")
    plt.savefig(path1, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path1}")
    plt.close()

    # ============ Chart 2: xG For and Against ============
    fig2, ax2 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig2.patch.set_facecolor(BG_COLOR)
    ax2.set_facecolor(BG_COLOR)

    ax2.plot(match_nums, p_for, color=color_for, linewidth=2.2, **PARTIAL_STYLE)
    ax2.plot(match_nums, p_ag, color=color_against, linewidth=2.2, **PARTIAL_STYLE)
    ax2.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax2.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')

    ax2.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax2.set_ylabel('xG', fontsize=14, fontweight='bold', color='white')
    ax2.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=12, frameon=False, labelcolor='white')
    style_axis(ax2)
    _integer_match_axis(ax2, len(match_nums))
    _lim = _limits_with_partial([xg_for_rolling, xg_against_rolling], [p_for, p_ag], floor_zero=False)
    if _lim:
        ax2.set_ylim(*_lim)
    _mark_window_start(ax2, window, len(match_nums),
                       avoid=[v for ser in [p_for, p_ag]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax2, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    fig2.text(0.5, 0.985, 'xG FOR & AGAINST', fontsize=11, fontweight='bold',
              color=TEXT_SECONDARY, ha='center', va='center')
    title2 = fig2.text(0.5, 0.92, title_base, ha='center', va='center',
                       fontsize=32, fontweight='bold', color='white')
    _add_team_color_bar(fig2, title2, color_for, bar_y=0.895)
    fig2.text(0.5, 0.86, f'{season_text}{window}-GAME ROLLING AVERAGE | {len(matches)} MATCHES',
              ha='center', fontsize=12, color=TEXT_SECONDARY)
    add_cbs_footer(fig2)

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path2 = os.path.join(output_folder, "rolling_xg_for_against.png")
    plt.savefig(path2, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path2}")
    plt.close()

    # ============ Chart 3: All Three Combined ============
    fig3, ax3 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig3.patch.set_facecolor(BG_COLOR)
    ax3.set_facecolor(BG_COLOR)

    ax3.axhline(y=0, color='#556B7F', linestyle='--', linewidth=1, alpha=0.5)
    for _p, _c in ((p_for, color_for), (p_ag, color_against), (p_diff, color_diff)):
        ax3.plot(match_nums, _p, color=_c, linewidth=1.8, **PARTIAL_STYLE)
    ax3.plot(match_nums, xg_for_rolling, color=color_for, linewidth=3, label='xG For')
    ax3.plot(match_nums, xg_against_rolling, color=color_against, linewidth=3, label='xG Against', linestyle='--')
    ax3.plot(match_nums, xg_diff_rolling, color=color_diff, linewidth=2, label='xG Diff', linestyle=':')

    ax3.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax3.set_ylabel('xG', fontsize=14, fontweight='bold', color='white')
    ax3.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=12, frameon=False, labelcolor='white')
    style_axis(ax3)
    _integer_match_axis(ax3, len(match_nums))
    _lim = _limits_with_partial([xg_for_rolling, xg_against_rolling, xg_diff_rolling], [p_for, p_ag, p_diff], floor_zero=False)
    if _lim:
        ax3.set_ylim(*_lim)
    _mark_window_start(ax3, window, len(match_nums),
                       avoid=[v for ser in [p_for, p_ag, p_diff]
                              for v in ser if not np.isnan(v)])
    draw_season_boundaries(ax3, season_segments, y_pos='top',
                           empty_from=_first_undrawn_match(
                               season_segments,
                               MIN_LEAD_IN_SAMPLES))

    fig3.text(0.5, 0.985, 'COMBINED xG VIEW', fontsize=11, fontweight='bold',
              color=TEXT_SECONDARY, ha='center', va='center')
    title3 = fig3.text(0.5, 0.92, title_base, ha='center', va='center',
                       fontsize=32, fontweight='bold', color='white')
    _add_team_color_bar(fig3, title3, color_for, bar_y=0.895)
    fig3.text(0.5, 0.86, f'{season_text}{window}-GAME ROLLING AVERAGE | {len(matches)} MATCHES',
              ha='center', fontsize=12, color=TEXT_SECONDARY)
    add_cbs_footer(fig3)

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path3 = os.path.join(output_folder, "rolling_xg_combined.png")
    plt.savefig(path3, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path3}")
    plt.close()

    # ============ Chart 4: Cumulative xG vs Goals ============
    fig4, ax4 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig4.patch.set_facecolor(BG_COLOR)
    ax4.set_facecolor(BG_COLOR)

    ax4.plot(match_nums, xg_for_cumul, color=color_for, linewidth=3, label='xG For')
    ax4.plot(match_nums, goals_for_cumul, color=color_goals_for, linewidth=2.4, linestyle=':', label='Goals For')
    ax4.plot(match_nums, xg_against_cumul, color=color_against, linewidth=3, label='xG Against')
    ax4.plot(match_nums, goals_against_cumul, color=color_goals_against, linewidth=2.4,
             linestyle=_goals_against_style, label='Goals Against')

    ax4.set_xlabel('MATCH', fontsize=14, fontweight='bold', color='white')
    ax4.set_ylabel('CUMULATIVE xG / GOALS', fontsize=14, fontweight='bold', color='white')
    ax4.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=4,
               fontsize=12, frameon=False, labelcolor='white')
    style_axis(ax4)
    _integer_match_axis(ax4, len(match_nums))
    draw_season_boundaries(ax4, season_segments, y_pos='top')

    fig4.text(0.5, 0.985, 'CUMULATIVE xG vs ACTUAL GOALS', fontsize=11, fontweight='bold',
              color=TEXT_SECONDARY, ha='center', va='center')
    title4 = fig4.text(0.5, 0.92, title_base, ha='center', va='center',
                       fontsize=32, fontweight='bold', color='white')
    _add_team_color_bar(fig4, title4, color_for, bar_y=0.895)
    fig4.text(0.5, 0.86, f'{season_text}{len(matches)} MATCHES',
              ha='center', fontsize=12, color=TEXT_SECONDARY)
    add_cbs_footer(fig4)

    plt.tight_layout(rect=[0, 0.03, 1, 0.88])
    path4 = os.path.join(output_folder, "rolling_xg_cumulative.png")
    plt.savefig(path4, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"Saved: {path4}")
    plt.close()


# ---------------------------------------------------------------------------
# Aspect variants - the xG For & Against view at 9:8 and 9:16
#
# Built here rather than in mockups/aspect_variants/rollingxg_9x16.py, which is
# a proof of concept carrying its own copy of the rolling maths and resolving
# colour by NAME. Keeping the variants beside the parent is the lesson the
# momentum cell paid for: one drawing implementation, no drift.
#
# TYPE FLOOR. Both frames are 9in wide delivered at 1080px, so 1pt = 1.667px
# and the 26px phone floor is 15.6pt. Nothing readable sits below 16pt. The 9:8
# tile takes the same floor - it plays as HALF a phone short and cannot buy
# legibility by being wider. Raising the small roles compresses the bottom of
# the ladder, which is correct: five distinct sizes under legibility are five
# illegible sizes, and separation down there comes from weight and colour.
# ---------------------------------------------------------------------------

_ROLLING_LAYOUT_9X16 = {
    'figsize': (9, 16), 'dpi': 120,          # -> 1080 x 1920
    'kicker_size': 17, 'title_size': 46,
    'charttype_size': 21, 'subtitle_size': 16,
    'stat_label_size': 17, 'stat_val_size': 44, 'stat_sub_size': 16,
    'stat_delta_size': 26,
    'axes_left': 0.100, 'axes_bottom': 0.085, 'axes_width': 0.848,
    'plot_gap': 0.030,
    'tick_size': 16,
    'line_w_for': 3.6, 'line_w_ag': 3.2, 'endpoint_ms': 8,
}
_ROLLING_LAYOUT_9X8 = {
    'figsize': (9, 8), 'dpi': 120,           # -> 1080 x 960
    'kicker_size': 16, 'title_size': 34,
    'charttype_size': 16, 'subtitle_size': 16,
    'stat_label_size': 16, 'stat_val_size': 30, 'stat_sub_size': 16,
    'stat_delta_size': 22,
    'axes_left': 0.104, 'axes_bottom': 0.140, 'axes_width': 0.840,
    'plot_gap': 0.026,
    'tick_size': 16,
    'line_w_for': 2.6, 'line_w_ag': 2.3, 'endpoint_ms': 6,
}
_ROLLING_LAYOUTS = {'9x16': _ROLLING_LAYOUT_9X16, '9x8': _ROLLING_LAYOUT_9X8}

# A ceiling shared across teams, so two charts can be read against each other.
# Measured over 366 production team-seasons on a full window, peak of EITHER
# series: p50 1.90, p90 2.42, p99 3.04, max 3.44.
#
#   2.5 (the mockup's value) clips 7.1% SILENTLY
#   3.0                      clips 1.6%
#   3.5                      clips 0% but spends 46% of the frame on nothing
#
# 3.0 with an expand-to-fit escape: 98.4% of charts share one scale and are
# genuinely comparable, and the rest visibly rescale rather than clipping. The
# defect being fixed is SILENT clipping; wasted headroom is only a cost.
_Y_SHARED_MAX = 3.0


def _aspect_y_max(*series):
    """The shared ceiling, raised whenever anything drawn would fall outside it.

    Pass the lead-in as well as the true series. The shared 3.0 buys
    cross-team comparability, but not at the price of a point being missing:
    it hid AZ's opening four and Southampton's first match. A chart that
    exceeds the shared scale says so on its own axis labels.
    """
    vals = [v for s in series for v in s if not np.isnan(v)]
    peak = max(vals) if vals else 0.0
    if peak <= _Y_SHARED_MAX:
        return _Y_SHARED_MAX
    return float(np.ceil(peak * 1.06 * 2) / 2)      # next half-unit up


def _wrap_title(name, max_chars=13):
    """Split an overlong club name across at most two lines."""
    words = name.split()
    if len(name) <= max_chars or len(words) == 1:
        return [name]
    out, cur = [], ''
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            out.append(cur)
            cur = w
        else:
            cur = f'{cur} {w}'.strip()
    if cur:
        out.append(cur)
    return out[:2]


def create_aspect_chart(matches, team_name, team_color, output_path, window=10,
                        aspect='9x16', custom_title=None, custom_subtitle=None):
    """The xG For & Against view, rendered for a phone-shaped frame.

    One chart, not the four-panel dashboard: a 2x2 grid at 9:16 gives four
    panels none of which is readable. This is the panel that carries the
    season on its own.
    """
    if aspect not in _ROLLING_LAYOUTS:
        raise ValueError(f'aspect must be one of {sorted(_ROLLING_LAYOUTS)}')
    L = _ROLLING_LAYOUTS[aspect]

    if not team_color:
        team_color = get_team_color(team_name)
    s = _series(matches, window)
    n = s['match_nums']
    roll_for, roll_ag = s['xg_for_rolling'], s['xg_against_rolling']

    color_for = ensure_contrast_with_background(team_color)
    color_against = get_contrast_color(team_color)

    fig = plt.figure(figsize=L['figsize'], dpi=L['dpi'])
    fig.patch.set_facecolor(BG_COLOR)

    # header, laid out top-down so a wrapped name pushes the rest down rather
    # than colliding with it
    fig_h_pt = L['figsize'][1] * 72.0
    mid = L['axes_left'] + L['axes_width'] / 2
    cur = 0.986

    def put(text, pt, color, weight='bold', gap=0.0):
        nonlocal cur
        cur -= gap + (pt * 1.16 / fig_h_pt) / 2
        o = fig.text(mid, cur, text, ha='center', va='center', fontsize=pt,
                     fontweight=weight, color=color)
        cur -= (pt * 1.16 / fig_h_pt) / 2
        return o

    kicker = put(s['season_text'] or 'ROLLING xG', L['kicker_size'], TEXT_MUTED)
    # The named-competitions caption can outgrow the frame, and 16pt is the
    # phone floor so it cannot shrink to fit. Measure, then fall back to the
    # count rather than run off the edge.
    fig.canvas.draw()
    if (kicker.get_window_extent().width
            / (L['figsize'][0] * L['dpi'])) > 0.94:
        kicker.set_text(format_season_text(s['segments'], compact=True)
                        or 'ROLLING xG')

    lines = _wrap_title((custom_title or team_name).upper())
    t_size = L['title_size'] if len(lines) == 1 else L['title_size'] * 0.66
    objs = [put(ln, t_size, TEXT_PRIMARY, gap=0.011 if i == 0 else 0.0)
            for i, ln in enumerate(lines)]
    fig.canvas.draw()
    boxes = [o.get_window_extent().transformed(fig.transFigure.inverted())
             for o in objs]
    # floor on the accent bar: a two-letter name (AZ) got a 50px stub that read
    # as a stray dash rather than a brand device
    widest = max(max(b.width for b in boxes), 0.12)
    bar_y = boxes[-1].y0 - 0.006
    fig.patches.append(Rectangle((mid - widest / 2, bar_y), widest, 0.0045,
                                 transform=fig.transFigure, facecolor=color_for,
                                 edgecolor='none', zorder=5))
    cur = min(cur, bar_y)

    put('xG CREATED & CONCEDED', L['charttype_size'], TEXT_SECONDARY,
        gap=0.015)
    put(custom_subtitle
        or f'TRAILING {window}-GAME AVERAGE OVER {len(matches)} MATCHES',
        L['subtitle_size'], TEXT_MUTED, weight='normal', gap=0.005)

    # the two headline numbers, aligned to the plot box
    x0 = L['axes_left']
    x1 = L['axes_left'] + L['axes_width']
    latest_for = next(v for v in reversed(roll_for) if not np.isnan(v))
    latest_ag = next(v for v in reversed(roll_ag) if not np.isnan(v))
    season_for = sum(s['xg_for']) / len(s['xg_for'])
    season_ag = sum(s['xg_against']) / len(s['xg_against'])

    lab_y = cur - 0.040
    val_y = lab_y - (L['stat_val_size'] * 1.16 / fig_h_pt) * 0.78
    sub_y = val_y - (L['stat_val_size'] * 1.16 / fig_h_pt) * 0.60
    # The KPI label carries the series' own line style, which is what lets the
    # bottom legend go. That legend repeated these two names in these two
    # colours 1475px lower down; its only unique payload was solid-vs-dashed,
    # and a swatch beside the label costs no vertical space at all. Removing
    # it returns 204px to the 9:16 frame and 91px to the tile - a 22% taller
    # plot on the tile, which was spending 56% of its canvas on chrome.
    sw = 0.055
    # "xG " is dropped from both labels: the chart-type line and the y axis
    # already say xG, and the two labels sat 20px apart on 9:16 - a cold
    # viewer read them as one run-together string.
    for x, ha, label, val, savg, col, style in (
            (x0, 'left', f'CREATED · LAST {window}', latest_for, season_for,
             color_for, '-'),
            (x1, 'right', f'CONCEDED · LAST {window}', latest_ag,
             season_ag, color_against, '--')):
        sx = (x, x + sw) if ha == 'left' else (x - sw, x)
        fig.add_artist(Line2D(
            sx, (lab_y, lab_y), color=col, linewidth=3.0,
            # an explicit dash pattern whose period divides the swatch, so the
            # last dash lands whole instead of clipped at the frame edge
            linestyle=(0, (3.2, 2.1)) if style == '--' else '-',
            transform=fig.transFigure))
        tx = x + sw + 0.016 if ha == 'left' else x - sw - 0.016
        fig.text(tx, lab_y, label, ha=ha, va='center', color=col,
                 fontsize=L['stat_label_size'], fontweight='bold')
        num = fig.text(x, val_y, f'{val:.2f}', ha=ha, va='center', color=col,
                       fontsize=L['stat_val_size'], fontweight='bold')
        if L['stat_sub_size']:
            delta = val - savg
            # escapes, not literal glyphs: this project has been bitten by
            # non-ASCII source on Streamlit Cloud before
            arrow = '▲' if delta >= 0 else '▼'
            fig.canvas.draw()
            nb = num.get_window_extent().transformed(fig.transFigure.inverted())
            dx = (nb.x1 + 0.014) if ha == 'left' else (nb.x0 - 0.014)
            fig.text(dx, val_y, f'{arrow}{abs(delta):.2f}',
                     ha='left' if ha == 'left' else 'right', va='center',
                     color=col, fontsize=L['stat_delta_size'],
                     fontweight='bold')
            fig.text(x, sub_y, f'SEASON {savg:.2f}', ha=ha, va='center',
                     color=TEXT_MUTED, fontsize=L['stat_sub_size'])

    # The plot takes whatever height is left under the header, rather than a
    # fixed rectangle: a hardcoded one left 17% of a 9:16 frame empty between
    # the callouts and the axes.
    header_bottom = (sub_y if L['stat_sub_size'] else val_y) - (
        L['stat_val_size'] * 1.16 / fig_h_pt) * 0.5
    # A season-boundary caption is drawn ABOVE the axes, so it needs room that
    # a single-season chart does not. Without it, on the short frame the
    # caption landed 9px under "SEASON 1.22", right-aligned to a different
    # edge, and read as a third line of the conceded stat block rather than
    # as a label for a shaded region 620px away.
    gap = L['plot_gap']
    if len(s['segments']) > 1:
        gap += (L['tick_size'] * 1.16 / fig_h_pt) + (9.0 / fig_h_pt)
    axes_top = header_bottom - gap
    ax = fig.add_axes([L['axes_left'], L['axes_bottom'], L['axes_width'],
                       axes_top - L['axes_bottom']])
    ax.set_facecolor(BG_COLOR)
    ax.plot(n, s['xg_for_partial'], color=color_for, lw=L['line_w_for'] * 0.7,
            zorder=3, **PARTIAL_STYLE)
    ax.plot(n, s['xg_against_partial'], color=color_against,
            lw=L['line_w_ag'] * 0.7, zorder=3, **PARTIAL_STYLE)
    ax.plot(n, roll_for, color=color_for, lw=L['line_w_for'], label='xG CREATED',
            zorder=4)
    ax.plot(n, roll_ag, color=color_against, lw=L['line_w_ag'], ls='--',
            label='xG CONCEDED', zorder=4)

    style_axis(ax)
    ax.yaxis.grid(True, linestyle='--', alpha=0.55, color=GRID_COLOR)
    _integer_match_axis(ax, len(n))
    ax.set_ylim(0, _aspect_y_max(roll_for, roll_ag,
                                 s['xg_for_partial'],
                                 s['xg_against_partial']))
    _mark_window_start(ax, window, len(n), fontsize=L['tick_size'],
                       avoid=[v for ser in (s['xg_for_partial'],
                                            s['xg_against_partial'])
                              for v in ser if not np.isnan(v)])
    ax.set_xlabel('MATCH', fontsize=L['tick_size'], fontweight='bold',
                  color=TEXT_PRIMARY)
    ax.set_ylabel('xG PER GAME', fontsize=L['tick_size'],
                  fontweight='bold', color=TEXT_SECONDARY)
    # white is reserved for DATA: a white-kitted club's line was the same
    # white as the ticks, the legend and the title, and read as furniture
    ax.tick_params(labelsize=L['tick_size'], labelcolor=TEXT_MUTED)
    draw_season_boundaries(ax, s['segments'], y_pos='top',
                           fontsize=L['tick_size'], show_count=True, label_pad=7,
                           empty_from=_first_undrawn_match(
                               s['segments'], MIN_LEAD_IN_SAMPLES))

    for series, col in ((roll_for, color_for), (roll_ag, color_against)):
        drawn = [i for i, v in enumerate(series) if not np.isnan(v)]
        if drawn:
            ax.plot([n[drawn[-1]]], [series[drawn[-1]]], 'o', color=col,
                    ms=L['endpoint_ms'], zorder=6)

    add_cbs_footer(fig)
    plt.savefig(output_path, dpi=L['dpi'], facecolor=BG_COLOR, edgecolor='none')
    print(f'Saved: {output_path}')
    plt.close(fig)


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

    # The chart refuses a window it cannot fill, so say so here rather than
    # letting InsufficientMatches surface as a stack trace in the launcher.
    usable = longest_segment(matches)
    if usable < window:
        print(f"\n[!] A {window}-game rolling average needs {window} matches "
              f"inside one season; the longest run here is {usable}.")
        print(f"    Re-run with window={max(usable, 3)} or fewer, or use a "
              f"file with more matches.")
        return

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
