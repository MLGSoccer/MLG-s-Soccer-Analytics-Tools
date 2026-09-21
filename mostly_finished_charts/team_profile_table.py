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
from matplotlib.lines import Line2D
from mostly_finished_charts.team_profile_chart import (
    _LAYOUTS as _FRAME_LAYOUTS, _header, _text, _ramp, _width_frac, SEP, NP_PREFIX,
)

# The pool word for the kicker: "LIVERPOOL . BIG 5 RANKING". A league is a
# LEAGUE RANKING; a wider pool names itself in two or three words (the
# scope line carries the full label).
_POOL_WORD = {
    "big 5 european leagues": "BIG 5",
    "americas big 4": "AMERICAS BIG 4",
    "big 4 women's leagues": "BIG 4 WOMEN'S",
    "europe + north america": "EUROPE + N AMERICA",
}
TAB_GUTTER = 0.015       # tab to the widest rank numeral, as a fraction of the width
TAB_X = 0.014            # the tab's left edge, as a fraction of the column (at 9:16, of the width)
TYPE_STRETCH_MAX = 1.3   # a short league's type grows with its pitch, up to this

SHOW_MAX = 20            # a POOL wider than SHOW_ALL_UP_TO shows the top twenty + the subject
SHOW_ALL_UP_TO = 30      # a LEAGUE shows every team - the Championship has 24, MLS 30

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
        'x_rank': 0.100, 'x_name': 0.130, 'x_bar0': 0.560, 'x_bar1': 0.850, 'x_value': 0.965,
        'name_size': 15, 'rank_size': 15, 'value_size': 15, 'unit_size': 13,   # the one place the unit is said
        'bar_frac': 0.52,
        'top_gap': 0.062, 'bottom': 0.075, 'name_frac': 0.41, 'tab_x': -0.034,   # the tab sits in the gutter
        'col_gap': 0.045,
    },
}

_SUFFIX = {1: 'st', 2: 'nd', 3: 'rd'}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{_SUFFIX.get(n % 10, 'th')}"


def _rows_to_show(df, subject_pos):
    """Every team of a league; the top SHOW_MAX plus the subject's own row
    for a pool wider than a league can be. Returns (rows, break_before_last).
    The first cut truncated at twenty - a Premier League assumption - and a
    Championship ranking read "TOP 20 OF 24"."""
    if len(df) <= SHOW_ALL_UP_TO:
        return df, False
    top = df.iloc[:SHOW_MAX]
    if subject_pos < SHOW_MAX:
        return top, False
    if subject_pos == SHOW_MAX:          # 21st: nothing omitted, no break
        return pd.concat([top, df.iloc[[subject_pos]]]), False
    return pd.concat([top, df.iloc[[subject_pos]]]), True


def _fit_name(fig, name, size, max_frac):
    """A club name that will not run into its bar.

    Full name at size, then two points smaller; then drop words from the
    END one at a time and try both sizes again - "West Ham United Women" ->
    "West Ham United" -> "West Ham". Never a result ending in "&" or "and",
    and never below two words while there are two to keep. (The first cut
    fell straight back to the first word and the WSL ranking read "West",
    "London", "Manchester".)
    """
    def fits(text, pt):
        return _width_frac(fig, text, pt, 'bold') <= max_frac

    words = name.split(' ')
    for k in range(len(words), 0, -1):
        cand = ' '.join(words[:k])
        if k > 1 and cand.split(' ')[-1].lower() in ('&', 'and', 'de', 'of'):
            continue
        if k < 2 and len(words) >= 2 and k != 1:
            continue
        for pt in (size, size - 2):
            if fits(cand, pt):
                return cand, pt
    return words[0], size - 2


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
    # A women's league: every name ends " Women", and the fit ladder that
    # drops words from the end stripped it from the four longest names
    # only - 8 with, 4 without read as a data error. Drop it from all.
    if len(df) and all(str(n).endswith(' Women') for n in df['display']):
        df['display'] = [str(n)[:-len(' Women')] for n in df['display']]
    pool_n = len(df)
    subject_pos = int(np.where(df['is_subject'].values)[0][0]) if df['is_subject'].any() else -1
    rows, has_break = _rows_to_show(df, subject_pos)

    fig = plt.figure(figsize=L['figsize'], dpi=100, facecolor=BG_COLOR)
    accent = ensure_line_contrast(profile.get('team_color') or '#888888', BG_COLOR)

    # -- header: the STAT is the title; the club is the highlighted row -----
    # The pool's matches, not the subject's: mid-season the teams differ
    # (MLS 23-26) and "25 MATCHES" over a league table read as everyone's.
    gps = cube.teams['gp'].reindex(df.index).dropna().astype(int)
    if len(gps) and gps.min() != gps.max():
        matches = f"{gps.min()}{chr(0x2013)}{gps.max()} MATCHES"
    else:
        gp = int(cube.teams.loc[subject, 'gp']) if subject in cube.index else 0
        matches = f"{gp} MATCHES"
    comp_name = (competition or profile.get('competition') or '').upper()
    years = profile.get('season_years') or ''
    scope = [matches, f"{comp_name} {years}".strip()]
    if pool_n > SHOW_ALL_UP_TO:
        scope = [f"TOP {SHOW_MAX} OF {pool_n}", (profile.get('pool_label') or '').upper()]
    if custom_subtitle:
        scope = [custom_subtitle]
    filter_line = ''                     # the title carries it: NON-PENALTY XG FOR
    label = tp.cell_label(h, situation, component, sep="\n")
    if profile.get('exclude_penalties'):
        label = f"{NP_PREFIX} {label}"       # "Non-Penalty Set-Piece\nGoals Above xG"
    title = custom_title or label.upper()
    # The frame line names the frame this stat was clicked on, so a
    # ranking stands alone: "XG AGAINST . SHOT-STOPPING BREAKDOWN".
    # Provenance - the frame this stat was clicked on - in the note's
    # sentence-case voice beside the definition. Set as a caps line it read
    # as a second subtitle over the scope line beneath it.
    if component != 'anchor' and situation != 'total':
        # a component inside a situation: the level-3 dial a click chose
        provenance = f"from {h.label}: {tp.SITUATION_PHRASE[situation]}"
    elif component != 'anchor':
        provenance = f"from {h.label}: {tp.order_phrase('component', h)}"
    elif situation != 'total':
        provenance = f"from {h.label}: {tp.order_phrase('situation')}"
    else:
        provenance = ''
    frame = []
    note = [spec.meaning or '', provenance]
    # The club in the kicker: the team was a bold row and nothing else, and
    # "LEAGUE RANKING" sat over a 96-club, five-league table.
    pool_label = (profile.get('pool_label') or '').strip()
    pool_word = 'LEAGUE' if mode == 'rank' else _POOL_WORD.get(pool_label.lower(), pool_label.upper())
    team = (profile.get('team_name') or '').upper()
    kicker = f"{team}{SEP}{pool_word} RANKING" if team else f"{pool_word} RANKING"
    bottom = _header(fig, L, kicker=kicker, title=title, accent=accent,
                     scope_parts=scope, frame_line=frame, filter_line=filter_line, note=note)

    # -- rows ---------------------------------------------------------------
    top_y = bottom - T['top_gap']
    bot_y = T['bottom']
    cols = T['cols']
    n_rows = len(rows)
    n_main = n_rows - (1 if has_break else 0)          # the ranked list proper
    per_col = n_main if cols == 1 else math.ceil(n_main / cols)
    rows_in_col = per_col
    # A truncated Big-5 list adds the subject's own row under a 0.6-row
    # gap that holds the break marker - not a whole empty slot.
    BREAK = 0.6
    slots = rows_in_col + ((1 + BREAK) if has_break else 0)
    # A short league keeps the page: rows spread to the foot and the TYPE
    # grows with the pitch (up to 1.3x), so 12 rows are not stretched
    # leading over the 20-row type; a capped pitch left a void instead. A
    # long league (30) keeps the type and tightens the pitch. The bar is
    # capped at 1.5x the name's height either way.
    row_h = (top_y - bot_y) / slots
    T = dict(T)
    scale = min(TYPE_STRETCH_MAX, max(1.0, T['per_col'] / float(rows_in_col))) if rows_in_col else 1.0
    for key in ('name_size', 'rank_size', 'value_size', 'unit_size'):
        T[key] = T[key] * scale
    # Larger type needs a wider name column, or the fit ladder drops words
    # ("Manchester" beside "Manchester City" at 1.3x): the name budget
    # scales with the type and the bars start that much further right.
    name_budget = T['name_frac'] * scale
    T['x_bar0'] = T['x_bar0'] + T['name_frac'] * (scale - 1.0)
    name_px = T['name_size'] * fig.dpi / 72.0
    bar_h = min(row_h * T['bar_frac'], 1.5 * name_px / fig.bbox.height)

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
    # Rank strings as drawn ("=" only where a rank is shared), the widest
    # of them, and a rank column that moves RIGHT to clear the tab and its
    # gutter - measuring "30th=" on every table once put the tab off the
    # left edge of the canvas.
    counts = rows['rank'].value_counts()
    rank_text = {int(k): ('T-' if counts[k] > 1 else '') + _ordinal(int(k)) for k in counts.index}
    widest_rank = max(_width_frac(fig, t, T['rank_size'], 'bold') for t in rank_text.values())
    tab_w = 0.006 if cols == 1 else 0.004
    x_rank_min = TAB_X + tab_w + TAB_GUTTER + widest_rank
    x_rank_shift = max(0.0, x_rank_min - (col_x0[0] + T['x_rank'] * col_w[0]))
    # The name column keeps its gutter from the numerals (the numerals
    # moved 22px for "T-11th" and the names did not: a 5 CSS px gap), and
    # the bars stop a gutter short of the widest printed value.
    widest_value = max(_width_frac(fig, (r['shown'] if isinstance(r.get('shown'), str)
                                         else tp.format_number(spec.fmt, float(r['value']))),
                                   T['value_size'], 'bold')
                       for _, r in rows.iterrows())
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
            # A WHITE tab at the row's edge: the marker says "this row",
            # nothing else. In club colour it read as a verdict - Liverpool's
            # red beside a green bar, and one swatch with the red 94th bar -
            # and the grey fallback read as a stray piece of spine. The club
            # colour lives in the title rule. It clears the widest rank
            # numeral in the table by a fixed gutter ("94th" had 2px).
            # In the first column the tab sits at the canvas edge; in a
            # later column, in the gutter before that column's rank.
            tab_left = TAB_X if c == 0 else (X(T['x_rank']) + x_rank_shift - widest_rank
                                             - TAB_GUTTER - tab_w)
            fig.add_artist(Rectangle((tab_left, y - bar_h * 0.8),
                                     tab_w, bar_h * 1.6, transform=fig.transFigure,
                                     facecolor=TEXT_PRIMARY, edgecolor='none'))

        if is_tail:
            # The break: the omitted ranks named at the name column and a
            # dotted rule across the row - three dots under the names read
            # as "more clubs", not as a 73-place jump.
            yb = y + row_h * (0.5 + BREAK / 2)
            first_omitted = int(rows.iloc[n_rows - 2]['rank']) + 1
            label = (f"{_ordinal(first_omitted)}{chr(0x2013)}{_ordinal(rank - 1)}"
                     if rank - 1 > first_omitted else _ordinal(first_omitted))
            t = _text(fig, X(T['x_name']), yb, label, T['unit_size'], TEXT_MUTED, ha='left')
            fig.canvas.draw()
            tx1 = t.get_window_extent(fig.canvas.get_renderer()).x1 / fig.bbox.width
            fig.add_artist(Line2D([tx1 + 0.012, X(T['x_value'])], [yb, yb], transform=fig.transFigure,
                                  color=TEXT_SECONDARY, linewidth=2.4, linestyle=(0, (1, 2.0))))

        _text(fig, X(T['x_rank']) + x_rank_shift, y, rank_text[rank], T['rank_size'], ink, 'bold',
              ha='right')
        name, nsize = _fit_name(fig, str(r['display']), T['name_size'], name_budget * cw - x_rank_shift)
        _text(fig, X(T['x_name']) + x_rank_shift, y, name, nsize, ink, wt, ha='left')

        bx0 = X(T['x_bar0'])
        bx1 = min(X(T['x_bar1']), X(T['x_value']) - widest_value - 0.045 * cw)   # ~50px at 1080, the siblings' gap
        bw = bx1 - bx0
        if np.isfinite(v):
            if signed:
                mid = bx0 + bw / 2
                length = (abs(v) / span) * (bw / 2)
                left = mid if v >= 0 else mid - length
                fig.add_artist(Rectangle((left, y - bar_h / 2), length, bar_h,
                                         transform=fig.transFigure, facecolor=colour, edgecolor='none'))
                # the zero tick: wide enough to survive the phone (1.6px of 1080 vanished)
                fig.add_artist(Rectangle((mid - 0.0014, y - bar_h * 0.7), 0.0028, bar_h * 1.4,
                                         transform=fig.transFigure, facecolor=TEXT_SECONDARY, edgecolor='none'))
            else:
                length = (v / span) * bw if span else 0
                fig.add_artist(Rectangle((bx0, y - bar_h / 2), max(length, 0), bar_h,
                                         transform=fig.transFigure, facecolor=colour, edgecolor='none'))
        shown = r['shown'] if isinstance(r.get('shown'), str) else tp.format_number(fmt, v)
        _text(fig, X(T['x_value']), y, shown, T['value_size'], ink, 'bold', ha='right')

    # the unit once, as the value column's header, not twenty times
    if spec.unit:
        for c in range(cols):
            _text(fig, col_x0[c] + T['x_value'] * col_w[c], top_y + row_h * 0.15, spec.unit,
                  T['unit_size'], TEXT_MUTED, ha='right', va='bottom')

    add_cbs_footer(fig, x0=m, x1=1.0 - m, y=footer_y(fig, at_least=L.get('footer_y', 0.0)))
    fig.tp_table = rows
    fig.tp_spec = spec
    return fig
