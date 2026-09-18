"""Team Profile - one team against its league or a wider pool, as six gauges.

Every frame of the family is the same instrument: a header naming the team
and the scope, then six semicircle gauges in a grid. What changes between
frames is which six cells of the cube they show (shared/team_profile.py):

    level 1   the six headlines            GF GA GD xG xGA xGD
    level 2   one headline by situation    Total OP SP Ahead Drawing Behind
              or by component              Anchor Context Shots Quality Finishing PSxG
    level 3   the other dimension, inside the level-2 pick

THE GAUGE. The arc is a STANDING scale - rank within a league, percentile
within a wider pool - because standing is what the user asked the gauge to
rate ("like an MPH gauge ... 3rd/20 ... a percentile"). A steel track is
FILLED from the bad end to the standing in the player-comparison ramp's
colour AT that standing - one colour per gauge, so a row of six says
good-from-bad at a glance (the first build painted the whole ramp on every
ring, identical six times over). The needle sits at the fill's edge; the
readout under the hub says the standing in words ("3rd/20", "78 PCTL") in
the fill's colour; the literal value, its unit and what it was made from
sit beneath, so nobody has to read a number off an arc. The gauge rates
GOODNESS: an against-metric fills green when it is low, and the scope line
declares the convention once ("1ST = BEST" / "HIGHER = BETTER"). Cells with
no good direction (time spent level) fill the track in steel to their
VALUE, show it in the readout position and say their standing in words
beneath ("5th highest of 20"), so they are not read as verdicts.

Every rate is per 90 minutes - played, or spent in the state - ONE time
base across the cube; a Premier League match runs ~101 minutes, so
per-match figures beside per-90 ones misled a cold analyst.

No distribution ticks on the arc, by construction rather than by taste:
on a standing scale every pool's marks are uniformly spaced by definition,
so ticks would say nothing. The pool median under the value is the
reference instead.

Two cold rounds (2026-09-15) shaped the header: a bold FRAME LINE names
what the six gauges are on levels 2-3, against-metrics carry "faced" /
"xGA" in their labels, and the level-3 "Season:" line is the headline's
own standing - the things two readers out of two attached wrongly when
they lived only in the kicker.

Returns the figure; the page saves it with bbox_inches=None (the shape
contract). Layout is a per-aspect dict - adding a variant is adding a key.
"""
from __future__ import annotations

import math

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle, Wedge

from shared.styles import (BG_COLOR, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
                           add_cbs_footer, fit_fontsize, footer_y)
from shared.colors import ensure_line_contrast
from shared import team_profile as tp

# Twin of mostly_finished_charts/player_comparison_chart.py:_PERCENTILE_CMAP -
# the same three stops, so a gauge here and a bar there say the same thing
# with the same colour. Copied rather than imported: that module is 2,300
# lines of player chart and this needs three hex values.
RAMP = LinearSegmentedColormap.from_list('tp_ramp', ['#E63946', '#F4D03F', '#2ECC71'])
# ONE colour per gauge. The arc is a track FILLED from the bad end to the
# standing, in the ramp colour AT the standing - the player-comparison bar
# bent into a semicircle. The first build painted the whole ramp on every
# ring and let the needle do the work; six identical rainbows told the user
# nothing at a glance ("the gauges themselves are all exactly the same").
# Now a good cell is a long green arc and a bad one a red sliver on an empty
# track, and the readout beneath wears the same colour as its fill.
# The ramp's red is the dimmest thing it produces on this ground - 4.1:1
# against 7.9:1 for its green - and is also Liverpool's exact red. Lifted to
# a floor - but not to 5.5, which turned the terminal pink (saturation
# 0.41). 4.6 keeps it an orange-red.
READOUT_MIN_CONTRAST = 4.6
FILL_MIN_CONTRAST = 3.0
# A verdict fill never vanishes: 20th of 20 keeps this many degrees, so the
# worst cell is a red sliver and not the bare track (which read as a graphic
# that had not finished loading).
MIN_FILL_DEG = 5.0
# The empty track, and the fill of a cell with no direction (a share of
# goals, time spent level): steel, so the cell built to abstain is the same
# instrument with the verdict colour withheld.
TRACK = '#354861'
NEUTRAL_RING = '#465C76'
HUB = '#F2F5F8'
SEP = '  \u00b7  '        # escaped, not typed (Streamlit Cloud ImportError trap)

_HAIR = ' '


def track(s, n=1):
    return (_HAIR * n).join(str(s))


# -- Layouts ---------------------------------------------------------------------
# Header numbers follow the pass map's, so the two families sit on one grid.
# Gauge type sizes clear the lint floors: 9.6pt on the 16in laptop frame,
# 15.6pt on the 9in phone frames - which is why the portrait numbers are not
# scaled-down copies of the landscape ones.
_LAYOUTS = {
    'default': {
        'figsize': (16, 9), 'margin': 0.04, 'type_floor': 9.6,
        'kicker_y': 0.969, 'kicker_size': 11.5,
        'title_y': 0.919, 'title_size': 30, 'title_frac': 0.72, 'title_floor': 17,
        'title_lines': 1,
        'bar_h': 0.0075, 'bar_gap': 0.0050,
        # scope_frac 0.80: the Big 5 scope ran 93.5% of the frame at 15px
        # tracked caps over a grid with ~200px side margins. Capped to the
        # grid and packed onto two lines instead.
        'scope_gap': 0.052, 'scope_size': 13, 'scope_track': 1, 'scope_frac': 0.80,
        'scope_lead': 0.030,
        # The subject is the one line that changes frame to frame; at 16pt
        # bold it measured 1px taller than the tracked grey scope under it
        # and sat fourth in reading order behind the six 34px rank numerals.
        'frame_gap': 0.046, 'frame_size': 20,
        'deck_gap': 0.036, 'deck_size': 12.5, 'show_deck': False,
        'cols': 3, 'rows': 2,
        'grid_gap': 0.030, 'grid_bottom': 0.075,
        # The gauge axes inside its cell, as fractions of the cell.
        'cell_pad_x': 0.06, 'cell_pad_y': 0.04,
        'label_size': 13, 'label_track': 1, 'readout_size': 30, 'readout_sub': 0.55,
        # Muted line a point smaller: three 406px muted lines 86px apart on
        # the by-situation frame read as one grey ribbon across the row.
        'value_size': 17, 'unit_size': 11.5, 'total_size': 10.5, 'show_ref': True,
        # ONE dial per aspect, near enough. The grid takes what the header
        # leaves, so the dial shrank 247 -> 179px from the overview to a Big-5
        # level 3 while the type inside the cell did not - three templates,
        # not one drilled into. The gauge box is capped and centred in its
        # cell; the overview's surplus becomes air between rows. Measured:
        # 0.275 holds ~190px on every frame but the tallest-header one (179),
        # a 6% drift, where the level-3 minimum (0.25) would starve the
        # overview to 160px for the last 6%.
        'gauge_h': 0.275,
        'ring_w': 0.30, 'needle_lw': 2.6,
    },
    '9x16': {
        'figsize': (9, 16), 'margin': 0.06, 'type_floor': 16,
        'kicker_y': 0.975, 'kicker_size': 16,
        'title_y': 0.944, 'title_size': 38, 'title_frac': 0.84, 'title_floor': 19,
        'title_lines': 2, 'title_lead_em': 1.25,
        'bar_h': 0.0042, 'bar_gap': 0.0043,
        'scope_gap': 0.0300, 'scope_size': 16, 'scope_track': 0, 'scope_frac': 0.94,
        'scope_lead': 0.0195,
        'frame_gap': 0.0260, 'frame_size': 24,
        'deck_gap': 0.0240, 'deck_size': 16, 'show_deck': False,
        'cols': 2, 'rows': 3,
        'grid_gap': 0.0220, 'grid_bottom': 0.0500,
        'cell_pad_x': 0.05, 'cell_pad_y': 0.03,
        'label_size': 17, 'label_track': 1, 'readout_size': 34, 'readout_sub': 0.55,
        'value_size': 20, 'unit_size': 16, 'total_size': 16, 'show_ref': True,
        # The median on its own line: "63 in 38 - pool median 1.19" at 16pt
        # never fitted a 380px column, so the frame rule was silently
        # dropping it on 27 of 28 sample frames. The 2x3 grid has the height.
        'ref_line': True,
        # The short unit here too: "1.79 per 90 min when behind" at 16pt ran
        # 91% of a 475px column and read as one sentence with its neighbour
        # across a 48px gutter. The subject line names the situation.
        'short_unit': True, 'gauge_h': 1.0,
        'ring_w': 0.30, 'needle_lw': 3.0,
        'footer_y': 0.0175,
    },
    '9x8': {
        'figsize': (9, 8), 'margin': 0.045, 'type_floor': 16,
        'kicker_y': 0.9625, 'kicker_size': 16,
        'title_y': 0.910, 'title_size': 30, 'title_frac': 0.86, 'title_floor': 19,
        'title_lines': 2, 'title_lead_em': 1.25,
        'bar_h': 0.0075, 'bar_gap': 0.0080,
        'scope_gap': 0.0420, 'scope_size': 16, 'scope_track': 0, 'scope_frac': 0.94,
        'scope_lead': 0.0330,
        'frame_gap': 0.0440, 'frame_size': 22,
        # No deck on the tile: the frame line names the level, the host
        # carries the rest, and the header is already four lines deep.
        'deck_gap': 0.0400, 'deck_size': 16, 'show_deck': False,
        'cols': 3, 'rows': 2,
        'grid_gap': 0.0300, 'grid_bottom': 0.0700,
        'cell_pad_x': 0.04, 'cell_pad_y': 0.03,
        # THE TILE SETS ITS LABELS SOLID AND DROPS THE MEDIAN. Three 300px
        # cells at the 16pt floor: tracked, "GOAL DIFFERENCE" ran into its
        # neighbours on both sides, and "63 in 38 - pool median 1.29" is
        # 340px wide. The pass map's phone rule - tracking survives only on
        # labels that own their line - and the tile's own brief (the host
        # carries context) both point the same way. 16:9 and 9:16 keep both.
        'label_size': 16, 'label_track': 0, 'readout_size': 28, 'readout_sub': 0.58,
        'value_size': 18, 'unit_size': 16, 'total_size': 16, 'show_ref': False,
        # The stack had 4px between the value and the total (2px on level 3)
        # while the header took a third of the tile. The lines get leading;
        # the header gave up a scope gap and the deck.
        'short_unit': True, 'gauge_h': 0.280,
        'ring_w': 0.30, 'needle_lw': 2.8,
        'footer_y': 0.0260,
    },
}


# -- Text helpers ----------------------------------------------------------------

def _text(fig, x, y, s, size, color=TEXT_PRIMARY, weight='normal',
          ha='center', va='center', spaced=0, **kw):
    if spaced:
        s = track(s, spaced)
    return fig.text(x, y, s, fontsize=size, color=color, fontweight=weight,
                    ha=ha, va=va, **kw)


def _width_frac(fig, s, size, weight='normal'):
    t = fig.text(0.5, 0.5, s, fontsize=size, fontweight=weight)
    fig.canvas.draw()
    w = t.get_window_extent(fig.canvas.get_renderer()).width / fig.bbox.width
    t.remove()
    return w


def _wrap_words(fig, text, size, max_frac, max_lines, floor, weight='bold'):
    """Break on words into at most `max_lines`, shrinking only if that fails.

    A club name is one to three words and a custom title is whatever was
    typed. Shrink-to-fit alone left "WOLVERHAMPTON WANDERERS v BRIGHTON AND
    HOVE ALBION" off both edges of a 9in frame at the floor; break first.
    """
    words = str(text).split()
    pt = float(size)
    while True:
        lines, cur = [], ''
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and _width_frac(fig, trial, pt, weight) > max_frac:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        if len(lines) <= max_lines and all(_width_frac(fig, ln, pt, weight) <= max_frac for ln in lines):
            return lines, pt
        if pt <= floor:
            return lines[:max_lines], pt
        pt = max(floor, pt - 1.0)


def _low(fig, art):
    return (art.get_window_extent(fig.canvas.get_renderer())
            .transformed(fig.transFigure.inverted()).y0)


def _pack(fig, parts, size, max_frac, spaced):
    """Line packing of scope parts, never splitting a part. One line when it
    fits; otherwise the two-line split that leaves the widest line
    narrowest (a greedy pack left "1ST = BEST" alone on line two under a
    line that ran the whole measure); three or more lines packed greedily."""
    def width(ps):
        text = SEP.join(ps)
        return _width_frac(fig, track(text, spaced) if spaced else text, size)

    if width(parts) <= max_frac or len(parts) < 2:
        return [SEP.join(parts)]
    best = None
    for k in range(1, len(parts)):
        a, b = parts[:k], parts[k:]
        w = max(width(a), width(b))
        if w <= max_frac and (best is None or w < best[0]):
            best = (w, [SEP.join(a), SEP.join(b)])
    if best:
        return best[1]
    lines, cur = [], []
    for part in parts:
        trial = cur + [part]
        if cur and width(trial) > max_frac:
            lines.append(SEP.join(cur))
            cur = [part]
        else:
            cur = trial
    if cur:
        lines.append(SEP.join(cur))
    return lines


def _header(fig, L, *, kicker, title, accent, scope_parts, frame_line, filter_line, deck):
    """Kicker, title, club-colour rule, scope, FRAME LINE, filter line, deck.

    Same furniture as the pass map header, in the same places, so the two
    families read as one house. The frame line is this chart's own: it says,
    at body weight, WHAT the six gauges below are ("POST-SHOT xGA - BY
    SITUATION"). The first build carried that only in the kicker and two
    cold readers out of two attached a level-3 body to the wrong metric.
    Returns the y under the last line drawn.
    """
    _text(fig, 0.5, L['kicker_y'], kicker, L['kicker_size'], TEXT_MUTED, 'bold', spaced=2)

    lines, size = _wrap_words(fig, title, L['title_size'], L['title_frac'],
                              int(L.get('title_lines', 1)), L['title_floor'])
    lead = size * L.get('title_lead_em', 1.25) / (72.0 * L['figsize'][1])
    arts = [_text(fig, 0.5, L['title_y'] - i * lead, ln, size, TEXT_PRIMARY, 'bold')
            for i, ln in enumerate(lines)]
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    inv = fig.transFigure.inverted()
    x0 = min(a.get_window_extent(r).transformed(inv).x0 for a in arts)
    x1 = max(a.get_window_extent(r).transformed(inv).x1 for a in arts)
    ybot = min(a.get_window_extent(r).transformed(inv).y0 for a in arts)
    # Below the DESCENDER line, and the gap is to the rule's top (the pass
    # map's lesson: "VIRGIL VAN DIJK" touched a rule that "LIVERPOOL" cleared).
    bar_y = ybot - L['bar_gap'] - L['bar_h']
    fig.patches.append(Rectangle((0.5 - (x1 - x0) / 2.0, bar_y), x1 - x0, L['bar_h'],
                                 transform=fig.transFigure, facecolor=accent,
                                 edgecolor='none', zorder=10))

    bottom = bar_y
    # THE SUBJECT COMES BEFORE THE BOILERPLATE. On levels 2-3 the frame line
    # is the chart's real title ("GOALS FOR - WHILE BEHIND - BY COMPONENT");
    # set under the scope it was the fourth thing in reading order and two
    # cold readers met "RANKED AMONG 20 TEAMS" before they knew what the six
    # gauges were. Solid at every aspect - one voice for one role. Fit
    # ladder: the full line, then without its "BY ..." tail (the six labels
    # beneath say it anyway), then shrink - never overrun.
    if frame_line:
        parts = list(frame_line) if isinstance(frame_line, (list, tuple)) else [frame_line]
        cands = [SEP.join(parts)]
        if len(parts) > 1 and parts[-1].startswith('BY '):
            cands.append(SEP.join(parts[:-1]))
        for cand in cands:
            if _width_frac(fig, cand, L['frame_size'], 'bold') <= 0.92:
                break
        pt = fit_fontsize(fig, cand, L['frame_size'], max_frac=0.92,
                          floor=min(L['frame_size'], 16), bold=True)
        a = _text(fig, 0.5, bottom - L['frame_gap'], cand, pt, TEXT_PRIMARY, 'bold')
        fig.canvas.draw()
        bottom = _low(fig, a)
    if filter_line:
        # The one line that makes this frame a different chart from its
        # sibling, in the subject's own voice. Set in the scope's weight it
        # was filtered as boilerplate and the frame taken for a repost.
        a = _text(fig, 0.5, bottom - L['frame_gap'], filter_line, L['frame_size'], TEXT_PRIMARY,
                  'bold')
        fig.canvas.draw()
        bottom = _low(fig, a)
    parts = [p for p in scope_parts if p]
    if parts:
        # Packed to the measure, never splitting a part: at 16in it is one
        # line, at 9in two or three. A fixed two-way split overflowed the
        # frame once the pool scope grew a fourth part.
        y = bottom - L['scope_gap']
        chunks = _pack(fig, parts, L['scope_size'], L['scope_frac'], L['scope_track'])
        for i, line in enumerate(chunks):
            pt = fit_fontsize(fig, track(line, L['scope_track']) if L['scope_track'] else line,
                              L['scope_size'], max_frac=L['scope_frac'],
                              floor=min(L['scope_size'], 11), bold=False)
            a = _text(fig, 0.5, y - i * L.get('scope_lead', 0.0), line, pt, TEXT_SECONDARY,
                      spaced=L['scope_track'])
            fig.canvas.draw()
            bottom = _low(fig, a)
    if deck and L.get('show_deck', True):
        pt = fit_fontsize(fig, deck, L['deck_size'], max_frac=0.92,
                          floor=min(L['deck_size'], 10), bold=False)
        a = _text(fig, 0.5, bottom - L['deck_gap'], deck, pt, TEXT_MUTED)
        fig.canvas.draw()
        bottom = _low(fig, a)
    return bottom


# -- The gauge ---------------------------------------------------------------------

def _ramp(needle):
    return matplotlib.colors.to_hex(RAMP(float(np.clip(needle, 0.0, 1.0))))


def _verdict_colour(needle):
    """The readout's colour: the ramp at the standing, lifted to text
    contrast."""
    return ensure_line_contrast(_ramp(needle), BG_COLOR, READOUT_MIN_CONTRAST)


def _fill_colour(needle):
    """The fill's colour: the same ramp at GRAPHIC contrast (3:1), which
    the ramp already clears everywhere. Lifted to the text floor the red
    end went salmon - 18th came out pinker and LIGHTER than 17th (measured
    0.325 vs 0.278 relative luminance), so the bad end did not order."""
    return ensure_line_contrast(_ramp(needle), BG_COLOR, FILL_MIN_CONTRAST)


def _fmt_min(m):
    return f"{m:,.0f} min"


def _fmt_like(spec, x):
    """Format a pool statistic the way the gauge formats its own value."""
    return tp.format_number(spec.fmt, x)


def _total_and_ref(spec, L):
    """The two halves of the line beneath the value: what the rate was made
    from, and the pool median it is read against."""
    total = tp.format_total(spec)
    if spec.component == 'xg_per_shot' and spec.n_shots is not None:
        total = f"of {spec.n_shots:.0f} shots"
    elif spec.component == 'minutes_pct' and spec.minutes is not None and spec.minutes_total:
        total = f"{spec.minutes:,.0f} of {_fmt_min(spec.minutes_total)}"
    med = float(np.median(spec.pool_values)) if spec.pool_values else float('nan')
    # "pool median": a bare "median 1.33" on Liverpool's own line was read as
    # Liverpool's median match; "league median" overran the portrait cell.
    # "league median" when the pool is the league - "pool" meant nothing to
    # a cold reader; "pool median" stays for the wider pools, whose names
    # do not shorten reliably.
    ref = f"{L.get('ref_word', 'pool median')} {_fmt_like(spec, med)}" if L.get('show_ref', True) else ''
    return total, ref


def _label_plan(fig, spec, L, cell_w):
    """(lines, pt, definition) for a gauge's label block. The name: fit
    ladder tracked, then solid, then shrunk to the delivery floor, then
    WRAPPED to two lines - "XG DIFFERENCE WHEN BEHIND" at the tile's 16pt
    floor ran into its neighbour and off the frame. The definition beneath
    it ("PSxG - xG"): at the delivery floor, muted, or dropped if even that
    overruns the cell. Decided per cell here, applied per FRAME by the
    caller: a second line takes its height from every dial on the frame,
    not just its own, so the six dials stay one size."""
    lab = spec.label.upper()
    lab_px = cell_w * fig.bbox.width * 0.92
    definition = spec.formula or ''
    if definition and _width_frac(fig, definition, L['type_floor']) * fig.bbox.width > lab_px:
        definition = ''
    cands = ([track(lab, L['label_track'])] if L['label_track'] else []) + [lab]
    lab_pt = L['label_size']
    for text in cands:
        if _width_frac(fig, text, lab_pt, 'bold') * fig.bbox.width <= lab_px:
            return [text], lab_pt, definition
    lab_pt = fit_fontsize(fig, lab, L['label_size'], max_frac=lab_px / fig.bbox.width,
                          floor=L['type_floor'], bold=True)
    if _width_frac(fig, lab, lab_pt, 'bold') * fig.bbox.width <= lab_px or ' ' not in lab:
        return [lab], lab_pt, definition
    lab_pt = L['label_size']
    words = lab.split(' ')
    best = None
    for k in range(1, len(words)):
        a, b = ' '.join(words[:k]), ' '.join(words[k:])
        w = max(_width_frac(fig, a, lab_pt, 'bold'), _width_frac(fig, b, lab_pt, 'bold'))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return best[1], lab_pt, definition


def _label_leads(fig, L, plans):
    """(wrap_px, def_px): the frame's two label slots in pixels - a second
    name line if any cell wraps, a definition line if any cell has one."""
    wrap_px = max((pt * fig.dpi / 72.0 * 1.2 for lines, pt, _ in plans if len(lines) > 1), default=0.0)
    def_px = L['type_floor'] * fig.dpi / 72.0 * 1.35 if any(d for _, _, d in plans) else 0.0
    return wrap_px, def_px


def _gauge(fig, box, spec, L, cell_w=None, show_ref=True, leads=None):
    """One dial in the figure-fraction box (x0, y0, w, h). `cell_w` is the
    grid cell's width as a figure fraction - the measure the text lines may
    use; the axes box itself shrinks to the dial under aspect='equal'."""
    ax = fig.add_axes(box)
    ax.set_aspect('equal')
    ax.axis('off')
    # Data space is sized so the dial fills the WIDTH of the axes and the text
    # under it has room; the axes box is taller than the dial on purpose.
    ax.set_xlim(-1.30, 1.30)
    # The floor is solved, not guessed: the text stack under the dial is a
    # fixed number of pixels (three fonts + gaps) and the dial takes what the
    # box leaves. box height px = span * unit_px, so
    # span = 1.36 + hub + gap + stack_px / unit_px; three fixed-point passes.
    stack_px = ((L['readout_size'] + L['value_size'] + L['total_size']
                 + (L['total_size'] if L.get('ref_line') else 0)) * fig.dpi / 72.0
                + 8 + 6 + 5 + 4 + (4 if L.get('ref_line') else 0))
    plan = _label_plan(fig, spec, L, cell_w if cell_w else box[2])
    lab_lines, lab_pt, definition = plan
    # The label slots are font-driven pixels, like the stack below: set in
    # dial units they overlapped on the frames whose taller header had
    # shrunk the dial. Per frame, not per cell, so the rows align.
    wrap_px, def_px = leads if leads is not None else _label_leads(fig, L, [plan])
    box_h_px = box[3] * fig.bbox.height
    span = 1.36 + 0.075
    wrap_lead = def_lead = 0.0
    for _ in range(3):
        unit = box_h_px / span
        wrap_lead, def_lead = wrap_px / unit, def_px / unit
        span = 1.36 + wrap_lead + def_lead + 0.075 + max(0.16, 8 / unit) + stack_px / unit
    top = 1.36 + wrap_lead + def_lead
    ax.set_ylim(top - span, top)
    R, rw = 1.0, L['ring_w']
    neutral = spec.direction == 0
    needle = spec.standing.needle if spec.standing.n else 0.5
    nan_value = spec.value is None or (isinstance(spec.value, float) and math.isnan(spec.value))

    # Ring: a steel track, FILLED from the bad end (left) to the standing in
    # the verdict colour. A cell with no direction wears a FULL steel ring:
    # the instrument present, the verdict withheld. (Filled to its share it
    # was a two-tone steel arc 18 levels apart that had to be measured to be
    # seen, and its empty remainder was the 20th-place silhouette.) The
    # track sits at ~1.9:1 - at 1.29:1 it vanished under broadcast gamma.
    if neutral and not nan_value:
        ax.add_patch(Wedge((0, 0), R, 0, 180, width=rw, facecolor=NEUTRAL_RING,
                           edgecolor='none', zorder=2))
    else:
        ax.add_patch(Wedge((0, 0), R, 0, 180, width=rw, facecolor=TRACK,
                           edgecolor='none', zorder=2))
    if not neutral and not nan_value and spec.standing.n:
        fill_deg = max(180.0 * float(np.clip(needle, 0, 1)), MIN_FILL_DEG)
        c = _fill_colour(needle)
        ax.add_patch(Wedge((0, 0), R, 180.0 - fill_deg, 180, width=rw, facecolor=c,
                           edgecolor=c, linewidth=0.6, zorder=3))
    # A hairline track under the ring's inner edge grounds the dial on the
    # dark page without a second colour.
    ax.add_patch(Wedge((0, 0), R - rw, 0, 180, width=0.012, facecolor='#2A3648',
                       edgecolor='none', zorder=1))

    # Everything below is measured in PIXELS off the dial and the fonts, not
    # in dial units: the dial shrinks on the taller level-3 headers (222px ->
    # 183px at 16:9) while the type does not, and a fixed offset put the
    # readout 3-4px off the hub there against 22px on the 9:16.
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    unit_px = ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0]
    d_px = 2.0 * R * unit_px                       # dial diameter in pixels

    def pt_u(pt):                                   # points -> dial units
        return pt * fig.dpi / 72.0 / unit_px

    def px_u(px):
        return px / unit_px

    # Needle at the standing: 180 deg is the worst, 0 deg the best. Width as
    # a share of the dial, so the three aspects agree (it was 3.2px on a
    # 222px dial and 5.9px on a 209px one).
    needle_pt = max(1.8, 0.014 * d_px * 72.0 / fig.dpi)
    hub_r = 0.075
    if not neutral and not nan_value and spec.standing.n:
        th = math.radians(180.0 - 180.0 * float(np.clip(needle, 0, 1)))
        tip = R - rw * 0.15
        ax.add_line(Line2D([0, tip * math.cos(th)], [0, tip * math.sin(th)],
                           color=HUB, lw=needle_pt, solid_capstyle='round', zorder=5))
    if not neutral:
        ax.add_patch(Circle((0, 0), hub_r, facecolor=HUB, edgecolor=BG_COLOR, lw=1.5, zorder=6))

    # The label, decided above; two lines stack upward from the same base.
    # Name above, definition beneath it, the definition slot nearest the
    # dial; a cell without a definition leaves that slot empty so the names
    # still sit on one line across the row.
    for i, line in enumerate(reversed(lab_lines)):
        ax.text(0, 1.20 + def_lead + i * wrap_lead, line, fontsize=lab_pt, color=TEXT_SECONDARY,
                fontweight='bold', ha='center', va='center')
    if definition:
        ax.text(0, 1.20, definition, fontsize=L['type_floor'], color=TEXT_MUTED,
                ha='center', va='center')

    # Readout under the hub. Rank: "3rd" big and "/20" small on one baseline;
    # percentile: the bare number with a small PCTL beside it (bare integer,
    # not 78% and not 78th - the player chart's finding).
    # The neutral hero in the secondary text colour: in white at 34px it was
    # the brightest numeral on the page, over the one arc that says nothing.
    colour = TEXT_SECONDARY if neutral else TEXT_PRIMARY if nan_value else _verdict_colour(needle)
    st = spec.standing
    # A cell with no direction shows its VALUE where the others show a
    # standing, and says the standing in words beneath ("5th highest of 20").
    # A grey arc over "5th/20" read as broken to a cold viewer; over "52%" it
    # reads as what it is - a share, ranked by size with no verdict.
    if nan_value or not st.n:
        main, sub = '\u2014', ''
    elif neutral:
        main, sub = tp.format_value(spec), ''
    elif st.mode == 'rank':
        # The tie mark belongs to the suffix, at its size and colour: set at
        # the ordinal's 34px in the ordinal's colour it read as "13th = /20".
        r = int(st.position)
        main, sub = tp.ordinal(r), f"{'=' if st.tied else ''}/{st.n}"
    else:
        main, sub = f"{st.position:.0f}", ' PCTL'
    CAP = 0.72                                      # cap height / font size
    gap_hub = max(px_u(8), 0.08 * 2 * R)            # >= 8px and >= 8% of the dial
    y_read = -(hub_r + gap_hub + pt_u(L['readout_size']) * CAP)
    # The share's number sits at the siblings' size: the instrument is the
    # same, only the needle and the verdict colour are absent. (Set smaller
    # it read as a different widget; set louder - bold white words under it
    # - it won the row. The words beneath now wear the value line's stack.)
    main_pt = L['readout_size']
    if sub:
        # One suffix rule: half the readout, floored at the delivery size.
        sub_pt = max(L['readout_size'] * L['readout_sub'], L['type_floor'])
        t1 = ax.text(0, y_read, main, fontsize=main_pt, color=colour,
                     fontweight='bold', ha='left', va='baseline')
        t2 = ax.text(0, y_read, sub, fontsize=sub_pt,
                     color=TEXT_MUTED, fontweight='bold', ha='left', va='baseline')
        w1 = t1.get_window_extent(rnd).width
        w2 = t2.get_window_extent(rnd).width
        x_left = -px_u(w1 + w2) / 2.0
        t1.set_position((x_left, y_read))
        t2.set_position((x_left + px_u(w1), y_read))
    else:
        ax.text(0, y_read, main, fontsize=main_pt, color=colour,
                fontweight='bold', ha='center', va='baseline')

    # Value and what it was made from, under the readout. On a neutral cell
    # the value is already the hero, so this line carries the standing, in
    # the value's size but not its weight.
    value = tp.format_value(spec)
    unit = spec.unit
    value_weight = 'bold'
    if neutral and st.n and not nan_value:
        # The standing in words, in the siblings' value + unit stack, at
        # regular weight: bold words under the hero made this the only cell
        # with two bold lines.
        value_weight = 'normal'
        if st.mode == 'rank':
            # Past the midpoint say it from the other end: "5th lowest of
            # 20" is how a person says "16th highest of 20".
            r = int(st.position)
            if r > st.n / 2 and not st.tied:
                value, unit = f"{tp.ordinal(st.n - r + 1)} lowest", f"of {st.n}"
            else:
                value, unit = f"{tp.ordinal(r)}{'=' if st.tied else ''} highest", f"of {st.n}"
        else:
            value, unit = f"{st.position:.0f} pctl", "by value"
    cell_px = (cell_w if cell_w else box[2]) * fig.bbox.width * 0.92
    # The unit yields in order when the cell cannot take the line: the
    # "when <state>" tail first (the frame line names the situation), then
    # the formula in front of a named link ("PSxG - xG"), which the label
    # already names. On the 9x8 tile the full Finishing line ran the whole
    # column and into both neighbours.
    no_when = unit.split(' when ')[0] if ' when ' in unit else unit
    unit_cands = [unit, no_when]
    if 'per 90' in no_when and not no_when.startswith('per 90'):
        unit_cands.append(no_when[no_when.index('per 90'):])
    if L.get('short_unit'):
        unit_cands = [u for u in unit_cands if ' when ' not in u]
    unit_cands = list(dict.fromkeys(unit_cands))
    y_val = y_read - (pt_u(L['readout_size']) * 0.28 + px_u(6) + pt_u(L['value_size']) * CAP)
    if unit:
        t1 = ax.text(0, y_val, value, fontsize=L['value_size'], color=TEXT_PRIMARY,
                     fontweight=value_weight, ha='left', va='baseline')
        w1 = t1.get_window_extent(rnd).width
        for u in unit_cands:
            t2 = ax.text(0, y_val, '  ' + u, fontsize=L['unit_size'], color=TEXT_MUTED,
                         ha='left', va='baseline')
            w2 = t2.get_window_extent(rnd).width
            if w1 + w2 <= cell_px or u == unit_cands[-1]:
                break
            t2.remove()
        x_left = -px_u(w1 + w2) / 2.0
        t1.set_position((x_left, y_val))
        t2.set_position((x_left + px_u(w1), y_val))
    else:
        ax.text(0, y_val, value, fontsize=L['value_size'], color=TEXT_PRIMARY,
                fontweight='bold', ha='center', va='baseline')
    y_total = y_val - (pt_u(L['value_size']) * 0.28 + px_u(5) + pt_u(L['total_size']) * CAP)

    total, ref = _total_and_ref(spec, L)
    if not show_ref:
        ref = ''
    # The line yields in order when the cell cannot take it: the penalty note
    # first, then - only if the frame as a whole could not carry it - the
    # median. Nothing overruns.
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    bare = total.split(' (')[0] if '(' in total else total
    own_line = bool(L.get('ref_line')) and bool(ref)
    beside = '' if own_line else ref
    cands = [SEP.join(p for p in (total, beside) if p)]
    if '(' in total:
        cands.append(SEP.join(p for p in (bare, beside) if p))
    if beside:
        cands.append(bare)
    for line in cands:
        t = ax.text(0, y_total, line, fontsize=L['total_size'], color=TEXT_MUTED,
                    ha='center', va='baseline')
        if t.get_window_extent(rnd).width <= cell_px or line == cands[-1]:
            break
        t.remove()
    if own_line:
        y_ref = y_total - (pt_u(L['total_size']) * 0.28 + px_u(4) + pt_u(L['total_size']) * CAP)
        ax.text(0, y_ref, ref, fontsize=L['total_size'], color=TEXT_MUTED,
                ha='center', va='baseline')
    return ax


# -- The frame -----------------------------------------------------------------------

_SITUATION_PHRASE = {'total': 'ALL PLAY', 'op': 'OPEN PLAY', 'sp': 'SET PIECES',
                     'ahead': 'WHILE AHEAD', 'level': 'WHILE LEVEL', 'behind': 'WHILE BEHIND'}


def _frame_line(headline, path, order):
    """What the six gauges ARE. The overview names its six too - without a
    subject line its header looked unfinished beside the levels below."""
    if not headline:
        return ['GOALS AND XG', 'FOR', 'AGAINST', 'DIFFERENCE']
    h = tp.HEADLINES[headline]
    H = h.label.upper()
    if not path:
        return [H, f"BY {'SITUATION' if order == 'situation' else 'COMPONENT'}"]
    (pick,) = path
    if order == 'situation':
        return [H, _SITUATION_PHRASE[pick], 'BY COMPONENT']
    comp = tp.resolve_component('total', pick)
    if comp == 'anchor':
        return [H, 'BY SITUATION']
    # The headline in front: "SHOT-STOPPING - BY SITUATION" alone read as a
    # level-2 frame with no parent.
    return [H, tp.component_label(h, comp, 'total').upper(), 'BY SITUATION']


def _deck(cube, subject, headline, path, order, mode):
    """The headline's SEASON standing, on level 3 only, labelled as such.

    Level 2 always contains its parent - the Total gauge or the anchor - so a
    deck there said the first gauge twice. Level 3 contains the level-2 pick
    the same way; what it does not contain is the headline it hangs from.
    "Season:" in front, because "Goals For 1.66 (4th/20)" above a gauge
    labelled GOALS FOR 3rd/20 read as a contradiction until the unit was read.
    """
    if not headline or not path or order != 'situation':
        # Component-first level 3 already has the season figure as its first
        # gauge (Total), and a "Season: xG Against" line over six ON TARGET %
        # gauges named a metric that was not on the page.
        return ''
    top = {g.key: g for g in tp.view(cube, subject, None, mode=mode)}
    g1 = top[f"{headline}.total.anchor"]
    read = g1.standing.readout + ('' if g1.standing.mode == 'rank' else ' pctl')
    return f"Season: {g1.label} {tp.format_value(g1)} {g1.unit}".strip() + f"  ({read})"


def create_team_profile(profile, *, headline=None, path=(), order='situation',
                        competition='', custom_title=None, custom_subtitle=None,
                        aspect='default'):
    """One frame of the Team Profile. Returns the figure.

    profile: the dict from shared.motherduck.get_team_profile.
    headline/path/order: which six cells (see module docstring).
    competition: overrides the header's competition name (the page passes
    the season's own competition; '' falls back to the profile's).
    The figure carries `fig.tp_gauge_boxes`, the six gauges' figure-fraction
    boxes in reading order, for the page's click-on-image layer.
    """
    L = _LAYOUTS.get(aspect, _LAYOUTS['default'])
    cube, subject = profile['cube'], profile['subject']
    mode = profile['pool_mode']
    specs = tp.view(cube, subject, headline, tuple(path), order, mode)

    fig = plt.figure(figsize=L['figsize'], dpi=100, facecolor=BG_COLOR)
    accent = ensure_line_contrast(profile.get('team_color') or '#888888', BG_COLOR)

    gp = int(cube.teams.loc[subject, 'gp']) if subject in cube.index else 0
    comp = (competition or profile.get('competition') or '').upper()
    years = profile.get('season_years') or ''
    n = profile.get('pool_n', 0)
    # The direction is declared ONCE, here, and every cell inherits it: an
    # against-metric's "3rd/20" is third BEST, i.e. third fewest. A cold
    # analyst read "76 PCTL xG Against" in the statistical sense (76% concede
    # less) until the colour corrected it; the words do it now.
    # The minutes beside the matches: every rate is per 90 and a match
    # runs ~101, so "63 in 38" beside "1.48 per 90 min" failed a reader's
    # arithmetic by 12% until the denominator turned up three frames later.
    mins = float(cube.teams.loc[subject, 'total_s']) / 60.0 if subject in cube.index else 0.0
    scope = [f"{gp} MATCHES", f"{mins:,.0f} MIN", f"{comp} {years}".strip()]
    L = dict(L, ref_word='league median' if mode == 'rank' else 'pool median')
    if mode == 'rank':
        scope += [f"RANKED AMONG {n} TEAMS", "1ST = BEST"]
    else:
        scope += [f"PERCENTILE AMONG {n} TEAMS", profile.get('pool_label', '').upper(),
                  "HIGHER = BETTER"]
    if custom_subtitle:
        scope = [custom_subtitle]
    filter_line = 'PENALTIES EXCLUDED' if profile.get('exclude_penalties') else ''

    title = custom_title or (profile.get('team_name') or '').upper()
    bottom = _header(fig, L, kicker='TEAM PROFILE', title=title, accent=accent,
                     scope_parts=scope, frame_line=_frame_line(headline, tuple(path), order),
                     filter_line=filter_line,
                     deck=_deck(cube, subject, headline, tuple(path), order, mode))

    # The grid takes what the header leaves.
    m = L['margin']
    top = bottom - L['grid_gap']
    gb = L['grid_bottom']
    cols, rows = L['cols'], L['rows']
    cw = (1.0 - 2 * m) / cols
    ch = (top - gb) / rows
    boxes = []
    # The median is shown on all six cells or on none. The fit ladder drops
    # it per cell when a line cannot take it, and one cell without it among
    # five with it read as "this one has no comparable median" (analyst,
    # round 2). Measured here once, at the total line's size, against the
    # cell's width, on the FULL line: the parenthesis now carries a phase's
    # share of the season ("44 of 63 (70%)"), which is content, and the
    # median is reference - so the median yields frame-wide before any one
    # cell loses its share.
    show_ref = bool(L.get('show_ref', True))
    cell_px = cw * fig.bbox.width * 0.92
    if show_ref and not L.get('ref_line'):
        for spec in specs[:cols * rows]:
            total, ref = _total_and_ref(spec, L)
            line = SEP.join(p for p in (total, ref) if p)
            if _width_frac(fig, line, L['total_size']) * fig.bbox.width > cell_px:
                show_ref = False
                break
    # Label tracking is one voice per frame too: four tracked labels over
    # two solid ones (the 9:16 GD frame) read as two kinds of gauge.
    label_track = L['label_track']
    if label_track:
        for spec in specs[:cols * rows]:
            lab = track(spec.label.upper(), label_track)
            if _width_frac(fig, lab, L['label_size'], 'bold') * fig.bbox.width > cell_px * 0.92 / 0.96:
                label_track = 0
                break
    L = dict(L, label_track=label_track)
    # One label height per frame: if any label needs two lines, every dial
    # on the frame gives up the same height, so the six stay one size.
    leads = _label_leads(fig, L, [_label_plan(fig, spec, L, cw) for spec in specs[:cols * rows]])
    if headline is None and cols == 2:
        # Two columns: actual beside expected (GF | xG, GA | xGA, GD | xGD).
        # In reading order the 3x2 grid's pairs sit one above the other; the
        # 2x3 reflow put Goal Difference beside xG For and a cold viewer
        # paired them.
        specs = [specs[i] for i in (0, 3, 1, 4, 2, 5)]
    for i, spec in enumerate(specs[:cols * rows]):
        r, c = divmod(i, cols)
        x0 = m + c * cw
        y0 = top - (r + 1) * ch
        bh = min(ch * (1 - 2 * L['cell_pad_y']), L.get('gauge_h', 1.0))
        box = [x0 + cw * L['cell_pad_x'], y0 + (ch - bh) / 2.0,
               cw * (1 - 2 * L['cell_pad_x']), bh]
        _gauge(fig, box, spec, L, cell_w=cw, show_ref=show_ref, leads=leads)
        boxes.append((x0, y0, x0 + cw, y0 + ch))
    fig.tp_gauge_boxes = boxes
    fig.tp_specs = specs

    add_cbs_footer(fig, x0=m, x1=1.0 - m, y=footer_y(fig, at_least=L.get('footer_y', 0.0)))
    return fig
