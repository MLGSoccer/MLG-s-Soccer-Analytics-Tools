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
from dataclasses import replace

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
        'note_gap': 0.036, 'note_size': 13, 'note_frac': 0.60,
        'cols': 3, 'rows': 2,
        'grid_gap': 0.030, 'grid_bottom': 0.075,
        # The gauge axes inside its cell, as fractions of the cell.
        'cell_pad_x': 0.06, 'cell_pad_y': 0.04,
        # One treatment on every frame: solid bold caps (the tracked form
        # drifted its "%" a word away and went solid on the long-label
        # frames anyway, so the six frames read as two families), the
        # meaning on its own line beneath at a step above the unit.
        'label_size': 15, 'label_track': 0, 'meaning_size': 12.5,
        'readout_size': 30, 'readout_sub': 0.55,
        # Muted line a point smaller: three 406px muted lines 86px apart on
        # the by-situation frame read as one grey ribbon across the row.
        'value_size': 17, 'unit_size': 11.5,
        # ONE dial per aspect. The grid takes what the header leaves, so
        # the dial shrank 247 -> 179px from the overview to a Big-5 level 3
        # while the type inside the cell did not - three templates, not one
        # drilled into. The DIAL is capped (in px at the 100-dpi figure), and
        # each frame's box is label block + dial + stack, so a frame with a
        # meaning line under its names keeps the same dial as one without.
        # (Capping the box instead left those frames 10% smaller.) Set to
        # what the tightest frame takes uncapped; the surplus becomes air.
        'dial_px': 213,
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
        'note_gap': 0.0240, 'note_size': 16,
        'cols': 2, 'rows': 3,
        'grid_gap': 0.0220, 'grid_bottom': 0.0500,
        'cell_pad_x': 0.05, 'cell_pad_y': 0.03,
        'label_size': 17, 'label_track': 0, 'readout_size': 34, 'readout_sub': 0.55,   # solid, as 16:9
        'value_size': 20, 'unit_size': 16,
        # The short unit here too: "1.79 per 90 min when behind" at 16pt ran
        # 91% of a 475px column and read as one sentence with its neighbour
        # across a 48px gutter. The subject line names the situation.
        'short_unit': True, 'dial_px': 274,
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
        'note_gap': 0.0400, 'note_size': 16,
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
        'value_size': 18, 'unit_size': 16,
        # The stack had 4px between the value and the total (2px on level 3)
        # while the header took a third of the tile. The lines get leading;
        # the header gave up a scope gap and the deck.
        # No meaning line on the tile: a wrapped name plus a 16pt gloss took
        # a third of the dial (126px against 180). The name still says the
        # stat; the gloss is on the two larger cuts and the page.
        'short_unit': True, 'meaning': False, 'dial_px': 177,
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
    # A newline in the text marks the seam a break should use ("SET-PIECE"
    # / "GOALS ABOVE XG"): one line when the whole fits, the two halves
    # when they do, else the ordinary wrap of the joined text.
    if "\n" in str(text):
        halves = [h.strip() for h in str(text).split("\n") if h.strip()]
        joined = " ".join(halves)
        if _width_frac(fig, joined, float(size), weight) <= max_frac:
            return [joined], float(size)
        if 1 < len(halves) <= max_lines:
            # the seam at size, then shrinking toward the floor - a break
            # inside the stat's name is the last resort, not the second
            pt = float(size)
            while pt >= floor:
                if all(_width_frac(fig, h, pt, weight) <= max_frac for h in halves):
                    return halves, pt
                pt = max(floor, pt - 1.0) if pt > floor else floor - 1
        text = joined
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
        if len(lines) == 2 and max_lines >= 2:
            # Balanced, not greedy: the greedy fill left "SET-PIECE GOALS
            # ABOVE" over an orphaned "XG"; the split with the narrowest
            # widest line gives "SET-PIECE" / "GOALS ABOVE XG".
            best = None
            for k in range(1, len(words)):
                a, b = ' '.join(words[:k]), ' '.join(words[k:])
                w = max(_width_frac(fig, a, pt, weight), _width_frac(fig, b, pt, weight))
                if w <= max_frac and (best is None or w < best[0]):
                    best = (w, [a, b])
            if best:
                lines = best[1]
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


def _break_balanced(fig, text, size, max_frac):
    """Word wrap at one size with a parenthetical kept as one unit when it
    fits a line; a two-line result is split where the widest line is
    narrowest (a width-only wrap left "box, penalties)" as a centred orphan
    under "throw-ins near the"). Never drops a word."""
    words = str(text).split()
    units, cur = [], []
    for w in words:                        # "(corners, ... penalties)" is one unit
        if cur or w.startswith('('):
            cur.append(w)
            if w.endswith(')') or w.endswith('),'):
                unit = ' '.join(cur)
                units.extend([unit] if _width_frac(fig, unit, size) <= max_frac else cur)
                cur = []
        else:
            units.append(w)
    units.extend(cur)
    # A dial's NAME is one unit: a run of capitalised words (and xG / %),
    # so "Goals Above Post-Shot xG" never breaks across the lines the way
    # a width-only split put "Goals Above" on one and "Post-Shot xG" on the next.
    merged = []
    for u in units:
        term = u[:1].isupper() or u.startswith(('xG', '%'))
        if term and merged and merged[-1][1] and not merged[-1][0].startswith('('):
            merged[-1] = (merged[-1][0] + ' ' + u, True)
        else:
            merged.append((u, term))
    units = [u for u, _ in merged]
    if _width_frac(fig, ' '.join(units), size) <= max_frac:
        return [' '.join(units)]
    best = None
    for k in range(1, len(units)):
        a, b = ' '.join(units[:k]), ' '.join(units[k:])
        w = max(_width_frac(fig, a, size), _width_frac(fig, b, size))
        if w <= max_frac and (best is None or w < best[0]):
            best = (w, [a, b])
    if best:
        return best[1]
    return _break_words(fig, ' '.join(units), size, max_frac)


def _break_words(fig, text, size, max_frac):
    """Greedy word wrap at ONE size, never dropping a word; a single word
    wider than the measure stands alone (the shrink step handles it)."""
    lines, cur = [], ''
    for w in str(text).split():
        trial = f"{cur} {w}".strip()
        if cur and _width_frac(fig, trial, size) > max_frac:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _header(fig, L, *, kicker, title, accent, scope_parts, frame_line, filter_line, note,
            frame_style='subject'):
    """Kicker, title, club-colour rule, FRAME LINE, filter line, scope, note.

    Same furniture as the pass map header, in the same places, so the two
    families read as one house. The frame line is this chart's own: it says,
    at body weight, WHAT the six gauges below are ("POST-SHOT xGA - BY
    SITUATION"). The first build carried that only in the kicker and two
    cold readers out of two attached a level-3 body to the wrong metric.
    Returns the y under the last line drawn.
    """
    # The ranking's kicker carries the club. A long club name loosens the
    # tracking first (2, 1, 0 hair spaces) and shrinks only after that:
    # "CHELSEA WOMEN . LEAGUE RANKING" shrunk to 13pt under the phone floor
    # while the same words untracked fit at size.
    # One tracking per kicker kind - a ladder that loosened per club made
    # the element look different on every ranking ("INTER MIAMI" at 1,
    # "LIVERPOOL" at 2). A club-carrying kicker (the ranking's, with a
    # separator) tracks at 1; the frames' "TEAM PROFILE" at 2.
    k_pt, k_sp = L['kicker_size'], (1 if SEP.strip() in kicker else 2)
    if _width_frac(fig, track(kicker, k_sp), k_pt, 'bold') > 0.92:
        k_pt = fit_fontsize(fig, track(kicker, k_sp), L['kicker_size'], max_frac=0.92,
                            floor=max(L['kicker_size'] - 4, 9), bold=True)
    _text(fig, 0.5, L['kicker_y'], kicker, k_pt, TEXT_MUTED, 'bold', spaced=k_sp)

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
    if frame_line and frame_style == 'provenance':
        # The ranking's frame line is where the stat was clicked, not the
        # subject: in the scope's voice, both parts kept, packed if long.
        # Set in the title's weight it read as a second title ("GOALS
        # PREVENTED" over "XG AGAINST": which is ranked?).
        parts = list(frame_line) if isinstance(frame_line, (list, tuple)) else [frame_line]
        size = L['scope_size'] + 1
        y = bottom - L['frame_gap']
        for i, line in enumerate(_pack(fig, parts, size, L['scope_frac'], L['scope_track'])):
            a = _text(fig, 0.5, y - i * L.get('scope_lead', 0.0), line, size, TEXT_SECONDARY,
                      spaced=L['scope_track'])
        fig.canvas.draw()
        bottom = _low(fig, a)
    elif frame_line:
        parts = list(frame_line) if isinstance(frame_line, (list, tuple)) else [frame_line]
        cands = [SEP.join(parts)]
        if len(parts) > 1 and _is_tail(parts[-1]):
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
    parts = ([str(x) for x in (note if isinstance(note, (list, tuple)) else [note]) if x]
             if note else [])
    if parts:
        # The notes under the scope: the frame's SPINE (what the six dials
        # add up to), the exposure caveat, the own goals - or a
        # component-first level 3's one shared definition. Packed to the
        # measure part by part, a long part broken on words, and only then
        # shrunk: at 9:16 "371 set pieces ... - no own goals scored for
        # them" had shrunk to 11pt to stay on one line, under the phone
        # floor. In the scope's colour: the line that closes the
        # arithmetic was the faintest thing on the page.
        size = float(L['note_size'])
        # The note's measure: 0.60 of a 16:9 frame, so a line of 10 CSS px
        # type never runs 90% of the width, wider than the title and the
        # grid (it read as a rule, not a sentence); the whole width on a
        # phone. A part breaks balanced, never inside a parenthesis.
        frac = L.get('note_frac', 0.92)
        lines = []
        for chunk in _pack(fig, parts, size, frac, 0):
            lines.extend(_break_balanced(fig, chunk, size, frac))
        pt = min(fit_fontsize(fig, ln, size, max_frac=frac, floor=min(size, 10), bold=False)
                 for ln in lines)
        lead = pt * 1.30 / (72.0 * L['figsize'][1])
        y = bottom - L['note_gap']
        for i, ln in enumerate(lines):
            a = _text(fig, 0.5, y - i * lead, ln, pt, TEXT_SECONDARY)
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


def _label_plan(fig, spec, L, cell_w):
    """A gauge's label block, decided per cell and applied per FRAME.

    The NAME: fit ladder tracked, then solid, then shrunk to the delivery
    floor, then WRAPPED to two lines - "XG DIFFERENCE WHEN BEHIND" at the
    tile's 16pt floor ran into its neighbour and off the frame. The
    MEANING, in parentheses at the unit's size, regular weight: on the
    same line as the name where the whole frame can take it ("GOALS ABOVE
    POST-SHOT XG (beating the keeper)"), else on its own line beneath -
    a frame is one form or the other, and a row of names
    with empty slots under them floated above its dials. (The previous
    build set a formula at the floor size under the name: smaller than the
    line the user had already called unreadable.)
    Returns dict(lines, pt, meaning, meaning_pt, inline_ok)."""
    lab = spec.label.upper()
    lab_px = cell_w * fig.bbox.width * 0.92
    meaning, meaning_pt, inline_ok = '', 0.0, True
    if spec.meaning and L.get('meaning', True):
        text = f"({spec.meaning})"
        for pt in (L.get('meaning_size', L['unit_size']), L['unit_size'], L['type_floor']):
            if _width_frac(fig, text, pt) * fig.bbox.width <= lab_px:
                meaning, meaning_pt = text, pt
                break
    plan = dict(meaning=meaning, meaning_pt=meaning_pt, inline_ok=inline_ok)
    cands = ([track(lab, L['label_track'])] if L['label_track'] else []) + [lab]
    lab_pt = L['label_size']
    for text in cands:
        if _width_frac(fig, text, lab_pt, 'bold') * fig.bbox.width <= lab_px:
            if meaning:
                # Beside the name the meaning wears the unit's size - the
                # secondary voice, like "/20" beside "4th".
                # Measured to 0.96 of the cell, not the name's 0.92: the
                # fallback here is the clean two-line form, not a shrunk
                # name, and centred neighbours leave the 2% each side real.
                inline = (_width_frac(fig, text, lab_pt, 'bold')
                          + _width_frac(fig, ' ' + meaning, L['unit_size'])) * fig.bbox.width
                plan['inline_ok'] = inline <= lab_px * 0.96 / 0.92
            return dict(plan, lines=[text], pt=lab_pt)
    plan['inline_ok'] = not meaning       # a shrunk or wrapped name takes no meaning beside it
    lab_pt = fit_fontsize(fig, lab, L['label_size'], max_frac=lab_px / fig.bbox.width,
                          floor=L['type_floor'], bold=True)
    if _width_frac(fig, lab, lab_pt, 'bold') * fig.bbox.width <= lab_px or ' ' not in lab:
        return dict(plan, lines=[lab], pt=lab_pt)
    lab_pt = L['label_size']
    words = lab.split(' ')
    best = None
    for k in range(1, len(words)):
        a, b = ' '.join(words[:k]), ' '.join(words[k:])
        w = max(_width_frac(fig, a, lab_pt, 'bold'), _width_frac(fig, b, lab_pt, 'bold'))
        if best is None or w < best[0]:
            best = (w, [a, b])
    return dict(plan, lines=best[1], pt=lab_pt)


def _label_leads(fig, L, plans):
    """The frame's label decision: dict(inline, wrap_px, meaning_px). Inline
    when every cell with a meaning can carry it beside its name; otherwise
    a meaning slot (px) under the names. wrap_px is a second name line if
    any cell wraps."""
    # One treatment: the name centred over its dial, the meaning beneath.
    # Inline on some frames and stacked on others read as two families,
    # and an inline pair pushed the name off its dial ("MISSED" left,
    # "(wide, over or the woodwork)" right).
    inline = False
    wrap_px = max((p['pt'] * fig.dpi / 72.0 * 1.2 for p in plans if len(p['lines']) > 1), default=0.0)
    meaning_px = 0.0 if inline else max((p['meaning_pt'] * fig.dpi / 72.0 * 1.3 for p in plans if p['meaning']),
                                        default=0.0)
    return dict(inline=inline, wrap_px=wrap_px, meaning_px=meaning_px)


def _stack_px(fig, L):
    """The text under the dial, in pixels: the readout and the value line.
    (A third line carried the total and the pool median, at a size the
    user could not read.)"""
    return (L['readout_size'] + L['value_size']) * fig.dpi / 72.0 + 8 + 6 + 6


def _box_px_for_dial(fig, L, d_px, wrap_px, meaning_px):
    """The axes box height (px) that gives a dial of diameter `d_px`: the
    inverse of the solve in `_gauge` - half-dial units 1.36 (dial + hub
    clearance) and 0.075 (hub), the 8px/8% gap, the label slots, the stack."""
    unit = d_px / 2.0
    return (1.36 + 0.075) * unit + max(0.16 * unit, 8) + wrap_px + meaning_px + _stack_px(fig, L)


def _gauge(fig, box, spec, L, cell_w=None, leads=None):
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
    stack_px = _stack_px(fig, L)
    plan = _label_plan(fig, spec, L, cell_w if cell_w else box[2])
    lab_lines, lab_pt, meaning, meaning_pt = plan['lines'], plan['pt'], plan['meaning'], plan['meaning_pt']
    # The label slots are font-driven pixels, like the stack below: set in
    # dial units they overlapped on the frames whose taller header had
    # shrunk the dial. Per frame, not per cell, so the rows align.
    leads = leads if leads is not None else _label_leads(fig, L, [plan])
    wrap_px, meaning_px, inline = leads['wrap_px'], leads['meaning_px'], leads['inline']
    box_h_px = box[3] * fig.bbox.height
    span = 1.36 + 0.075
    wrap_lead = meaning_lead = 0.0
    for _ in range(3):
        unit = box_h_px / span
        wrap_lead, meaning_lead = wrap_px / unit, meaning_px / unit
        span = 1.36 + wrap_lead + meaning_lead + 0.075 + max(0.16, 8 / unit) + stack_px / unit
    top = 1.36 + wrap_lead + meaning_lead
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

    # The label, decided above. Inline: name and meaning on one line, the
    # meaning at the name's size in regular weight. Otherwise the names sit
    # on one line across the frame (the meaning slot is frame-level) with
    # the meaning beneath, nearest the dial. A wrapped name extends upward.
    base = 1.20 + meaning_lead
    if inline and meaning:
        t1 = ax.text(0, base, lab_lines[0], fontsize=lab_pt, color=TEXT_SECONDARY,
                     fontweight='bold', ha='left', va='center')
        t2 = ax.text(0, base, ' ' + meaning, fontsize=L['unit_size'], color=TEXT_SECONDARY,
                     ha='left', va='center')
        w1 = t1.get_window_extent(rnd).width
        w2 = t2.get_window_extent(rnd).width
        x_left = -px_u(w1 + w2) / 2.0
        t1.set_position((x_left, base))
        t2.set_position((x_left + px_u(w1), base))
    else:
        if meaning:
            ax.text(0, 1.20, meaning, fontsize=meaning_pt, color=TEXT_SECONDARY,
                    ha='center', va='center')
        # ONE name baseline per row. Bottom-anchoring a bare name to its arc
        # set it 12 CSS px below its neighbour's name - on the neighbour's
        # definition line - and a cold designer measured it as
        # misregistration, worst where the frame's parent dial sat at the
        # definition tier beside its children. The air under a bare name is
        # the cheaper cost.
        name_y = base
        for i, line in enumerate(reversed(lab_lines)):
            ax.text(0, name_y + i * wrap_lead, line, fontsize=lab_pt, color=TEXT_SECONDARY,
                    fontweight='bold', ha='center', va='center')

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
        # A shared place is "T-13th", the house style; an "=" between the
        # ordinal and "/20" read as a typo to a cold viewer.
        r = int(st.position)
        main, sub = f"{'T-' if st.tied else ''}{tp.ordinal(r)}", f"/{st.n}"
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
                value, unit = f"{'T-' if st.tied else ''}{tp.ordinal(r)} highest", f"of {st.n}"
        else:
            value, unit = f"{st.position:.0f} pctl", "by value"
    cell_px = (cell_w if cell_w else box[2]) * fig.bbox.width * 0.92
    # The unit yields in order when the cell cannot take the line: the
    # "when <state>" tail first (the frame line names the situation), then
    # the formula in front of a named link ("PSxG - xG"), which the label
    # already names. On the 9x8 tile the full Finishing line ran the whole
    # column and into both neighbours.
    no_when = unit.split(' when ')[0] if ' when ' in unit else unit
    state = unit.replace(' when ', ' ') if ' when ' in unit else None    # "per 90 min behind"
    unit_cands = [unit] + ([state] if state else []) + [no_when]
    if 'per 90' in no_when and not no_when.startswith('per 90'):
        unit_cands.append(no_when[no_when.index('per 90'):])
    if L.get('unit_bare'):
        # the frame line names the situation: the bare unit, as before
        unit_cands = [u for u in unit_cands if u not in (unit, state)] or [no_when]
    elif L.get('short_unit'):
        # the narrow column drops "when" but keeps the state: on the
        # by-situation frame "1.63 per 90 min" under AHEAD read as a share
        # of the season's 1.48 (two cold readers added the three states)
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
    return ax


# -- The frame -----------------------------------------------------------------------

def _frame_line(headline, path, order):
    """Which slice of the cube the six gauges are - levels 2 and 3 only.
    The overview has none: its headline is the kicker, the club and the
    scope ("GOALS AND XG - FOR - AGAINST - DIFFERENCE" listed the page, and
    "ATTACK AND DEFENCE" said nothing - the user's words - so it went). The
    last part is the drill dimension in a reader's words; the header's fit
    ladder may drop it, the six labels beneath say it anyway."""
    if not headline:
        return []
    h = tp.HEADLINES[headline]
    H = h.label.upper()
    if not path:
        return [H, tp.order_phrase(order, h).upper()]
    (pick,) = path
    if order == 'situation':
        return [H, tp.SITUATION_PHRASE[pick].upper(), tp.order_phrase('component', h).upper()]
    comp = tp.resolve_component('total', pick, h.side)
    if comp == 'anchor':
        return [H, tp.order_phrase('situation').upper()]
    # The headline in front: "SHOT-STOPPING - BY SITUATION" alone read as a
    # level-2 frame with no parent.
    return [H, tp.component_label(h, comp, 'total').upper(), tp.order_phrase('situation').upper()]


def _is_tail(part):
    # Is this frame-line part the drill dimension (droppable by the fit ladder)?
    tails = {tp.order_phrase('situation')} | {tp.order_phrase('component', h) for h in tp.HEADLINES.values()}
    return part in {t.upper() for t in tails}


_MINUS = chr(0x2212)
_SPINES = ('gf', 'ga', 'xg', 'xga', 'xgd')


def _shows_components(headline, path, order):
    """Is this frame the six-component breakdown of one headline (level 2 by
    component, or a situation's level 3)? The overview, the by-situation
    level 2 and a component-first level 3 are six situations instead."""
    return bool(headline) and ((not path and order == 'component')
                               or (bool(path) and order == 'situation'))


def _og_terms(profile, sit):
    """(own goals for, own goals against, per-90 function) for the subject
    in one situation, or None when the subject is not in the cube."""
    cube, subject = profile['cube'], profile['subject']
    if subject not in cube.index:
        return None
    den = float(tp._denominator(cube, sit).loc[subject]) or 0.0
    og_f = float(tp._q(cube, 'for', sit, 'og').loc[subject])
    og_a = float(tp._q(cube, 'against', sit, 'og').loc[subject])
    return og_f, og_a, (lambda n: (n / den) if den > 0 else 0.0)


def _spine_note(profile, headline, path, order):
    """What the six dials add up to, in the dials' own names.

    The frames were built as decompositions - the three shares split every
    shot; Goals Above xG is placement plus beating keepers plus own goals -
    and three cold readers out of three found the arithmetic only by doing
    it themselves, reading the own-goal line as trivia when it was the term
    that closed the chain. The identity is stated once, under the scope;
    the own-goal term is folded in where it belongs. Nothing on the shot
    comparison (gd): its dials are two ends of a funnel, not a sum.
    """
    if headline not in _SPINES or not _shows_components(headline, path, order):
        return ''
    h = tp.HEADLINES[headline]
    sit = path[0] if path else 'total'
    lab = lambda comp: tp.component_label(h, comp, sit)
    if headline == 'gf':
        return f"{lab('on_target_pct')}, {lab('blocked_pct')} and {lab('missed_pct')} split every shot"
    if headline == 'ga':
        return f"{lab('on_target_pct')}, {lab('blocked_pct')} and {lab('missed_pct')} split every shot faced"
    og = _og_terms(profile, sit)
    if og is None:
        return ''
    og_f, og_a, per90 = og
    signed = lambda n: tp.format_number('signed', per90(n))
    if headline == 'xg':
        term = 'none' if og_f == 0 else f"{int(og_f)} = {signed(og_f)} per 90"
        return (f"{lab('gap')} = {lab('placement')} + {lab('beat_keeper')} "
                f"+ opposition own goals ({term})")
    if headline == 'xga':
        term = 'none' if og_a == 0 else f"{int(og_a)} = {signed(og_a)} per 90"
        return (f"{lab('gap')} = {lab('placement')} {_MINUS} {lab('stopping')} "
                f"+ own goals conceded ({term})")
    term = ('none' if og_f == 0 and og_a == 0
            else f"{int(og_f)} for, {int(og_a)} against = {signed(og_f - og_a)} per 90")
    return (f"{lab('net')} = {lab('placement')} {_MINUS} {lab('placement_faced')} "
            f"+ {lab('beat_keeper')} + {lab('stopping')} + own goals ({term})")


def _own_goals_note(profile, headline, path, order):
    """Own goals, said once under the header - never a dial.

    On an xG frame they are the term that CLOSES the gap the frame states:
    Goals Above xG = placement + beating keepers + own goals, exactly. Two
    dials that visibly do not sum to the third is a frame a reader stops
    trusting. On a goals frame they are the goals the shot partition cannot
    account for (no shot, so no outcome). Situation-first frames read them
    from that situation's cells; the overview and the component-first
    level 3 say nothing (one stat in six situations has its own note).
    """
    if not headline or order != 'situation' and path:
        return ''
    if headline is None:
        return ''
    if headline in _SPINES and headline not in ('gf', 'ga') and _shows_components(headline, path, order):
        return ''            # the spine carries the own-goal term on the xG frames
    h = tp.HEADLINES[headline]
    sit = path[0] if path else 'total'
    og = _og_terms(profile, sit)
    if og is None:
        return ''
    og_f, og_a, per90 = og
    if path and og_f == 0 and og_a == 0:
        return ''        # a zero is worth a line on the season frame, not on a situation's
    plural = lambda n: 'own goal' if n == 1 else 'own goals'
    # "2 own goals for them" read either way (a cold analyst took it as
    # own goals BY the team until another frame corrected him).
    if h.side == 'for':
        if og_f == 0:
            return 'no opposition own goals'
        return (f"Goals For includes {int(og_f)} opposition {plural(og_f)} "
                f"({tp.format_number('goals', per90(og_f))} per 90)")
    if h.side == 'against':
        if og_a == 0:
            return 'no own goals conceded'
        return (f"Goals Against includes {int(og_a)} {plural(og_a)} conceded "
                f"({tp.format_number('goals', per90(og_a))} per 90)")
    if og_f == 0 and og_a == 0:
        return 'no own goals, for or against'
    return (f"own goals: {int(og_f)} for, {int(og_a)} against "
            f"({tp.format_number('signed', per90(og_f - og_a))} per 90 to the difference)")


def _situation_note(profile, headline, path, order):
    """What dial two used to carry, now under the header.

    Time in state is the EXPOSURE caveat - per-90-in-state means a team
    that trailed for forty minutes all season would otherwise rank on forty
    minutes of evidence - and set pieces taken is the supply a Set Piece
    frame rests on. Both were the context slot until the respec fixed all
    six dials per frame.
    """
    if not headline or not path or order != 'situation':
        return ''
    sit = path[0]
    cube, subject = profile['cube'], profile['subject']
    if subject not in cube.index:
        return ''
    t = cube.teams.loc[subject]
    if sit in tp.STATE_SITUATIONS:
        total = float(t['total_s']) or 0.0
        if total <= 0:
            return ''
        secs = float(t[f'{sit}_s'])
        word = tp.SITUATION_PHRASE[sit].lower().replace('when ', '')
        return (f"{word} for {secs / total:.0%} of the season "
                f"({secs / 60.0:,.0f} minutes)")
    if sit == 'sp':
        # What the count IS: corners, free kicks in the final third,
        # throw-ins level with the box, penalties - "set pieces taken"
        # alone read as every restart (371 in 38 matches is fewer than a
        # side's throw-ins). A difference frame gives both ends.
        h = tp.HEADLINES[headline]
        what = 'corners, free kicks, throw-ins near the box, penalties'
        vals = {}
        for col in ('sp_for', 'sp_against'):
            n = t.get(col)
            vals[col] = None if n is None or (isinstance(n, float) and math.isnan(n)) else int(n)
        if h.side == 'diff':
            if vals['sp_for'] is None or vals['sp_against'] is None:
                return ''
            return (f"final-third set pieces: {vals['sp_for']:,} taken, {vals['sp_against']:,} faced "
                    f"({what})")
        n = vals['sp_against' if h.side == 'against' else 'sp_for']
        if n is None:
            return ''
        word = 'faced' if h.side == 'against' else 'taken'
        return f"{n:,} final-third set pieces {word} ({what})"
    return ''


def _shared_meaning(specs, headline, path, order):
    """On a component-first level 3 the six cells are one stat in six
    situations; its name and meaning, said once under the header, replace
    six identical parentheticals. '' when there is nothing shared to say."""
    if not headline or not path or order != 'component':
        return ''
    meanings = {s.meaning for s in specs}
    if len(meanings) != 1 or not specs[0].meaning:
        return ''
    h = tp.HEADLINES[headline]
    comp = tp.resolve_component('total', path[0], h.side)
    return f"{tp.component_label(h, comp, 'total')} ({specs[0].meaning})"


def _column_major(headline, path, order, cols):
    """Do the frame's groups run down the columns (else along the rows)?"""
    if headline is None:
        return False                       # the overview's pairs sit in rows at 2-wide, columns at 3-wide
    pairs = headline in ('gd', 'xgd') and _shows_components(headline, path, order)
    return (cols == 3 and pairs) or (cols == 2 and not pairs)


def _group_dividers(fig, L, headline, path, order, cols, rows, m, cw, ch, top, gb):
    """A hairline between the frame's groups, along the axis they run on.

    The slot order lays each group along the axis that fits it, but
    adjacency alone did not say so: with equal gaps both ways a cold viewer
    at 9:16 paired GOALS FOR with ON TARGET % across the row - after the
    reorder, and after reading the note. One rule says "columns" (or
    "rows") and nothing else. On the overview the rule sits between the
    goals row and the xG row at 16:9 and between the three pairs at 9:16.
    """
    colour, lw = TEXT_MUTED, 1.2
    inset = 0.06                           # fraction of a cell, so the rule stops short of the dials
    if _column_major(headline, path, order, cols):
        for c in range(1, cols):
            x = m + c * cw
            fig.add_artist(Line2D([x, x], [gb + ch * inset, top - ch * inset], transform=fig.transFigure,
                                  color=colour, linewidth=lw, alpha=0.55))
        return
    # Between rows: centred on the gap between the ink, not on the cell
    # boundary - a row ends on its value line and the next starts with a
    # label and its lead, and a fixed nudge left the rule 29px under one
    # and 11px over the other, reading as a header rule FOR the lower row.
    fig.canvas.draw()
    rnd = fig.canvas.get_renderer()
    inv = fig.transFigure.inverted()
    gauges = [ax for ax in fig.axes if ax.get_aspect() == 1.0]
    lows, highs = {}, {}
    for i, ax in enumerate(gauges):
        r = i // cols
        for t in ax.texts:
            if not t.get_text():
                continue
            bb = t.get_window_extent(rnd).transformed(inv)
            lows[r] = min(lows.get(r, 1.0), bb.y0)
            highs[r] = max(highs.get(r, 0.0), bb.y1)
    for r in range(1, rows):
        y = (lows.get(r - 1, top - r * ch) + highs.get(r, top - r * ch)) / 2.0
        fig.add_artist(Line2D([m + cw * inset, 1.0 - m - cw * inset], [y, y], transform=fig.transFigure,
                              color=colour, linewidth=lw, alpha=0.55))


def _slot_order(specs, headline, path, order, cols, rows):
    """Reading order into the grid. A group is laid along the axis whose
    length matches it: the six-situation frames and the shot / xG
    breakdowns are two triples (rows at 3-wide, columns at 2-wide); the
    two difference breakdowns are three for/against pairs (columns at
    3-wide, rows at 2-wide). Adjacency is the only thing saying "these
    belong together": at 9:16 the three shares that split every shot sat
    across two rows and a cold reader never saw them as a set; at 16:9 ON
    TARGET % and ON TARGET % FACED sat diagonal. The click map follows,
    since boxes and specs are stored in the same slot order."""
    n = cols * rows
    if headline is None or len(specs) < n or not _column_major(headline, path, order, cols):
        return specs
    return [specs[c * rows + r] for r in range(rows) for c in range(cols)]


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
    # The scope is the season: matches and competition. The ranking legend
    # ("RANKED AMONG 20 TEAMS - 1ST = BEST") and the minutes were boilerplate
    # the user struck; the readouts say "/20" and the colour says which way
    # is up. A wider pool still names itself, since the readout does not.
    scope = [f"{gp} MATCHES", f"{comp} {years}".strip()]
    if mode != 'rank':
        scope.append(f"PERCENTILES vs {profile.get('pool_label', '').upper()}")
    if custom_subtitle:
        scope = [custom_subtitle]
    filter_line = 'PENALTIES EXCLUDED' if profile.get('exclude_penalties') else ''

    title = custom_title or (profile.get('team_name') or '').upper()
    # Two different notes. _shared_meaning REPLACES six identical
    # parentheticals, so it silences them; _situation_note is the
    # exposure caveat for a state (or the set-piece supply) and says
    # nothing about the stats, so the cells keep their own meanings.
    shared = _shared_meaning(specs, headline, tuple(path), order)
    if shared:
        specs = [replace(s, meaning='') for s in specs]
    else:
        shared = [_situation_note(profile, headline, tuple(path), order),
                  _spine_note(profile, headline, tuple(path), order),
                  _own_goals_note(profile, headline, tuple(path), order)]
    bottom = _header(fig, L, kicker='TEAM PROFILE', title=title, accent=accent,
                     scope_parts=scope, frame_line=_frame_line(headline, tuple(path), order),
                     filter_line=filter_line, note=shared)

    # The bare unit only where the frame line names the situation (a
    # situation's level 3); the by-situation frame keeps "per 90 min ahead".
    L = dict(L, unit_bare=bool(L.get('short_unit') and path and order == 'situation'))
    # The grid takes what the header leaves.
    m = L['margin']
    top = bottom - L['grid_gap']
    gb = L['grid_bottom']
    cols, rows = L['cols'], L['rows']
    cw = (1.0 - 2 * m) / cols
    ch = (top - gb) / rows
    boxes = []
    cell_px = cw * fig.bbox.width * 0.92
    # Label tracking is one voice per frame: four tracked labels over
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
    specs = _slot_order(specs, headline, tuple(path), order, cols, rows)
    for i, spec in enumerate(specs[:cols * rows]):
        r, c = divmod(i, cols)
        x0 = m + c * cw
        y0 = top - (r + 1) * ch
        bh = ch * (1 - 2 * L['cell_pad_y'])
        if L.get('dial_px'):
            bh = min(bh, _box_px_for_dial(fig, L, L['dial_px'], leads['wrap_px'], leads['meaning_px'])
                     / fig.bbox.height)
        box = [x0 + cw * L['cell_pad_x'], y0 + (ch - bh) / 2.0,
               cw * (1 - 2 * L['cell_pad_x']), bh]
        _gauge(fig, box, spec, L, cell_w=cw, leads=leads)
        boxes.append((x0, y0, x0 + cw, y0 + ch))
    fig.tp_gauge_boxes = boxes
    fig.tp_specs = specs
    _group_dividers(fig, L, headline, tuple(path), order, cols, rows, m, cw, ch, top, gb)

    add_cbs_footer(fig, x0=m, x1=1.0 - m, y=footer_y(fig, at_least=L.get('footer_y', 0.0)))
    return fig
