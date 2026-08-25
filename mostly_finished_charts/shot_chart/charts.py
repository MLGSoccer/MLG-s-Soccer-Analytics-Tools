"""High-level chart assembly: title, subtitles, legend, stats row, pitch,
stats card, footer. Produces the final figure objects.

Depends on:
    .colors   — color math for contrast + brand preservation
    .data     — loading, reconciliation, highlight classification, types
    .drawing  — low-level marker drawing
"""
import os
from typing import NamedTuple

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from mplsoccer import Pitch, VerticalPitch

from shared.colors import TEAM_COLORS, fuzzy_match_team
from shared.styles import (
    BG_COLOR, CBS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, add_cbs_footer,
    fit_fontsize, render_two_team_score_header, resolve_figsize,
)

from .colors import (
    PITCH_COLOR, check_bg_contrast, ensure_bg_readable, ensure_pitch_contrast,
)
from .data import (
    GOAL_TYPES, classify_highlight, compute_highlight_stats, compute_pen_stats,
    detect_csv_mode, load_multi_match_shot_data, load_shot_data, reconcile_team_goals,
)
from .drawing import (
    compute_ylim_floor, plot_shots_horizontal, plot_shots_vertical,
)


# Displayed height/width of a drawn mplsoccer pitch — measured off the axes
# rather than derived, because mplsoccer pins the aspect and any layout that
# guesses it either letterboxes or overflows. A vertical half pitch cropped to
# floor F is (HALF_PITCH_TOP - F) * HALF_PITCH_ASPECT / HALF_PITCH_XSPAN.
HALF_PITCH_ASPECT = 1.5441
HALF_PITCH_TOP    = 101.943
HALF_PITCH_XSPAN  = 102.0
FULL_PITCH_ASPECT = 0.6359   # horizontal full pitch, pads 3/3/1/1

_TEAM_SHOT_LAYOUT_DEFAULT = {
    'axes_position':  None,
    'tight_rect':     [0.02, 0.14, 0.98, 0.84],
    'crop_floor':     60,     'title_mode':    'full',
    'bar_gap':        None,   'line2_size':    None, 'line2_gap': None,
    'block':          False,
    'title_y':        0.97,  'title_size':    20,
    'bar_y':          0.933, 'bar_height':    0.005,
    'subtitle_y':     0.91,  'subtitle_size': 11,
    'legend_y':       0.89,  'legend_size':   10,
    'stat_val_y':     0.095, 'stat_val_size': 26,
    'stat_label_y':   0.055, 'stat_label_size': 10,
    'stat_xs':        (0.30, 0.50, 0.70),
    'extras_x_goals': 0.715, 'extras_x_xg':   0.545,
    'extras_size':    14,
    'highlight_y':    0.025, 'highlight_size': 10,
    'caption_y':      0.01,  'caption_size':   8,
}

# 9:16 fullscreen overlay. The half pitch is pinned to ~1.54:1, so at 9in
# wide it can only be 5.6in tall — a third of this frame. Portrait therefore
# gets a second block (see the note above _block_rows_by_player), and the
# header band gets a real hierarchy rather than one undersized subtitle line:
# that band is 2.9in tall, and a single line inks 13% of it. Moving "SHOT MAP"
# off the title frees the title to be the subject alone — shorter, so it can
# be 40pt — with the result on its own line beneath. Inked: ~45%.
_TEAM_SHOT_LAYOUT_9X16 = {
    'axes_position':  [0.02, 0.4450, 0.96, 0.2950],
    'tight_rect':     None,
    'crop_floor':     68,
    'title_y':        0.9330, 'title_size':    40, 'title_mode': 'name',
    'bar_y':          None,   'bar_height':    0.0048, 'bar_gap': 0.0115,
    'line2_size':     26,     'line2_gap':     0.0290,
    'subtitle_y':     0.8340, 'subtitle_size': 14,
    'legend_y':       0.7900, 'legend_size':   15,
    'block':          True,
    'lead_head_y':    0.3860, 'lead_top':      0.3480, 'lead_bot': 0.1620,
    'lead_n':         5,      'head_size':     13,     'name_size': 18,
    'rank_x':         0.0700, 'name_x':        0.1300,
    'shots_x':        0.7600, 'xg_x':          0.9300,
    'stat_val_y':     0.1000, 'stat_val_size': 38,
    'stat_label_y':   0.0640, 'stat_label_size': 12,
    'stat_xs':        (0.22, 0.50, 0.78),
    'extras_x_goals': 0.815,  'extras_x_xg':   0.555,
    'extras_size':    14,
    'highlight_y':    0.0330, 'highlight_size': 11,
    'caption_y':      0.0130, 'caption_size':   9,
}

# 9:8 tile overlay - chart lives in HALF of a 9:16 short while the host
# fills the other half. The pitch's natural ~0.77 aspect fits this
# almost perfectly (height/width = 0.78 in this layout), so the pitch
# fills ~83% of vertical with no internal dead space. Top + bottom
# bands are intentionally tight; the host carries the verbal context
# so we drop the subtitle, legend, highlight line, and "circle size"
# caption. A `None` y-coordinate means the block is omitted.
_TEAM_SHOT_LAYOUT_9X8 = {
    # Frame: 9 wide x 8 tall. Top band ~6% (title + bar). Pitch axes
    # ~77% (axes aspect 0.77 matches half-pitch natural aspect, no
    # internal dead bands). Bottom band ~11% (stats values + labels
    # with clearance above CBS footer at y=0.01).
    #
    # The height is 0.77 rather than the 0.83 it was: matplotlib centres the
    # aspect-pinned pitch inside whatever box it is given, so a typical crop
    # floor (~60) leaves the box under-filled and the pitch lands identically
    # either way. But a shot struck from a team's own half pins the floor at
    # 50, the pitch then wants the WHOLE box, and at 0.83 its top edge reached
    # 0.940 and ran into the title. Keeping the band's midpoint at 0.525 while
    # shrinking it means the common case is pixel-identical and the extreme
    # case now stops short of the title instead of colliding with it.
    'axes_position':  [0.02, 0.140, 0.96, 0.77],
    'tight_rect':     None,
    'crop_floor':     60,     'title_mode':    'full',
    'bar_gap':        None,   'line2_size':    None, 'line2_gap': None,
    'block':          False,
    'title_y':        0.97,  'title_size':    22,
    # bar_y is the bar's bottom edge. 22pt title in an 8" tall figure
    # extends ~0.038 in figure coords below the title_y; bar must sit
    # below 0.97 - 0.038 = 0.932 to avoid overlapping the text.
    'bar_y':          0.925, 'bar_height':    0.005,
    'subtitle_y':     None,  'subtitle_size': None,
    'legend_y':       None,  'legend_size':   None,
    'stat_val_y':     0.075, 'stat_val_size': 22,
    'stat_label_y':   0.040, 'stat_label_size': 9,
    'stat_xs':        (0.20, 0.50, 0.80),
    'extras_x_goals': 0.84,  'extras_x_xg':   0.555,
    'extras_size':    11,
    'highlight_y':    None,  'highlight_size': None,
    'caption_y':      None,  'caption_size':  None,
}


_TEAM_SHOT_LAYOUTS = {
    'default': _TEAM_SHOT_LAYOUT_DEFAULT,
    '9x16':    _TEAM_SHOT_LAYOUT_9X16,
    '9x8':     _TEAM_SHOT_LAYOUT_9X8,
}


def _marker_key(pinned=0):
    """The marker key line. Names the chevron only when one is on the chart.

    A half pitch cannot show a shot struck from a team's own half, so those
    are pinned to the bottom edge with a chevron (see drawing.EDGE_PIN_PAD).
    The chevron is a convention nobody is born knowing, so it gets named —
    but only when it appears, since on most charts it never does.
    """
    key = 'Circle size = xG'
    if pinned:
        noun = 'shot' if pinned == 1 else 'shots'
        key += f'   ·   ▽ {pinned} {noun} from own half, shown at edge'
    return key


def create_team_shot_chart(shots_df, team_name, team_color, match_info,
                           opponent_name, team_final_score=0, opponent_goals=0,
                           own_goals_for=0, own_goals_against=0,
                           flip_coords=False, competition='',
                           exclude_penalties=False, highlight_mode='All',
                           player_name=None, is_home=True,
                           custom_title=None, custom_subtitle=None,
                           aspect='default'):
    """Create a single team's shot chart using mplsoccer VerticalPitch.

    aspect: 'default' = (12, 9) frame, 16:9-era layout. '9x16' = (9, 16)
    frame with a layout that's native to portrait social: pitch axes
    explicitly sized to fit (no letterbox bands), bigger title and
    stats typography to fill the vertical real estate, tighter top and
    bottom regions to make every band feel deliberate.
    """
    from shared.styles import resolve_figsize

    layout = _TEAM_SHOT_LAYOUTS.get(aspect, _TEAM_SHOT_LAYOUT_DEFAULT)

    pitch = VerticalPitch(
        pitch_type='opta',
        half=True,
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=1.3,
        goal_type='box',
        pad_top=3,
        pad_bottom=0,
        pad_left=1,
        pad_right=1
    )

    fig, ax = plt.subplots(figsize=resolve_figsize(aspect, category='pitch'))
    if layout['axes_position'] is not None:
        ax.set_position(layout['axes_position'])
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Green rectangle for the pitch area (inside the lines)
    # VerticalPitch half=True: y=0-100 (width), x=50-100 (attacking half)
    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Penalty stats from shots_df BEFORE the filter. Use pre-computed stats
    # from match_info if available (CSV path), else derive from unfiltered
    # shots_df (DB path). Ensures labeling is correct regardless of data path.
    pen_map_cached = match_info.get('pen_stats_by_team') or {}
    pen_stats = pen_map_cached.get(team_name) or compute_pen_stats(shots_df)

    # Filter penalties before classifying or plotting
    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    # Classify shots for highlighting
    shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Clip view: dynamic floor crops dead space, expands if a shot lives there
    ax.set_xlim(-1, 101)
    ax.set_ylim(compute_ylim_floor(shots_df, flip_coords=flip_coords,
                                  default_floor=layout['crop_floor']), 103)

    # Plot shots
    pinned = plot_shots_vertical(ax, pitch, shots_df, team_color,
                                 flip_coords=flip_coords,
                                 highlight_mode=highlight_mode)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
    highlight_stats = compute_highlight_stats(shots_df, highlight_mode)

    # Primary title: identifies whose chart this is (team or player)
    shot_map_label = "NON-PENALTY SHOT MAP" if exclude_penalties else "SHOT MAP"
    if custom_title:
        title_text = custom_title
    elif player_name:
        title_text = f"{player_name.upper()} {shot_map_label}"
    else:
        title_text = f"{team_name.upper()} {shot_map_label}"

    # In portrait the title carries the SUBJECT alone and the scope drops to a
    # second line; see the layout note. `line2` is None at every other aspect,
    # where the one-line title has always fitted a 12in frame.
    line2 = None
    if layout['title_mode'] == 'name' and not custom_title:
        title_text = (player_name or team_name).upper()
        line2 = shot_map_label

    title_obj = fig.suptitle(title_text,
                              fontsize=fit_fontsize(fig, title_text,
                                                    layout['title_size'],
                                                    floor=16, max_frac=0.92),
                              fontweight='bold',
                              color=TEXT_PRIMARY, y=layout['title_y'])

    # Team color accent bar matching title width
    fig.canvas.draw()
    title_bbox = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    title_bbox_fig = title_bbox.transformed(fig.transFigure.inverted())
    bar_edge = 'white' if not check_bg_contrast(team_color) else 'none'
    bar_lw = 0.8 if bar_edge == 'white' else 0
    bar_y = (layout['bar_y'] if layout['bar_y'] is not None
             else title_bbox_fig.y0 - layout['bar_gap'])
    fig.patches.append(Rectangle(
        (title_bbox_fig.x0, bar_y), title_bbox_fig.width,
        layout['bar_height'],
        transform=fig.transFigure, facecolor=team_color,
        edgecolor=bar_edge, linewidth=bar_lw, zorder=10
    ))

    if line2 is not None:
        if player_name:
            result = (f"{team_name.upper()} {team_final_score}-{opponent_goals} "
                      f"{opponent_name.upper()}" if is_home else
                      f"{opponent_name.upper()} {opponent_goals}-{team_final_score} "
                      f"{team_name.upper()}")
        else:
            result = f"{team_final_score}-{opponent_goals}  vs  {opponent_name.upper()}"
        fig.text(0.5, bar_y - layout['line2_gap'], result, ha='center',
                 va='center',
                 fontsize=fit_fontsize(fig, result, layout['line2_size'],
                                       floor=15, max_frac=0.90),
                 fontweight='bold', color=TEXT_SECONDARY)

    # Subtitle: match context (score, opponent, competition, date).
    # Skipped entirely when layout['subtitle_y'] is None (e.g. 9:8 tile
    # mode where the host provides the verbal context).
    if layout['subtitle_y'] is None:
        pass
    elif custom_subtitle:
        fig.text(0.5, layout['subtitle_y'], custom_subtitle,
                 ha='center', va='center',
                 fontsize=layout['subtitle_size'], color=TEXT_SECONDARY)
    else:
        # Match line — score from the focal team's perspective
        if player_name:
            # Player chart: show full match matchup so team context is clear
            if is_home:
                match_str = f"{team_name.upper()} {team_final_score}-{opponent_goals} {opponent_name.upper()}"
            else:
                match_str = f"{opponent_name.upper()} {opponent_goals}-{team_final_score} {team_name.upper()}"
        else:
            match_str = f"{team_final_score}-{opponent_goals} vs {opponent_name.upper()}"

        subtitle_parts = [] if line2 is not None else [match_str]
        if line2 is not None:
            subtitle_parts.append(shot_map_label)
        if highlight_mode != 'All':
            subtitle_parts.append(f"{highlight_mode.upper()} SHOTS HIGHLIGHTED")
        if competition:
            subtitle_parts.append(competition.upper())
        if match_info.get('date_formatted'):
            subtitle_parts.append(match_info['date_formatted'])

        sep = '   ·   ' if line2 is not None else ' | '
        fig.text(0.5, layout['subtitle_y'], sep.join(subtitle_parts),
                 ha='center', va='center', fontsize=layout['subtitle_size'],
                 color=TEXT_MUTED if line2 is not None else TEXT_SECONDARY)

    # Legend: shape-only (Goal = star, Shot = circle; size encodes xG
    # qualitatively). Skipped entirely when layout['legend_y'] is None
    # (e.g. 9:8 tile mode where the shape convention is understood).
    if layout['legend_y'] is not None:
        legend_handles = [
            Line2D([0], [0], marker='*', color='none', markerfacecolor=team_color,
                   markeredgecolor='white', markeredgewidth=1, markersize=14, label='Goal'),
            Line2D([0], [0], marker='o', color='none', markerfacecolor=team_color,
                   markeredgecolor='white', markeredgewidth=1, markersize=10,
                   label='Shot'),
        ]
        fig.legend(handles=legend_handles, loc='upper center',
                    bbox_to_anchor=(0.5, layout['legend_y']), ncol=2, frameon=False,
                    fontsize=layout['legend_size'], labelcolor=TEXT_SECONDARY,
                    handletextpad=0.5, columnspacing=3.0)

    if layout['block']:
        _match_block(fig, layout, shots_df, team_color, player_name)

    # Stats row: large numbers with small uppercase labels beneath.
    # Label xG explicitly as "Non-Pen xG" when penalties are filtered, so the
    # number's meaning is unambiguous without needing inline reconciliation.
    xg_label = "Non-Pen xG" if exclude_penalties else "xG"
    x_shots, x_xg, x_goals = layout['stat_xs']
    stat_cols = [
        (x_shots, str(total_shots), "SHOTS"),
        (x_xg, f"{total_xg:.2f}", xg_label),
        (x_goals, str(goals), "GOALS"),
    ]
    for x, val, lbl in stat_cols:
        fig.text(x, layout['stat_val_y'], val, ha='center', va='center',
                 fontsize=layout['stat_val_size'], fontweight='bold',
                 color=TEXT_PRIMARY)
        fig.text(x, layout['stat_label_y'], lbl, ha='center', va='center',
                 fontsize=layout['stat_label_size'], color=TEXT_SECONDARY)

    # Inline modifiers next to the primary numbers at smaller font:
    #   GOALS: "(+N OG)" and/or "(+N pen)" when filter hides pen goals
    #   xG:    "(+X.XX pen)" when filter hides penalty xG (paired with goal pen)
    pen_goals = pen_stats.get('goals', 0)
    pen_xg = pen_stats.get('xg', 0.0)
    show_pen_annotation = exclude_penalties and pen_goals > 0

    goals_extras = []
    # An own goal belongs to the TEAM, not to any player, so it has no place
    # on a player's chart. The page passes the team's count through whether or
    # not a player is selected, which put "(+1 OG)" beside a player's goal
    # tally and implied he had scored one.
    if own_goals_for > 0 and not player_name:
        goals_extras.append(f"+{own_goals_for} OG")
    if show_pen_annotation:
        goals_extras.append(f"+{pen_goals} pen")
    if goals_extras:
        fig.text(layout['extras_x_goals'], layout['stat_val_y'],
                 f"({', '.join(goals_extras)})",
                 ha='left', va='center',
                 fontsize=layout['extras_size'], color=TEXT_SECONDARY)

    if show_pen_annotation:
        fig.text(layout['extras_x_xg'], layout['stat_val_y'],
                 f"(+{pen_xg:.2f} pen)",
                 ha='left', va='center',
                 fontsize=layout['extras_size'], color=TEXT_SECONDARY)

    if highlight_stats and layout['highlight_y'] is not None:
        hl_text = (f"{highlight_mode}:  {highlight_stats['shots']} shots  ·  "
                   f"{highlight_stats['xg']:.2f} xG  ·  {highlight_stats['goals']} goals")
        fig.text(0.5, layout['highlight_y'], hl_text,
                 ha='center', va='center',
                 fontsize=layout['highlight_size'],
                 color=TEXT_SECONDARY, style='italic')

    if layout['tight_rect'] is not None:
        plt.tight_layout(rect=layout['tight_rect'])

    add_cbs_footer(fig)
    if layout['caption_y'] is not None:
        fig.text(0.5, layout['caption_y'], _marker_key(pinned),
                 ha='center', va='bottom',
                 fontsize=layout['caption_size'],
                 color=TEXT_MUTED, style='italic')

    return fig


# Season / multi-match layouts. Same layout-table pattern as the single-match
# chart: a `None` y-coordinate means the block is omitted at that aspect.
_MULTI_LAYOUT_DEFAULT = {
    'axes_position': None, 'tight_rect': [0.02, 0.14, 0.98, 0.84],
    'title_y': 0.97,   'title_size': 20,  'title_mode': 'full',
    'bar_y': 0.933,    'bar_height': 0.005,
    'line2_y': None,   'line2_size': None, 'line2_gap': None,
    'subtitle_y': 0.91, 'subtitle_size': 11,
    'legend_y': 0.89,  'legend_size': 10,
    'block': False,
    'stat_val_y': 0.105, 'stat_val_size': 26,
    'stat_label_y': 0.07, 'stat_label_size': 10,
    'context_y': 0.035, 'context_size': 10,
    'caption_y': 0.012, 'caption_size': 8, 'hl_size': 9,
}

# 9:16. The half pitch fills only ~35% of a portrait frame, so this aspect
# gains a second block; see the note above _block_rows_by_player for why the
# block's CONTENT depends on the subject rather than the aspect.
_MULTI_LAYOUT_9X16 = {
    'axes_position': [0.02, 0.4450, 0.96, 0.2950], 'tight_rect': None,
    'title_y': 0.9330, 'title_size': 40, 'title_mode': 'name',
    'bar_y': None,     'bar_height': 0.0048, 'bar_gap': 0.0115,
    'line2_y': 0.8780, 'line2_size': 26, 'line2_gap': 0.0290,
    'subtitle_y': 0.8340, 'subtitle_size': 14,
    'legend_y': 0.7900, 'legend_size': 15,
    'block': True,
    'lead_head_y': 0.3860, 'lead_top': 0.3480, 'lead_bot': 0.1620,
    'lead_n': 5, 'head_size': 13, 'name_size': 18,
    'rank_x': 0.0700, 'name_x': 0.1300, 'shots_x': 0.7600, 'xg_x': 0.9300,
    'stat_val_y': 0.1000, 'stat_val_size': 38,
    'stat_label_y': 0.0640, 'stat_label_size': 12,
    'context_y': 0.0340, 'context_size': 11,
    'caption_y': 0.0130, 'caption_size': 9, 'hl_size': 10,
}

# 9:8 tile. The half pitch's natural ~0.77 aspect fills this frame almost
# exactly, so no second block is needed — and the host carries the verbal
# context, so the subtitle, legend, per-game context line and marker key all
# drop out, exactly as they do on the single-match tile.
_MULTI_LAYOUT_9X8 = {
    'axes_position': [0.02, 0.140, 0.96, 0.77], 'tight_rect': None,
    'title_y': 0.97,   'title_size': 22, 'title_mode': 'full',
    'bar_y': 0.925,    'bar_height': 0.005,
    'line2_y': None,   'line2_size': None, 'line2_gap': None,
    'subtitle_y': None, 'subtitle_size': None,
    'legend_y': None,  'legend_size': None,
    'block': False,
    'stat_val_y': 0.075, 'stat_val_size': 22,
    'stat_label_y': 0.040, 'stat_label_size': 9,
    'context_y': None, 'context_size': None,
    'caption_y': None, 'caption_size': None, 'hl_size': None,
}

_MULTI_LAYOUTS = {
    'default': _MULTI_LAYOUT_DEFAULT,
    '9x16':    _MULTI_LAYOUT_9X16,
    '9x8':     _MULTI_LAYOUT_9X8,
}


def create_multi_match_shot_chart(shots_df, team_name, team_color, multi_match_info,
                                   competition='', player_name=None,
                                   exclude_penalties=False, highlight_mode='All',
                                   shots_against=False,
                                   custom_title=None, custom_subtitle=None,
                                   minutes=None, aspect='default'):
    """Create a multi-match shot chart for one team or player on a half pitch.

    Marker style:
        - Non-goals: black fill, white edge, circle
        - Goals: team_color fill, white edge, circle
        - Size scaled by xG

    aspect: 'default' (12x9), '9x8' (tile) or '9x16' (fullscreen portrait).
    Only 9:16 carries a second block, because only 9:16 has room it cannot
    fill with the pitch.
    """
    layout = _MULTI_LAYOUTS.get(aspect, _MULTI_LAYOUT_DEFAULT)

    pitch = VerticalPitch(
        pitch_type='opta', half=True, pitch_color='none', line_color='white',
        linewidth=1.3, goal_type='box',
        pad_top=3, pad_bottom=0, pad_left=1, pad_right=1,
    )

    fig, ax = plt.subplots(figsize=resolve_figsize(aspect, category='pitch'))
    if layout['axes_position'] is not None:
        ax.set_position(layout['axes_position'])
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    pitch_rect = Rectangle((0, 50), 100, 50, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Filter penalties before classifying or plotting
    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    # Classify shots for highlighting
    shots_df = classify_highlight(shots_df.copy(), highlight_mode)

    # Clip view: dynamic floor crops dead space, expands if a shot lives there
    ax.set_xlim(-1, 101)
    ax.set_ylim(compute_ylim_floor(shots_df), 103)

    # Plot shots with multi-match marker style (per-row flipping via _needs_flip)
    pinned = plot_shots_vertical(ax, pitch, shots_df, team_color,
                                 marker_style='multi',
                                 highlight_mode=highlight_mode)

    # Calculate stats
    total_shots = len(shots_df)
    total_xg = shots_df['xG'].sum()
    goals = len(shots_df[shots_df['playType'].isin(GOAL_TYPES)])
    total_matches = multi_match_info.get('total_matches', 0)
    shots_per_game = total_shots / total_matches if total_matches > 0 else 0
    highlight_stats = compute_highlight_stats(shots_df, highlight_mode)

    # The season label. multi_match_info['season_span'] is resolved from the
    # seasonIds actually present in the data, which is the only source that is
    # right in every case: these charts are scoped either by an arbitrary set
    # of gameIds or, for a player, by their whole record across clubs and
    # seasons, so there is no single season to look up.
    #
    # The date fallback below is kept for CSV input, which carries no
    # seasonId. It is WRONG for the first half of every split-year season -
    # every Premier League match before January sits in one calendar year, so
    # it reads "2025" until a January fixture lands - and it is correct for
    # calendar-year leagues like MLS, which is why it cannot simply always
    # hyphenate.
    season_str = multi_match_info.get('season_span') or ''
    if not season_str and 'Date' in shots_df.columns:
        dates = pd.to_datetime(shots_df['Date'], errors='coerce').dropna()
        if not dates.empty:
            min_year = dates.min().year
            max_year = dates.max().year
            if min_year == max_year:
                season_str = str(min_year)
            else:
                season_str = f"{min_year}-{str(max_year)[-2:]}"

    # Title and subtitle
    map_label = "SHOTS AGAINST MAP" if shots_against else "SHOT MAP"
    if player_name:
        if shots_against:
            auto_title = f"{player_name.upper()} SHOTS AGAINST {team_name.upper()} {season_str}".strip()
            subtitle_parts = []
        else:
            auto_title = f"{player_name.upper()} {season_str} SHOT MAP".strip()
            subtitle_parts = [team_name.upper()]
    else:
        auto_title = f"{team_name.upper()} {season_str} {map_label}".strip()
        subtitle_parts = []

    # In portrait the title carries the SUBJECT only and the scope moves to a
    # second line. That band is 2.9in tall; one line of type inks 13% of it,
    # and the string is long enough that inflating that one line would wrap.
    # Splitting it lets the name go to 40pt and inks ~45%.
    if layout['title_mode'] == 'name' and not custom_title:
        title_text = (player_name or team_name).upper()
        line2 = ' '.join(p for p in (season_str, map_label) if p).strip()
        if player_name and shots_against:
            line2 = f"SHOTS AGAINST {team_name.upper()} {season_str}".strip()
        elif player_name:
            subtitle_parts = [team_name.upper()]
    else:
        title_text = custom_title or auto_title
        line2 = None

    title_obj = fig.suptitle(
        title_text,
        fontsize=fit_fontsize(fig, title_text, layout['title_size'],
                              floor=14, max_frac=0.92),
        fontweight='bold', color=TEXT_PRIMARY, y=layout['title_y'])

    # Team color accent bar matching title width
    fig.canvas.draw()
    title_bbox = title_obj.get_window_extent(renderer=fig.canvas.get_renderer())
    title_bbox_fig = title_bbox.transformed(fig.transFigure.inverted())
    bar_edge = 'white' if not check_bg_contrast(team_color) else 'none'
    bar_lw = 0.8 if bar_edge == 'white' else 0
    bar_y = (layout['bar_y'] if layout['bar_y'] is not None
             else title_bbox_fig.y0 - layout['bar_gap'])
    fig.patches.append(Rectangle(
        (title_bbox_fig.x0, bar_y), title_bbox_fig.width, layout['bar_height'],
        transform=fig.transFigure, facecolor=team_color,
        edgecolor=bar_edge, linewidth=bar_lw, zorder=10
    ))

    if line2 and layout['line2_y'] is not None:
        # Anchored to the MEASURED bar, not to a constant. suptitle positions
        # from the top of the text, so the bar — which tracks the title's
        # bottom edge — moves whenever fit_fontsize shrinks a long name, and a
        # fixed second line ends up underneath it.
        fig.text(0.5, bar_y - layout['line2_gap'], line2, ha='center', va='center',
                 fontsize=fit_fontsize(fig, line2, layout['line2_size'],
                                       floor=14, max_frac=0.90),
                 fontweight='bold', color=TEXT_SECONDARY)

    if layout['subtitle_y'] is None:
        pass
    elif custom_subtitle:
        fig.text(0.5, layout['subtitle_y'], custom_subtitle, ha='center',
                 va='center', fontsize=layout['subtitle_size'],
                 color=TEXT_SECONDARY)
    else:
        if competition:
            subtitle_parts.append(competition.upper())
        # Match count gives the data scope without duplicating the season label
        # (already in title) or the date range (was redundant for single-season
        # charts). Falls back to the only context line for CSVs with no league.
        if total_matches:
            match_word = 'MATCH' if total_matches == 1 else 'MATCHES'
            subtitle_parts.append(f"{total_matches} {match_word}")
        # highlight stats go in the bottom stats row, not here -- keeps all
        # summary numbers in one place.
        if exclude_penalties:
            subtitle_parts.append('Non-Penalty Shots')

        if subtitle_parts:
            sep = '   ·   ' if layout['title_mode'] == 'name' else ' | '
            fig.text(0.5, layout['subtitle_y'], sep.join(subtitle_parts),
                     ha='center', va='center',
                     fontsize=layout['subtitle_size'],
                     color=TEXT_MUTED if layout['title_mode'] == 'name'
                     else TEXT_SECONDARY)

    # Legend: color-only (Goal = team color, Shot = black)
    if layout['legend_y'] is not None:
        legend_handles = [
            Line2D([0], [0], marker='o', color='none', markerfacecolor=team_color,
                   markeredgecolor='white', markeredgewidth=1, markersize=11, label='Goal'),
            Line2D([0], [0], marker='o', color='none', markerfacecolor='#000000',
                   markeredgecolor='white', markeredgewidth=1, markersize=11,
                   label='Shot'),
        ]
        fig.legend(handles=legend_handles, loc='upper center',
                    bbox_to_anchor=(0.5, layout['legend_y']), ncol=2, frameon=False,
                    fontsize=layout['legend_size'], labelcolor=TEXT_SECONDARY,
                    handletextpad=0.5, columnspacing=3.0)

    if layout['block']:
        _season_block(fig, layout, shots_df, team_color, team_name,
                      player_name, shots_against)

    # Big-number row: per-90 rates for player charts, raw totals for team charts.
    # Smaller context line below gives the sample-size details without competing.
    if minutes and player_name:
        shots_90 = total_shots / minutes * 90
        xg_90 = total_xg / minutes * 90
        goals_90 = goals / minutes * 90
        stat_cols = [
            (0.25, f"{goals_90:.2f}", "GOALS/90"),
            (0.50, f"{shots_90:.2f}", "SHOTS/90"),
            (0.75, f"{xg_90:.2f}", "xG/90"),
        ]
        context_text = (
            f"{goals} goals  ·  {total_shots} shots  ·  {total_xg:.1f} xG  "
            f"·  {total_matches} matches  ·  {minutes} minutes"
        )
    else:
        stat_cols = [
            (0.25, str(goals), "GOALS"),
            (0.50, str(total_shots), "SHOTS"),
            (0.75, f"{total_xg:.1f}", "xG"),
        ]
        goals_per_game = goals / total_matches if total_matches else 0
        xg_per_game = total_xg / total_matches if total_matches else 0
        context_text = (
            f"{total_matches} matches  ·  {goals_per_game:.1f} goals/game  "
            f"·  {shots_per_game:.1f} shots/game  ·  {xg_per_game:.2f} xG/game"
        )

    for x, val, lbl in stat_cols:
        fig.text(x, layout['stat_val_y'], val, ha='center', va='center',
                 fontsize=layout['stat_val_size'], fontweight='bold',
                 color=TEXT_PRIMARY)
        fig.text(x, layout['stat_label_y'], lbl, ha='center', va='center',
                 fontsize=layout['stat_label_size'], color=TEXT_SECONDARY)

    if layout['context_y'] is not None:
        fig.text(0.5, layout['context_y'], context_text, ha='center',
                 va='center', fontsize=layout['context_size'],
                 color=TEXT_SECONDARY)

    # Highlight breakdown sits where the marker key would -- one or the other,
    # not both, so they don't collide. The own-half note is the exception: it
    # describes a mark that is ON the chart, so it has to survive either
    # branch. A season map is where it turns up most, and a season map is the
    # one most likely to be filtered.
    if layout['caption_y'] is not None:
        pin_note = ''
        if pinned:
            noun = 'shot' if pinned == 1 else 'shots'
            pin_note = f"  ·  ▽ {pinned} {noun} from own half"
        if highlight_stats:
            g = highlight_stats['goals']
            goal_word = 'goal' if g == 1 else 'goals'
            shot_word = 'shot' if highlight_stats['shots'] == 1 else 'shots'
            hl_text = (f"{highlight_mode.lower()}:  {g} {goal_word}  ·  "
                       f"{highlight_stats['shots']} {shot_word}  ·  "
                       f"{highlight_stats['xg']:.1f} xG" + pin_note)
            fig.text(0.5, layout['caption_y'], hl_text, ha='center',
                     va='center', fontsize=layout['hl_size'],
                     color=TEXT_SECONDARY, style='italic')
        else:
            fig.text(0.5, layout['caption_y'], _marker_key(pinned),
                     ha='center', va='center', fontsize=layout['caption_size'],
                     color=TEXT_MUTED, style='italic')

    if layout['tight_rect'] is not None:
        plt.tight_layout(rect=layout['tight_rect'])

    add_cbs_footer(fig)

    return fig


# ---------------------------------------------------------------------------
# The 9:16 second block.
#
# A half pitch is pinned to ~1.54:1, so at 9in wide it can only be 5.6in tall —
# 70% of a 9:8 frame but barely a third of a 9:16 one. Portrait therefore needs
# a second block of real content; no amount of respacing fills it. Cropping
# tighter makes the box SHORTER, not fuller.
#
# Which block is a property of the SUBJECT, not the aspect:
#
#   group by PLAYER   team, one match      who drove the attack
#                     team, whole season   the squad's xG leaders
#   group by MATCH    player, whole season that player's biggest games
#                     team season, against the games that leaked most
#   group by SHOT     player, one match    the only granularity left
#
# The per-shot list is a deliberate exception. At team or season level it burns
# rows on repetition and never answers who or when; for one player in one match
# there is nothing below the shot to group by.


def _block_rows_by_player(shots_df, accent, limit):
    """Leaders: one row per shooter, ranked by xG."""
    col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
    if col not in shots_df.columns or shots_df.empty:
        return []
    g = (shots_df.assign(_g=shots_df['playType'].isin(GOAL_TYPES).astype(int))
         .groupby(col, as_index=False)
         .agg(shots=('xG', 'size'), xg=('xG', 'sum'), goals=('_g', 'sum'))
         .sort_values(['xg', 'goals'], ascending=False)
         .head(limit))
    return [{'accent': accent, 'label': str(r[col]).upper(),
             'goals': int(r['goals']), 'v1': str(int(r['shots'])),
             'v2': f"{float(r['xg']):.2f}"}
            for _, r in g.iterrows()]


def _block_rows_by_match(shots_df, accent, limit, team_name):
    """Top matches by xG: one row per game, labelled by opponent.

    The opponent is derived per match rather than stored: `homeTeam` and
    `awayTeam` are on every row, so whichever of the two is not the subject
    team is the opponent. In shots-AGAINST mode the `Team` column holds the
    opponents' values, which is why the subject is passed in explicitly
    instead of being read off the frame.
    """
    if shots_df.empty or '_match_id' not in shots_df.columns:
        return []
    have_teams = {'homeTeam', 'awayTeam'}.issubset(shots_df.columns)
    g = (shots_df.assign(_g=shots_df['playType'].isin(GOAL_TYPES).astype(int))
         .groupby('_match_id', as_index=False)
         .agg(shots=('xG', 'size'), xg=('xG', 'sum'), goals=('_g', 'sum'),
              home=('homeTeam', 'first') if have_teams else ('xG', 'size'),
              away=('awayTeam', 'first') if have_teams else ('xG', 'size'),
              date=('Date', 'first') if 'Date' in shots_df.columns
                   else ('xG', 'size'))
         .sort_values(['xg', 'goals'], ascending=False)
         .head(limit))

    rows = []
    for _, r in g.iterrows():
        if have_teams:
            home_is_subject = str(r['home']) == str(team_name)
            opp = r['away'] if home_is_subject else r['home']
            label = f"{'v' if home_is_subject else '@'} {str(opp).upper()}"
        else:
            label = str(r['_match_id']).upper()
        rows.append({'accent': accent, 'label': label,
                     'goals': int(r['goals']), 'v1': str(int(r['shots'])),
                     'v2': f"{float(r['xg']):.2f}"})
    return rows


def _block_rows_by_shot(shots_df, accent, limit):
    """One row per shot, ranked by xG: minute and outcome."""
    if shots_df.empty:
        return []
    # Ranked by xG to decide WHICH shots make the cut, then re-sorted into
    # time order to display. A MIN column running 12' 89' 46' reads as a bug,
    # and unlike the ranking blocks this one is a match narrative.
    df = shots_df.sort_values('xG', ascending=False).head(limit)
    if 'minute' in df.columns:
        df = df.sort_values('minute', na_position='last')
    rows = []
    for _, r in df.iterrows():
        pt = str(r.get('playType', ''))
        style = str(r.get('ShotPlayStyle', '') or '')
        label = _SHOT_OUTCOME.get(pt, pt.upper() or 'SHOT')
        if style and style.lower() not in ('none', 'nan', 'regularplay'):
            label = f"{label}  ·  {style.upper()}"
        minute = r.get('minute', r.get('gameClock', None))
        rows.append({'accent': accent, 'label': label,
                     'goals': 1 if pt in GOAL_TYPES else 0,
                     'v1': f"{int(minute)}'" if pd.notna(minute) else '—',
                     'v2': f"{float(r['xG']):.2f}"})
    return rows


# One name for one block. It was three — PLAYER LEADERS on a team match,
# LEADING SHOOTERS on a combined match, SQUAD xG LEADERS on a season — for a
# table that is the same table every time. Scope is already stated in the
# title, so the heading does not need to restate it.
_BLOCK_LEADERS = 'LEADING SHOOTERS'

# Goals are stars on a single-match pitch and colour-filled circles on a
# season pitch (marker_style='multi'). The block borrows whichever its own
# pitch uses.
_SEASON_GOAL_GLYPH = '●'

_STAR_RUN_MAX = 3   # see _draw_stat_block

_SHOT_OUTCOME = {
    'Goal': 'GOAL', 'Post': 'WOODWORK', 'AttemptSaved': 'SAVED',
    'Miss': 'OFF TARGET', 'MissedShots': 'OFF TARGET', 'Blocked': 'BLOCKED',
    'ShotBlocked': 'BLOCKED', 'PenaltyGoal': 'PENALTY SCORED',
}


def _draw_stat_block(fig, layout, heading, col_heads, rows,
                     goal_glyph='★'):
    """Render a titled table: accent rule, label, two right-aligned columns.

    Rows are DISTRIBUTED across the band rather than stepped down from its top
    at a fixed pitch. A team can field four shooters as easily as five, and a
    fixed step leaves a hole above whatever sits below the block exactly when
    the list is short.
    """
    if not rows:
        return
    x0 = layout['rank_x'] - 0.015
    head_y, h_size = layout['lead_head_y'], layout['head_size']
    fig.text(x0, head_y, heading, ha='left', va='center', fontsize=h_size,
             fontweight='bold', color=TEXT_MUTED)
    fig.text(layout['shots_x'], head_y, col_heads[0], ha='right', va='center',
             fontsize=h_size, fontweight='bold', color=TEXT_MUTED)
    fig.text(layout['xg_x'], head_y, col_heads[1], ha='right', va='center',
             fontsize=h_size, fontweight='bold', color=TEXT_MUTED)
    fig.patches.append(Rectangle(
        (x0, head_y - 0.014), layout['xg_x'] - x0, 0.0012,
        transform=fig.transFigure, facecolor='#31435A', edgecolor='none',
        zorder=3))

    step = (layout['lead_top'] - layout['lead_bot']) / max(len(rows), 1)
    name_size = layout['name_size']
    for i, row in enumerate(rows):
        y = layout['lead_top'] - step * (i + 0.5)
        scored = row['goals'] > 0
        accent = row['accent']
        weak = not check_bg_contrast(accent)
        fig.patches.append(Rectangle(
            (x0, y - 0.013), 0.006, 0.026, transform=fig.transFigure,
            facecolor=accent, edgecolor='white' if weak else 'none',
            linewidth=0.6 if weak else 0, zorder=4))
        label = row['label']
        size = fit_fontsize(fig, label, name_size, floor=11, bold=scored,
                            max_frac=layout['shots_x'] - layout['name_x'] - 0.09)
        nt = fig.text(layout['name_x'], y, label, ha='left', va='center',
                      fontsize=size, fontweight='bold' if scored else 'normal',
                      color=TEXT_PRIMARY if scored else TEXT_SECONDARY,
                      zorder=4)
        if scored:
            # The glyph is the one the pitch ABOVE uses for a goal, which is
            # not the same across the family: single-match maps draw goals as
            # stars, season maps draw them as colour-filled circles. Hardcoding
            # a star put a mark in the season list that appears nowhere on the
            # season pitch and is named nowhere in its key.
            #
            # One glyph per goal reuses the pitch key's vocabulary and reads
            # instantly at match scale, where nobody scores four. Over a
            # season it does not: a 13-goal run of stars is unreadable, wider
            # than the name it follows, and impossible to count at a glance.
            # Past three, the star becomes a unit and the number does the work.
            n = row['goals']
            mark = (goal_glyph * n if n <= _STAR_RUN_MAX
                    else f"{goal_glyph} {n}")
            fig.canvas.draw()
            nb = nt.get_window_extent(
                renderer=fig.canvas.get_renderer()).transformed(
                    fig.transFigure.inverted())
            fig.text(nb.x1 + 0.012, y, mark, ha='left',
                     va='center', fontsize=13, color=TEXT_SECONDARY, zorder=4)
        fig.text(layout['shots_x'], y, row['v1'], ha='right', va='center',
                 fontsize=name_size - 2,
                 color=TEXT_SECONDARY if scored else TEXT_MUTED, zorder=4)
        fig.text(layout['xg_x'], y, row['v2'], ha='right', va='center',
                 fontsize=name_size, fontweight='bold',
                 color=TEXT_PRIMARY if scored else TEXT_SECONDARY, zorder=4)


def _match_block(fig, layout, shots_df, accent, player_name):
    """Pick and draw the right second block for a SINGLE-MATCH chart."""
    n = layout['lead_n']
    if player_name:
        # The one place a per-shot list is right: for one player in one match
        # there is nothing below the shot to group by, and the map has no time
        # dimension, so the minute is genuinely additive rather than a repeat
        # of what the pitch already shows.
        rows = _block_rows_by_shot(shots_df, accent, n)
        # The heading is a claim about completeness, so it has to know whether
        # it is telling the truth.
        heading = 'EVERY SHOT' if len(rows) >= len(shots_df) else f'TOP {n} CHANCES'
        return _draw_stat_block(fig, layout, heading, ('MIN', 'xG'), rows)
    return _draw_stat_block(fig, layout, _BLOCK_LEADERS, ('SHOTS', 'xG'),
                            _block_rows_by_player(shots_df, accent, n))


def _season_block(fig, layout, shots_df, accent, team_name, player_name,
                  shots_against):
    """Pick and draw the right second block for a SEASON chart."""
    n = layout['lead_n']
    if player_name:
        # A leaderboard of one is nonsense; this player's biggest games are not.
        return _draw_stat_block(
            fig, layout, 'TOP MATCHES BY xG', ('SHOTS', 'xG'),
            _block_rows_by_match(shots_df, accent, n, team_name),
            goal_glyph=_SEASON_GOAL_GLYPH)
    if shots_against:
        # "Squad leaders" here would list opposition players, which across a
        # season is an arbitrary crowd. The games that leaked most is the
        # question a shots-against map is actually being asked.
        return _draw_stat_block(
            fig, layout, 'MOST xG CONCEDED', ('SHOTS', 'xG'),
            _block_rows_by_match(shots_df, accent, n, team_name),
            goal_glyph=_SEASON_GOAL_GLYPH)
    return _draw_stat_block(
        fig, layout, _BLOCK_LEADERS, ('SHOTS', 'xG'),
        _block_rows_by_player(shots_df, accent, n),
        goal_glyph=_SEASON_GOAL_GLYPH)


class _CombinedCtx(NamedTuple):
    """Everything the three combined-chart renderers share.

    The prep — penalty stats before filtering, the filter itself, highlight
    classification, the per-team split and the goal reconciliation — is
    identical whatever shape the output is, and it is the part with the
    subtle rules in it. Doing it once in the dispatcher keeps the three
    renderers about layout only.
    """
    s1: object          # team1 shots, filtered + classified
    s2: object
    name1: str
    name2: str
    color1: str
    color2: str
    flip1: bool         # per-team mirror for the VERTICAL half-pitch panels
    flip2: bool
    goals1: int         # full match score, own goals included
    goals2: int
    shot_goals1: int    # goals attributable to a marker on the map
    shot_goals2: int
    og1: int
    og2: int
    pen1: dict
    pen2: dict
    match_info: dict
    competition: str
    exclude_penalties: bool
    highlight_mode: str
    custom_title: object
    custom_subtitle: object


# 9:16 fullscreen overlay. The horizontal full pitch (0.636 h/w) takes only
# 34% of a portrait frame; the rest carries a head-to-head strip and a
# leading-shooters list. See _combined_portrait for the two rejected shapes.
_COMBINED_LAYOUT_9X16 = {
    'kicker_y':     0.9760, 'kicker_size':   20,
    'subtitle_y':   0.9520, 'subtitle_size': 12,
    'cap_y':        0.9180, 'cap_size':      20, 'cap_max_frac': 0.40,
    'note_size':    11,
    'rule_drop':    0.0140, 'rule_h':        0.0045,
    'pitch_x':      0.0200, 'pitch_w':       0.9600, 'pitch_top': 0.8950,
    'h2h_top':      0.5250, 'h2h_bot':       0.3800,
    'h2h_val_size': 40,     'h2h_lab_size':  13,
    'h2h_x_left':   0.2600, 'h2h_x_right':   0.7400,
    'lead_head_y':  0.3330, 'lead_top':      0.3010, 'lead_bot': 0.0850,
    'lead_n':       5,      'head_size':     12,
    'rank_x':       0.0700, 'name_x':        0.1300,
    'shots_x':      0.7600, 'xg_x':          0.9300, 'name_size': 18,
    'key_y':        0.0330, 'key_size':      12,
}

# 9:8 tile — the horizontal full pitch, unchanged in geometry from the 12x9
# default because its natural 0.636 h/w fills 70% of this frame with no
# letterboxing. Only the typography and the stats block are re-cut, and the
# subtitle, shape legend, highlight line and caption drop out the way they do
# in the single-team tile: the host carries the verbal context.
_COMBINED_LAYOUT_9X8 = {
    'title_size':  21,     'y_title':  0.9430, 'y_bar':   0.9080,
    'pitch_x':     0.0100, 'pitch_w':  0.9800, 'pitch_y': 0.1520,
    'name_y':      0.1200, 'name_size': 11,
    'val_y':       0.0790, 'val_size':  21,
    'lab_y':       0.0450, 'lab_size':  9,
    'centre_x':    (0.255, 0.745), 'col_dx': 0.078,
    'swatch_w':    0.0140, 'swatch_h': 0.0090,
    'extras_size': 10,
}


def _team_swatch_label(fig, x, y, name, color, size, sw, sh, ha='left',
                       gap=0.014):
    """White team name with a brand-colour swatch beside it.

    Running the NAME through ensure_bg_readable turns a black-kit side grey,
    which reads as "muted" next to a vivid opponent and disagrees with the
    accent bar directly above it, where the same team is painted in its raw
    colour with a white edge. So the name stays white and the colour becomes
    a swatch drawn by the accent bar's own rule.
    """
    weak = not check_bg_contrast(color)
    t = fig.text(x, y, name.upper(), ha=ha, va='center', fontsize=size,
                 fontweight='bold', color=TEXT_PRIMARY, zorder=6)
    fig.canvas.draw()
    bb = t.get_window_extent(renderer=fig.canvas.get_renderer()).transformed(
        fig.transFigure.inverted())
    if ha == 'left':
        t.set_x(x + sw + gap)
        sx = x
    else:                                   # centred: re-centre name + swatch
        t.set_x(bb.x0 + (sw + gap) / 2)
        t.set_ha('left')
        sx = bb.x0 - (sw + gap) / 2
    fig.patches.append(Rectangle(
        (sx, y - sh / 2), sw, sh, transform=fig.transFigure, facecolor=color,
        edgecolor='white' if weak else 'none',
        linewidth=0.8 if weak else 0, zorder=6))


def _combined_portrait(c):
    """9:16 fullscreen overlay — horizontal full pitch, stats stacked beneath.

    Two rejected alternatives, kept because the reasoning is not obvious from
    the result:

    A full VERTICAL pitch is what a portrait frame seems to want, and it is
    what shipped first. It leaves the middle ~45% of the image as empty grass
    — the two defensive halves, where by definition no shots happen — with no
    stats block and no legend, so the frame is simultaneously empty and
    uninformative. Banding the head-to-head across the halfway line recovers
    some of that and looks the part, but ~25% is still grass.

    Two stacked CROPPED half pitches remove the dead space entirely. Rejected:
    a full pitch is the industry standard for a two-team shot map and
    deviating from it costs more than the grass does.

    So: the horizontal full pitch, exactly the map the 12x9 chart draws. Its
    0.636 aspect means it only occupies 34% of a portrait frame, which is the
    trade — the pitch stops being the dominant object — but the 55% left over
    is enough for the head-to-head AND a leading-shooters list, and nothing
    has to overlay the pitch to get there.
    """
    layout = _COMBINED_LAYOUT_9X16
    fig = plt.figure(figsize=resolve_figsize('9x16', category='pitch'))
    fig.patch.set_facecolor(BG_COLOR)

    pitch = Pitch(pitch_type='opta', pitch_color='none', line_color='white',
                  linewidth=1.3, goal_type='box',
                  pad_top=1, pad_bottom=1, pad_left=3, pad_right=3)
    pitch_h = layout['pitch_w'] * 9 * FULL_PITCH_ASPECT / 16
    ax = fig.add_axes([layout['pitch_x'], layout['pitch_top'] - pitch_h,
                       layout['pitch_w'], pitch_h])
    ax.set_facecolor(BG_COLOR)
    ax.add_patch(Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0))
    pitch.draw(ax=ax)

    avg1 = c.s1['EventX'].mean() if not c.s1.empty else 50
    avg2 = c.s2['EventX'].mean() if not c.s2.empty else 50
    plot_shots_horizontal(ax, pitch, c.s1, ensure_pitch_contrast(c.color1),
                          flip_x=avg1 > 50, flip_y=True,
                          highlight_mode=c.highlight_mode)
    plot_shots_horizontal(ax, pitch, c.s2, ensure_pitch_contrast(c.color2),
                          flip_x=avg2 < 50, flip_y=False,
                          highlight_mode=c.highlight_mode)

    # Header: what the chart is. The fixture goes on the small context line
    # rather than into a big one-line score header, which at 9in wide clips a
    # third of "Wolverhampton Wanderers 2-2 Brighton and Hove Albion" off both
    # edges. At 12pt it always fits, and the captions below carry the fixture
    # at size anyway.
    shot_map_label = ("NON-PENALTY SHOT MAP" if c.exclude_penalties
                      else "SHOT MAP")
    fig.text(0.5, layout['kicker_y'], c.custom_title or shot_map_label,
             ha='center', va='center', fontsize=layout['kicker_size'],
             fontweight='bold', color=TEXT_SECONDARY)

    if c.custom_subtitle:
        sub = c.custom_subtitle
    else:
        parts = [f"{c.name1.upper()} {c.goals1}-{c.goals2} {c.name2.upper()}"]
        if c.highlight_mode != 'All':
            parts.append(f"{c.highlight_mode.upper()} HIGHLIGHTED")
        if c.competition:
            parts.append(c.competition.upper())
        if c.match_info.get('date_formatted'):
            parts.append(c.match_info['date_formatted'])
        sub = '   ·   '.join(parts)
    fig.text(0.5, layout['subtitle_y'], sub, ha='center', va='center',
             fontsize=fit_fontsize(fig, sub, layout['subtitle_size'], floor=9),
             color=TEXT_MUTED)

    # Captions left and right, matching the halves each side occupies on the
    # pitch directly below. Names stay white with the colour carried by a
    # rule: ensure_bg_readable on the NAME greys out a black-kit side, which
    # reads as "muted" beside a vivid opponent.
    px0, px1 = layout['pitch_x'], layout['pitch_x'] + layout['pitch_w']
    for x, ha, name, score, color, og in (
            (px0, 'left', c.name1, c.goals1, c.color1, c.og1),
            (px1, 'right', c.name2, c.goals2, c.color2, c.og2)):
        label = f"{name.upper()}  {score}"
        size = fit_fontsize(fig, label, layout['cap_size'], floor=13,
                            max_frac=layout['cap_max_frac'])
        t = fig.text(x, layout['cap_y'], label, ha=ha, va='center',
                     fontsize=size, fontweight='bold', color=TEXT_PRIMARY)
        fig.canvas.draw()
        bb = t.get_window_extent(renderer=fig.canvas.get_renderer()).transformed(
            fig.transFigure.inverted())
        # The own-goal qualifier sits INBOARD on the caption line, not below
        # it: the pitch starts immediately under this row, so a second line
        # lands on the grass. Inboard also keeps it pointing at the score it
        # explains rather than drifting to the frame edge.
        if og:
            fig.text(bb.x1 + 0.008 if ha == 'left' else bb.x0 - 0.008,
                     layout['cap_y'], f"incl. {og} OG",
                     ha='left' if ha == 'left' else 'right', va='center',
                     fontsize=layout['note_size'], color=TEXT_MUTED,
                     style='italic')
        weak = not check_bg_contrast(color)
        fig.patches.append(Rectangle(
            (bb.x0, layout['cap_y'] - layout['rule_drop']), bb.width,
            layout['rule_h'], transform=fig.transFigure, facecolor=color,
            edgecolor='white' if weak else 'none',
            linewidth=0.8 if weak else 0, zorder=6))

    # Head-to-head. Volume, then total quality, then quality per attempt.
    # Goals are absent on purpose: they are the outcome, and the outcome is
    # already in both captions above. What is left is how each side created
    # what it created, and the third row is the line that separates "had more
    # of them" from "had better ones".
    xg_label = "NON-PEN xG" if c.exclude_penalties else "xG"

    def _per_shot(sub):
        return f"{sub['xG'].sum() / len(sub):.2f}" if len(sub) else "—"

    rows = [
        (str(len(c.s1)), "SHOTS", str(len(c.s2))),
        (f"{c.s1['xG'].sum():.2f}", xg_label, f"{c.s2['xG'].sum():.2f}"),
        (_per_shot(c.s1), f"{xg_label} / SHOT", _per_shot(c.s2)),
    ]
    step = (layout['h2h_top'] - layout['h2h_bot']) / len(rows)
    for i, (v1, lab, v2) in enumerate(rows):
        y = layout['h2h_top'] - step * (i + 0.5)
        fig.text(layout['h2h_x_left'], y, v1, ha='center', va='center',
                 fontsize=layout['h2h_val_size'], fontweight='bold',
                 color=TEXT_PRIMARY)
        fig.text(layout['h2h_x_right'], y, v2, ha='center', va='center',
                 fontsize=layout['h2h_val_size'], fontweight='bold',
                 color=TEXT_PRIMARY)
        fig.text(0.5, y, lab, ha='center', va='center',
                 fontsize=layout['h2h_lab_size'], color=TEXT_MUTED)

    # Leading shooters across BOTH teams — the block that pays for the space
    # the horizontal pitch does not use. Aggregated per PLAYER: a shot-level
    # list burns rows on repetition and never answers who drove the attack.
    _draw_stat_block(fig, layout, _BLOCK_LEADERS, ('SHOTS', 'xG'),
                     _combined_leader_rows(c, layout['lead_n']))

    _marker_key_row(fig, layout['key_y'], layout['key_size'])
    add_cbs_footer(fig)
    return fig


def _combined_leader_rows(c, limit):
    """Leading shooters across BOTH teams, each row accented by its own team.

    The single-subject builder cannot do this: it has one accent colour, and
    here the accent is what tells you which side a name belongs to. Grouping
    on (shooter, Team) rather than shooter alone also keeps two players with
    the same surname on opposite teams from being merged.
    """
    col = 'shooter' if 'shooter' in c.s1.columns else 'Player'
    both = pd.concat([c.s1, c.s2])
    if both.empty or col not in both.columns:
        return []
    g = (both.assign(_g=both['playType'].isin(GOAL_TYPES).astype(int))
         .groupby([col, 'Team'], as_index=False)
         .agg(shots=('xG', 'size'), xg=('xG', 'sum'), goals=('_g', 'sum'))
         .sort_values(['xg', 'goals'], ascending=False)
         .head(limit))
    return [{'accent': c.color1 if r['Team'] == c.name1 else c.color2,
             'label': str(r[col]).upper(), 'goals': int(r['goals']),
             'v1': str(int(r['shots'])), 'v2': f"{float(r['xg']):.2f}"}
            for _, r in g.iterrows()]


def _marker_key_row(fig, y, size):
    """Marker vocabulary and the size encoding on one line at the foot."""
    fig.text(0.395, y, 'CIRCLE SIZE = xG', ha='right', va='center',
             fontsize=size, color=TEXT_MUTED)
    fig.text(0.485, y, '★', ha='right', va='center',
             fontsize=size + 5, color=TEXT_SECONDARY)
    fig.text(0.500, y, 'GOAL', ha='left', va='center', fontsize=size,
             color=TEXT_MUTED)
    fig.text(0.595, y, '●', ha='right', va='center', fontsize=size,
             color=TEXT_SECONDARY)
    fig.text(0.610, y, 'SHOT', ha='left', va='center', fontsize=size,
             color=TEXT_MUTED)


def _combined_tile(c):
    """9:8 side-by-side tile — horizontal full pitch, compact stats block."""
    layout = _COMBINED_LAYOUT_9X8
    fig = plt.figure(figsize=resolve_figsize('9x8', category='pitch'))
    fig.patch.set_facecolor(BG_COLOR)

    pitch = Pitch(pitch_type='opta', pitch_color='none', line_color='white',
                  linewidth=1.3, goal_type='box',
                  pad_top=1, pad_bottom=1, pad_left=3, pad_right=3)
    pitch_h = layout['pitch_w'] * 9 * FULL_PITCH_ASPECT / 8
    ax = fig.add_axes([layout['pitch_x'], layout['pitch_y'],
                       layout['pitch_w'], pitch_h])
    ax.set_facecolor(BG_COLOR)
    ax.add_patch(Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0))
    pitch.draw(ax=ax)

    # Same half assignment the landscape layout uses: home attacks left, away
    # attacks right, mirroring whichever side was recorded the other way.
    ax1 = c.s1['EventX'].mean() if not c.s1.empty else 50
    ax2 = c.s2['EventX'].mean() if not c.s2.empty else 50
    plot_shots_horizontal(ax, pitch, c.s1, ensure_pitch_contrast(c.color1),
                          flip_x=ax1 > 50, flip_y=True,
                          highlight_mode=c.highlight_mode)
    plot_shots_horizontal(ax, pitch, c.s2, ensure_pitch_contrast(c.color2),
                          flip_x=ax2 < 50, flip_y=False,
                          highlight_mode=c.highlight_mode)

    render_two_team_score_header(
        fig,
        home_name=c.name1, home_score=c.goals1, home_color=c.color1,
        away_name=c.name2, away_score=c.goals2, away_color=c.color2,
        custom_title=c.custom_title,
        # The tile keeps the one-line header the landscape charts use.
        # Stacking it the way 9:16 does would order the teams top to bottom,
        # and everything below this line — the pitch halves, the two stat
        # groups — orders them left to right. Two contradictory orderings in
        # one frame is worse than a smaller title, and at a 21pt nominal the
        # worst fixture in the league only pulls it down to about 16.
        fontsize_title=fit_fontsize(
            fig, c.custom_title or
            f"{c.name1.upper()} {c.goals1}-{c.goals2} {c.name2.upper()}",
            layout['title_size'], floor=14),
        y_title=layout['y_title'], y_bar=layout['y_bar'],
        bar_contrast_edge=True,
    )

    # Stats block. The tile drops the standalone xG score line the landscape
    # layout carries under the title: at this size it is the same two numbers
    # twice, and the band it needs is worth more to the pitch.
    xg_label = "Non-Pen xG" if c.exclude_penalties else "xG"

    def _stats(cx, name, color, sub, goals, og, pen_stats):
        _team_swatch_label(fig, cx, layout['name_y'], name, color,
                           layout['name_size'], layout['swatch_w'],
                           layout['swatch_h'], ha='center', gap=0.010)
        cols = [(cx - layout['col_dx'], str(len(sub)), "SHOTS"),
                (cx, f"{sub['xG'].sum():.2f}", xg_label),
                (cx + layout['col_dx'], str(goals), "GOALS")]
        for x, val, lbl in cols:
            fig.text(x, layout['val_y'], val, ha='center', va='center',
                     fontsize=layout['val_size'], fontweight='bold',
                     color=TEXT_PRIMARY)
            fig.text(x, layout['lab_y'], lbl, ha='center', va='center',
                     fontsize=layout['lab_size'], color=TEXT_MUTED)

        pen_goals = pen_stats.get('goals', 0)
        extras = []
        if og:
            extras.append(f"incl. {og} OG")
        if c.exclude_penalties and pen_goals > 0:
            extras.append(f"+{pen_goals} pen")
        if extras:
            fig.text(cx + layout['col_dx'] + 0.030, layout['val_y'],
                     ', '.join(extras), ha='left', va='center',
                     fontsize=layout['extras_size'], color=TEXT_MUTED,
                     style='italic')

    _stats(layout['centre_x'][0], c.name1, c.color1, c.s1, c.goals1, c.og1,
           c.pen1)
    _stats(layout['centre_x'][1], c.name2, c.color2, c.s2, c.goals2, c.og2,
           c.pen2)

    add_cbs_footer(fig)
    return fig


def _combined_landscape(c):
    """12:9 broadcast frame — both teams on one horizontal full pitch."""
    pitch = Pitch(
        pitch_type='opta',
        pitch_color='none',  # We'll draw the green rectangle manually
        line_color='white',
        linewidth=1.3,
        goal_type='box',
        pad_top=1,
        pad_bottom=1,
        pad_left=3,
        pad_right=3
    )

    fig, ax = plt.subplots(figsize=resolve_figsize('default', category='pitch'))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    pitch_rect = Rectangle((0, 0), 100, 100, facecolor=PITCH_COLOR, zorder=0)
    ax.add_patch(pitch_rect)

    pitch.draw(ax=ax)

    # Per-team flip_x predicate: home -> LEFT (low x), away -> RIGHT (high x).
    # flip_y is the empirical TruMedia home/away cross-pitch convention (see
    # drawing.py for the historical reason these values were chosen).
    team1_avg_x = c.s1['EventX'].mean() if not c.s1.empty else 50
    team2_avg_x = c.s2['EventX'].mean() if not c.s2.empty else 50

    plot_shots_horizontal(ax, pitch, c.s1, c.color1, flip_x=team1_avg_x > 50,
                          flip_y=True, highlight_mode=c.highlight_mode)
    plot_shots_horizontal(ax, pitch, c.s2, c.color2, flip_x=team2_avg_x < 50,
                          flip_y=False, highlight_mode=c.highlight_mode)

    team1_xg = c.s1['xG'].sum()
    team2_xg = c.s2['xG'].sum()

    render_two_team_score_header(
        fig,
        home_name=c.name1, home_score=c.goals1, home_color=c.color1,
        away_name=c.name2, away_score=c.goals2, away_color=c.color2,
        custom_title=c.custom_title,
        y_title=0.97,
        y_bar=0.933,
        bar_contrast_edge=True,
    )

    # xG sub-line directly under the score. Non-Pen label when penalties filtered.
    xg_subline_label = "Non-Pen xG" if c.exclude_penalties else "xG"
    fig.text(0.5, 0.92, f"{xg_subline_label}  {team1_xg:.2f} — {team2_xg:.2f}",
             ha='center', va='center',
             fontsize=12, fontweight='bold', color=TEXT_SECONDARY)

    team1_hl_stats = compute_highlight_stats(c.s1, c.highlight_mode)
    team2_hl_stats = compute_highlight_stats(c.s2, c.highlight_mode)

    if c.custom_subtitle:
        fig.text(0.5, 0.895, c.custom_subtitle, ha='center', va='center',
                 fontsize=11, color=TEXT_SECONDARY)
    else:
        shot_map_label = "NON-PENALTY SHOT MAP" if c.exclude_penalties else "SHOT MAP"
        subtitle_parts = [shot_map_label]
        if c.highlight_mode != 'All':
            subtitle_parts.append(f"{c.highlight_mode.upper()} SHOTS HIGHLIGHTED")
        if c.competition:
            subtitle_parts.append(c.competition.upper())
        if c.match_info.get('date_formatted'):
            subtitle_parts.append(c.match_info['date_formatted'])

        fig.text(0.5, 0.895, ' | '.join(subtitle_parts),
                 ha='center', va='center', fontsize=11, color=TEXT_SECONDARY)

    # Shape legend only (team colors are communicated by the colored team
    # names in the stats row at the bottom of the chart).
    shape_handles = [
        Line2D([0], [0], marker='*', color='none', markerfacecolor='#888888',
               markeredgecolor='white', markeredgewidth=1, markersize=14, label='Goal'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#888888',
               markeredgecolor='white', markeredgewidth=1, markersize=10,
               label='Shot'),
    ]
    fig.legend(handles=shape_handles, loc='upper center',
                bbox_to_anchor=(0.5, 0.87), ncol=2, frameon=False,
                fontsize=10, labelcolor=TEXT_SECONDARY,
                handletextpad=0.5, columnspacing=3.0)

    # Per-team stat groups: team name header (in team color), then number/label columns.
    # Combined chart's tight columns can't fit inline pen annotations on xG, so
    # the filter status is communicated via the "Non-Pen xG" label.
    xg_label_combined = "Non-Pen xG" if c.exclude_penalties else "xG"

    def _draw_team_stats(cx, name, color, shots_n, xg_val, goals_n, og, pen_stats):
        fig.text(cx, 0.135, name.upper(), ha='center', va='center',
                 fontsize=11, fontweight='bold', color=ensure_bg_readable(color))
        cols = [
            (cx - 0.07, str(shots_n), "SHOTS"),
            (cx,        f"{xg_val:.2f}", xg_label_combined),
            (cx + 0.07, str(goals_n), "GOALS"),
        ]
        for x, val, lbl in cols:
            fig.text(x, 0.095, val, ha='center', va='center',
                     fontsize=22, fontweight='bold', color=TEXT_PRIMARY)
            fig.text(x, 0.06, lbl, ha='center', va='center',
                     fontsize=9, color=TEXT_SECONDARY)

        pen_goals = pen_stats.get('goals', 0)
        show_pen_annotation = c.exclude_penalties and pen_goals > 0

        goals_extras = []
        if og > 0:
            goals_extras.append(f"+{og} OG")
        if show_pen_annotation:
            goals_extras.append(f"+{pen_goals} pen")
        if goals_extras:
            fig.text(cx + 0.082, 0.095, f"({', '.join(goals_extras)})",
                     ha='left', va='center',
                     fontsize=12, color=TEXT_SECONDARY)

    _draw_team_stats(0.24, c.name1, c.color1,
                     len(c.s1), team1_xg, c.shot_goals1, c.og1, c.pen1)
    _draw_team_stats(0.76, c.name2, c.color2,
                     len(c.s2), team2_xg, c.shot_goals2, c.og2, c.pen2)

    if team1_hl_stats and team2_hl_stats:
        hl_text = (f"{c.highlight_mode}:  {c.name1} {team1_hl_stats['shots']}sh · "
                   f"{team1_hl_stats['xg']:.2f}xG · {team1_hl_stats['goals']}g    "
                   f"{c.name2} {team2_hl_stats['shots']}sh · "
                   f"{team2_hl_stats['xg']:.2f}xG · {team2_hl_stats['goals']}g")
        fig.text(0.5, 0.025, hl_text, ha='center', va='center',
                 fontsize=9, color=TEXT_SECONDARY, style='italic')

    plt.tight_layout(rect=[0.02, 0.16, 0.98, 0.84])

    add_cbs_footer(fig)
    fig.text(0.5, 0.01, 'Circle size = xG', ha='center', va='bottom',
             fontsize=8, color=TEXT_MUTED, style='italic')

    return fig


_COMBINED_RENDERERS = {
    'default': _combined_landscape,
    '9x16':    _combined_portrait,
    '9x8':     _combined_tile,
}


def create_combined_shot_chart(shots_df, team1_name, team1_color, team1_flip,
                                team2_name, team2_color, team2_flip,
                                match_info, competition='',
                                exclude_penalties=False, highlight_mode='All',
                                custom_title=None, custom_subtitle=None,
                                aspect='default'):
    """Create a combined shot chart showing both teams in a single match.

    Three genuinely different charts share this entry point, because the
    aspect ratio decides the geometry rather than just the spacing:

    'default'  horizontal Pitch in a 12x9 frame. Home attacks LEFT, away
               attacks RIGHT — the broadcast view.
    '9x8'      the same horizontal pitch re-cut for a side-by-side tile. Its
               natural 0.636 h/w fills 70% of the frame, so the geometry is
               unchanged and only the typography and stats block differ.
    '9x16'     two CROPPED half pitches stacked, both attacking up, with a
               head-to-head strip between them. A full pitch in a portrait
               frame wastes its middle 45% on the two defensive halves.

    team1_flip / team2_flip only apply to '9x16', where each team gets its own
    vertical half pitch; the two horizontal layouts derive their own mirror
    per team from mean shot x.
    """
    # Per-team penalty stats computed BEFORE any filter runs. Prefer the pre-
    # computed match_info['pen_stats_by_team'] (CSV path) if present, else
    # derive from the unfiltered shots_df (DB path).
    pen_map_cached = match_info.get('pen_stats_by_team') or {}
    team1_pen_stats = pen_map_cached.get(team1_name) or compute_pen_stats(
        shots_df[shots_df['Team'] == team1_name])
    team2_pen_stats = pen_map_cached.get(team2_name) or compute_pen_stats(
        shots_df[shots_df['Team'] == team2_name])

    if exclude_penalties:
        if 'ShotPlayStyle' in shots_df.columns:
            shots_df = shots_df[shots_df['ShotPlayStyle'] != 'Penalty'].copy()
        else:
            shots_df = shots_df[shots_df['playType'] != 'PenaltyGoal'].copy()

    team1_shots = classify_highlight(
        shots_df[shots_df['Team'] == team1_name].copy(), highlight_mode)
    team2_shots = classify_highlight(
        shots_df[shots_df['Team'] == team2_name].copy(), highlight_mode)

    team1_goals = match_info.get('home_score', 0)
    team2_goals = match_info.get('away_score', 0)
    t1 = reconcile_team_goals(team1_shots, team1_goals, team1_pen_stats,
                              exclude_penalties)
    t2 = reconcile_team_goals(team2_shots, team2_goals, team2_pen_stats,
                              exclude_penalties)

    ctx = _CombinedCtx(
        s1=team1_shots, s2=team2_shots,
        name1=team1_name, name2=team2_name,
        color1=team1_color, color2=team2_color,
        flip1=team1_flip, flip2=team2_flip,
        goals1=team1_goals, goals2=team2_goals,
        shot_goals1=t1.shot_goals, shot_goals2=t2.shot_goals,
        og1=t1.own_goals, og2=t2.own_goals,
        pen1=team1_pen_stats, pen2=team2_pen_stats,
        match_info=match_info, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode,
        custom_title=custom_title, custom_subtitle=custom_subtitle,
    )
    return _COMBINED_RENDERERS.get(aspect, _combined_landscape)(ctx)


# ---------------------------------------------------------------------------
# Top-level orchestrators — load data, assemble all charts for a match/season.

def create_shot_charts(file_path, output_folder=None, competition='', save=True,
                       exclude_penalties=False, highlight_mode='All'):
    """Main function to create shot charts for both teams in a match.

    Returns:
        list of (fig, filename) tuples
    """
    shots_df, match_info, team_colors = load_shot_data(
        file_path, exclude_penalties=exclude_penalties)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    teams = shots_df['Team'].unique().tolist()
    home_team = match_info['home_team']
    away_team = match_info['away_team']

    def match_team_name(target, team_list):
        for t in team_list:
            if target.lower() in t.lower() or t.lower() in target.lower():
                return t
        return team_list[0] if team_list else target

    team1_name = match_team_name(home_team, teams)
    team2_name = match_team_name(away_team, [t for t in teams if t != team1_name])

    # Resolve team colors with CSV → fuzzy-match → gray fallback chain
    def resolve_color(team_name, team_colors_dict):
        if team_name in team_colors_dict:
            return team_colors_dict[team_name]
        for csv_team, color in team_colors_dict.items():
            if team_name.lower() in csv_team.lower() or csv_team.lower() in team_name.lower():
                return color
        color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
        if color:
            return color
        return '#888888'

    team1_color_raw = resolve_color(team1_name, team_colors)
    team2_color_raw = resolve_color(team2_name, team_colors)

    team1_color = ensure_pitch_contrast(team1_color_raw)
    team2_color = ensure_pitch_contrast(team2_color_raw)

    print(f"\nHome: {team1_name} ({team1_color_raw}"
          + (f" -> {team1_color})" if team1_color != team1_color_raw else ")"))
    print(f"Away: {team2_name} ({team2_color_raw}"
          + (f" -> {team2_color})" if team2_color != team2_color_raw else ")"))

    team1_shots = shots_df[shots_df['Team'] == team1_name]
    team2_shots = shots_df[shots_df['Team'] == team2_name]

    team1_avg_x = team1_shots['EventX'].mean() if not team1_shots.empty else 50
    team2_avg_x = team2_shots['EventX'].mean() if not team2_shots.empty else 50
    team1_flip = team1_avg_x < 50
    team2_flip = team2_avg_x < 50

    print(f"\n{team1_name} avg shot X: {team1_avg_x:.1f} (flip: {team1_flip})")
    print(f"{team2_name} avg shot X: {team2_avg_x:.1f} (flip: {team2_flip})")

    team1_final_score = match_info.get('home_score', 0)
    team2_final_score = match_info.get('away_score', 0)

    # Reconcile each team's shot goals with the scoreline (handles OG + pen)
    pen_map = match_info.get('pen_stats_by_team') or {}
    t1_breakdown = reconcile_team_goals(
        team1_shots, team1_final_score,
        pen_map.get(team1_name) or compute_pen_stats(team1_shots),
        exclude_penalties,
    )
    t2_breakdown = reconcile_team_goals(
        team2_shots, team2_final_score,
        pen_map.get(team2_name) or compute_pen_stats(team2_shots),
        exclude_penalties,
    )
    team1_own_goals = t1_breakdown.own_goals
    team2_own_goals = t2_breakdown.own_goals

    results = []

    print(f"\nCreating shot chart for {team1_name}...")
    fig1 = create_team_shot_chart(
        team1_shots, team1_name, team1_color, match_info,
        team2_name, team_final_score=team1_final_score, opponent_goals=team2_final_score,
        own_goals_for=team1_own_goals, own_goals_against=team2_own_goals,
        flip_coords=team1_flip, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename1 = f"shot_chart_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig1, filename1))

    print(f"Creating shot chart for {team2_name}...")
    fig2 = create_team_shot_chart(
        team2_shots, team2_name, team2_color, match_info,
        team1_name, team_final_score=team2_final_score, opponent_goals=team1_final_score,
        own_goals_for=team2_own_goals, own_goals_against=team1_own_goals,
        flip_coords=team2_flip, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename2 = f"shot_chart_{team2_name.replace(' ', '_')}_vs_{team1_name.replace(' ', '_')}.png"
    results.append((fig2, filename2))

    print("Creating combined shot chart...")
    fig_combined = create_combined_shot_chart(
        shots_df, team1_name, team1_color, team1_flip,
        team2_name, team2_color, team2_flip,
        match_info, competition=competition,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )
    filename_combined = f"shot_chart_combined_{team1_name.replace(' ', '_')}_vs_{team2_name.replace(' ', '_')}.png"
    results.append((fig_combined, filename_combined))

    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, filename in results:
            filepath = os.path.join(output_folder, filename)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                        facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def create_multi_match_charts(file_path, output_folder=None, competition='',
                               player_name=None, save=True, exclude_penalties=False,
                               highlight_mode='All'):
    """Main function to create multi-match shot charts for a single team.

    Returns:
        list of (fig, filename) tuples
    """
    shots_df, multi_match_info, team_color_raw = load_multi_match_shot_data(
        file_path, exclude_penalties=exclude_penalties)

    if shots_df.empty:
        print("No shots found in data!")
        return []

    team_name = multi_match_info['team_name']

    color, _, _ = fuzzy_match_team(team_name, TEAM_COLORS)
    if team_color_raw and team_color_raw != '#888888':
        team_color = ensure_pitch_contrast(team_color_raw)
    elif color:
        team_color = ensure_pitch_contrast(color)
    else:
        team_color = '#888888'

    print(f"Team: {team_name} (color: {team_color})")

    if player_name:
        shooter_col = 'shooter' if 'shooter' in shots_df.columns else 'Player'
        shots_df = shots_df[shots_df[shooter_col] == player_name].copy()
        multi_match_info['total_matches'] = shots_df['_match_id'].nunique()
        print(f"Filtered to {player_name}: {len(shots_df)} shots")

    if shots_df.empty:
        print(f"No shots found for player: {player_name}")
        return []

    results = []

    fig = create_multi_match_shot_chart(
        shots_df, team_name, team_color, multi_match_info,
        competition=competition, player_name=player_name,
        exclude_penalties=exclude_penalties, highlight_mode=highlight_mode
    )

    name_part = team_name.replace(' ', '_')
    if player_name:
        name_part = f"{player_name.replace(' ', '_')}_{name_part}"
    filename = f"shot_map_{name_part}_season.png"
    results.append((fig, filename))

    if save and output_folder:
        os.makedirs(output_folder, exist_ok=True)
        for fig, fn in results:
            filepath = os.path.join(output_folder, fn)
            fig.savefig(filepath, dpi=300, bbox_inches='tight',
                        facecolor=BG_COLOR, edgecolor='none')
            print(f"Saved: {filepath}")

    return results


def run(config):
    """Entry point for launcher/GUI integration.

    Auto-detects single vs multi-match mode using detect_csv_mode().
    """
    file_path = config.get('file_path')
    output_folder = config.get('output_folder', os.path.dirname(file_path))
    competition = config.get('competition', '')
    exclude_penalties = config.get('exclude_penalties', False)
    highlight_mode = config.get('highlight_mode', 'All')
    save = config.get('save', True)

    df = pd.read_csv(file_path)
    mode = detect_csv_mode(df)
    print(f"Detected CSV mode: {mode}")

    if mode == 'multi':
        results = create_multi_match_charts(
            file_path, output_folder, competition,
            save=save, exclude_penalties=exclude_penalties,
            highlight_mode=highlight_mode
        )
    else:
        results = create_shot_charts(
            file_path, output_folder, competition,
            save=save, exclude_penalties=exclude_penalties,
            highlight_mode=highlight_mode
        )

    if not save:
        for fig, _ in results:
            plt.show()

    saved_paths = []
    for fig, fn in results:
        if save and output_folder:
            saved_paths.append(os.path.join(output_folder, fn))
        plt.close(fig)

    print("\nDone!")
    return saved_paths


def main():
    """Standalone entry point — prompts user for inputs."""
    print("\n" + "=" * 60)
    print("CBS SPORTS SHOT CHART BUILDER")
    print("=" * 60)

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
