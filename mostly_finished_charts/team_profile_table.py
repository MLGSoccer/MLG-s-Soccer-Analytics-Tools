"""Team Profile - the league ranking: ONE stat, every team in the pool.

The gauges rank one club against its league and throw the other nineteen
rows away. This graphic is those rows: click Liverpool's xG Against, get
the Premier League ranked on xG Against with Liverpool marked third.

ONE stat, not a table. A six-column table twenty deep was measured and
killed: at 9:16 the render is 1080px wide and is delivered at ~400 CSS px,
so eight columns leave ~46 CSS px per stat and ~10 CSS px of type against
a floor of 16. One stat gives the row a rank, a club, a bar and a number,
with type that clears the floor.

The bar is the VALUE - length from zero, or either side of zero for a
signed stat - coloured by the club's standing on the same red-amber-green
ramp the dials use, so the two artifacts read as one family. The subject's
bar keeps that ramp colour too - Liverpool's red IS the ramp's red, and a
3rd-place bar in club colour read as 20th - so the club colour marks the
row from the edge instead: a tab beside the rank, the name in white bold.
The whole point is finding your team in the list.

Aspect logic INVERTS against the gauge frames. Twenty rows fit 9:16
comfortably, so it is the primary here; 16:9 runs two columns of ten; the
9x8 tile cannot hold a league and is not offered.

A pool wider than twenty (the Big 5, ~96 teams) shows the top twenty and,
when the subject sits outside them, its own row below a break with its true
rank - and the scope line says TOP 20 OF 96 so nobody reads a partial list
as the whole.

Reuses the gauge frame's header and furniture directly (same kicker, rule,
frame line, scope, footer) so a ranking and the frame it came from sit
side by side as one house.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from shared.styles import BG_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, add_cbs_footer, footer_y
from shared.colors import ensure_line_contrast
from shared import team_profile as tp
from mostly_finished_charts.team_profile_chart import (
    _LAYOUTS as _FRAME_LAYOUTS, _header, _text, _ramp, _width_frac,
)

SHOW_MAX = 20            # rows on one graphic; a wider pool shows the top twenty + the subject

_LAYOUTS = {
    '9x16': {
        'cols': 1, 'per_col': SHOW_MAX,
        'x_rank': 0.115, 'x_name': 0.145, 'x_bar0': 0.535, 'x_bar1': 0.820, 'x_value': 0.935,
        'name_size': 19, 'rank_size': 19, 'value_size': 19, 'unit_size': 16,
        'bar_frac': 0.56,            # bar height as a fraction of the row
        'top_gap': 0.052, 'bottom': 0.050, 'name_frac': 0.36, 'tab_x': 0.036,
    },
    'default': {
        'cols': 2, 'per_col': 10,
        # per column, in column-fraction terms; the two columns split the width
        'x_rank': 0.092, 'x_name': 0.122, 'x_bar0': 0.560, 'x_bar1': 0.850, 'x_value': 0.965,
        'name_size': 15, 'rank_size': 15, 'value_size': 15, 'unit_size': 11.5,
        'bar_frac': 0.52,
        'top_gap': 0.062, 'bottom': 0.075, 'name_frac': 0.42, 'tab_x': -0.022,   # the tab sits in the gutter
        'col_gap': 0.045,
    },
}

_SUFFIX = {1: 'st', 2: 'nd', 3: 'rd'}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{_SUFFIX.get(n % 10, 'th')}"


def _rows_to_show(df, subject_pos):
    """Top SHOW_MAX plus the subject's own row when it sits outside them.
    Returns (rows DataFrame, break_before_last: bool)."""
    if len(df) <= SHOW_MAX:
        return df, False
    top = df.iloc[:SHOW_MAX]
    if subject_pos < SHOW_MAX:
        return top, False
    return pd.concat([top, df.iloc[[subject_pos]]]), True


def _fit_name(fig, name, size, max_frac):
    """A club name that will not run into its bar: shrink two points, then
    take the first word ('Wolverhampton Wanderers' -> 'Wolverhampton')."""
    if _width_frac(fig, name, size, 'bold') <= max_frac:
        return name, size
    if _width_frac(fig, name, size - 2, 'bold') <= max_frac:
        return name, size - 2
    short = name.split(' ')[0]
    return short, size - 1 if _width_frac(fig, short, size, 'bold') > max_frac else size


def create_league_ranking(profile, *, headline, situation='total', component='anchor',
                          aspect='9x16', competition='', custom_title=None, custom_subtitle=None):
    """The pool ranked on one stat. Returns the figure; save with bbox_inches=None."""
    if aspect not in _LAYOUTS:
        aspect = '9x16'
    L = dict(_FRAME_LAYOUTS[aspect if aspect in _FRAME_LAYOUTS else 'default'])
    T = _LAYOUTS[aspect]
    cube, subject, mode = profile['cube'], profile['subject'], profile['pool_mode']
    h = tp.HEADLINES[headline]

    # The stat's identity comes from the same gauge the frame would draw.
    spec = tp.gauge(cube, subject, headline, situation, component, mode)
    df = tp.league_table(cube, subject, headline, situation, component, mode)
    names = profile.get('pool_names') or {}
    df['display'] = [names.get(ix[1]) or nm for ix, nm in zip(df.index, df['name'])]
    pool_n = len(df)
    subject_pos = int(np.where(df['is_subject'].values)[0][0]) if df['is_subject'].any() else -1
    rows, has_break = _rows_to_show(df, subject_pos)

    fig = plt.figure(figsize=L['figsize'], dpi=100, facecolor=BG_COLOR)
    accent = ensure_line_contrast(profile.get('team_color') or '#888888', BG_COLOR)

    # -- header: the STAT is the title; the club is the highlighted row -----
    gp = int(cube.teams.loc[subject, 'gp']) if subject in cube.index else 0
    comp_name = (competition or profile.get('competition') or '').upper()
    years = profile.get('season_years') or ''
    scope = [f"{gp} MATCHES", f"{comp_name} {years}".strip()]
    if pool_n > SHOW_MAX:
        scope = [f"TOP {SHOW_MAX} OF {pool_n}", (profile.get('pool_label') or '').upper()]
    if custom_subtitle:
        scope = [custom_subtitle]
    filter_line = 'PENALTIES EXCLUDED' if profile.get('exclude_penalties') else ''
    label = spec.label if component != 'anchor' or situation != 'total' else h.label
    if component == 'anchor' and situation != 'total':
        label = tp.situation_label(h, situation)
    title = custom_title or label.upper()
    # The frame line names the frame this stat was clicked on, so a
    # ranking stands alone: "XG AGAINST . SHOT-STOPPING BREAKDOWN".
    if component != 'anchor':
        frame = [h.label.upper(), tp.order_phrase('component', h).upper()]
    elif situation != 'total':
        frame = [h.label.upper(), 'BY GAME SITUATION']
    else:
        frame = []
    note = f"{spec.meaning}" if spec.meaning else ''
    bottom = _header(fig, L, kicker='LEAGUE RANKING', title=title, accent=accent,
                     scope_parts=scope, frame_line=frame, filter_line=filter_line, note=note)

    # -- rows ---------------------------------------------------------------
    top_y = bottom - T['top_gap']
    bot_y = T['bottom']
    cols = T['cols']
    per_col = T['per_col']
    n_rows = len(rows)
    n_main = n_rows - (1 if has_break else 0)          # the ranked list proper
    rows_in_col = n_main if cols == 1 else per_col
    # A truncated Big-5 list adds the subject's own row under a 0.6-row
    # gap that holds the break marker - not a whole empty slot.
    BREAK = 0.6
    slots = rows_in_col + ((1 + BREAK) if has_break else 0)
    row_h = (top_y - bot_y) / slots
    bar_h = row_h * T['bar_frac']

    m = L['margin']
    if cols == 1:
        col_x0, col_w = [0.0], [1.0]
    else:
        gap = T['col_gap']
        w = (1.0 - 2 * m - gap) / 2
        col_x0, col_w = [m, m + w + gap], [w, w]

    vals = rows['value'].astype(float)
    finite = vals[np.isfinite(vals)]
    vmin, vmax = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
    signed = vmin < 0 < vmax or spec.fmt.startswith('signed')
    span = max(abs(vmin), abs(vmax)) or 1.0

    fmt = spec.fmt
    for i, (ix, r) in enumerate(rows.iterrows()):
        is_tail = has_break and i == n_rows - 1
        if is_tail:                      # the subject's row under the break
            c, k_draw = cols - 1, rows_in_col + BREAK
        elif cols == 1:
            c, k_draw = 0, i
        else:
            c, k_draw = divmod(i, per_col)
        y = top_y - (k_draw + 0.5) * row_h
        x0, cw = col_x0[c], col_w[c]
        X = lambda f: x0 + f * cw

        is_subj = bool(r['is_subject'])
        rank = int(r['rank'])
        v = float(r['value'])
        standing = (r['pctl'] / 100.0) if np.isfinite(r['pctl']) else 0.5
        colour = _ramp(standing)                 # honest for every row, the subject's too
        ink = TEXT_PRIMARY if is_subj else TEXT_SECONDARY
        wt = 'bold' if is_subj else 'normal'
        if is_subj:
            # the club-colour tab at the row's edge - the same colour as
            # the title rule, so the row and the header say the same club
            tab_w = 0.006 if cols == 1 else 0.004
            fig.add_artist(Rectangle((X(T['tab_x']), y - bar_h * 0.8),
                                     tab_w, bar_h * 1.6, transform=fig.transFigure,
                                     facecolor=accent, edgecolor='none'))

        if is_tail:
            _text(fig, X(T['x_name']), y + row_h * (0.5 + BREAK / 2), '\u00b7 \u00b7 \u00b7',
                  T['name_size'] * 1.6, TEXT_SECONDARY, 'bold', ha='left')

        _text(fig, X(T['x_rank']), y, _ordinal(rank), T['rank_size'], ink, 'bold', ha='right')
        name, nsize = _fit_name(fig, str(r['display']), T['name_size'], T['name_frac'] * cw)
        _text(fig, X(T['x_name']), y, name, nsize, ink, wt, ha='left')

        bx0, bx1 = X(T['x_bar0']), X(T['x_bar1'])
        bw = bx1 - bx0
        if np.isfinite(v):
            if signed:
                mid = bx0 + bw / 2
                length = (abs(v) / span) * (bw / 2)
                left = mid if v >= 0 else mid - length
                fig.add_artist(Rectangle((left, y - bar_h / 2), length, bar_h,
                                         transform=fig.transFigure, facecolor=colour, edgecolor='none'))
                fig.add_artist(Rectangle((mid - 0.0008, y - bar_h * 0.7), 0.0016, bar_h * 1.4,
                                         transform=fig.transFigure, facecolor=TEXT_MUTED, edgecolor='none'))
            else:
                length = (v / span) * bw if span else 0
                fig.add_artist(Rectangle((bx0, y - bar_h / 2), max(length, 0), bar_h,
                                         transform=fig.transFigure, facecolor=colour, edgecolor='none'))
        _text(fig, X(T['x_value']), y, tp.format_number(fmt, v), T['value_size'], ink, 'bold', ha='right')

    # the unit once, as the value column's header, not twenty times
    if spec.unit:
        for c in range(cols):
            _text(fig, col_x0[c] + T['x_value'] * col_w[c], top_y + row_h * 0.15, spec.unit,
                  T['unit_size'], TEXT_MUTED, ha='right', va='bottom')

    add_cbs_footer(fig, x0=m, x1=1.0 - m, y=footer_y(fig, at_least=L.get('footer_y', 0.0)))
    fig.tp_table = rows
    fig.tp_spec = spec
    return fig
