"""
Player Rolling Average Chart Builder
Creates rolling average charts for individual player performance analysis.
Tracks shots, goals, and xG on a per-90-minutes basis.

The rolling maths, the season segmentation and the lead-in convention all come
from `shared/rolling.py` and from the TEAM chart, which is the reviewed
reference implementation for this family. This file used to carry its own
copies of all three and they had drifted: the window was expanding rather than
trailing, it reached back across season boundaries, and a two-match selection
still rendered as a "10-GAME ROLLING AVERAGE". Measured over the mirror, 39.8%
of players with 15+ games had the drawn peak of their "rolling" line sitting
inside the under-filled lead-in, and 28.1% had it exceeding the true peak by
more than 15% - Julian Malatini's line STARTED at 85.50 xG/90 against a true
peak of 0.15, and that one point set the y-axis for the whole chart.
"""
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as mpe
import numpy as np
import os

# Import shared utilities
from shared.colors import get_team_color, get_contrast_color, ensure_contrast_with_background
from mostly_finished_charts.team_rollingxg_chart import (
    format_season_text, _add_team_color_bar, _limits_with_partial,
    _mark_window_start, _first_undrawn_match, _integer_match_axis,
    _wrap_title, PARTIAL_STYLE, MIN_LEAD_IN_SAMPLES,
)
from shared.rolling import (
    find_season_segments, segment_starts, longest_usable_window,
    rolling_ratio,
    partial_rolling_ratio, draw_season_boundaries, fill_signed,
    InsufficientMatches,
)
from shared.styles import (
    BG_COLOR, SPINE_COLOR, style_axis, GRID_COLOR,
    add_cbs_footer, BROADCAST_FIGSIZE, DASHBOARD_FIGSIZE, TEXT_SECONDARY,
    TEXT_PRIMARY, TEXT_MUTED,
    footer_y,
)
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from shared.file_utils import get_file_path, get_output_folder

__all__ = ['parse_player_summary_csv', 'create_rolling_charts',
           'create_individual_charts', 'create_aspect_chart',
           'InsufficientMatches', 'NoShots', 'run', 'main']


class NoShots(ValueError):
    """Raised when the selection contains no shots at all.

    Four panels of an xG chart drawn from zero shots are four empty boxes with
    a y-axis ticked -0.04 to 0.04 - a scale that does not exist, over data that
    does not exist. A cold viewer called it "broken" without hesitating and a
    cold designer called it "a rendering failure, not a finding".

    Not a rare edge: 2,477 of the 11,212 selectable players (22.1%) have no
    shot in the database, 222 of them with 20+ games. Every goalkeeper in the
    picker is one click away from this.
    """

    def __init__(self, games):
        self.games = games
        super().__init__(
            f"No shots in this selection ({games} matches). An xG chart needs "
            f"at least one shot to draw."
        )


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


# Type scales. The floor is 16 delivered px; a 16-inch-wide figure delivered
# at 1920 CSS px gives 1.667 px per point, so the floor is 9.6pt - see
# layout_lint.DELIVERY. The dashboard packs four panels into that width and had
# drifted well under it: legends at 9pt (15.0px), season dividers at 8pt
# (13.3px), the last-10 opponent ticks at 7pt (11.7px) and its bar value
# labels at 6pt (10.0px). The standalone panels were already re-typeset larger;
# the dashboard just never got the same pass.
_DASH_TYPE = {'title': 14, 'axis': 12, 'tick': 10, 'legend': 10, 'bar': 10}
_SOLO_TYPE = {'title': 16, 'axis': 14, 'tick': 12, 'legend': 11, 'bar': 11}


def _integer_ticks(ax, axis='y'):
    """Whole-number ticks. Shots are counted, so there is no 2.5 of one."""
    from matplotlib.ticker import MaxNLocator
    getattr(ax, f'{axis}axis').set_major_locator(
        MaxNLocator(integer=True, nbins='auto'))


def _window_rule(ax, window, n):
    """The window-start rule WITHOUT its caption.

    `_mark_window_start` draws both, which is right on a chart seen alone. On
    the four-panel dashboard it put the same three-line caption on three
    panels, and on the bar panel it landed in the bars - the placement logic
    picks whichever half of the lead-in margin the LINE avoids, and a bar panel
    has ink in both halves. Panel 1 carries the caption; the other panels carry
    the same dotted glyph, which now means something the reader has been told.
    """
    if window <= 1 or window > n:
        return
    ax.axvline(x=window, color=SPINE_COLOR, linestyle=':', linewidth=2.2,
               alpha=1.0)


def _season_of(match):
    """The season NAME for segmentation. DB mode carries both keys."""
    return match.get('season_name') or match.get('season', '') or ''


def find_club_changes(matches):
    """Where each CLUB spell starts. [(match_number, club_name), ...], 1-indexed.

    Empty for a single-club selection. When there IS a move, the list includes
    the FIRST spell as well as the changes - the first club is otherwise named
    nowhere on the chart. The season divider deliberately leaves its first
    segment unlabelled because the subtitle names the whole span, but there is
    no equivalent for clubs: the title carries only the current one, so on
    Burgzorg's chart matches 1-25 belonged to a club the frame never mentioned.

    National-team appearances are skipped rather than treated as a move: a
    player who plays for his country mid-season has not left his club, and
    marking two "transfers" around every international window would be worse
    than marking none.
    """
    # Resolve nothing unless there is something to resolve. The international
    # lookup opens a MotherDuck connection, and calling it unconditionally put
    # a 5-second network round-trip on the CSV-upload path - twice per chart,
    # for a question CSV matches cannot even answer, since they carry season
    # NAMES rather than the seasonIds the lookup returns.
    clubs = {m.get('team_id') or m.get('team_name') or '' for m in matches}
    clubs.discard('')
    if len(clubs) < 2:
        return []

    intl = set()
    if any(m.get('team_id') for m in matches):
        try:
            from shared.motherduck import international_season_ids
            intl = international_season_ids()
        except Exception:
            intl = set()

    spells, current = [], None
    for i, m in enumerate(matches):
        if (m.get('season') or '') in intl:
            continue
        tid = m.get('team_id') or m.get('team_name') or ''
        if not tid or tid == current:
            continue
        spells.append((i + 1, m.get('team_name') or ''))
        current = tid
    return spells if len(spells) > 1 else []


def _draw_club_changes(ax, changes, fontsize, color=TEXT_MUTED):
    """Mark where the player moved clubs.

    The chart is titled with ONE club - the most recent one - and carries every
    match in the selection, so a transfer inside the window silently attributes
    the old club's matches to the new one. 1,214 of the 11,212 selectable
    players (10.8%) have more than one club in production, and a cold analyst
    reading the set said a mid-window club change "would be invisible".

    Deliberately a DIFFERENT glyph from the season divider: solid rather than
    dashed, and the label reads up the rule rather than sitting along the top.
    The two boundaries mean different things and land on the same axis, so they
    must not look alike.
    """
    if not changes:
        return
    y0, y1 = ax.get_ylim()
    x_lo, x_hi = ax.get_xlim()
    span = max(x_hi - x_lo, 1e-9)
    for i, (match_num, club) in enumerate(changes):
        x = match_num - 0.5
        # No rule before the first spell - there is no boundary there, only a
        # label saying whose matches these are.
        if i:
            ax.axvline(x=x, color=SPINE_COLOR, linestyle='-', linewidth=1.4,
                       alpha=0.9, zorder=1)
        if not club:
            continue
        # The first spell's label has no rule to sit beside, so it was pinned
        # against the axis wall - a cold designer measured 2px of clearance
        # from the left spine, where it read as a y-axis annotation rather than
        # as a segment marker. Inset it instead.
        lx = max(x, x_lo + span * 0.012)
        ax.annotate(
            f' {club.upper()} ', xy=(lx, y0 + (y1 - y0) * 0.02),
            rotation=90, ha='left', va='bottom', fontsize=fontsize,
            color=color, alpha=1.0, zorder=8,
            # An opaque plate, not a stroke. A stroke hugs the glyphs, so the
            # shot bars were drawn straight THROUGH the letterforms - the same
            # designer measured 2.18:1 where a letter crossed a bar and called
            # the word "physically cut into segments". The plate also lets the
            # label step back to a muted tone: it was the brightest thing
            # inside the plot at 9.02:1, against a 2.85:1 rule and a 4.13:1
            # data series, which inverted the whole hierarchy.
            bbox=dict(boxstyle='square,pad=0.18', facecolor=BG_COLOR,
                      edgecolor='none', alpha=0.88))


def _stats_line(goals, shots, xg, club_changes):
    """The totals, scoped when they are not one club's.

    The title names the CURRENT club, so on a transfer these totals sit
    directly under a club name they do not belong to. Burgzorg's frame read
    "PRESTON NORTH END" over "3 Goals | 42 Shots | 5.08 xG" when Preston is 5
    of his 45 matches and accounts for 0 goals and about 0.46 xG. A cold
    analyst called it "the most damaging single misreading in the set".
    """
    line = f'{goals} Goals | {shots} Shots | {xg:.2f} xG'
    if len(club_changes) > 1:
        line += f'  ·  ACROSS {len(club_changes)} CLUBS'
    return line


def _subtitle(season_text, window, n_matches, club_changes):
    """The header line, carrying the two conventions a reader cannot infer.

    Both were previously legible only as chrome INSIDE the plot, and a cold
    viewer missed both: the dotted lead-in ("I had already concluded the chart
    was broken" before finding the caption, which moves around the frame) and
    a three-club career ("I completely missed that this is three clubs" - the
    rotated club labels read as furniture in a four-second look). Neither is
    optional information, so both get a place at reading size. The in-plot
    marks stay: the subtitle says THAT, the plot says WHERE.
    """
    parts = [season_text] if season_text else []
    if len(club_changes) > 1:
        parts.append(f'{len(club_changes)} CLUBS')
    parts.append(f'{window}-GAME ROLLING · DOTTED UNTIL FULL')
    parts.append(f'{n_matches} MATCHES')
    return ' | '.join(parts)


def _draw_last_n_panel(ax, matches, s, color_xg, color_goals, L, max_bars=10):
    """The last-N-matches panel, drawn ONCE for both builders.

    THE DEFECT THIS FIXES. Each bar's HEIGHT was that match's value divided by
    that series' own season mean, while its LABEL printed the raw value. So
    the two encodings in one cluster disagreed, and there was no y-axis to
    settle it. In the two-match case a bar labelled '1' (shots) and one
    labelled '0.05' (xG) drew at exactly the same height - both are 2.00x their
    own mean. On Watkins' chart LIV's 6 shots drew at 2.92 while its 1.52 xG
    drew at 3.87, so the xG bar stood TALLER than the shots bar. All three cold
    lenses independently read the heights as a comparison; a cold analyst put
    it first on their list and a cold designer said the frame "promises a
    shared scale".

    THE FIX: keep the normalisation, which is the panel's whole point - how did
    the last ten compare to normal - and make it visible. The y-axis is back,
    ticked in multiples of the player's own average, so the dashed rule at 1x
    finally means something a reader can read off. The per-bar value labels are
    GONE: with an axis they are redundant, they were the smallest type on the
    page at 6pt (10.0px delivered, against a 16px floor), and they landed ON
    the reference rule for any match near average - which is most of them.
    The raw totals still live in the header line, and panel 3 still carries
    every match's shot count at full size.
    """
    ax.set_facecolor(BG_COLOR)
    shown = matches[-max_bars:] if len(matches) > max_bars else matches
    x = np.arange(len(shown))

    avg_shots = (s['total_shots'] / len(matches)) or 1
    avg_xg = (s['total_xg'] / len(matches)) or 1
    shots_norm = [m['shots'] / avg_shots for m in shown]
    xg_norm = [m['xg'] / avg_xg for m in shown]

    w = 0.35
    ax.bar(x - w / 2, shots_norm, w, label='Shots', color=color_goals,
           edgecolor=BG_COLOR, linewidth=0.5)
    ax.bar(x + w / 2, xg_norm, w, label='xG', color=color_xg,
           edgecolor=BG_COLOR, linewidth=0.5)

    ax.axhline(y=1.0, color='white', linestyle='--', linewidth=1.5, alpha=0.7)

    # A match he played and took nothing in drew as an empty slot, which reads
    # as a MISSING match rather than a blank one. A baseline stub was tried
    # first and was not enough: a cold viewer still read the gaps as missing
    # data on three separate files, including two where the player had been on
    # the pitch for a full 90. Zero and no-data must not look alike, so the
    # zero is written out.
    top = max(shots_norm + xg_norm + [1.0]) * 1.12
    for xi, (sv, xv) in enumerate(zip(shots_norm, xg_norm)):
        for off, v in ((-w / 2, sv), (w / 2, xv)):
            if v == 0:
                ax.plot([xi + off - w / 2, xi + off + w / 2], [0, 0],
                        color=SPINE_COLOR, linewidth=2.0, solid_capstyle='butt',
                        zorder=3)
                ax.annotate('0', xy=(xi + off, 0), xytext=(0, 4),
                            textcoords='offset points', ha='center',
                            va='bottom', fontsize=L['bar'], color=SPINE_COLOR,
                            fontweight='bold', zorder=4)

    ax.set_xticks(x)
    # STACKED, not one line. At the readable size a one-line "NOR (120')" is
    # 41-49px wide with 8-13px between labels, while the space INSIDE one is
    # 4px - so the gap between two matches was barely larger than the gap
    # inside one, and on the goalkeeper frame "NOR (120')ARG (90')" ran
    # together with no break at all. Rotating 45 degrees was tried first and
    # the lint still found nine adjacent collisions across the set; breaking
    # the line halves the width instead of shearing it, and keeps the labels
    # horizontal, which is what a reader can actually scan.
    ax.set_xticklabels([f"{m['opponent']}\n({m['minutes']}')" for m in shown],
                       fontsize=L['bar'], color='white', linespacing=1.15)

    ax.set_ylabel('vs HIS AVERAGE', fontsize=L['axis'], fontweight='bold',
                  color='white')
    ax.set_ylim(0, top)
    from matplotlib.ticker import FixedLocator, FuncFormatter
    # 1x is a MANDATORY tick, because the dashed reference line sits on it.
    # An automatic locator chose 0/2x/4x on the taller frames, leaving that
    # line floating unlabelled between two ticks - a cold viewer could only
    # work out what it meant by comparing against a different chart, which a
    # single posted image does not allow.
    ticks = [0.0, 1.0]
    step = 1.0 if top <= 3.5 else 2.0
    v = 1.0 + step
    while v < top:
        ticks.append(v)
        v += step
    ax.yaxis.set_major_locator(FixedLocator(ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: '0' if v == 0 else f'{v:g}×'))
    ax.tick_params(axis='y', colors=SPINE_COLOR, labelcolor='white',
                   labelsize=L['tick'])
    ax.tick_params(axis='x', colors='white', labelsize=L['bar'])

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(SPINE_COLOR)
    ax.spines['bottom'].set_color(SPINE_COLOR)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, linestyle='--', alpha=0.25, color=SPINE_COLOR)
    return len(shown)


def _series(matches, window):
    """Every series both chart builders need, computed once.

    The two builders each carried their own copy of this block - the same
    parallel-copy pattern that had already let the team and player charts drift
    apart. One source now, and it is the one that refuses to draw an unfilled
    window.
    """
    if not matches:
        raise NoShots(0)

    segments = find_season_segments(
        [{'season': _season_of(m)} for m in matches])
    starts = segment_starts(segments)

    xg = [m['xg'] for m in matches]
    goals = [m['goals'] for m in matches]
    shots = [m['shots'] for m in matches]
    minutes = [m['minutes'] for m in matches]

    if sum(shots) == 0:
        raise NoShots(len(matches))

    usable = longest_usable_window([{'season': _season_of(m)} for m in matches])
    if usable < window:
        raise InsufficientMatches(window, usable)

    def rate(num, den, scale=1.0):
        return (rolling_ratio(num, den, window, starts, scale),
                partial_rolling_ratio(num, den, window, starts, scale))

    xg90, xg90_part = rate(xg, minutes, 90)
    goals90, goals90_part = rate(goals, minutes, 90)
    shots90, shots90_part = rate(shots, minutes, 90)
    # PER MATCH, not per 90. The xG TREND panel scatters each match's own xG,
    # so its line has to be in the same unit or the panel contradicts itself:
    # with a per-90 line over per-match dots, Stuani's average peaked at 2.95
    # while no dot on the panel exceeded 1.75, and a cold viewer said "an
    # average that sits above every point it averages looks broken" and called
    # the chart wrong. It was not wrong - it was two units sharing an axis,
    # which is worse, because it reads as correct on every player who plays 90
    # minutes and only breaks in public on a substitute.
    xg_match, xg_match_part = rate(xg, [1] * len(xg))
    # xG per shot is the one rate whose denominator can legitimately be zero.
    # rolling_ratio returns NaN there rather than 0.0 - drawn as 0.0 it read as
    # "he was taking worthless shots" rather than "he took none".
    xgshot, xgshot_part = rate(xg, shots)

    total_minutes = sum(minutes)
    return {
        'segments': segments,
        'club_changes': find_club_changes(matches),
        'match_nums': list(range(1, len(matches) + 1)),
        'minutes': minutes, 'xg': xg, 'goals': goals, 'shots': shots,
        'xg_rolling': xg90, 'xg_partial': xg90_part,
        'goals_rolling': goals90, 'goals_partial': goals90_part,
        'shots_rolling': shots90, 'shots_partial': shots90_part,
        'xg_per_shot_rolling': xgshot, 'xg_per_shot_partial': xgshot_part,
        'xg_match_rolling': xg_match, 'xg_match_partial': xg_match_part,
        'season_avg_xg': (sum(xg) / total_minutes * 90) if total_minutes else 0,
        'avg_xg_match': (sum(xg) / len(matches)) if matches else 0,
        'total_goals': sum(goals), 'total_shots': sum(shots),
        'total_xg': sum(xg), 'total_minutes': total_minutes,
        'season_text': format_season_text(
            list(dict.fromkeys(_season_of(m) for m in matches if _season_of(m)))),
    }


def create_rolling_charts(matches, player_name, team_name, team_color, season, output_path, window=10, player_info=None,
                          custom_title=None, custom_subtitle=None):
    """Create the 4-panel player rolling chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    if player_info is None:
        player_info = {'age': '', 'nationality': '', 'height': '', 'weight': ''}

    s = _series(matches, window)
    season_segments = s['segments']
    match_nums = s['match_nums']
    xg_values, goals_values = s['xg'], s['goals']
    shots_values, minutes = s['shots'], s['minutes']
    xg_rolling, goals_rolling = s['xg_rolling'], s['goals_rolling']
    xg_per_shot_rolling = s['xg_per_shot_rolling']

    # The per-match dots are the match's OWN xG, not its per-90 rate. Dividing
    # by minutes played is what put a dot at 71 xG/90 on Stuani's chart - a
    # 1-minute cameo holding a 0.79 chance - and flattened the other 20
    # matches onto the floor. Measured on production, the axis is set by a
    # cameo rather than a real match for 11.8% of regular attackers, and the
    # worst point in the database is 85.5. Clipping is not the answer (the
    # standing rule on this family is that the axis must contain everything
    # drawn); the unstable statistic is. A match total is bounded, is what a
    # reader thinks a dot means anyway, and needs no denominator at all.
    xg_per_match = xg_values

    # Colours carry ONE meaning across the whole dashboard: the club colour is
    # always EXPECTED, the companion is always ACTUAL. They used to swap - the
    # club colour was xG in panels 1-2 and Shots in panels 3-4, while the
    # companion was Goals in panel 1, xG-per-shot in panel 3 and xG in panel 4.
    # A cold viewer read a tall cyan bar as goals because the top-left panel
    # had taught them cyan meant goals four seconds earlier.
    color_xg = ensure_contrast_with_background(team_color)   # expected
    color_goals = get_contrast_color(team_color)             # actual
    # Measured across all 223 authored club colours this pair never sits closer
    # than CIEDE2000 22.9 (median 50.6) and neither side ever falls below 3:1
    # against the background - better separation than the team chart's
    # derive_companion_line manages (min 21.2, median 24.7). The generator is
    # sound; only the assignment above was broken.

    fig = plt.figure(figsize=DASHBOARD_FIGSIZE)
    fig.patch.set_facecolor(BG_COLOR)

    # Check if we have player info to display
    has_player_info = any([player_info.get('age'), player_info.get('nationality'),
                           player_info.get('height'), player_info.get('weight')])

    # Adjust grid top based on whether info strip is shown
    grid_top = 0.78 if has_player_info else 0.82
    # bottom=0.155, not matplotlib's default 0.11: the bottom-row panels
    # hang their legends below the axes (bbox_to_anchor -0.22 and -0.30)
    # and at the default the lower legend box reached row 0 of the canvas.
    # The crop used to hide that by widening the saved image around it.
    gs = fig.add_gridspec(2, 2, hspace=0.55, wspace=0.25, top=grid_top,
                          bottom=0.155)

    empty_from = _first_undrawn_match(season_segments, MIN_LEAD_IN_SAMPLES)
    L = _DASH_TYPE

    # ============ Panel 1: Rolling xG/90 vs Goals/90 with shading (top left) ============
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(BG_COLOR)

    # The two-tone signed fill, the best-liked element on this chart - a cold
    # designer called it "the best idea in the set". Kept exactly, but drawn
    # through shared/rolling's fill_signed so the lead-in is filled faintly
    # rather than at full strength.
    # NO fill under the lead-in. The band is shaded only where both series are
    # genuine 10-game averages. Filled through the lead-in it became the
    # loudest mark on the panel exactly where it meant least: a cold analyst
    # measured Suarez's largest wedge sitting at matches 1-3, shading between a
    # one-match and a two-match number, and Burgzorg's whole GOALS/90 panel
    # peaking at 0.96 on a two-match average from a player with 3 goals in 45.
    fill_signed(ax1, match_nums, s['goals_rolling'], color_goals, color_xg,
                baseline=s['xg_rolling'], alpha=0.36, alpha_negative=0.36)

    ax1.plot(match_nums, s['xg_partial'], color=color_xg, linewidth=2.2,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, s['goals_partial'], color=color_goals, linewidth=2.2,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label='xG/90')
    ax1.plot(match_nums, goals_rolling, color=color_goals, linewidth=3, label='Goals/90')
    ax1.set_ylim(*_limits_with_partial(
        [xg_rolling, goals_rolling], [s['xg_partial'], s['goals_partial']]))

    ax1.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax1.set_ylabel('PER 90 MINUTES', fontsize=L['axis'], fontweight='bold', color='white')
    # Shortened from 'GOALS/90 vs xG/90 (10-GAME ROLLING)'. The window is now
    # stated in the subtitle, and the long form ran far enough right to touch
    # the season divider's label: a cold designer measured 28px of shared
    # column and read the two as one string, "...ROLLING)2026/27".
    ax1.set_title('GOALS/90 vs xG/90', fontsize=L['title'], fontweight='bold',
                  color='white', pad=10)
    ax1.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.22), ncol=2)

    style_axis(ax1)
    _mark_window_start(ax1, window, len(match_nums), fontsize=L['tick'],
                       avoid=[v for v in s['xg_partial'][:window]])
    draw_season_boundaries(ax1, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax1, s['club_changes'], L['tick'])

    # ============ Panel 2: xG Per 90 Trend (top right) ============
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(BG_COLOR)

    # Dots are the match's OWN xG; the line is the rolling per-90 rate. Both
    # are expected goals, so one axis carries them honestly - see the note
    # where xg_per_match is built.
    ax2.scatter(match_nums, xg_per_match, color=color_xg, alpha=0.75, s=46,
                zorder=2, label='xG this match')
    ax2.plot(match_nums, s['xg_match_partial'], color=color_xg, linewidth=2.2,
             zorder=3, **PARTIAL_STYLE)
    ax2.plot(match_nums, s['xg_match_rolling'], color=color_xg, linewidth=3,
             label=f'{window}-match average', zorder=4)
    ax2.axhline(y=s['avg_xg_match'], color='white', linestyle='--',
                linewidth=1.5, alpha=0.7,
                label=f"Average: {s['avg_xg_match']:.2f} per match")
    ax2.set_ylim(*_limits_with_partial(
        [s['xg_match_rolling'], xg_per_match], [s['xg_match_partial']]))

    ax2.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax2.set_ylabel('xG PER MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax2.set_title('xG TREND', fontsize=L['title'], fontweight='bold',
                  color='white', pad=10)
    ax2.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.22), ncol=3)

    style_axis(ax2)
    _window_rule(ax2, window, len(match_nums))
    draw_season_boundaries(ax2, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax2, s['club_changes'], L['tick'])

    # ============ Panel 3: Shot Volume & Quality (bottom left) ============
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(BG_COLOR)

    # Bars at FULL strength. At alpha 0.7 over the navy they composited to a
    # different colour from their own axis label and from the same series one
    # panel to the right - #BABD0E olive against #FFFF00 lemon on a yellow
    # club, 1.89:1 against each other. A cold designer read the dashboard as
    # carrying three series colours rather than two.
    ax3.bar(match_nums, shots_values, color=color_goals,
            edgecolor=BG_COLOR, linewidth=0.5, label='Shots (per match)', zorder=2)

    ax3.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax3.set_ylabel('SHOTS', fontsize=L['axis'], fontweight='bold', color=color_goals)
    ax3.set_title('SHOT VOLUME & QUALITY', fontsize=L['title'], fontweight='bold',
                  color='white', pad=10)

    # Style primary axis
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_color(color_xg)
    ax3.spines['left'].set_color(SPINE_COLOR)
    ax3.spines['bottom'].set_color(SPINE_COLOR)
    ax3.tick_params(colors=SPINE_COLOR, labelsize=L['tick'])
    ax3.tick_params(axis='y', colors=color_goals)
    _integer_ticks(ax3)
    ax3.set_axisbelow(True)
    ax3.grid(axis='y', color=SPINE_COLOR, linestyle='-', linewidth=0.5, alpha=0.3)

    # Secondary axis for rolling xG per shot
    ax3b = ax3.twinx()
    ax3b.plot(match_nums, s['xg_per_shot_partial'], color=color_xg,
              linewidth=2.2, zorder=3, **PARTIAL_STYLE)
    ax3b.plot(match_nums, xg_per_shot_rolling, color=color_xg, linewidth=3,
              label=f'xG/Shot ({window}-game rolling)', zorder=4)
    ax3b.set_ylim(*_limits_with_partial(
        [xg_per_shot_rolling], [s['xg_per_shot_partial']], floor_zero=True))
    ax3b.set_ylabel('xG PER SHOT', fontsize=L['axis'], fontweight='bold', color=color_xg)
    ax3b.tick_params(axis='y', labelcolor=color_xg, labelsize=L['tick'])
    ax3b.spines['right'].set_color(color_xg)
    # A twin axis inherits the x ticks and draws its own set on top of them.
    # Every x label on this panel was rendered twice, at the same position.
    ax3b.tick_params(axis='x', which='both', bottom=False, labelbottom=False)

    # Combined legend below x-axis
    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3b.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper center',
               fontsize=L['legend'], facecolor=BG_COLOR, edgecolor=SPINE_COLOR,
               labelcolor='white', bbox_to_anchor=(0.5, -0.22), ncol=2)
    _window_rule(ax3, window, len(match_nums))
    draw_season_boundaries(ax3, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax3, s['club_changes'], L['tick'])

    # ============ Panel 4: Last 10 Matches vs Season Average (bottom right) ============
    ax4 = fig.add_subplot(gs[1, 1])
    n_shown = _draw_last_n_panel(ax4, matches, s, color_xg, color_goals, L)
    ax4.set_title(f'LAST {n_shown} MATCHES vs HIS AVERAGE',
                  fontsize=L['title'], fontweight='bold', color='white', pad=10)
    ax4.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.30), ncol=2)

    for _ax in (ax1, ax2, ax3):
        _integer_match_axis(_ax, len(match_nums))
    ax3b.set_xlim(*ax3.get_xlim())

    season_text = s['season_text'] or season
    total_goals, total_shots = s['total_goals'], s['total_shots']
    total_xg = s['total_xg']

    # Header: kicker → title (matches xG race / momentum / team-rolling convention)
    # 0.9737, not 0.99: at 0.99 the kicker's ink stopped 3px short of the
    # canvas top on this 1100px frame, which reads as clipped now that nothing
    # crops. This puts it the same 0.19in off the top edge that footer_y puts
    # the credit off the bottom.
    fig.text(0.5, 0.9737, 'PLAYER ROLLING xG', fontsize=11, fontweight='bold',
             color=TEXT_SECONDARY, ha='center', va='center')
    title_obj = fig.text(
        0.5, 0.95, custom_title or f'{player_name.upper()}  •  {team_name.upper()}',
        ha='center', va='center', fontsize=22, fontweight='bold', color='white'
    )

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
        auto_subtitle = _subtitle(season_text, window, len(matches),
                                 s['club_changes'])
        fig.text(0.5, 0.87, custom_subtitle or auto_subtitle,
                 ha='center', fontsize=13, color=TEXT_SECONDARY)
        fig.text(0.5, 0.84, _stats_line(total_goals, total_shots, total_xg, s['club_changes']),
                 ha='center', fontsize=11, color='white', fontweight='bold')
    else:
        # Layout without info strip -- add a thin team-color accent bar instead,
        # matching the xG race / momentum / team-rolling convention.
        _add_team_color_bar(fig, title_obj, color_xg, bar_y=0.92)
        auto_subtitle = _subtitle(season_text, window, len(matches),
                                 s['club_changes'])
        fig.text(0.5, 0.895, custom_subtitle or auto_subtitle,
                 ha='center', fontsize=13, color=TEXT_SECONDARY)
        fig.text(0.5, 0.87, _stats_line(total_goals, total_shots, total_xg, s['club_changes']),
                 ha='center', fontsize=11, color='white', fontweight='bold')

    # Footer (standard convention)
    add_cbs_footer(fig, y=footer_y(fig))

    plt.savefig(output_path, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"\nSaved: {output_path}")
    plt.close()


def create_individual_charts(matches, player_name, team_name, team_color, season, output_folder, window=10, player_info=None):
    """Create each panel as a standalone chart."""

    if not team_color:
        team_color = get_team_color(team_name)

    if player_info is None:
        player_info = {'age': '', 'nationality': '', 'height': '', 'weight': ''}

    s = _series(matches, window)
    L = _SOLO_TYPE
    season_segments = s['segments']
    empty_from = _first_undrawn_match(season_segments, MIN_LEAD_IN_SAMPLES)
    match_nums = s['match_nums']
    xg_values, goals_values = s['xg'], s['goals']
    shots_values, minutes = s['shots'], s['minutes']
    xg_rolling, goals_rolling = s['xg_rolling'], s['goals_rolling']
    xg_per_shot_rolling = s['xg_per_shot_rolling']
    xg_per_match = xg_values          # see the note in create_rolling_charts
    season_text = s['season_text'] or season

    # Club colour = EXPECTED, companion = ACTUAL, on every panel.
    color_xg = ensure_contrast_with_background(team_color)
    color_goals = get_contrast_color(team_color)

    total_goals, total_shots = s['total_goals'], s['total_shots']
    total_xg = s['total_xg']

    title_base = f'{player_name.upper()}  •  {team_name.upper()}'
    subtitle = _subtitle(season_text, window, len(matches),
                         s['club_changes'])
    stats_line = _stats_line(total_goals, total_shots, total_xg,
                             s['club_changes'])

    # Check if we have player info to display
    has_player_info = any([player_info.get('age'), player_info.get('nationality'),
                           player_info.get('height'), player_info.get('weight')])

    def add_info_strip_to_figure(fig, kicker, title, chart_subtitle):
        """Add header (kicker → title → strip → subtitle) to a chart figure.

        kicker: panel description (rendered uppercase above the title)
        title: player • team line
        chart_subtitle: season / metadata text below the strip
        """
        if has_player_info:
            # Kicker → title
            fig.text(0.5, 0.99, kicker, fontsize=11, fontweight='bold',
                     color=TEXT_SECONDARY, ha='center', va='center')
            fig.text(0.5, 0.95, title, ha='center', fontsize=26,
                     fontweight='bold', color='white')

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
            fig.text(0.5, 0.87, chart_subtitle, ha='center', fontsize=11, color=TEXT_SECONDARY)
            fig.text(0.5, 0.84, stats_line, ha='center', fontsize=10, color='white', fontweight='bold')
            return [0, 0.08, 1, 0.82]  # tight_layout rect with strip
        else:
            # No info strip -- kicker, title, accent bar, subtitle, stats
            fig.text(0.5, 0.985, kicker, fontsize=11, fontweight='bold',
                     color=TEXT_SECONDARY, ha='center', va='center')
            title_obj = fig.text(0.5, 0.93, title, ha='center', va='center',
                                 fontsize=26, fontweight='bold', color='white')
            _add_team_color_bar(fig, title_obj, color_xg, bar_y=0.905)
            fig.text(0.5, 0.875, chart_subtitle, ha='center', fontsize=11, color=TEXT_SECONDARY)
            fig.text(0.5, 0.845, stats_line, ha='center', fontsize=10, color='white', fontweight='bold')
            # 0.88 put the axes' top spine ABOVE the stats line, which then sat
            # inside the plotting rectangle - measured 2px above the topmost
            # data marker on the Messi xG panel, and straight through the
            # frame's own top border on the shot-volume panel. The strip branch
            # above already clears it; this one never did.
            return [0, 0.08, 1, 0.825]  # tight_layout rect without strip

    # ============ Chart 1: Rolling xG/90 vs Goals/90 with shading ============
    fig1, ax1 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig1.patch.set_facecolor(BG_COLOR)
    ax1.set_facecolor(BG_COLOR)

    fill_signed(ax1, match_nums, goals_rolling, color_goals, color_xg,
                baseline=xg_rolling, alpha=0.36, alpha_negative=0.36)

    ax1.plot(match_nums, s['xg_partial'], color=color_xg, linewidth=2.4,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, s['goals_partial'], color=color_goals, linewidth=2.4,
             **PARTIAL_STYLE)
    ax1.plot(match_nums, xg_rolling, color=color_xg, linewidth=3, label='xG/90')
    ax1.plot(match_nums, goals_rolling, color=color_goals, linewidth=3, label='Goals/90')
    ax1.set_ylim(*_limits_with_partial(
        [xg_rolling, goals_rolling], [s['xg_partial'], s['goals_partial']]))

    ax1.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax1.set_ylabel('PER 90 MINUTES', fontsize=L['axis'], fontweight='bold', color='white')
    ax1.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=2)
    style_axis(ax1)
    ax1.tick_params(labelsize=L['tick'])
    _integer_match_axis(ax1, len(match_nums))
    _mark_window_start(ax1, window, len(match_nums), fontsize=L['tick'],
                       avoid=[v for v in s['xg_partial'][:window]])
    draw_season_boundaries(ax1, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax1, s['club_changes'], L['tick'])

    layout_rect = add_info_strip_to_figure(fig1, f'GOALS/90 vs xG/90  •  {window}-GAME ROLLING', title_base, subtitle)
    add_cbs_footer(fig1, y=footer_y(fig1))

    plt.tight_layout(rect=layout_rect)
    path1 = os.path.join(output_folder, "player_goals_vs_xg_rolling.png")
    plt.savefig(path1, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"Saved: {path1}")
    plt.close()

    # ============ Chart 2: xG Per 90 Trend ============
    fig2, ax2 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig2.patch.set_facecolor(BG_COLOR)
    ax2.set_facecolor(BG_COLOR)

    ax2.scatter(match_nums, xg_per_match, color=color_xg, alpha=0.75, s=56,
                zorder=2, label='xG this match')
    ax2.plot(match_nums, s['xg_match_partial'], color=color_xg, linewidth=2.4,
             zorder=3, **PARTIAL_STYLE)
    ax2.plot(match_nums, s['xg_match_rolling'], color=color_xg, linewidth=3,
             label=f'{window}-match average', zorder=4)
    ax2.axhline(y=s['avg_xg_match'], color='white', linestyle='--',
                linewidth=1.5, alpha=0.7,
                label=f"Average: {s['avg_xg_match']:.2f} per match")
    ax2.set_ylim(*_limits_with_partial(
        [s['xg_match_rolling'], xg_per_match], [s['xg_match_partial']]))

    ax2.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax2.set_ylabel('xG PER MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax2.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.12), ncol=3)
    style_axis(ax2)
    ax2.tick_params(labelsize=L['tick'])
    _integer_match_axis(ax2, len(match_nums))
    _mark_window_start(ax2, window, len(match_nums), fontsize=L['tick'],
                       avoid=[v for v in s['xg_match_partial'][:window]])
    draw_season_boundaries(ax2, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax2, s['club_changes'], L['tick'])

    layout_rect = add_info_strip_to_figure(fig2, 'xG TREND', title_base, subtitle)
    add_cbs_footer(fig2, y=footer_y(fig2))

    plt.tight_layout(rect=layout_rect)
    path2 = os.path.join(output_folder, "player_xg_per90_trend.png")
    plt.savefig(path2, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"Saved: {path2}")
    plt.close()

    # ============ Chart 3: Shot Volume & Quality ============
    fig3, ax3 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig3.patch.set_facecolor(BG_COLOR)
    ax3.set_facecolor(BG_COLOR)

    # Bars at full strength - see the note in create_rolling_charts.
    ax3.bar(match_nums, shots_values, color=color_goals,
            edgecolor=BG_COLOR, linewidth=0.5, label='Shots (per match)', zorder=2)

    ax3.set_xlabel('MATCH', fontsize=L['axis'], fontweight='bold', color='white')
    ax3.set_ylabel('SHOTS', fontsize=L['axis'], fontweight='bold', color=color_goals)

    # Style primary axis
    ax3.spines['top'].set_visible(False)
    ax3.spines['right'].set_color(color_xg)
    ax3.spines['left'].set_color(SPINE_COLOR)
    ax3.spines['bottom'].set_color(SPINE_COLOR)
    ax3.tick_params(colors=SPINE_COLOR, labelsize=L['tick'])
    ax3.tick_params(axis='y', colors=color_goals)
    _integer_ticks(ax3)
    ax3.set_axisbelow(True)
    ax3.grid(axis='y', color=SPINE_COLOR, linestyle='-', linewidth=0.5, alpha=0.3)

    # Secondary axis for rolling xG per shot
    ax3b = ax3.twinx()
    ax3b.plot(match_nums, s['xg_per_shot_partial'], color=color_xg,
              linewidth=2.4, zorder=3, **PARTIAL_STYLE)
    ax3b.plot(match_nums, xg_per_shot_rolling, color=color_xg, linewidth=3,
              label=f'xG/Shot ({window}-game rolling)', zorder=4)
    ax3b.set_ylim(*_limits_with_partial(
        [xg_per_shot_rolling], [s['xg_per_shot_partial']], floor_zero=True))
    ax3b.set_ylabel('xG PER SHOT', fontsize=L['axis'], fontweight='bold', color=color_xg)
    ax3b.tick_params(axis='y', labelcolor=color_xg, labelsize=L['tick'])
    ax3b.spines['right'].set_color(color_xg)
    ax3b.tick_params(axis='x', which='both', bottom=False, labelbottom=False)

    # Combined legend below x-axis
    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3b.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper center',
               fontsize=L['legend'], facecolor=BG_COLOR, edgecolor=SPINE_COLOR,
               labelcolor='white', bbox_to_anchor=(0.5, -0.12), ncol=2)
    _integer_match_axis(ax3, len(match_nums))
    ax3b.set_xlim(*ax3.get_xlim())
    _mark_window_start(ax3, window, len(match_nums), fontsize=L['tick'],
                       avoid=[float(v) for v in shots_values[:window]])
    draw_season_boundaries(ax3, season_segments, y_pos='top',
                           fontsize=L['tick'], empty_from=empty_from)
    _draw_club_changes(ax3, s['club_changes'], L['tick'])

    layout_rect = add_info_strip_to_figure(fig3, 'SHOT VOLUME & QUALITY', title_base, subtitle)
    add_cbs_footer(fig3, y=footer_y(fig3))

    plt.tight_layout(rect=layout_rect)
    path3 = os.path.join(output_folder, "player_shot_volume_quality.png")
    plt.savefig(path3, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"Saved: {path3}")
    plt.close()

    # ============ Chart 4: Last 10 Matches vs Season Average ============
    fig4, ax4 = plt.subplots(figsize=BROADCAST_FIGSIZE)
    fig4.patch.set_facecolor(BG_COLOR)
    ax4.set_facecolor(BG_COLOR)

    n_shown = _draw_last_n_panel(ax4, matches, s, color_xg, color_goals, L)
    ax4.set_xlabel('OPPONENT (MINUTES PLAYED)', fontsize=L['axis'],
                   fontweight='bold', color='white')
    ax4.legend(loc='upper center', fontsize=L['legend'], facecolor=BG_COLOR,
               edgecolor=SPINE_COLOR, labelcolor='white',
               bbox_to_anchor=(0.5, -0.19), ncol=2)

    layout_rect = add_info_strip_to_figure(
        fig4, f'LAST {n_shown} MATCHES  •  vs HIS AVERAGE', title_base, subtitle)
    add_cbs_footer(fig4, y=footer_y(fig4))

    plt.tight_layout(rect=layout_rect)
    path4 = os.path.join(output_folder, "player_last10_vs_avg.png")
    plt.savefig(path4, dpi=300, facecolor=BG_COLOR, edgecolor='none')
    print(f"Saved: {path4}")
    plt.close()


# ---------------------------------------------------------------------------
# Aspect variants - the GOALS/90 vs xG/90 view at 9:8 and 9:16
#
# One panel, not the four-panel dashboard: a 2x2 grid at 9:16 gives four panels
# none of which is readable. This is the panel that carries the player on its
# own, and it is the one two cold designers picked out - "the best idea in the
# set", "you can read the story from six feet away".
#
# TYPE FLOOR. Both frames are 9in wide delivered at 1080px, so 1pt = 1.667px
# and the 26px phone floor is 15.6pt. Nothing readable sits below 16pt. The 9:8
# tile takes the same floor: it plays as HALF a phone short and cannot buy
# legibility by being wider.
# ---------------------------------------------------------------------------

_PLAYER_LAYOUT_9X16 = {
    'figsize': (9, 16), 'dpi': 120,          # -> 1080 x 1920
    'kicker_size': 17, 'title_size': 46,
    'charttype_size': 21, 'subtitle_size': 16,
    'stat_label_size': 17, 'stat_val_size': 44, 'stat_sub_size': 16,
    'stat_delta_size': 26,
    'axes_left': 0.100, 'axes_bottom': 0.085, 'axes_width': 0.848,
    'plot_gap': 0.030, 'tick_size': 16,
    'line_w_xg': 3.6, 'line_w_goals': 3.2, 'endpoint_ms': 8,
}
_PLAYER_LAYOUT_9X8 = {
    'figsize': (9, 8), 'dpi': 120,           # -> 1080 x 960
    'kicker_size': 16, 'title_size': 34,
    'charttype_size': 16, 'subtitle_size': 16,
    'stat_label_size': 16, 'stat_val_size': 30, 'stat_sub_size': 16,
    'stat_delta_size': 22,
    'axes_left': 0.104, 'axes_bottom': 0.140, 'axes_width': 0.840,
    'plot_gap': 0.026, 'tick_size': 16,
    'line_w_xg': 2.6, 'line_w_goals': 2.3, 'endpoint_ms': 6,
}
_PLAYER_LAYOUTS = {'9x16': _PLAYER_LAYOUT_9X16, '9x8': _PLAYER_LAYOUT_9X8}


def _player_aspect_ylim(*series):
    """Per-chart limits. NO shared ceiling, unlike the team variants.

    The team charts share a 3.0 ceiling so two clubs can be read against each
    other, and that works because every team's xG per game sits in a narrow
    band - measured p50 1.90 against a 3.44 max.

    The player population is not like that. Measured over 2,987 chartable
    players on a full window, the peak of either series is p50 0.31, p90 0.95,
    p99 1.76, max 3.53 - goalkeepers to strikers in one distribution. A shared
    ceiling low enough to give the median chart a usable frame clips the top:
    1.0 still clips 8.8% AND leaves the median peak using 31% of the frame,
    while 2.0 clips 0.5% and leaves it 15%. There is no value that buys
    comparability without spending most of the canvas on nothing, so the
    comparability is not bought.
    """
    vals = [v for s in series for v in s if not np.isnan(v)]
    peak = max(vals) if vals else 0.0
    return 0.0, max(float(np.ceil(peak * 1.08 * 20) / 20), 0.10)


def create_aspect_chart(matches, player_name, team_name, team_color,
                        output_path, window=10, aspect='9x16',
                        custom_title=None, custom_subtitle=None):
    """The GOALS/90 vs xG/90 view, rendered for a phone-shaped frame."""
    if aspect not in _PLAYER_LAYOUTS:
        raise ValueError(f'aspect must be one of {sorted(_PLAYER_LAYOUTS)}')
    L = _PLAYER_LAYOUTS[aspect]

    if not team_color:
        team_color = get_team_color(team_name)
    s = _series(matches, window)
    n = s['match_nums']
    roll_xg, roll_goals = s['xg_rolling'], s['goals_rolling']

    color_xg = ensure_contrast_with_background(team_color)
    color_goals = get_contrast_color(team_color)

    fig = plt.figure(figsize=L['figsize'], dpi=L['dpi'])
    fig.patch.set_facecolor(BG_COLOR)

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

    # The CLUB is the kicker and the PLAYER is the title. On the dashboard the
    # two share one line, which a phone frame cannot hold at title size.
    club_line = team_name.upper()
    if len(s['club_changes']) > 1:
        club_line = f"{club_line}  ·  {len(s['club_changes'])} CLUBS"
    put(club_line, L['kicker_size'], TEXT_MUTED)

    lines = _wrap_title((custom_title or player_name).upper())
    t_size = L['title_size'] if len(lines) == 1 else L['title_size'] * 0.66
    objs = [put(ln, t_size, TEXT_PRIMARY, gap=0.011 if i == 0 else 0.0)
            for i, ln in enumerate(lines)]
    fig.canvas.draw()
    boxes = [o.get_window_extent().transformed(fig.transFigure.inverted())
             for o in objs]
    widest = max(max(b.width for b in boxes), 0.12)
    bar_y = boxes[-1].y0 - 0.006
    fig.patches.append(Rectangle((mid - widest / 2, bar_y), widest, 0.0045,
                                 transform=fig.transFigure, facecolor=color_xg,
                                 edgecolor='none', zorder=5))
    cur = min(cur, bar_y)

    put('GOALS vs EXPECTED GOALS', L['charttype_size'], TEXT_SECONDARY,
        gap=0.015)
    # The dotted convention is NOT repeated here, unlike on the dashboard.
    # It was tried and reverted: the compact two-competition caption plus the
    # note overflowed the frame (the lint caught it clipping past x=1.015 on
    # Guirassy), and it is not needed at this size anyway - the in-plot
    # "DOTTED: UNDER 10 GAMES" caption is set at the 16pt phone floor here,
    # where on the dashboard it is 10pt. The defect the note fixes is
    # size-specific, so the fix is too.
    sub = put(custom_subtitle or
              f"{s['season_text']}  ·  TRAILING {window}-GAME AVERAGE",
              L['subtitle_size'], TEXT_MUTED, weight='normal', gap=0.005)
    # The named-competitions caption can outgrow the frame and 16pt is the
    # phone floor, so it cannot shrink to fit. Measure, then fall back.
    fig.canvas.draw()
    if sub.get_window_extent().width / (L['figsize'][0] * L['dpi']) > 0.94:
        sub.set_text(f"{format_season_text(s['segments'], compact=True)}"
                     f"  ·  {window}-GAME AVERAGE")

    x0 = L['axes_left']
    x1 = L['axes_left'] + L['axes_width']
    latest_xg = next(v for v in reversed(roll_xg) if not np.isnan(v))
    latest_goals = next(v for v in reversed(roll_goals) if not np.isnan(v))
    mins = sum(s['minutes']) or 1
    season_xg = sum(s['xg']) / mins * 90
    season_goals = sum(s['goals']) / mins * 90

    lab_y = cur - 0.040
    val_y = lab_y - (L['stat_val_size'] * 1.16 / fig_h_pt) * 0.78
    sub_y = val_y - (L['stat_val_size'] * 1.16 / fig_h_pt) * 0.60
    sw = 0.055
    for x, ha, label, val, savg, col in (
            (x0, 'left', f'xG/90 · LAST {window}', latest_xg, season_xg, color_xg),
            (x1, 'right', f'GOALS/90 · LAST {window}', latest_goals,
             season_goals, color_goals)):
        sx = (x, x + sw) if ha == 'left' else (x - sw, x)
        fig.add_artist(Line2D(sx, (lab_y, lab_y), color=col, linewidth=3.0,
                              transform=fig.transFigure))
        tx = x + sw + 0.016 if ha == 'left' else x - sw - 0.016
        fig.text(tx, lab_y, label, ha=ha, va='center', color=col,
                 fontsize=L['stat_label_size'], fontweight='bold')
        num = fig.text(x, val_y, f'{val:.2f}', ha=ha, va='center', color=col,
                       fontsize=L['stat_val_size'], fontweight='bold')
        delta = val - savg
        arrow = '▲' if delta >= 0 else '▼'
        fig.canvas.draw()
        nb = num.get_window_extent().transformed(fig.transFigure.inverted())
        dx = (nb.x1 + 0.014) if ha == 'left' else (nb.x0 - 0.014)
        fig.text(dx, val_y, f'{arrow}{abs(delta):.2f}', ha=ha, va='center',
                 color=col, fontsize=L['stat_delta_size'], fontweight='bold')
        fig.text(x, sub_y, f'SEASON {savg:.2f}', ha=ha, va='center',
                 color=TEXT_MUTED, fontsize=L['stat_sub_size'])

    header_bottom = sub_y - (L['stat_val_size'] * 1.16 / fig_h_pt) * 0.5
    gap = L['plot_gap']
    if len(s['segments']) > 1:
        gap += (L['tick_size'] * 1.16 / fig_h_pt) + (9.0 / fig_h_pt)
    axes_top = header_bottom - gap
    ax = fig.add_axes([L['axes_left'], L['axes_bottom'], L['axes_width'],
                       axes_top - L['axes_bottom']])
    ax.set_facecolor(BG_COLOR)

    fill_signed(ax, n, roll_goals, color_goals, color_xg, baseline=roll_xg,
                alpha=0.36, alpha_negative=0.36)
    ax.plot(n, s['xg_partial'], color=color_xg, lw=L['line_w_xg'] * 0.7,
            zorder=3, **PARTIAL_STYLE)
    ax.plot(n, s['goals_partial'], color=color_goals,
            lw=L['line_w_goals'] * 0.7, zorder=3, **PARTIAL_STYLE)
    ax.plot(n, roll_xg, color=color_xg, lw=L['line_w_xg'], zorder=4)
    ax.plot(n, roll_goals, color=color_goals, lw=L['line_w_goals'], zorder=4)

    style_axis(ax)
    ax.yaxis.grid(True, linestyle='--', alpha=0.55, color=GRID_COLOR)
    _integer_match_axis(ax, len(n))
    ax.set_ylim(*_player_aspect_ylim(roll_xg, roll_goals, s['xg_partial'],
                                     s['goals_partial']))
    _mark_window_start(ax, window, len(n), fontsize=L['tick_size'],
                       avoid=[v for ser in (s['xg_partial'], s['goals_partial'])
                              for v in ser if not np.isnan(v)])
    ax.set_xlabel('MATCH', fontsize=L['tick_size'], fontweight='bold',
                  color=TEXT_PRIMARY)
    ax.set_ylabel('PER 90 MINUTES', fontsize=L['tick_size'], fontweight='bold',
                  color=TEXT_SECONDARY)
    ax.tick_params(labelsize=L['tick_size'], labelcolor=TEXT_MUTED)
    draw_season_boundaries(ax, s['segments'], y_pos='top',
                           fontsize=L['tick_size'], show_count=True, label_pad=7,
                           empty_from=_first_undrawn_match(
                               s['segments'], MIN_LEAD_IN_SAMPLES))
    _draw_club_changes(ax, s['club_changes'], L['tick_size'])

    for series, col in ((roll_xg, color_xg), (roll_goals, color_goals)):
        drawn = [i for i, v in enumerate(series) if not np.isnan(v)]
        if drawn:
            ax.plot([n[drawn[-1]]], [series[drawn[-1]]], 'o', color=col,
                    ms=L['endpoint_ms'], zorder=6)

    add_cbs_footer(fig, y=footer_y(fig))
    plt.savefig(output_path, dpi=L['dpi'], facecolor=BG_COLOR, edgecolor='none')
    print(f'Saved: {output_path}')
    plt.close(fig)


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
